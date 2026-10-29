"""The effect contract: what is attached to a creature, and how it ends.

Rage, Vex and Reckless Attack are the three endings DESIGN.md § Durations names:
a deadline its handlers slide forward, a deadline and a charge, and an ending
before the owner's next turn begins.

Sources: DESIGN.md § Durations; `core/timeline.py`; `docs/decisions.md` § w1c.
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
    3 and leaves one Rage attached.
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
    """Refreshing twice inside the same turn gives the same deadline as
    refreshing once."""
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


def test_a_refresh_resolves_again_only_when_the_clock_or_the_duration_moved() -> None:
    """Inside one turn a refresh keeps the deadline it holds; a new duration on
    the same clock, or a sweep, resolves it again."""
    barbarian = creature()
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    rage = barbarian.effects.apply(
        Rage(barbarian, duration=Duration.until_end_of_next_turn_of(BARBARIAN))
    )
    assert rage is not None
    held = rage.expiry
    rage.refresh()
    assert rage.expiry is held

    rage.duration = Duration.until_end_of_current_turn()
    rage.refresh()
    assert rage.expiry == Expiry(TimeKey(1, BARBARIAN, TurnPhase.END), True)

    rage.duration = Duration.until_end_of_next_turn_of(BARBARIAN)
    rage.refresh()
    barbarian.effects.expire_before(at(2, BARBARIAN, TurnPhase.START))
    rage.refresh()
    assert rage.expiry == Expiry(TimeKey(3, BARBARIAN, TurnPhase.END), True)


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
    """An effect ending at the end of a turn is still attached after
    `expire_before()` at that boundary and gone after `expire_after()`."""
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
    """Under `REJECT` the incumbent is untouched and `apply()` returns None."""
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
    """A `UNIQUE_PER_SOURCE` effect with no caster raises when it is built."""
    quarry = creature(name="quarry")
    with pytest.raises(ValueError, match="unique per"):
        Mark(quarry)


# --- Attachment and charges -------------------------------------------------


def test_an_applied_effect_is_attached_and_an_expired_one_is_detached() -> None:
    """The store attaches what it applies and detaches what it expires."""
    barbarian = creature()
    barbarian.effects.expire_after(at(1, BARBARIAN, TurnPhase.END))
    watcher = barbarian.effects.apply(Reckless(barbarian))
    assert watcher is not None

    assert barbarian.dispatcher.sources() == (watcher,)
    barbarian.effects.expire_before(at(2, BARBARIAN, TurnPhase.START))
    assert barbarian.dispatcher.sources() == ()


def test_an_effect_owned_by_somebody_else_is_refused() -> None:
    """Applying an effect owned by another creature raises, and nothing is
    held afterwards."""
    barbarian, ogre = creature(), creature(name="ogre", initiative=OGRE)

    with pytest.raises(ValueError, match="cannot be attached"):
        ogre.effects.apply(Rage(barbarian))
    assert len(ogre.effects) == 0


def test_consuming_more_charges_than_remain_raises() -> None:
    """Overdrawing charges raises and leaves the count unchanged."""
    fighter = creature(name="fighter")
    effect = fighter.effects.apply(Vex(fighter, charges=2))
    assert effect is not None

    with pytest.raises(ValueError, match="2 charge"):
        effect.consume(3)
    assert effect.charges == 2


def test_consuming_an_effect_that_counts_no_charges_raises() -> None:
    """Consuming an effect built with `charges=None` raises."""
    barbarian = creature()
    rage = barbarian.effects.apply(Rage(barbarian))
    assert rage is not None

    with pytest.raises(ValueError, match="counts no charges"):
        rage.consume()


def test_zero_charges_is_refused_where_it_is_written() -> None:
    """`charges=0` raises at construction."""
    barbarian = creature()
    with pytest.raises(ValueError, match="0 charges"):
        Rage(barbarian, charges=0)


def test_an_unattached_effect_can_be_neither_refreshed_nor_consumed() -> None:
    """Refreshing or consuming an effect no store holds raises."""
    barbarian = creature()
    loose = Rage(barbarian, duration=Duration.until_end_of_next_turn_of(BARBARIAN))

    with pytest.raises(EngineAssemblyError, match="refreshed"):
        loose.refresh()
    with pytest.raises(EngineAssemblyError, match="consumed"):
        Vex(barbarian, charges=1).consume()


# --- The clock the store is told --------------------------------------------


def test_a_duration_resolves_against_the_clock_the_store_was_last_told() -> None:
    """An effect resolves against the boundary the store was last swept to
    (`docs/decisions.md` § w1c). The two anchors below are the same duration
    applied at the same moment, and they differ by a round because one
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


def test_a_store_starts_at_one_arbitrary_point_in_an_undecided_order() -> None:
    """A store never swept reads round 1 at `UNSEATED` (debt #29): an anchor at
    or above `initiative=0` ends in round 2, one below it in round 1.
    """
    assert creature().effects.now == TimeKey(1, UNSEATED, TurnPhase.START)

    duration = Duration.until_end_of_next_turn_of(BARBARIAN)
    assert duration.resolve(creature().effects.now).end.round == 2

    below = key(-1)
    assert (
        Duration.until_end_of_next_turn_of(below)
        .resolve(creature().effects.now)
        .end.round
        == 1
    )


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
    """Applying an effect fires `on_effect_applied` on its creature."""
    barbarian = creature()
    watcher = Watcher(barbarian)
    barbarian.attach(watcher)

    barbarian.effects.apply(Rage(barbarian))

    assert watcher.seen == ["applied:barbarian.rage"]


def test_an_extension_is_an_application_too() -> None:
    """A second application the incumbent absorbs fires `on_effect_applied` as
    the first did."""
    barbarian = creature()
    watcher = Watcher(barbarian)
    barbarian.attach(watcher)

    barbarian.effects.apply(Rage(barbarian))
    barbarian.effects.apply(Rage(barbarian))

    assert watcher.seen == ["applied:barbarian.rage"] * 2
    assert len(barbarian.effects) == 1


def test_a_rejected_application_fires_nothing() -> None:
    """A `REJECT`ed second application fires no hook."""
    cleric = creature(name="cleric")
    watcher = Watcher(cleric)
    cleric.attach(watcher)

    cleric.effects.apply(Blessing(cleric))
    cleric.effects.apply(Blessing(cleric))

    assert watcher.seen == ["applied:test.blessing"]


def test_a_veto_on_the_application_keeps_the_effect_off() -> None:
    """A veto at `on_effect_applied` makes `apply()` return None and attach
    nothing."""
    barbarian = creature()
    barbarian.attach(Immune(barbarian))

    assert barbarian.effects.apply(Rage(barbarian)) is None
    assert len(barbarian.effects) == 0


def test_both_ways_an_effect_ends_fire_the_expiry_hook() -> None:
    """Running out of charges and reaching the expiry both fire
    `on_effect_expired`."""
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
    """`on_effect_applied` fires before the attach and `on_effect_expired`
    before the detach, so an effect hears its own ending and not its own
    arrival (`docs/decisions.md` § w1c)."""
    barbarian = creature()
    watcher = ReportingEffect(barbarian)

    barbarian.effects.apply(watcher)
    barbarian.effects.remove(watcher)

    assert watcher.seen == ["expired:test.reporting_effect"]


def test_a_veto_on_an_ending_is_refused() -> None:
    """A veto at `on_effect_expired` raises rather than being ignored."""
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


class DispelsOnArrival(HookSource):
    """A handler that removes what is already held when something arrives."""

    __slots__ = ("_quarry", "_store")

    ID = "test.dispels_on_arrival"

    def __init__(self, owner: Creature, quarry: Effect) -> None:
        super().__init__(owner)
        self._store = owner.effects
        self._quarry = quarry

    @hook("on_effect_applied")
    def dispel(self, ctx: EffectContext) -> R:
        if self._quarry not in list(self._store):
            return R.SKIPPED
        self._store.remove(self._quarry)
        return R.APPLIED


class AppliesOnArrival(HookSource):
    """A handler that applies a Rage of its own the first time anything
    arrives."""

    __slots__ = ("_owner", "_store", "fired")

    ID = "test.applies_on_arrival"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self._owner = owner
        self._store = owner.effects
        self.fired = False

    @hook("on_effect_applied")
    def pile_on(self, ctx: EffectContext) -> R:
        if self.fired:
            return R.SKIPPED
        self.fired = True
        self._store.apply(Rage(self._owner))
        return R.APPLIED


def test_an_incumbent_dispelled_during_the_announce_is_not_refreshed() -> None:
    """`apply()` asks what is held on both sides of the dispatch. A handler that
    dispels the incumbent leaves the remembered one attached to nothing, and
    refreshing it would raise out of a re-application that is perfectly
    legitimate. The second Rage attaches instead."""
    barbarian = creature()
    first = barbarian.effects.apply(Rage(barbarian))
    assert first is not None
    barbarian.attach(DispelsOnArrival(barbarian, first))

    second = barbarian.effects.apply(Rage(barbarian))

    assert second is not None and second is not first
    assert list(barbarian.effects) == [second]


def test_an_incumbent_applied_during_the_announce_absorbs_the_application() -> None:
    """The mirror: a handler that applies a same-key effect during the announce
    leaves a remembered `None`, and attaching on it would put two
    `Stacking.REFRESH` effects of one class on the creature."""
    barbarian = creature()
    barbarian.attach(AppliesOnArrival(barbarian))

    landed = barbarian.effects.apply(Rage(barbarian))

    assert landed is not None
    assert len(barbarian.effects) == 1
    assert list(barbarian.effects) == [landed]
