"""M1: a level-5 Fighter with a greatsword kills a dummy (ROADMAP § M1).

Run at the encounter level, below the API, on `ScriptedRng`. Every number is
an exact integer derived from the build file and the faces alone:

- The Fighter (`builds/fighter_gwm_l5.yaml`) has Strength 15 + 2 = 17 (+3),
  Dexterity 13 (+1) and a level-5 proficiency bonus of +3, so the greatsword
  hits at d20 + 6 for 2d6 + 3 slashing (XPHB "Attack Rolls", "Damage
  Rolls"). It has no class features before W4 (ROADMAP § M1), and its
  `apl:` attacks once per Action.
- The dummy has AC 15, 30 hit points, Dexterity +0, and no routine; it dies
  at 0 hit points.
- Each Initiative roll is a d20 and a tiebreak face; face 1 is a tiebreak of
  0.0. The Fighter rolls 15 + 1 = 16, the dummy 10 + 0 = 10: the Fighter acts
  first (XPHB "Initiative").
- Round 1: 14 + 6 = 20 hits AC 15; 6 + 5 + 3 = 14. 30 -> 16.
- Round 2: 3 + 6 = 9 misses; a miss rolls no damage.
- Round 3: a natural 20 is a Critical Hit and doubles the dice, not the flat
  (XPHB "Critical Hits"): 4d6 of 2 + 3 + 4 + 1 = 10, + 3 = 13. 16 -> 3.
- Round 4: 9 + 6 = 15 meets AC 15 and hits; 1 + 1 + 3 = 5 on 3 hit points.
  The dummy drops and the fight ends before its turn in round 4.

So: 4 rounds, the party wins, 14 + 0 + 13 + 5 = 32 damage dealt (the 5
counted whole, as `resolve_damage()` reports it), 32 / 4 = 8 per round, one
Command and one attack in each round.
"""

from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.dummy import DummySpec
from dndsim.engine.attack import ATTACK
from dndsim.engine.dying import DROPPED
from dndsim.engine.turn import INITIATIVE
from dndsim.rules.enums import Outcome
from dndsim.sim.scenario import PARTY, Scenario, fight, load
from dndsim.sim.scenario import Outcome as FightOutcome
from m1_fixtures import CATALOGUE, fighter

FACES = [
    15, 1,  # the Fighter's Initiative and tiebreak
    10, 1,  # the dummy's
    14, 6, 5,  # round 1: a hit
    3,  # round 2: a miss
    20, 2, 3, 4, 1,  # round 3: a Critical Hit
    9, 1, 1,  # round 4: a hit that kills
]  # fmt: skip


def test_the_m1_fighter_kills_the_dummy() -> None:
    scenario = Scenario(
        builds=(fighter(),),
        catalogue=CATALOGUE,
        dummy=DummySpec(ac=15, hp=30),
    )
    log = EventLog(LogLevel.SUMMARY)
    rng = ScriptedRng(FACES)

    outcome = fight(load(scenario), rng=rng, log=log)

    assert outcome == FightOutcome(
        rounds=4,
        winner=PARTY,
        damage=32,
        actions_per_round=(1, 1, 1, 1),
        attacks_per_round=(1, 1, 1, 1),
    )
    assert rng.exhausted
    name, dummy, sword = "Fighter (GWM)", "Target Dummy", "Greatsword"
    assert [r for r in log if r[0] == INITIATIVE] == [
        (INITIATIVE, name, 15, 16, 0.0),
        (INITIATIVE, dummy, 10, 10, 0.0),
    ]
    assert [r for r in log if r[0] in (ATTACK, DROPPED)] == [
        (ATTACK, name, dummy, sword, Outcome.HIT, 14),
        (ATTACK, name, dummy, sword, Outcome.MISS, 0),
        (ATTACK, name, dummy, sword, Outcome.CRITICAL_HIT, 13),
        (DROPPED, name, dummy, True),
        (ATTACK, name, dummy, sword, Outcome.HIT, 5),
    ]
