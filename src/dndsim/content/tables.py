"""The class and subclass tables, read by `table_value()`.

Spell-slot rows are checked against `rules.constants` rather than returned as
columns, and a dataset that disagrees raises (`docs/decisions.md` § w0b,
§ w2a).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from dndsim.content.loader import SOURCE, ContentError, Dataset, plain
from dndsim.content.refs import class_files
from dndsim.core.dice import Dice
from dndsim.rules.constants import MAX_CHARACTER_LEVEL, spell_slots
from dndsim.rules.enums import CasterProgression

__all__ = ["ClassTable", "TableValue", "class_tables", "table_value"]

TableValue = int | Dice

_PROGRESSIONS: Final = MappingProxyType(
    {
        "full": CasterProgression.FULL,
        # 5etools' name for the 2024 half-caster row, which starts at level 1.
        "artificer": CasterProgression.HALF,
        "1/2": CasterProgression.HALF,
        "1/3": CasterProgression.THIRD,
        "pact": CasterProgression.PACT,
    }
)

_PACT_COLUMNS: Final = ("Spell Slots", "Slot Level")


@dataclass(frozen=True, slots=True)
class ClassTable:
    """One class's or subclass's table: each column by its printed heading,
    one value per class level 1-20. Spell-slot columns are not here."""

    name: str
    columns: Mapping[str, tuple[TableValue, ...]]
    progression: CasterProgression | None

    def value(self, column: str, level: int) -> TableValue:
        """The value in `column` at class level `level`.

        Raises `KeyError` naming the columns for an unknown heading, and
        `ValueError` for a level outside 1-20.
        """
        if not 1 <= level <= MAX_CHARACTER_LEVEL:
            raise ValueError(f"level {level} is outside 1-{MAX_CHARACTER_LEVEL}")
        try:
            return self.columns[column][level - 1]
        except KeyError:
            raise KeyError(
                f"{self.name} has no column {column!r}; it has {list(self.columns)}"
            ) from None


def _cell(cell: Any, where: str) -> TableValue:
    if isinstance(cell, int):
        return cell
    if isinstance(cell, str):
        text = plain(cell)
        if text.isdigit():
            return int(text)
        try:
            return Dice.parse(text)
        except ValueError:
            pass
    if isinstance(cell, dict):
        if cell.get("type") in ("bonus", "bonusSpeed"):
            return int(cell["value"])
        if cell.get("type") == "dice" and len(cell.get("toRoll", ())) == 1:
            die = cell["toRoll"][0]
            return Dice(int(die["number"]), int(die["faces"]))
    raise ContentError(f"{where}: cannot read table cell {cell!r}")


def _check_slots(
    name: str, groups: list[Any], progression: CasterProgression | None
) -> None:
    rows = [g["rowsSpellProgression"] for g in groups if "rowsSpellProgression" in g]
    if progression is None or progression is CasterProgression.PACT:
        if rows:
            raise ContentError(f"{name} prints spell slots but has no progression")
        return
    if len(rows) != 1:
        raise ContentError(f"{name} is a caster with {len(rows)} spell-slot tables")
    for level, row in enumerate(rows[0], start=1):
        printed = {lvl: n for lvl, n in enumerate(row, start=1) if n}
        if printed != dict(spell_slots(progression, level)):
            raise ContentError(
                f"{name} level {level} prints slots {printed}; rules.constants "
                f"says {dict(spell_slots(progression, level))}"
            )


def _table(name: str, entry: Any, groups_key: str) -> ClassTable:
    raw = entry.get("casterProgression")
    if raw is not None and raw not in _PROGRESSIONS:
        raise ContentError(f"{name}: unknown caster progression {raw!r}")
    progression = None if raw is None else _PROGRESSIONS[raw]
    groups = entry.get(groups_key, [])
    _check_slots(name, groups, progression)
    columns: dict[str, tuple[TableValue, ...]] = {}
    for group in groups:
        if "rows" not in group:
            continue
        if len(group["rows"]) != MAX_CHARACTER_LEVEL:
            raise ContentError(f"{name}: a table without {MAX_CHARACTER_LEVEL} rows")
        for i, label in enumerate(group["colLabels"]):
            heading = plain(label)
            if heading in columns:
                raise ContentError(f"{name}: two columns headed {heading!r}")
            columns[heading] = tuple(
                _cell(row[i], f"{name} {heading!r}") for row in group["rows"]
            )
    if progression is CasterProgression.PACT:
        _check_pact(name, columns)
    return ClassTable(name, MappingProxyType(columns), progression)


def _check_pact(name: str, columns: Mapping[str, tuple[TableValue, ...]]) -> None:
    count, slot_level = (columns.get(c) for c in _PACT_COLUMNS)
    if count is None or slot_level is None:
        raise ContentError(f"{name} has Pact Magic but no {_PACT_COLUMNS} columns")
    for level in range(1, MAX_CHARACTER_LEVEL + 1):
        printed = {slot_level[level - 1]: count[level - 1]}
        if printed != dict(spell_slots(CasterProgression.PACT, level)):
            raise ContentError(f"{name} level {level} prints Pact Magic {printed}")


def class_tables(data: Dataset) -> Mapping[tuple[str, str | None], ClassTable]:
    """Every XPHB class table keyed `(class, None)`, and every subclass table
    keyed `(class, subclass short name)`. Raises `ContentError` for a table
    shaped otherwise than read, or slots that disagree with `rules.constants`.
    """
    tables: dict[tuple[str, str | None], ClassTable] = {}
    for file in class_files(data):
        for entry in file.get("class", []):
            if entry.get("source") == SOURCE:
                name = entry["name"]
                tables[(name, None)] = _table(name, entry, "classTableGroups")
        for entry in file.get("subclass", []):
            if entry.get("source") == SOURCE and "subclassTableGroups" in entry:
                key = (entry["className"], entry["shortName"])
                label = f"{key[0]} ({key[1]})"
                tables[key] = _table(label, entry, "subclassTableGroups")
    return MappingProxyType(tables)


def table_value(
    tables: Mapping[tuple[str, str | None], ClassTable],
    class_name: str,
    column: str,
    level: int,
    subclass: str | None = None,
) -> TableValue:
    """`tables[(class_name, subclass)].value(column, level)`: e.g.
    `table_value(tables, "Barbarian", "Rage Damage", 9)` is 3."""
    try:
        table = tables[(class_name, subclass)]
    except KeyError:
        raise KeyError(f"no table for {(class_name, subclass)}") from None
    return table.value(column, level)
