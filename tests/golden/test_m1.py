"""The M1 golden (ROADMAP `w2g`): `builds/`'s level 5 Fighter, a greatsword
and an explicit attack `apl:`, against a static target dummy, run through the
encounter on `ScriptedRng` to the dummy's death.

The Fighter has no class features (ROADMAP § W2's M1 note): Strength 15 + 2 =
17 (+3), proficiency +3, so natural + 6 to hit; a greatsword, 2d6 + 3 Slashing.
Dexterity 13 (+1). The dummy: AC 15, 30 hp, Dexterity 10 (+0), static, one
square away. Each initiative roll is a d20 and a tiebreak face; face 1 draws a
tiebreak of 0.0.

- Initiative: Fighter 12 + 1 = 13; dummy 8 + 0 = 8. The Fighter acts first.
- Round 1: natural 14, 20 vs AC 15, hits; 6 + 5 + 3 = 14. Dummy 30 -> 16.
- Round 2: natural 3, 9, misses; no damage rolled.
- Round 3: natural 20, a Critical Hit: the dice twice, 4d6 = 2 + 3 + 4 + 5 =
  14, the flat once, + 3 = 17. Dummy 16 -> 0, and it dies.

Three rounds, three Actions, three attacks, 14 + 17 = 31 damage dealt.

Sources: XPHB (2024) "Combat" -> Initiative, Attack Rolls, Critical Hits;
ROADMAP `w2g`; `docs/decisions.md` § w2g.
"""

from dndsim.builds.loader import load_build
from dndsim.core.log import LogLevel
from dndsim.core.rng import ScriptedRng
from dndsim.engine.attack import ATTACK
from dndsim.engine.encounter import ROUND_START
from dndsim.engine.turn import INITIATIVE
from dndsim.reporting.result import IterationResult
from dndsim.rules.enums import Outcome
from dndsim.sim.runner import PARTY, run_iteration
from scenario_fixtures import CATALOGUE, scenario

FACES = [12, 1, 8, 1, 14, 6, 5, 3, 20, 2, 3, 4, 5]


def test_the_m1_fighter_kills_the_dummy() -> None:
    fight = scenario()
    builds = [load_build(spec, CATALOGUE) for spec in fight.builds]
    rng = ScriptedRng(FACES)

    record, log = run_iteration(fight, builds, rng, seed=7, log_level=LogLevel.FULL)

    assert rng.exhausted
    assert record == IterationResult(
        seed=7, rounds=3, winner=PARTY, damage=31, actions=3, attacks=3
    )
    assert [r[1:4] for r in log if r[0] == INITIATIVE] == [
        ("Fighter (GWM)", 12, 13),
        ("Target Dummy", 8, 8),
    ]
    assert [r[1:] for r in log if r[0] == ATTACK] == [
        ("Fighter (GWM)", "Target Dummy", "Greatsword", Outcome.HIT, 14),
        ("Fighter (GWM)", "Target Dummy", "Greatsword", Outcome.MISS, 0),
        ("Fighter (GWM)", "Target Dummy", "Greatsword", Outcome.CRITICAL_HIT, 17),
    ]
    assert sum(1 for r in log if r[0] == ROUND_START) == 3
