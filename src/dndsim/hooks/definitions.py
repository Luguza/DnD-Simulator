"""The 39 hooks: the closed list every feature plugs into.

Each constant is the `HookSpec` the engine dispatches on (`dispatch(ON_HIT, ctx)`);
feature authors write the name (`@hook("on_hit")`). A reaction with no hook here
is a `NEEDS_HOOK` (ROADMAP § Standing rules). The scope of each hook, and the
four `SELF`/`TARGET` pairs, are `docs/decisions.md` § w0e; the list itself is
DESIGN.md § The closed hook list.
"""

from typing import Final

from dndsim.hooks.registry import HookGroup, HookSpec, Scope, define_hook

__all__ = [
    "ON_ABILITY_CHECK",
    "ON_ATTACK_RESOLVED",
    "ON_ATTACK_ROLLED",
    "ON_CAST_SPELL",
    "ON_CONCENTRATION_CHECK",
    "ON_CONCENTRATION_START",
    "ON_CONDITION_GAINED",
    "ON_CONDITION_LOST",
    "ON_DAMAGE_ROLL",
    "ON_DAMAGE_TAKEN",
    "ON_DEATH_SAVE",
    "ON_DECLARE_ATTACK",
    "ON_EFFECT_APPLIED",
    "ON_EFFECT_EXPIRED",
    "ON_ENCOUNTER_END",
    "ON_ENCOUNTER_START",
    "ON_FORCED_SAVE",
    "ON_HEAL",
    "ON_HIT",
    "ON_HIT_TAKEN",
    "ON_INCOMING_ATTACK_DECLARED",
    "ON_INCOMING_DAMAGE",
    "ON_INITIATIVE_ROLL",
    "ON_KILL",
    "ON_LEAVE_REACH",
    "ON_LONG_REST",
    "ON_MISS",
    "ON_MOVE",
    "ON_REDUCED_TO_ZERO",
    "ON_RESOURCE_SPENT",
    "ON_ROUND_END",
    "ON_ROUND_START",
    "ON_SAVE_RESOLVED",
    "ON_SAVING_THROW",
    "ON_SHORT_REST",
    "ON_STABILIZE",
    "ON_TEMP_HP_GAINED",
    "ON_TURN_END",
    "ON_TURN_START",
]

# --- Timeline (9) -----------------------------------------------------------

ON_ENCOUNTER_START: Final[HookSpec] = define_hook(
    "on_encounter_start",
    scope=Scope.GLOBAL,
    group=HookGroup.TIMELINE,
    context="EncounterContext",
    summary="Combat begins, before initiative is rolled.",
)
ON_ENCOUNTER_END: Final[HookSpec] = define_hook(
    "on_encounter_end",
    scope=Scope.GLOBAL,
    group=HookGroup.TIMELINE,
    context="EncounterContext",
    summary="Combat ends; the last place to observe state before it is torn down.",
)
ON_INITIATIVE_ROLL: Final[HookSpec] = define_hook(
    "on_initiative_roll",
    scope=Scope.SELF,
    group=HookGroup.TIMELINE,
    context="InitiativeContext",
    summary="The owner rolls initiative (Feral Instinct, Alert, Uncanny Metabolism).",
)
ON_ROUND_START: Final[HookSpec] = define_hook(
    "on_round_start",
    scope=Scope.GLOBAL,
    group=HookGroup.TIMELINE,
    context="RoundContext",
    summary="A round begins, before the first creature's turn.",
)
ON_ROUND_END: Final[HookSpec] = define_hook(
    "on_round_end",
    scope=Scope.GLOBAL,
    group=HookGroup.TIMELINE,
    context="RoundContext",
    summary="A round ends, after the last creature's turn.",
)
ON_TURN_START: Final[HookSpec] = define_hook(
    "on_turn_start",
    scope=Scope.SELF,
    group=HookGroup.TIMELINE,
    context="TurnContext",
    summary="The owner's turn begins.",
)
ON_TURN_END: Final[HookSpec] = define_hook(
    "on_turn_end",
    scope=Scope.SELF,
    group=HookGroup.TIMELINE,
    context="TurnContext",
    summary="The owner's turn ends.",
)
ON_SHORT_REST: Final[HookSpec] = define_hook(
    "on_short_rest",
    scope=Scope.SELF,
    group=HookGroup.TIMELINE,
    context="RestContext",
    summary="The owner takes a short rest (hit dice, Song of Rest, recharges).",
)
ON_LONG_REST: Final[HookSpec] = define_hook(
    "on_long_rest",
    scope=Scope.SELF,
    group=HookGroup.TIMELINE,
    context="RestContext",
    summary="The owner takes a long rest.",
)

# --- Attack (7) -------------------------------------------------------------

ON_DECLARE_ATTACK: Final[HookSpec] = define_hook(
    "on_declare_attack",
    scope=Scope.SELF,
    group=HookGroup.ATTACK,
    context="AttackDeclaredContext",
    summary="The owner declares an attack, before the roll (Reckless Attack).",
)
ON_INCOMING_ATTACK_DECLARED: Final[HookSpec] = define_hook(
    "on_incoming_attack_declared",
    scope=Scope.TARGET,
    group=HookGroup.ATTACK,
    context="AttackDeclaredContext",
    summary="The owner is attacked, before the roll (Shield, cover, Dodge).",
)
ON_ATTACK_ROLLED: Final[HookSpec] = define_hook(
    "on_attack_rolled",
    scope=Scope.SELF,
    group=HookGroup.ATTACK,
    context="AttackRollContext",
    summary="The owner's attack roll is made but not yet compared to AC.",
)
ON_HIT: Final[HookSpec] = define_hook(
    "on_hit",
    scope=Scope.SELF,
    group=HookGroup.ATTACK,
    context="HitContext",
    summary="The owner's attack hits (Sneak Attack, Divine Smite, masteries).",
)
ON_HIT_TAKEN: Final[HookSpec] = define_hook(
    "on_hit_taken",
    scope=Scope.TARGET,
    group=HookGroup.ATTACK,
    context="HitContext",
    summary="The owner is hit, before damage is rolled (Heavy Armor Master).",
)
ON_MISS: Final[HookSpec] = define_hook(
    "on_miss",
    scope=Scope.SELF,
    group=HookGroup.ATTACK,
    context="MissContext",
    summary="The owner's attack misses (Graze, Great Weapon Fighting rerolls).",
)
ON_ATTACK_RESOLVED: Final[HookSpec] = define_hook(
    "on_attack_resolved",
    scope=Scope.SELF,
    group=HookGroup.ATTACK,
    context="AttackResolvedContext",
    summary="The owner's attack is over, hit or miss; the place to spend or count.",
)

# --- Damage (5) -------------------------------------------------------------

ON_DAMAGE_ROLL: Final[HookSpec] = define_hook(
    "on_damage_roll",
    scope=Scope.SELF,
    group=HookGroup.DAMAGE,
    context="DamageRollContext",
    summary="The owner rolls damage (Rage damage, Savage Attacker, brutal dice).",
)
ON_INCOMING_DAMAGE: Final[HookSpec] = define_hook(
    "on_incoming_damage",
    scope=Scope.TARGET,
    group=HookGroup.DAMAGE,
    context="IncomingDamageContext",
    summary="The owner is about to take damage (Uncanny Dodge, resistance grants).",
)
ON_DAMAGE_TAKEN: Final[HookSpec] = define_hook(
    "on_damage_taken",
    scope=Scope.TARGET,
    group=HookGroup.DAMAGE,
    context="DamageTakenContext",
    summary="The owner has taken damage, after the resolution order ran.",
)
ON_REDUCED_TO_ZERO: Final[HookSpec] = define_hook(
    "on_reduced_to_zero",
    scope=Scope.TARGET,
    group=HookGroup.DAMAGE,
    context="ReducedToZeroContext",
    summary="The owner drops to 0 hit points (Relentless Endurance, Undead Fortitude).",
)
ON_KILL: Final[HookSpec] = define_hook(
    "on_kill",
    scope=Scope.SELF,
    group=HookGroup.DAMAGE,
    context="KillContext",
    summary="The owner drops a creature to 0 (Great Weapon Master's extra attack).",
)

# --- Healing & dying (4) ----------------------------------------------------
# `on_heal` is the recipient's hook (`docs/decisions.md` § w0e, debt #17).

ON_HEAL: Final[HookSpec] = define_hook(
    "on_heal",
    scope=Scope.TARGET,
    group=HookGroup.HEALING_AND_DYING,
    context="HealContext",
    summary="The owner regains hit points (Second Wind, hit dice, Cure Wounds).",
)
ON_TEMP_HP_GAINED: Final[HookSpec] = define_hook(
    "on_temp_hp_gained",
    scope=Scope.TARGET,
    group=HookGroup.HEALING_AND_DYING,
    context="TempHpContext",
    summary="The owner gains temporary hit points, the one step of the damage "
    "order nothing else can observe.",
)
ON_DEATH_SAVE: Final[HookSpec] = define_hook(
    "on_death_save",
    scope=Scope.SELF,
    group=HookGroup.HEALING_AND_DYING,
    context="DeathSaveContext",
    summary="The owner rolls a death saving throw.",
)
ON_STABILIZE: Final[HookSpec] = define_hook(
    "on_stabilize",
    scope=Scope.SELF,
    group=HookGroup.HEALING_AND_DYING,
    context="StabilizeContext",
    summary="The owner stops dying without regaining hit points.",
)

# --- Checks & saves (4) -----------------------------------------------------

ON_ABILITY_CHECK: Final[HookSpec] = define_hook(
    "on_ability_check",
    scope=Scope.SELF,
    group=HookGroup.CHECKS_AND_SAVES,
    context="AbilityCheckContext",
    summary="The owner makes an ability check (Reliable Talent, Jack of All Trades).",
)
ON_FORCED_SAVE: Final[HookSpec] = define_hook(
    "on_forced_save",
    scope=Scope.SELF,
    group=HookGroup.CHECKS_AND_SAVES,
    context="ForcedSaveContext",
    summary="The owner forces another creature to save; the DC's side of the roll.",
)
ON_SAVING_THROW: Final[HookSpec] = define_hook(
    "on_saving_throw",
    scope=Scope.TARGET,
    group=HookGroup.CHECKS_AND_SAVES,
    context="SavingThrowContext",
    summary="The owner rolls a saving throw (Danger Sense, Aura of Protection).",
)
ON_SAVE_RESOLVED: Final[HookSpec] = define_hook(
    "on_save_resolved",
    scope=Scope.TARGET,
    group=HookGroup.CHECKS_AND_SAVES,
    context="SaveResolvedContext",
    summary="The owner's save has succeeded or failed (Legendary Resistance, Evasion).",
)

# --- Casting (3) ------------------------------------------------------------

ON_CAST_SPELL: Final[HookSpec] = define_hook(
    "on_cast_spell",
    scope=Scope.SELF,
    group=HookGroup.CASTING,
    context="CastSpellContext",
    summary="The owner casts a spell (metamagic, Potent Spellcasting).",
)
ON_CONCENTRATION_START: Final[HookSpec] = define_hook(
    "on_concentration_start",
    scope=Scope.SELF,
    group=HookGroup.CASTING,
    context="ConcentrationContext",
    summary="The owner begins concentrating, dropping whatever it concentrated on.",
)
ON_CONCENTRATION_CHECK: Final[HookSpec] = define_hook(
    "on_concentration_check",
    scope=Scope.SELF,
    group=HookGroup.CASTING,
    context="ConcentrationCheckContext",
    summary="The owner rolls to maintain concentration (War Caster).",
)

# --- Effects (4) ------------------------------------------------------------

ON_CONDITION_GAINED: Final[HookSpec] = define_hook(
    "on_condition_gained",
    scope=Scope.SELF,
    group=HookGroup.EFFECTS,
    context="ConditionContext",
    summary="The owner gains a condition.",
)
ON_CONDITION_LOST: Final[HookSpec] = define_hook(
    "on_condition_lost",
    scope=Scope.SELF,
    group=HookGroup.EFFECTS,
    context="ConditionContext",
    summary="The owner loses a condition.",
)
ON_EFFECT_APPLIED: Final[HookSpec] = define_hook(
    "on_effect_applied",
    scope=Scope.SELF,
    group=HookGroup.EFFECTS,
    context="EffectContext",
    summary="An effect is attached to the owner (Rage extending rather than stacking).",
)
ON_EFFECT_EXPIRED: Final[HookSpec] = define_hook(
    "on_effect_expired",
    scope=Scope.SELF,
    group=HookGroup.EFFECTS,
    context="EffectContext",
    summary="An effect on the owner reaches its expiry or runs out of charges.",
)

# --- Movement & resources (3) -----------------------------------------------


ON_MOVE: Final[HookSpec] = define_hook(
    "on_move",
    scope=Scope.SELF,
    group=HookGroup.MOVEMENT_AND_RESOURCES,
    context="MoveContext",
    summary="The owner moves along a resolved route (Charger, Mobile, Cunning Action).",
)
ON_LEAVE_REACH: Final[HookSpec] = define_hook(
    "on_leave_reach",
    scope=Scope.OBSERVER,
    group=HookGroup.MOVEMENT_AND_RESOURCES,
    context="LeaveReachContext",
    summary="A creature leaves the owner's reach; the opportunity attack trigger.",
)
ON_RESOURCE_SPENT: Final[HookSpec] = define_hook(
    "on_resource_spent",
    scope=Scope.SELF,
    group=HookGroup.MOVEMENT_AND_RESOURCES,
    context="ResourceSpentContext",
    summary="The owner spends a resource (a slot, a rage, a superiority die).",
)
