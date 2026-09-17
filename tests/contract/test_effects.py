"""The w1c effect contract: what is attached to a creature, and how it ends.

The three cases DESIGN.md § Durations names are the three shapes of ending, and
each of them is a test below:

- **Rage** ends on a deadline that its own handlers slide forward, so a
  re-application must not become a second Rage and a refresh must recompute
  against the clock rather than add a round.
- **Vex** carries a deadline *and* a charge, and ends on whichever runs out
  first — which is only a property if neither can end it while the other still
  has something left to give.
- **Reckless Attack** ends "until the start of your next turn", the half of
  `Expiry.through_phase` that is gone *before* the boundary's own handlers run.
  It is the hard stop: nothing on the owner's turn may see it.

Around them, the two things the store owns that an effect must not: attachment,
because `hooks/dispatch.py` rebuilds a dispatch table on every attach and
detach, and the clock, because a `Duration` is relative and only the store is
told what the moment is.

Sources: DESIGN.md § Durations; `core/timeline.py`; ROADMAP w1c.
"""

from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.errors import EngineAssemblyError, FeatureContractError
from dndsim.core.geometry import Cell
from dndsim.core.timeline import Duration, Expiry, InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.creatures.effects import Effect, EffectStore, Stacking
from dndsim.creatures.stats import StatBlock
from dndsim.hooks.contexts import Combatant, EffectContext, TurnContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import ON_TURN_START
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))


def key(initiative: int) -> InitiativeKey:
    return InitiativeKey(
        initiative=initiative, dexterity=10, player_character=True, tiebreak=0.5
    )


BARBARIAN: Final = key(15)
"""Acts first."""

OGRE: Final = key(5)
"""Acts second, so a deadline anchored to one of them lands in a different round
depending on which is acting."""


def creature(
    name: str = "barbarian", *, initiative: InitiativeKey = BARBARIAN
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=20, level=1),
        position=Cell(0, 0),
        initiative=initiative,
    )


def at(round_: int, initiative: InitiativeKey, phase: TurnPhase) -> TimeKey:
    return TimeKey(round_, initiative, phase)


class Rage(Effect):
    """Resistance for a round, extended by attacking (DESIGN.md § Durations)."""

    __slots__ = ()

    ID = "barbarian.rage"


class Vex(Effect):
    """Advantage on the next attack, and not for longer than a round."""

    __slots__ = ()

    ID = "fighter.vex"


class Mark(Effect):
    """One per hunter, so two hunters mark the same quarry separately."""

    __slots__ = ()

    ID = "ranger.hunters_mark"
    STACKING = Stacking.UNIQUE_PER_SOURCE


class Poison(Effect):
    """Two doses are two doses."""

    __slots__ = ()

    ID = "test.poison"
    STACKING = Stacking.STACK


class Blessing(Effect):
    """Once only: a second casting does nothing at all."""

    __slots__ = ()

    ID = "test.blessing"
    STACKING = Stacking.REJECT


class Reckless(Effect):
    """Advantage against you until your next turn begins, recording whether it
    was still attached when that turn began."""

    __slots__ = ("seen",)

    ID = "barbarian.reckless_attack"

    def __init__(self, owner: Combatant) -> None:
        super().__init__(
            owner, duration=Duration.until_start_of_next_turn_of(BARBARIAN)
        )
        self.seen: list[TimeKey] = []

    @hook("on_turn_start")
    def note(self, ctx: TurnContext) -> R:
        self.seen.append(ctx.time)
        return R.APPLIED


# --- The three shapes of ending ---------------------------------------------


def test_rage_is_extended_rather_than_stacked() -> None:
    """Rage is applied on the barbarian's first turn, ending at the end of its
    next one — round 2. Saying it again on round 2 slides the deadline to round
    3 and leaves one Rage attached, which is what `Stacking.REFRESH` buys and
    why no feature writes "am I already raging".
    """
    barbarian = creature()
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    duration = Duration.until_end_of_next_turn_of(BARBARIAN)

    first = barbarian.effects.apply(Rage(barbarian, duration=duration))
    assert first is not None
    assert first.expiry == duration.resolve(at(1, BARBARIAN, TurnPhase.END))

    barbarian.effects.expire_before(at(2, BARBARIAN, TurnPhase.START))
    again = barbarian.effects.apply(Rage(barbarian, duration=duration))

    assert again is first
    assert len(barbarian.effects) == 1
    assert first.expiry == duration.resolve(at(2, BARBARIAN, TurnPhase.START))


def test_refresh_recomputes_against_the_clock_rather_than_adding_a_round() -> None:
    """DESIGN.md's Rage row calls `refresh()` from three hooks, all of which can
    fire more than once on one turn. Refreshing twice inside the same turn must
    therefore be the same deadline as refreshing once, which addition would
    not be."""
    barbarian = creature()
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    rage = barbarian.effects.apply(
        Rage(barbarian, duration=Duration.until_end_of_next_turn_of(BARBARIAN))
    )
    assert rage is not None

    barbarian.effects.expire_before(at(2, BARBARIAN, TurnPhase.START))
    rage.refresh()
    once = rage.expiry
    rage.refresh()

    assert rage.expiry == once
    assert once == Expiry(TimeKey(3, BARBARIAN, TurnPhase.END), through_phase=True)


def test_vex_ends_on_its_charge_while_its_deadline_still_stands() -> None:
    """Both halves are live and the charge runs out first: consuming it removes
    the effect on the turn it was applied, long before the deadline it also
    carries."""
    fighter = creature(name="fighter")
    vex = fighter.effects.apply(
        Vex(
            fighter,
            duration=Duration.until_end_of_next_turn_of(BARBARIAN),
            charges=1,
        )
    )
    assert vex is not None

    assert vex.consume() == 0
    assert len(fighter.effects) == 0


def test_vex_ends_on_its_deadline_while_its_charge_still_stands() -> None:
    """The other way round: the attack that would have spent the charge never
    came, and the sweep at the end of the next turn takes it anyway."""
    fighter = creature(name="fighter")
    fighter.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    vex = fighter.effects.apply(
        Vex(
            fighter,
            duration=Duration.until_end_of_next_turn_of(BARBARIAN),
            charges=1,
        )
    )
    assert vex is not None

    fighter.effects.expire_after(at(2, BARBARIAN, TurnPhase.END))

    assert len(fighter.effects) == 0
    assert vex.charges == 1


def test_reckless_attack_is_gone_before_its_owners_turn_starts() -> None:
    """The hard stop. "Until the start of your next turn" is
    `through_phase=False`, so the sweep that runs *before* a boundary's handlers
    takes it — and the effect's own `on_turn_start` handler never sees the turn
    it was due to end on.
    """
    barbarian = creature()
    watcher = barbarian.effects.apply(Reckless(barbarian))
    assert isinstance(watcher, Reckless)

    start = at(2, BARBARIAN, TurnPhase.START)
    barbarian.effects.expire_before(start)
    barbarian.dispatch(ON_TURN_START, TurnContext(creature=barbarian, time=start))

    assert len(barbarian.effects) == 0
    assert watcher.seen == []


def test_an_effect_lasting_through_its_boundary_still_answers_on_it() -> None:
    """Rage's half of the same flag, and the reason the sweep is two calls
    rather than a boolean: an effect ending at the end of a turn is attached
    while that turn's handlers run and gone once they have."""
    barbarian = creature()
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    barbarian.effects.apply(
        Rage(barbarian, duration=Duration.until_end_of_next_turn_of(BARBARIAN))
    )

    end = at(2, BARBARIAN, TurnPhase.END)
    barbarian.effects.expire_before(end)
    assert len(barbarian.effects) == 1

    barbarian.effects.expire_after(end)
    assert len(barbarian.effects) == 0


# --- Stacking, the Strategy read off the class ------------------------------


def test_a_stacking_effect_is_held_twice() -> None:
    """The one policy under which a creature holds two of one effect, each with
    its own charges."""
    ogre = creature(name="ogre", initiative=OGRE)
    ogre.effects.apply(Poison(ogre, charges=2))
    ogre.effects.apply(Poison(ogre, charges=3))

    assert sorted(held.charges or 0 for held in ogre.effects) == [2, 3]


def test_a_rejected_effect_lands_nothing_and_says_so() -> None:
    """`REJECT` is the policy whose whole content is the return value: the
    incumbent is untouched and the caller is told nothing happened, which is
    what lets a scorer price a re-application at zero without a condition
    written anywhere."""
    cleric = creature(name="cleric")
    first = cleric.effects.apply(Blessing(cleric))

    assert cleric.effects.apply(Blessing(cleric)) is None
    assert list(cleric.effects) == [first]


def test_unique_per_source_separates_two_casters_and_merges_one() -> None:
    """Two rangers marking the same quarry are two marks; the same ranger
    marking twice is one."""
    quarry = creature(name="quarry", initiative=OGRE)
    one, two = creature(name="one"), creature(name="two")

    first = quarry.effects.apply(Mark(quarry, caster=one))
    assert quarry.effects.apply(Mark(quarry, caster=one)) is first
    assert quarry.effects.apply(Mark(quarry, caster=two)) is not first

    assert len(quarry.effects) == 2


def test_unique_per_source_without_a_source_is_refused_at_construction() -> None:
    """The one declaration that cannot be honoured: a policy keyed on the caster
    with no caster to key on. Refused where it is written rather than at the
    second application, which might be a different fight."""
    quarry = creature(name="quarry")
    with pytest.raises(ValueError, match="unique per"):
        Mark(quarry)


# --- Attachment and charges -------------------------------------------------


def test_an_applied_effect_is_attached_and_an_expired_one_is_detached() -> None:
    """The store owns both halves. An effect that attached itself would put a
    second hand on the dispatch table `hooks/dispatch.py` rebuilds on every
    attach and detach."""
    barbarian = creature()
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    watcher = barbarian.effects.apply(Reckless(barbarian))
    assert watcher is not None

    assert barbarian.dispatcher.sources() == (watcher,)
    barbarian.effects.expire_before(at(2, BARBARIAN, TurnPhase.START))
    assert barbarian.dispatcher.sources() == ()


def test_an_effect_owned_by_somebody_else_is_refused() -> None:
    """Going through `Creature.attach()` rather than the dispatcher is what
    makes the ownership check cover an effect: `self.owner` inside its handlers
    being the wrong creature is the one mistake hook `Scope` exists to prevent.
    Nothing is held when it is refused."""
    barbarian, ogre = creature(), creature(name="ogre", initiative=OGRE)

    with pytest.raises(ValueError, match="cannot be attached"):
        ogre.effects.apply(Rage(barbarian))
    assert len(ogre.effects) == 0


def test_consuming_more_charges_than_remain_raises() -> None:
    """`charges` is readable, so reaching this means a caller decided not to
    ask — the bargain `ActionEconomy` makes about spending what is not
    there."""
    fighter = creature(name="fighter")
    effect = fighter.effects.apply(Vex(fighter, charges=2))
    assert effect is not None

    with pytest.raises(ValueError, match="2 charge"):
        effect.consume(3)
    assert effect.charges == 2


def test_consuming_an_effect_that_counts_no_charges_raises() -> None:
    """An effect with no charges ends on its deadline or when something removes
    it; consuming one is a feature that thinks it declared `charges=` and did
    not."""
    barbarian = creature()
    rage = barbarian.effects.apply(Rage(barbarian))
    assert rage is not None

    with pytest.raises(ValueError, match="counts no charges"):
        rage.consume()


def test_zero_charges_is_refused_where_it_is_written() -> None:
    """An effect that is over before it starts is a mistake in the feature, and
    `charges=None` is how "does not count them" is said."""
    barbarian = creature()
    with pytest.raises(ValueError, match="0 charges"):
        Rage(barbarian, charges=0)


def test_an_unattached_effect_can_be_neither_refreshed_nor_consumed() -> None:
    """The store holds the clock a refresh reads and the creature a spent
    effect detaches from, so neither means anything before one has it."""
    barbarian = creature()
    loose = Rage(barbarian, duration=Duration.until_end_of_next_turn_of(BARBARIAN))

    with pytest.raises(EngineAssemblyError, match="refreshed"):
        loose.refresh()
    with pytest.raises(EngineAssemblyError, match="consumed"):
        Vex(barbarian, charges=1).consume()


# --- The clock the store is told --------------------------------------------


def test_a_duration_resolves_against_the_clock_the_store_was_last_told() -> None:
    """No attack context carries a `TimeKey` and `resolve_attack()` is handed
    none, so an effect applied from inside an attack resolves against the
    boundary the turn loop last swept. The two anchors below are the same
    duration applied at the same moment, and they differ by a round because one
    creature's turn has been and the other's has not.
    """
    ogre = creature(name="ogre", initiative=OGRE)
    ogre.effects.expire_after(at(3, BARBARIAN, TurnPhase.END))

    mine = ogre.effects.apply(
        Poison(ogre, duration=Duration.until_end_of_next_turn_of(BARBARIAN))
    )
    theirs = ogre.effects.apply(
        Poison(ogre, duration=Duration.until_end_of_next_turn_of(OGRE))
    )
    assert mine is not None and theirs is not None

    assert mine.expiry is not None and mine.expiry.end.round == 4
    assert theirs.expiry is not None and theirs.expiry.end.round == 3


def test_a_store_starts_at_the_top_of_the_encounter() -> None:
    """Nothing advances the clock until w2c's turn loop (debt #29), so a
    creature that has never been swept reads round 1. It is stated here so the
    debt has a number attached to it rather than a default nobody chose."""
    assert creature().effects.now == TimeKey(1, UNSEATED, TurnPhase.START)


def test_a_store_holds_nothing_until_something_is_applied() -> None:
    barbarian = creature()
    assert len(barbarian.effects) == 0
    assert list(barbarian.effects) == []
    assert isinstance(barbarian.effects, EffectStore)


# --- The two hooks only an `EffectStore` can fire ---------------------------


class ReportingEffect(Effect):
    """An effect carrying both hooks itself, to pin which of the two it is
    attached for."""

    __slots__ = ("seen",)

    ID = "test.reporting_effect"

    def __init__(self, owner: Combatant) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_effect_applied")
    def applied(self, ctx: EffectContext) -> R:
        self.seen.append(f"applied:{ctx.effect.name}")
        return R.APPLIED

    @hook("on_effect_expired")
    def expired(self, ctx: EffectContext) -> R:
        self.seen.append(f"expired:{ctx.effect.name}")
        return R.APPLIED


class Watcher(HookSource):
    """Records both effect hooks in the order they fire, by effect name."""

    __slots__ = ("seen",)

    ID = "test.watcher"

    def __init__(self, owner: Combatant) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_effect_applied")
    def applied(self, ctx: EffectContext) -> R:
        self.seen.append(f"applied:{ctx.effect.name}")
        return R.APPLIED

    @hook("on_effect_expired")
    def expired(self, ctx: EffectContext) -> R:
        self.seen.append(f"expired:{ctx.effect.name}")
        return R.APPLIED


class Immune(HookSource):
    """A feature that refuses one effect outright — what a veto on the
    application is for."""

    __slots__ = ()

    ID = "test.immune"

    @hook("on_effect_applied", phase=Phase.EARLY)
    def refuse(self, ctx: EffectContext) -> R:
        ctx.veto("immune")
        return R.APPLIED


class Clings(HookSource):
    """A feature vetoing an ending, which is the mutator used where it has no
    meaning."""

    __slots__ = ()

    ID = "test.clings"

    @hook("on_effect_expired", phase=Phase.EARLY)
    def refuse(self, ctx: EffectContext) -> R:
        ctx.veto("not yet")
        return R.APPLIED


def test_applying_an_effect_fires_the_hook_only_it_can_fire() -> None:
    """`hooks/definitions.py` closes the registry at 39 and two of them —
    `on_effect_applied` and `on_effect_expired` — have no possible caller but
    this store. A hook nothing fires is a feature that runs nothing and says
    nothing, which is what the closed registry exists to prevent."""
    barbarian = creature()
    watcher = Watcher(barbarian)
    barbarian.attach(watcher)

    barbarian.effects.apply(Rage(barbarian))

    assert watcher.seen == ["applied:barbarian.rage"]


def test_an_extension_is_an_application_too() -> None:
    """`on_effect_applied`'s own summary names it — "Rage extending rather than
    stacking" — so a second application that the incumbent absorbs fires the
    hook as the first did."""
    barbarian = creature()
    watcher = Watcher(barbarian)
    barbarian.attach(watcher)

    barbarian.effects.apply(Rage(barbarian))
    barbarian.effects.apply(Rage(barbarian))

    assert watcher.seen == ["applied:barbarian.rage"] * 2
    assert len(barbarian.effects) == 1


def test_a_rejected_application_fires_nothing() -> None:
    """`REJECT` means nothing happened, and a hook that fired anyway would be a
    handler told about an event that did not take place."""
    cleric = creature(name="cleric")
    watcher = Watcher(cleric)
    cleric.attach(watcher)

    cleric.effects.apply(Blessing(cleric))
    cleric.effects.apply(Blessing(cleric))

    assert watcher.seen == ["applied:test.blessing"]


def test_a_veto_on_the_application_keeps_the_effect_off() -> None:
    """An effect is "on its way onto a creature" (`hooks/contexts.py`) until
    this hook has run, so refusing one here is what a feature immune to it
    does — and nothing is attached afterwards."""
    barbarian = creature()
    barbarian.attach(Immune(barbarian))

    assert barbarian.effects.apply(Rage(barbarian)) is None
    assert len(barbarian.effects) == 0


def test_both_ways_an_effect_ends_fire_the_expiry_hook() -> None:
    """The summary names the two: "reaches its expiry or runs out of charges"."""
    fighter = creature(name="fighter")
    watcher = Watcher(fighter)
    fighter.attach(watcher)
    fighter.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))

    vex = fighter.effects.apply(Vex(fighter, charges=1))
    assert vex is not None
    vex.consume()

    fighter.effects.apply(
        Rage(fighter, duration=Duration.until_end_of_next_turn_of(BARBARIAN))
    )
    fighter.effects.expire_after(at(2, BARBARIAN, TurnPhase.END))

    assert watcher.seen == [
        "applied:fighter.vex",
        "expired:fighter.vex",
        "applied:barbarian.rage",
        "expired:barbarian.rage",
    ]


def test_an_effect_answers_its_own_ending_and_not_its_own_arrival() -> None:
    """The span an effect's handlers run for is exactly the span it is
    attached. `on_effect_applied` fires before the attach, which is the
    snapshot rule; `on_effect_expired` fires before the detach, which is the
    only moment an ending effect has left to act in."""
    barbarian = creature()
    watcher = ReportingEffect(barbarian)

    barbarian.effects.apply(watcher)
    barbarian.effects.remove(watcher)

    assert watcher.seen == ["expired:test.reporting_effect"]


def test_a_veto_on_an_ending_is_refused() -> None:
    """The other half of the split `engine/attack.py` makes: a veto where it has
    no meaning and never will be given one raises rather than being ignored."""
    barbarian = creature()
    barbarian.attach(Clings(barbarian))
    rage = barbarian.effects.apply(Rage(barbarian))
    assert rage is not None

    with pytest.raises(FeatureContractError, match="vetoed barbarian.rage"):
        barbarian.effects.remove(rage)


class Dispels(HookSource):
    """A handler that removes another effect when any effect on its owner ends
    — a chain ending with whatever anchored it."""

    __slots__ = ("_quarry", "_store")

    ID = "test.dispels"

    def __init__(self, owner: Creature, quarry: Effect) -> None:
        super().__init__(owner)
        self._store = owner.effects
        self._quarry = quarry

    @hook("on_effect_expired")
    def dispel(self, ctx: EffectContext) -> R:
        if self._quarry not in list(self._store):
            return R.SKIPPED
        self._store.remove(self._quarry)
        return R.APPLIED


def test_a_sweep_survives_a_handler_that_removes_what_it_was_about_to() -> None:
    """Two effects expire on the same boundary and the first one's handler
    dispels the second. The sweep took its snapshot before either ran, so
    without a check it would remove the second a second time — firing its
    expiry hook twice and then dying inside `list.remove`."""
    barbarian = creature()
    watcher = Watcher(barbarian)
    barbarian.attach(watcher)
    duration = Duration.until_end_of_next_turn_of(BARBARIAN)
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))

    first = barbarian.effects.apply(Rage(barbarian, duration=duration))
    second = barbarian.effects.apply(Vex(barbarian, duration=duration))
    assert first is not None and second is not None
    barbarian.attach(Dispels(barbarian, second))

    barbarian.effects.expire_after(at(2, BARBARIAN, TurnPhase.END))

    assert watcher.seen.count("expired:fighter.vex") == 1
    assert len(barbarian.effects) == 0


def test_removing_an_effect_twice_says_so_rather_than_firing_twice() -> None:
    """The store lets go before the dispatch, so a second removal — a handler's
    or a caller's — finds nothing to remove and is refused with a sentence
    rather than dying inside `list.remove`."""
    barbarian = creature()
    watcher = Watcher(barbarian)
    barbarian.attach(watcher)
    rage = barbarian.effects.apply(Rage(barbarian))
    assert rage is not None

    barbarian.effects.remove(rage)
    with pytest.raises(ValueError, match="not attached to barbarian"):
        barbarian.effects.remove(rage)

    assert watcher.seen.count("expired:barbarian.rage") == 1
