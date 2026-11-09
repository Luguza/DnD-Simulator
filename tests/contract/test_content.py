"""The content ACL against hand-built 5etools-shaped fixtures.

The fetched data is gitignored; `tests/content/` reads the real thing when
asked (`-m content`). These pin each translation and each refusal.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from dndsim.content import fetch as fetch_module
from dndsim.content import weapons as weapons_module
from dndsim.content.backgrounds import backgrounds
from dndsim.content.classes import ArmourTraining, SkillChoice, classes
from dndsim.content.feats import FeatCategory, Prerequisite, feats
from dndsim.content.loader import ContentError, Dataset, FeatureText, plain
from dndsim.content.refs import class_features, optional_feature, subclass_features
from dndsim.content.species import species
from dndsim.content.tables import class_tables, table_value
from dndsim.content.weapons import armour, masteries, weapons
from dndsim.core.dice import Dice
from dndsim.creatures.stats import Armour
from dndsim.rules.constants import FULL_CASTER_SLOTS, PACT_MAGIC_SLOTS
from dndsim.rules.enums import (
    Ability,
    CasterProgression,
    DamageType,
    Mastery,
    Size,
    WeaponProperty,
)

SHA = "0123456789abcdef0123456789abcdef01234567"


def _item(name: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "source": "XPHB", "type": f"{kind}|XPHB", **fields}


LONGBOW = _item(
    "Longbow",
    "R",
    weapon=True,
    dmg1="1d8",
    dmgType="P",
    property=["A|XPHB", "H|XPHB", "2H|XPHB"],
    mastery=["Slow|XPHB"],
    range="150/600",
    weaponCategory="martial",
)
GLAIVE = _item(
    "Glaive",
    "M",
    weapon=True,
    dmg1="1d10",
    dmgType="S",
    property=["H|XPHB", "R|XPHB", "2H|XPHB"],
    mastery=["Graze|XPHB"],
    weaponCategory="martial",
)
LONGSWORD = _item(
    "Longsword",
    "M",
    weapon=True,
    dmg1="1d8",
    dmg2="1d10",
    dmgType="S",
    property=["V|XPHB"],
    mastery=["Sap|XPHB"],
    weaponCategory="martial",
)
LANCE = _item(
    "Lance",
    "M",
    weapon=True,
    dmg1="1d10",
    dmgType="P",
    property=["H|XPHB", "R|XPHB", {"uid": "2H|XPHB", "note": "unless mounted"}],
    mastery=["Topple|XPHB"],
    weaponCategory="martial",
)
BLOWGUN = _item(
    "Blowgun",
    "R",
    weapon=True,
    weaponCategory="martial",
    dmg1="1",
    dmgType="P",
    range="25/100",
)
STAFF = _item("Staff", "SCF", weapon=True, dmg1="1d6", dmgType="B")
LEATHER = _item("Leather Armor", "LA", armor=True, ac=11)
BREASTPLATE = _item("Breastplate", "MA", armor=True, ac=14)
PLATE = _item("Plate Armor", "HA", armor=True, ac=18, strength="15")
RING = _item("Ring Mail", "HA", armor=True, ac=14)
SHIELD = _item("Shield", "S", ac=2)
MASTERIES = [{"name": m.value.title(), "source": "XPHB"} for m in Mastery]


def _rows(*cells: Any) -> list[list[Any]]:
    return [list(cells)] * 20


def _wizard_slots() -> list[list[int]]:
    return [list(row) for row in FULL_CASTER_SLOTS]


BARBARIAN = {
    "name": "Barbarian",
    "source": "XPHB",
    "classTableGroups": [
        {
            "colLabels": ["Rages", "Rage Damage"],
            "rows": [
                [
                    str(2 + (row >= 2)),
                    {"type": "bonus", "value": 2 + (row >= 8) + (row >= 15)},
                ]
                for row in range(20)
            ],
        }
    ],
    "classFeatures": [
        "Rage|Barbarian|XPHB|1",
        {
            "classFeature": "Barbarian Subclass|Barbarian|XPHB|3",
            "gainSubclassFeature": True,
        },
    ],
}
ROGUE = {
    "name": "Rogue",
    "source": "XPHB",
    "classTableGroups": [
        {
            "colLabels": ["Sneak Attack"],
            "rows": _rows(
                {
                    "type": "dice",
                    "toRoll": [{"number": 1, "faces": 6}],
                    "rollable": True,
                }
            ),
        }
    ],
    "classFeatures": [],
}
WIZARD = {
    "name": "Wizard",
    "source": "XPHB",
    "casterProgression": "full",
    "classTableGroups": [
        {
            "colLabels": ["{@filter Cantrips|spells|level=0|class=Wizard}"],
            "rows": _rows(3),
        },
        {
            "title": "Spell Slots per Spell Level",
            "rowsSpellProgression": _wizard_slots(),
        },
    ],
    "classFeatures": ["Spellcasting|Wizard|XPHB|1"],
}
BARBARIAN_FEATURES = [
    {
        "name": "Rage",
        "source": "XPHB",
        "className": "Barbarian",
        "classSource": "XPHB",
        "level": 1,
        "entries": ["Enter it as a {@action Bonus Action|XPHB}."],
    },
    {
        "name": "Barbarian Subclass",
        "source": "XPHB",
        "className": "Barbarian",
        "classSource": "XPHB",
        "level": 3,
        "entries": ["Choose a subclass."],
    },
]
BERSERKER = {
    "name": "Path of the Berserker",
    "shortName": "Berserker",
    "source": "XPHB",
    "className": "Barbarian",
    "classSource": "XPHB",
    "subclassFeatures": ["Path of the Berserker|Barbarian|XPHB|Berserker|XPHB|3"],
}
BERSERKER_FEATURES = [
    {
        "name": "Path of the Berserker",
        "source": "XPHB",
        "className": "Barbarian",
        "classSource": "XPHB",
        "subclassShortName": "Berserker",
        "subclassSource": "XPHB",
        "level": 3,
        "entries": [
            "Channel Rage into violent fury.",
            {
                "type": "refSubclassFeature",
                "subclassFeature": "Frenzy|Barbarian|XPHB|Berserker|XPHB|3",
            },
        ],
    },
    {
        "name": "Frenzy",
        "source": "XPHB",
        "className": "Barbarian",
        "classSource": "XPHB",
        "subclassShortName": "Berserker",
        "subclassSource": "XPHB",
        "level": 3,
        "entries": ["Roll {@damage 1d6} extra; {@dc 15}."],
    },
]
WARLOCK = {
    "name": "Warlock",
    "source": "XPHB",
    "casterProgression": "pact",
    "classTableGroups": [
        {
            "colLabels": ["Spell Slots", "Slot Level"],
            "rows": [list(row) for row in PACT_MAGIC_SLOTS],
        }
    ],
    "classFeatures": ["Eldritch Invocation Options|Warlock|XPHB|1"],
}
WARLOCK_FEATURES = [
    {
        "name": "Eldritch Invocation Options",
        "source": "XPHB",
        "className": "Warlock",
        "classSource": "XPHB",
        "level": 1,
        "entries": [
            {
                "type": "options",
                "entries": [
                    {
                        "type": "refOptionalfeature",
                        "optionalfeature": "Agonizing Blast|XPHB",
                    }
                ],
            }
        ],
    }
]
OPTIONAL = [
    {
        "name": "Agonizing Blast",
        "source": "XPHB",
        "entries": ["Add your {@skill Charisma|XPHB} modifier."],
    }
]
ORC = {
    "name": "Orc",
    "source": "XPHB",
    "size": ["M"],
    "speed": 30,
    "darkvision": 120,
    "entries": [
        {
            "type": "entries",
            "name": "Relentless Endurance",
            "entries": ["When reduced to 0 {@variantrule Hit Points|XPHB}, drop to 1."],
        }
    ],
}
HUMAN = {
    "name": "Human",
    "source": "XPHB",
    "size": ["S", "M"],
    "speed": {"walk": 30},
    "entries": [],
}


def _dataset(tmp_path: Path, **override: Any) -> Dataset:
    files: dict[str, Any] = {
        "items-base.json": {
            "baseitem": [
                LONGBOW,
                GLAIVE,
                LONGSWORD,
                LANCE,
                BLOWGUN,
                STAFF,
                LEATHER,
                BREASTPLATE,
                PLATE,
                RING,
                SHIELD,
            ],
            "itemMastery": MASTERIES,
        },
        "races.json": {"race": [ORC, HUMAN]},
        "optionalfeatures.json": {"optionalfeature": OPTIONAL},
        "class/index.json": {
            "barbarian": "class-barbarian.json",
            "warlock": "class-warlock.json",
            "wizard": "class-wizard.json",
            "rogue": "class-rogue.json",
        },
        "class/class-barbarian.json": {
            "class": [BARBARIAN],
            "subclass": [BERSERKER],
            "classFeature": BARBARIAN_FEATURES,
            "subclassFeature": BERSERKER_FEATURES,
        },
        "class/class-warlock.json": {
            "class": [WARLOCK],
            "classFeature": WARLOCK_FEATURES,
        },
        "class/class-wizard.json": {
            "class": [WIZARD],
            "classFeature": [
                {
                    "name": "Spellcasting",
                    "source": "XPHB",
                    "className": "Wizard",
                    "classSource": "XPHB",
                    "level": 1,
                    "entries": ["Cast spells."],
                }
            ],
        },
        "class/class-rogue.json": {"class": [ROGUE]},
    }
    files.update(override)
    for path, body in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "VERSION").write_text(SHA + "\n", encoding="utf-8")
    return Dataset(tmp_path)


# -- the dataset and its version ----------------------------------------------


def test_a_dataset_cites_the_commit_it_was_fetched_at(tmp_path: Path) -> None:
    assert _dataset(tmp_path).version == SHA


def test_data_with_no_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="no fetched content"):
        Dataset(tmp_path)


def test_a_missing_file_is_a_content_error(tmp_path: Path) -> None:
    data = _dataset(tmp_path)
    with pytest.raises(ContentError, match="missing"):
        data.read("bestiary.json")


@pytest.mark.parametrize(
    ("tagged", "printed"),
    [
        ("{@damage 2d6} fire", "2d6 fire"),
        ("a {@dc 15} save", "a DC 15 save"),
        ("{@hit 5} to hit", "+5 to hit"),
        ("{@spell Fire Bolt|XPHB}", "Fire Bolt"),
        ("{@variantrule Hit Points|XPHB|hit points}", "hit points"),
        ("{@filter Cantrips|spells|level=0}", "Cantrips"),
        ("{@quickref Cover||3||cover}", "cover"),
        ("{@i {@spell Shield|XPHB}}", "Shield"),
        ("{@classFeature Rage|Barbarian|XPHB|1}", "Rage"),
        ("{@classFeature Rage|Barbarian|XPHB|1|XPHB|raging}", "raging"),
        ("{@subclassFeature Frenzy|Barbarian|XPHB|Berserker|XPHB|3}", "Frenzy"),
        ("{@subclass Armorer|Artificer|EFA|EFA}", "Armorer"),
        ("{@deity Bane|Faerûnian|FRHoF}", "Bane"),
        ("no tags", "no tags"),
    ],
)
def test_plain_prints_what_the_page_prints(tagged: str, printed: str) -> None:
    assert plain(tagged) == printed


@pytest.mark.parametrize("tagged", ["{@atkr m}", "{@actSave dex}", "{@d20 5}"])
def test_plain_refuses_a_tag_it_cannot_read(tagged: str) -> None:
    with pytest.raises(ContentError, match="no reading"):
        plain(tagged)


# -- weapons, armour, masteries -----------------------------------------------


def test_weapons_translate_every_field(tmp_path: Path) -> None:
    table = weapons(_dataset(tmp_path))
    longbow = table["Longbow"]
    assert longbow.damage == Dice(1, 8)
    assert longbow.damage_type is DamageType.PIERCING
    assert longbow.properties == {
        WeaponProperty.AMMUNITION,
        WeaponProperty.HEAVY,
        WeaponProperty.TWO_HANDED,
    }
    assert longbow.mastery is Mastery.SLOW
    assert longbow.range_ft == (150, 600)
    assert longbow.reach_ft == 5
    assert table["Glaive"].reach_ft == 10
    assert table["Glaive"].range_ft is None
    assert table["Longsword"].versatile_damage == Dice(1, 10)
    assert table["Lance"].has(WeaponProperty.TWO_HANDED)


def test_only_simple_and_martial_weapons_import(tmp_path: Path) -> None:
    assert set(weapons(_dataset(tmp_path))) == {
        "Blowgun",
        "Glaive",
        "Lance",
        "Longbow",
        "Longsword",
    }


def test_the_melee_ranged_column_is_kept(tmp_path: Path) -> None:
    table = weapons(_dataset(tmp_path))
    assert table["Longbow"].ranged
    assert table["Blowgun"].ranged
    assert not table["Glaive"].ranged


def test_a_bare_number_imports_as_a_flat_damage(tmp_path: Path) -> None:
    """The Blowgun's 1 is an `int`, which a Critical Hit never doubles; a d1
    would double to 2 (`docs/decisions.md` § w2f2)."""
    assert weapons(_dataset(tmp_path))["Blowgun"].damage == 1


def test_a_weapon_dropped_from_the_data_cannot_stay_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(weapons_module, "NOT_IMPORTED", {"Blowgun": "a reason"})
    items = {"baseitem": [LONGBOW], "itemMastery": MASTERIES}
    with pytest.raises(ContentError, match="Blowgun"):
        weapons(_dataset(tmp_path, **{"items-base.json": items}))


@pytest.mark.parametrize(
    "broken",
    [
        {**BLOWGUN, "dmg1": "0"},
        {key: value for key, value in BLOWGUN.items() if key != "range"},
    ],
)
def test_a_weapon_the_domain_refuses_is_a_content_error(
    tmp_path: Path, broken: Any
) -> None:
    items = {"baseitem": [broken], "itemMastery": MASTERIES}
    with pytest.raises(ContentError, match="Blowgun"):
        weapons(_dataset(tmp_path, **{"items-base.json": items}))


@pytest.mark.parametrize(
    "broken",
    [
        {**LONGBOW, "property": ["X|XPHB"]},
        {**LONGBOW, "dmgType": "Q"},
        {**LONGBOW, "mastery": ["Smash|XPHB"]},
        {**LONGBOW, "dmg1": "1d8+1"},
        {**LONGBOW, "mastery": ["Slow|XPHB", "Vex|XPHB"]},
        {**LONGBOW, "weaponCategory": "exotic"},
        {k: v for k, v in LONGBOW.items() if k != "weaponCategory"},
    ],
)
def test_a_weapon_shaped_otherwise_is_refused(tmp_path: Path, broken: Any) -> None:
    items = {"baseitem": [broken, BLOWGUN], "itemMastery": MASTERIES}
    with pytest.raises(ContentError, match="Longbow"):
        weapons(_dataset(tmp_path, **{"items-base.json": items}))


def test_armour_carries_its_kind_and_strength(tmp_path: Path) -> None:
    table = armour(_dataset(tmp_path))
    assert table["Leather Armor"] == Armour.light("Leather Armor", 11)
    assert table["Breastplate"] == Armour.medium("Breastplate", 14)
    assert table["Plate Armor"] == Armour.heavy(
        "Plate Armor", 18, strength_requirement=15
    )
    assert table["Ring Mail"].strength_requirement is None
    assert table["Plate Armor"].abilities == ()
    assert table["Breastplate"].abilities == ((Ability.DEXTERITY, 2),)
    assert "Shield" not in table


def test_a_shield_that_disagrees_with_the_constant_is_refused(tmp_path: Path) -> None:
    items = {"baseitem": [{**SHIELD, "ac": 3}], "itemMastery": MASTERIES}
    with pytest.raises(ContentError, match="Shield grants 3"):
        armour(_dataset(tmp_path, **{"items-base.json": items}))


def test_light_armour_with_a_strength_requirement_is_refused(tmp_path: Path) -> None:
    items = {"baseitem": [{**LEATHER, "strength": "13"}], "itemMastery": MASTERIES}
    with pytest.raises(ContentError, match="Strength"):
        armour(_dataset(tmp_path, **{"items-base.json": items}))


def test_masteries_must_match_the_enum(tmp_path: Path) -> None:
    assert masteries(_dataset(tmp_path)) == frozenset(Mastery)
    items = {"baseitem": [], "itemMastery": MASTERIES[1:]}
    with pytest.raises(ContentError, match="masteries"):
        masteries(_dataset(tmp_path, **{"items-base.json": items}))


# -- class tables --------------------------------------------------------------


def test_table_value_reads_the_printed_column(tmp_path: Path) -> None:
    tables = class_tables(_dataset(tmp_path))
    assert table_value(tables, "Barbarian", "Rage Damage", 9) == 3
    assert table_value(tables, "Barbarian", "Rages", 1) == 2
    assert table_value(tables, "Rogue", "Sneak Attack", 1) == Dice(1, 6)
    assert table_value(tables, "Wizard", "Cantrips", 4) == 3
    assert tables[("Wizard", None)].progression is CasterProgression.FULL
    assert tables[("Warlock", None)].progression is CasterProgression.PACT


def test_spell_slot_rows_are_checked_not_returned(tmp_path: Path) -> None:
    assert list(class_tables(_dataset(tmp_path))[("Wizard", None)].columns) == [
        "Cantrips"
    ]


def test_slots_that_disagree_with_the_rules_constants_are_refused(
    tmp_path: Path,
) -> None:
    slots = _wizard_slots()
    slots[4] = [4, 3, 3, 0, 0, 0, 0, 0, 0]
    wizard = {
        **WIZARD,
        "classTableGroups": [
            WIZARD["classTableGroups"][0],
            {"rowsSpellProgression": slots},
        ],
    }
    with pytest.raises(ContentError, match="Wizard level 5"):
        class_tables(
            _dataset(tmp_path, **{"class/class-wizard.json": {"class": [wizard]}})
        )


def test_an_unknown_column_or_level_names_what_exists(tmp_path: Path) -> None:
    tables = class_tables(_dataset(tmp_path))
    with pytest.raises(KeyError, match="Rage Damage"):
        table_value(tables, "Barbarian", "Rage Dmg", 1)
    with pytest.raises(ValueError, match="21"):
        table_value(tables, "Barbarian", "Rages", 21)
    with pytest.raises(KeyError, match="Bard"):
        table_value(tables, "Bard", "Bardic Die", 1)


def test_an_unreadable_cell_is_refused(tmp_path: Path) -> None:
    rogue = {**ROGUE, "classTableGroups": [{"colLabels": ["X"], "rows": _rows("—")}]}
    with pytest.raises(ContentError, match="cannot read table cell"):
        class_tables(
            _dataset(tmp_path, **{"class/class-rogue.json": {"class": [rogue]}})
        )


# -- reference resolution -----------------------------------------------------


def test_a_subclass_reference_becomes_a_feature_of_its_own(tmp_path: Path) -> None:
    assert subclass_features(_dataset(tmp_path), "Barbarian", "Berserker") == (
        FeatureText(
            "Path of the Berserker", 3, "Channel Rage into violent fury.\nFrenzy"
        ),
        FeatureText("Frenzy", 3, "Roll 1d6 extra; DC 15."),
    )


def test_an_optional_feature_reference_inherits_its_parents_level(
    tmp_path: Path,
) -> None:
    features = class_features(_dataset(tmp_path), "Warlock")
    assert features[1] == FeatureText(
        "Agonizing Blast", 1, "Add your Charisma modifier."
    )


def test_class_features_resolve_both_reference_shapes(tmp_path: Path) -> None:
    features = class_features(_dataset(tmp_path), "Barbarian")
    assert [(f.name, f.level) for f in features] == [
        ("Rage", 1),
        ("Barbarian Subclass", 3),
    ]
    assert features[0].text == "Enter it as a Bonus Action."


def test_an_optional_feature_is_found_by_name(tmp_path: Path) -> None:
    assert optional_feature(_dataset(tmp_path), "Agonizing Blast").name == (
        "Agonizing Blast"
    )


def test_a_dangling_reference_is_refused(tmp_path: Path) -> None:
    broken = [
        {
            **BERSERKER_FEATURES[0],
            "entries": [
                {
                    "type": "refSubclassFeature",
                    "subclassFeature": "Rampage|Barbarian|XPHB|Berserker|XPHB|3",
                }
            ],
        },
    ]
    file = {
        "class": [BARBARIAN],
        "subclass": [BERSERKER],
        "classFeature": BARBARIAN_FEATURES,
        "subclassFeature": broken,
    }
    data = _dataset(tmp_path, **{"class/class-barbarian.json": file})
    with pytest.raises(ContentError, match="Rampage"):
        subclass_features(data, "Barbarian", "Berserker")


# -- species ------------------------------------------------------------------


def test_species_carry_size_speed_darkvision_and_traits(tmp_path: Path) -> None:
    table = species(_dataset(tmp_path))
    assert table["Orc"].sizes == {Size.MEDIUM}
    assert table["Orc"].darkvision_ft == 120
    assert table["Orc"].traits == (
        FeatureText(
            "Relentless Endurance", None, "When reduced to 0 Hit Points, drop to 1."
        ),
    )
    assert table["Human"].sizes == {Size.SMALL, Size.MEDIUM}
    assert table["Human"].speed_ft == 30
    assert table["Human"].darkvision_ft == 0


def test_a_copied_species_is_refused(tmp_path: Path) -> None:
    races = {"race": [{**ORC, "_copy": {"name": "Orc", "source": "PHB"}}]}
    with pytest.raises(ContentError, match="copy"):
        species(_dataset(tmp_path, **{"races.json": races}))


def test_a_darkvision_that_is_not_an_integer_is_refused(tmp_path: Path) -> None:
    sighted = {"race": [{**ORC, "darkvision": 60}, HUMAN]}
    table = species(_dataset(tmp_path / "sighted", **{"races.json": sighted}))
    assert table["Orc"].darkvision_ft == 60
    assert table["Human"].darkvision_ft == 0
    printed = {"race": [{**ORC, "darkvision": "60 ft."}]}
    with pytest.raises(ContentError):
        species(_dataset(tmp_path / "printed", **{"races.json": printed}))


def test_a_trait_keeps_the_text_of_its_table(tmp_path: Path) -> None:
    lead = "Choose a dragon from the Draconic Ancestors table."
    ancestry = {
        "type": "entries",
        "name": "Draconic Ancestry",
        "entries": [
            lead,
            {
                "type": "table",
                "colLabels": ["Dragon", "Damage Type"],
                "rows": [["Red", "Fire"], ["Blue", "Lightning"]],
            },
        ],
    }
    races = {"race": [{**HUMAN, "name": "Dragonborn", "entries": [ancestry]}]}
    (trait,) = species(_dataset(tmp_path, **{"races.json": races}))["Dragonborn"].traits
    assert trait.name == "Draconic Ancestry"
    for printed in (lead, "Dragon", "Damage Type", "Red", "Fire", "Blue", "Lightning"):
        assert printed in trait.text


def test_a_weapon_carries_its_category(tmp_path: Path) -> None:
    dagger = {**LONGBOW, "name": "Dagger", "weaponCategory": "simple"}
    items = {"baseitem": [dagger, LONGBOW], "itemMastery": MASTERIES}
    table = weapons(_dataset(tmp_path, **{"items-base.json": items}))
    assert table["Longbow"].martial
    assert not table["Dagger"].martial


# -- classes, backgrounds, feats ------------------------------------------------

ROGUE_FILTER = (
    "Martial weapons that have the {@filter Finesse or Light|items|"
    "type=martial weapon|property=finesse;light} property"
)


def _class(name: str, **fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "source": "XPHB",
        "hd": {"number": 1, "faces": 8},
        "proficiency": ["dex", "int"],
        "startingProficiencies": {
            "armor": ["light"],
            "weapons": ["simple", ROGUE_FILTER],
            "skills": [{"choose": {"from": ["stealth", "acrobatics"], "count": 1}}],
        },
        "classFeatures": [
            f"Weapon Mastery|{name}|XPHB|1",
            {"classFeature": f"{name} Subclass|{name}|XPHB|3"},
            f"Ability Score Improvement|{name}|XPHB|4",
            f"Ability Score Improvement|{name}|XPHB|8",
        ],
    }
    entry.update(fields)
    return entry


def _classes(tmp_path: Path, *entries: Any) -> Dataset:
    return _dataset(
        tmp_path,
        **{
            "class/index.json": {"x": "class-x.json"},
            "class/class-x.json": {"class": list(entries)},
        },
    )


def _weapon(name: str, category: str, *properties: str) -> Any:
    return {
        **LONGBOW,
        "name": name,
        "weaponCategory": category,
        "property": [f"{p}|XPHB" for p in properties],
        "range": "20/60",
    }


def test_a_class_carries_its_core_traits(tmp_path: Path) -> None:
    rogue = classes(_classes(tmp_path, _class("Rogue")))["Rogue"]
    assert rogue.hit_die == Dice(1, 8)
    assert rogue.saving_throws == {Ability.DEXTERITY, Ability.INTELLIGENCE}
    assert rogue.armour_training == {ArmourTraining.LIGHT}
    assert rogue.skills == SkillChoice(1, frozenset({"stealth", "acrobatics"}))
    assert rogue.ability_score_improvement_levels == (4, 8)
    assert rogue.subclass_level == 3
    assert rogue.weapon_mastery_level == 1
    assert rogue.fighting_style_level is None


def test_weapon_training_is_a_union_of_its_entries(tmp_path: Path) -> None:
    """XPHB Rogue "Core Traits": Simple weapons and Martial weapons that have
    the Finesse or Light property."""
    items = {
        "baseitem": [
            _weapon("Dagger", "simple", "F", "L"),
            _weapon("Club", "simple"),
            _weapon("Rapier", "martial", "F"),
            _weapon("Scimitar", "martial", "L"),
            _weapon("Longsword", "martial", "V"),
        ],
        "itemMastery": MASTERIES,
    }
    data = _dataset(
        tmp_path,
        **{
            "items-base.json": items,
            "class/index.json": {"x": "class-x.json"},
            "class/class-x.json": {"class": [_class("Rogue")]},
        },
    )
    rogue = classes(data)["Rogue"]
    trained = {name for name, w in weapons(data).items() if rogue.trained_with(w)}
    assert trained == {"Dagger", "Club", "Rapier", "Scimitar"}


def test_any_skills_is_no_option_list(tmp_path: Path) -> None:
    bard = _class(
        "Bard",
        startingProficiencies={"weapons": ["simple"], "skills": [{"any": 3}]},
    )
    assert classes(_classes(tmp_path, bard))["Bard"].skills == SkillChoice(3, None)


@pytest.mark.parametrize(
    "broken",
    [
        ["simple", "Martial weapons that have the Finesse property"],
        ["simple", ROGUE_FILTER.replace("property=", "mastery=")],
        ["simple", ROGUE_FILTER.replace("finesse;", "sharp;")],
        ["simple", ROGUE_FILTER.replace("type=martial", "type=simple")],
        ["exotic"],
        [{"martial": True}],
    ],
)
def test_a_weapon_training_it_cannot_parse_is_refused(
    tmp_path: Path, broken: Any
) -> None:
    proficiencies = {"weapons": broken, "skills": [{"any": 1}]}
    entry = _class("Rogue", startingProficiencies=proficiencies)
    with pytest.raises(ContentError, match="Rogue"):
        classes(_classes(tmp_path, entry))


@pytest.mark.parametrize(
    "broken",
    [
        {"hd": {"number": 2, "faces": 8}},
        {"proficiency": ["dex"]},
        {"proficiency": ["dex", "luck"]},
        {"classFeatures": ["Weapon Mastery|Rogue|XPHB|1"]},
        {
            "startingProficiencies": {
                "armor": ["robes"],
                "weapons": ["simple"],
                "skills": [{"any": 1}],
            }
        },
        {
            "startingProficiencies": {
                "weapons": ["simple"],
                "skills": [{"choose": {"from": ["juggling"], "count": 1}}],
            }
        },
    ],
)
def test_a_class_shaped_otherwise_is_refused(tmp_path: Path, broken: Any) -> None:
    with pytest.raises(ContentError, match="Rogue"):
        classes(_classes(tmp_path, _class("Rogue", **broken)))


def _feat(name: str, category: str, *prerequisite: Any, **fields: Any) -> Any:
    entry = {"name": name, "source": "XPHB", "category": category, **fields}
    if prerequisite:
        entry["prerequisite"] = list(prerequisite)
    return entry


FEATS = [
    _feat("Savage Attacker", "O"),
    _feat(
        "Magic Initiate",
        "O",
        repeatable=True,
        _versions=[
            {"name": "Magic Initiate; Cleric", "source": "XPHB"},
            {"name": "Magic Initiate; Wizard", "source": "XPHB"},
        ],
    ),
    _feat(
        "Grappler",
        "G",
        {"level": 4, "ability": [{"str": 13}]},
        {"level": 4, "ability": [{"dex": 13}]},
    ),
    _feat("War Caster", "G", {"level": 4, "spellcasting2020": True}),
    _feat("Heavy Armor Master", "G", {"level": 4, "proficiency": [{"armor": "heavy"}]}),
    _feat("Archery", "FS", {"feature": ["Fighting Style"]}),
    _feat(
        "Blessed Warrior",
        "FS:P",
        {"otherSummary": {"entry": "Paladin", "entrySummary": "Special"}},
    ),
    _feat("Boon of Speed", "EB", {"level": 19}),
]


def _origins(tmp_path: Path, feat_list: Any = FEATS, *background: Any) -> Dataset:
    return _dataset(
        tmp_path,
        **{
            "feats.json": {"feat": feat_list},
            "backgrounds.json": {"background": list(background)},
        },
    )


def test_feats_carry_category_and_prerequisites(tmp_path: Path) -> None:
    table = feats(_origins(tmp_path))
    assert table["Savage Attacker"].category is FeatCategory.ORIGIN
    assert table["Savage Attacker"].prerequisites == ()
    assert table["Grappler"].prerequisites == (
        Prerequisite(level=4, abilities=((Ability.STRENGTH, 13),)),
        Prerequisite(level=4, abilities=((Ability.DEXTERITY, 13),)),
    )
    assert table["War Caster"].prerequisites == (
        Prerequisite(level=4, spellcasting=True),
    )
    assert table["Heavy Armor Master"].prerequisites == (
        Prerequisite(level=4, armour_training=ArmourTraining.HEAVY),
    )
    assert table["Archery"].category is FeatCategory.FIGHTING_STYLE
    assert table["Archery"].prerequisites == ()
    assert table["Blessed Warrior"].class_name == "Paladin"
    assert table["Boon of Speed"].category is FeatCategory.EPIC_BOON
    assert table["Magic Initiate"].options == ("Cleric", "Wizard")
    assert table["Magic Initiate"].repeatable


@pytest.mark.parametrize(
    "broken",
    [
        _feat("Odd", "X"),
        _feat("Odd", "G", {"level": 4, "race": [{"name": "elf"}]}),
        _feat("Odd", "G", {"level": {"level": 4, "class": "Fighter"}}),
        _feat("Odd", "G", {"feature": ["Fighting Style"]}),
        _feat("Odd", "G", {"otherSummary": {"entry": "?"}}),
        _feat("Odd", "G", {"proficiency": [{"weapon": "martial"}]}),
    ],
)
def test_a_feat_shaped_otherwise_is_refused(tmp_path: Path, broken: Any) -> None:
    with pytest.raises(ContentError, match="Odd"):
        feats(_origins(tmp_path, [broken]))


def _background(name: str, feat: str, **fields: Any) -> Any:
    weighted = {"from": ["str", "dex", "con"]}
    entry = {
        "name": name,
        "source": "XPHB",
        "ability": [
            {"choose": {"weighted": {**weighted, "weights": [2, 1]}}},
            {"choose": {"weighted": {**weighted, "weights": [1, 1, 1]}}},
        ],
        "feats": [{feat: True}],
        "skillProficiencies": [{"athletics": True, "intimidation": True}],
    }
    entry.update(fields)
    return entry


def test_a_background_carries_abilities_feat_and_skills(tmp_path: Path) -> None:
    data = _origins(
        tmp_path,
        FEATS,
        _background("Soldier", "savage attacker|xphb"),
        _background("Acolyte", "magic initiate; cleric|xphb"),
    )
    soldier, acolyte = (backgrounds(data)[n] for n in ("Soldier", "Acolyte"))
    assert soldier.abilities == {
        Ability.STRENGTH,
        Ability.DEXTERITY,
        Ability.CONSTITUTION,
    }
    assert soldier.increases == ((2, 1), (1, 1, 1))
    assert (soldier.feat, soldier.feat_option) == ("Savage Attacker", None)
    assert soldier.skills == {"athletics", "intimidation"}
    assert (acolyte.feat, acolyte.feat_option) == ("Magic Initiate", "Cleric")


@pytest.mark.parametrize(
    "broken",
    [
        _background("Odd", "grappler|xphb"),
        _background("Odd", "nonesuch|xphb"),
        _background("Odd", "magic initiate; bard|xphb"),
        _background(
            "Odd",
            "savage attacker|xphb",
            ability=[{"choose": {"weighted": {"from": ["str"], "weights": [3]}}}],
        ),
        _background(
            "Odd",
            "savage attacker|xphb",
            skillProficiencies=[{"juggling": True}],
        ),
        _background("Odd", "savage attacker|xphb", _copy={"name": "Soldier"}),
    ],
)
def test_a_background_shaped_otherwise_is_refused(tmp_path: Path, broken: Any) -> None:
    with pytest.raises(ContentError, match="Odd"):
        backgrounds(_origins(tmp_path, FEATS, broken))


# -- fetch -----------------------------------------------------------------------


def _upstream(files: dict[str, Any], seen: list[str]) -> fetch_module.Download:
    def download(url: str) -> bytes:
        seen.append(url)
        path = url.split("/data/", 1)[1]
        if path not in files:
            raise OSError(f"404 {url}")
        return json.dumps(files[path]).encode()

    return download


UPSTREAM = {
    "items-base.json": {},
    "races.json": {},
    "backgrounds.json": {},
    "feats.json": {},
    "optionalfeatures.json": {},
    "class/index.json": {"rogue": "class-rogue.json"},
    "class/class-rogue.json": {"class": []},
}


def test_fetch_reads_one_commit_and_records_it(tmp_path: Path) -> None:
    seen: list[str] = []
    dest = tmp_path / "5etools"
    assert fetch_module.fetch(dest, SHA, _upstream(UPSTREAM, seen)) == SHA
    assert Dataset(dest).version == SHA
    assert all(f"/{SHA}/data/" in url for url in seen)
    assert (dest / "class" / "class-rogue.json").is_file()


def test_a_failed_fetch_leaves_the_old_data_alone(tmp_path: Path) -> None:
    dest = tmp_path / "5etools"
    fetch_module.fetch(dest, SHA, _upstream(UPSTREAM, []))
    partial = {k: v for k, v in UPSTREAM.items() if k != "class/class-rogue.json"}
    with pytest.raises(OSError):
        fetch_module.fetch(dest, "f" * 40, _upstream(partial, []))
    assert Dataset(dest).version == SHA
    assert [p.name for p in tmp_path.iterdir()] == ["5etools"]


@pytest.mark.parametrize("ref", ["main", "v1.0", "ABCDEF" * 7, "abc123"])
def test_fetch_refuses_anything_but_a_commit_sha(tmp_path: Path, ref: str) -> None:
    seen: list[str] = []
    with pytest.raises(ContentError, match="commit SHA"):
        fetch_module.fetch(tmp_path / "5etools", ref, _upstream(UPSTREAM, seen))
    assert seen == []
