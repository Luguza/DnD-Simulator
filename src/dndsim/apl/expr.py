"""The clause compiler: parse, reject any node outside an allowlist, resolve
every dotted path against a `NamespaceSchema`, and emit a callable of one
`Namespace` (DESIGN.md § Condition language).

What is executed is the checked tree, rewritten, never the author's text: a
root becomes a read off the namespace, a group compared with a number its
length, and a field read off a creature-valued expression a call that reads
`0` from nobody (`docs/decisions.md` § w2e).
"""

import ast
import difflib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from dndsim.apl.namespace import Namespace, hp_pct
from dndsim.apl.schema import (
    CREATURE_FIELDS,
    GROUP_ROOTS,
    PICKERS,
    ROOTS,
    Field,
    Kind,
    NamespaceSchema,
)
from dndsim.core.errors import DndSimError
from dndsim.core.geometry import distance_ft
from dndsim.creatures.creature import Creature

__all__ = [
    "AplLoadError",
    "Compiled",
    "MoveClause",
    "MoveMode",
    "compile_condition",
    "compile_move",
    "compile_param",
    "compile_selector",
]


class AplLoadError(DndSimError, ValueError):
    """An APL clause or entry that cannot be compiled, raised at load time."""


@dataclass(frozen=True, slots=True)
class Compiled:
    """One compiled clause: its text, the kind it evaluates to, the paths it
    reads, and the callable that evaluates it."""

    source: str
    kind: Kind
    reads: frozenset[str]
    fn: Callable[[Namespace], Any]


class MoveMode(Enum):
    TOWARD = "toward"
    AWAY_FROM = "away_from"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class MoveClause:
    """A compiled `move:`: the mode, the selector it moves relative to, and for
    `away_from` the distance it keeps (`None` with `to_max_range`)."""

    source: str
    mode: MoveMode
    selector: Compiled | None = None
    keep_ft: int | None = None


# --- what compiled code calls -------------------------------------------------


def _lowest_hp(group: Sequence[Creature]) -> Creature | None:
    return min(group, key=_hp, default=None)


def _highest_hp(group: Sequence[Creature]) -> Creature | None:
    return max(group, key=_hp, default=None)


def _first(group: Sequence[Creature]) -> Creature | None:
    return group[0] if group else None


def _nearest(ns: Namespace, group: Sequence[Creature]) -> Creature | None:
    return min(group, key=_distance_from(ns), default=None)


def _farthest(ns: Namespace, group: Sequence[Creature]) -> Creature | None:
    return max(group, key=_distance_from(ns), default=None)


def _distance_from(ns: Namespace) -> Callable[[Creature], int]:
    own = ns.self.creature
    origin, size = own.position, own.size

    def distance(creature: Creature) -> int:
        return distance_ft(
            origin, creature.position, origin_size=size, destination_size=creature.size
        )

    return distance


def _hp(creature: Creature) -> int:
    return creature.hp


_READ_CREATURE: Final[dict[str, Callable[[Creature | None], Any]]] = {
    "hp": lambda c: 0 if c is None else c.hp,
    "max_hp": lambda c: 0 if c is None else c.max_hp,
    "hp_pct": lambda c: 0.0 if c is None else hp_pct(c),
    "ac": lambda c: 0 if c is None else c.armour_class,
    "reach_ft": lambda c: 0 if c is None else c.reach_ft,
    "speed_ft": lambda c: 0 if c is None else c.speed_ft,
    "down": lambda c: c is not None and c.is_down,
}

_GLOBALS: Final[dict[str, Any]] = {
    "__builtins__": {},
    "_len": len,
    "_lowest_hp": _lowest_hp,
    "_highest_hp": _highest_hp,
    "_first": _first,
    "_nearest": _nearest,
    "_farthest": _farthest,
    **{f"_creature_{name}": read for name, read in _READ_CREATURE.items()},
}

_NEEDS_ORIGIN: Final = frozenset({"nearest", "farthest"})

_COMPARISONS: Final = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
_ARITHMETIC: Final = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_TRUTHY: Final = frozenset({Kind.BOOL, Kind.GROUP})


# --- the checker and rewriter --------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Typed:
    node: ast.expr
    kind: Kind
    path: str
    members: Field | None = None


class _Compiler:
    __slots__ = ("reads", "schema", "source")

    def __init__(self, source: str, schema: NamespaceSchema) -> None:
        self.source = source
        self.schema = schema
        self.reads: set[str] = set()

    def fail(self, message: str) -> AplLoadError:
        return AplLoadError(f"{self.source!r}: {message}")

    def value(self, node: ast.expr) -> _Typed:
        """`node` checked and rewritten, as a value: a bare group root reads
        as its `alive`."""
        typed = self.walk(node)
        if typed.kind is not Kind.NODE:
            return typed
        if typed.path in GROUP_ROOTS:
            self.reads.add(f"{typed.path}.alive")
            return _Typed(
                ast.Attribute(typed.node, "alive", ast.Load()), Kind.GROUP, typed.path
            )
        members = sorted(self._members_of(typed))
        raise self.fail(
            f"{typed.path} is not a value; read one of its members: {members}"
        )

    def number(self, node: ast.expr) -> ast.expr:
        typed = self.value(node)
        if typed.kind is Kind.GROUP:
            return ast.Call(ast.Name("_len", ast.Load()), [typed.node], [])
        if typed.kind is not Kind.NUMBER:
            raise self.fail(f"{typed.path} is a {typed.kind.value}, not a number")
        return typed.node

    def truthy(self, node: ast.expr) -> ast.expr:
        typed = self.value(node)
        if typed.kind not in _TRUTHY:
            raise self.fail(
                f"{typed.path} is a {typed.kind.value}; a condition is a bool, "
                "or a group that is true when it is not empty"
            )
        return typed.node

    def walk(self, node: ast.expr) -> _Typed:
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.Name):
            return self._name(node)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.BoolOp):
            values = [self.truthy(v) for v in node.values]
            return _Typed(ast.BoolOp(node.op, values), Kind.BOOL, "a condition")
        if isinstance(node, ast.UnaryOp):
            return self._unary(node)
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ARITHMETIC):
                raise self.fail(f"{type(node.op).__name__} is not allowed")
            if isinstance(node.op, ast.Div) and not _nonzero_literal(node.right):
                # A clause that loads must not raise mid-encounter, and an
                # unbound `target` reads 0 (`docs/decisions.md` § w2e).
                raise self.fail("division is only by a non-zero number written out")
            arithmetic: ast.expr = ast.BinOp(
                self.number(node.left), node.op, self.number(node.right)
            )
            return _Typed(arithmetic, Kind.NUMBER, "a sum")
        if isinstance(node, ast.IfExp):
            body, orelse = self.value(node.body), self.value(node.orelse)
            if body.kind is not orelse.kind:
                raise self.fail(
                    f"both arms of an if-else are one kind; got {body.kind.value} "
                    f"and {orelse.kind.value}"
                )
            conditional: ast.expr = ast.IfExp(
                self.truthy(node.test), body.node, orelse.node
            )
            return _Typed(conditional, body.kind, "an if-else")
        raise self.fail(
            f"{type(node).__name__} is not allowed in an APL clause; the grammar "
            "is comparisons, and/or/not, arithmetic, if-else, namespace paths, "
            "selectors and constants"
        )

    def _constant(self, node: ast.Constant) -> _Typed:
        value = node.value
        if isinstance(value, bool):
            return _Typed(node, Kind.BOOL, repr(value))
        if isinstance(value, int | float):
            return _Typed(node, Kind.NUMBER, repr(value))
        raise self.fail(f"{value!r} is not a number or a bool")

    def _name(self, node: ast.Name) -> _Typed:
        name = node.id
        if name.startswith("_"):
            raise self.fail(f"{name} is private")
        if name in ROOTS:
            read = ast.Attribute(ast.Name("ns", ast.Load()), name, ast.Load())
            return _Typed(read, Kind.NODE, name)
        if name in PICKERS:
            raise self.fail(f"{name} is a selector: write `{name} of <group>`")
        raise self.fail(f"unknown name {name!r}{_suggest(name, [*ROOTS, *PICKERS])}")

    def _members_of(self, typed: _Typed) -> dict[str, Field]:
        if typed.members is not None:
            return dict(typed.members.members)
        return dict(self.schema.members(typed.path))

    def _attribute(self, node: ast.Attribute) -> _Typed:
        name = node.attr
        if name.startswith("_"):
            raise self.fail(f"{name} is private")
        owner = self.walk(node.value)
        path = f"{owner.path}.{name}"
        if owner.kind is Kind.CREATURE:
            if name not in CREATURE_FIELDS:
                raise self.fail(
                    f"a creature has no {name!r}{_suggest(name, CREATURE_FIELDS)}"
                )
            self.reads.add(path)
            of_creature: ast.expr = ast.Call(
                ast.Name(f"_creature_{name}", ast.Load()), [owner.node], []
            )
            return _Typed(of_creature, CREATURE_FIELDS[name].kind, path)
        if owner.kind is not Kind.NODE:
            raise self.fail(f"{owner.path} is a {owner.kind.value}; it has no {name!r}")
        members = self._members_of(owner)
        if name not in members:
            raise self.fail(f"unknown path {path!r}{_suggest(name, members)}")
        found = members[name]
        if found.params is not None:
            raise self.fail(f"{path} takes arguments: write {path}(...)")
        self.reads.add(path)
        member: ast.expr = ast.Attribute(owner.node, name, ast.Load())
        return _Typed(
            member, found.kind, path, found if found.kind is Kind.NODE else None
        )

    def _call(self, node: ast.Call) -> _Typed:
        if node.keywords:
            raise self.fail("keyword arguments are not allowed")
        func = node.func
        if isinstance(func, ast.Name) and func.id in PICKERS:
            if len(node.args) != 1:
                raise self.fail(f"{func.id} takes one group")
            group = self.value(node.args[0])
            if group.kind is not Kind.GROUP:
                raise self.fail(f"{func.id} picks from a group, not {group.path}")
            args: list[ast.expr] = [group.node]
            if func.id in _NEEDS_ORIGIN:
                args.insert(0, ast.Name("ns", ast.Load()))
            picked = ast.Call(ast.Name(f"_{func.id}", ast.Load()), args, [])
            return _Typed(picked, Kind.CREATURE, f"{func.id} of {group.path}")
        if isinstance(func, ast.Attribute) and not func.attr.startswith("_"):
            owner = self.walk(func.value)
            if owner.kind is Kind.NODE:
                members = self._members_of(owner)
                found = members.get(func.attr)
                path = f"{owner.path}.{func.attr}"
                if found is None:
                    raise self.fail(
                        f"unknown path {path!r}{_suggest(func.attr, members)}"
                    )
                if found.params is None:
                    raise self.fail(f"{path} is not a method")
                if len(node.args) != len(found.params):
                    raise self.fail(f"{path} takes {len(found.params)} argument(s)")
                self.reads.add(path)
                args = [self.number(arg) for arg in node.args]
                call = ast.Call(
                    ast.Attribute(owner.node, func.attr, ast.Load()), args, []
                )
                return _Typed(call, found.kind, path)
        raise self.fail(
            f"only a selector ({sorted(PICKERS)}) or a namespace method can be called"
        )

    def _compare(self, node: ast.Compare) -> _Typed:
        for op in node.ops:
            if not isinstance(op, _COMPARISONS):
                raise self.fail(f"{type(op).__name__} is not allowed")
        operands = [node.left, *node.comparators]
        kinds = {self.value(operand).kind for operand in operands}
        ordering = any(not isinstance(op, ast.Eq | ast.NotEq) for op in node.ops)
        if ordering or kinds & {Kind.NUMBER, Kind.GROUP}:
            rewritten = [self.number(operand) for operand in operands]
        elif kinds == {Kind.BOOL} or kinds == {Kind.CELL}:
            rewritten = [self.value(operand).node for operand in operands]
        else:
            raise self.fail(
                "only numbers and bools compare, and squares for equality; got "
                f"{sorted(k.value for k in kinds)}"
            )
        return _Typed(
            ast.Compare(rewritten[0], node.ops, rewritten[1:]),
            Kind.BOOL,
            "a comparison",
        )

    def _unary(self, node: ast.UnaryOp) -> _Typed:
        if isinstance(node.op, ast.Not):
            return _Typed(
                ast.UnaryOp(node.op, self.truthy(node.operand)), Kind.BOOL, "not"
            )
        if isinstance(node.op, ast.USub):
            return _Typed(
                ast.UnaryOp(node.op, self.number(node.operand)),
                Kind.NUMBER,
                "a negation",
            )
        raise self.fail(f"{type(node.op).__name__} is not allowed")


def _suggest(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1)
    return f"; did you mean {close[0]!r}?" if close else ""


def _compile(
    source: str, schema: NamespaceSchema, allowed: frozenset[Kind]
) -> Compiled:
    compiler = _Compiler(source, schema)
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as error:
        raise compiler.fail(f"not an expression ({error.msg})") from None
    typed = compiler.value(tree.body)
    if typed.kind not in allowed:
        wanted = " or ".join(sorted(kind.value for kind in allowed))
        raise compiler.fail(f"evaluates to a {typed.kind.value}, not a {wanted}")
    arguments = ast.arguments(
        posonlyargs=[],
        args=[ast.arg("ns")],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )
    lambda_ = ast.Expression(ast.Lambda(arguments, typed.node))
    code = compile(ast.fix_missing_locations(lambda_), "<apl>", "eval")
    fn = eval(code, dict(_GLOBALS))  # the checked, rewritten tree; no author text
    return Compiled(source, typed.kind, frozenset(compiler.reads), fn)


def _nonzero_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def compile_condition(source: str, schema: NamespaceSchema) -> Compiled:
    """An `if:` clause, true or false. Raises `AplLoadError`."""
    return _compile(source, schema, frozenset({Kind.BOOL, Kind.GROUP}))


def compile_param(source: str, schema: NamespaceSchema) -> Compiled:
    """A `with:` parameter: a number or a truth value. Raises `AplLoadError`."""
    return _compile(source, schema, frozenset({Kind.NUMBER, Kind.BOOL}))


_OF: Final = re.compile(r"(?P<picker>\w+)\s+of\s+(?P<group>.+)")


def compile_selector(source: str, schema: NamespaceSchema) -> Compiled:
    """A `target:` clause or a `move:` clause's selector: one creature, or an
    ordered group. `<picker> of <group>` reads as `<picker>(<group>)`.
    Raises `AplLoadError`."""
    text = source.strip()
    match = _OF.fullmatch(text)
    if match is not None:
        picker = match["picker"]
        if picker not in PICKERS:
            raise AplLoadError(
                f"{source!r}: unknown selector {picker!r}{_suggest(picker, PICKERS)}"
            )
        text = f"{picker}({match['group']})"
    compiled = _compile(text, schema, frozenset({Kind.CREATURE, Kind.GROUP}))
    return Compiled(source, compiled.kind, compiled.reads, compiled.fn)


_TOWARD: Final = re.compile(r"toward\s+(?P<selector>.+?)\s+until\s+in_reach")
_AWAY: Final = re.compile(
    r"away_from\s+(?P<selector>.+?)\s+(?:to_max_range|to\s+(?P<feet>\d+)\s+ft)"
)


def compile_move(source: str, schema: NamespaceSchema) -> MoveClause:
    """A `move:` clause: `toward <selector> until in_reach`,
    `away_from <selector> to_max_range`, `away_from <selector> to <n> ft` or
    `hold`. Raises `AplLoadError`."""
    text = source.strip()
    if text == "hold":
        return MoveClause(source, MoveMode.HOLD)
    match = _TOWARD.fullmatch(text)
    if match is not None:
        selector = compile_selector(match["selector"], schema)
        return MoveClause(source, MoveMode.TOWARD, selector)
    match = _AWAY.fullmatch(text)
    if match is not None:
        selector = compile_selector(match["selector"], schema)
        feet = match["feet"]
        keep = None if feet is None else int(feet)
        if keep is not None and keep < 5:
            raise AplLoadError(f"{source!r}: a retreat keeps 5 ft or more")
        return MoveClause(source, MoveMode.AWAY_FROM, selector, keep)
    raise AplLoadError(
        f"{source!r}: a move: is `toward <selector> until in_reach`, "
        "`away_from <selector> to_max_range`, `away_from <selector> to <n> ft` "
        "or `hold`"
    )
