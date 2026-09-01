"""Item database package (`item_drops.json` scan, offline, no game).

The former single-module data layer was split into focused modules mirroring
the UI page split (catalog / sources / stats / labels):

- catalog.py  raw item_drops.json access: the item catalog + search
- sources.py  drop sources: resolve_drops, rift flags, gear loot-table groups
- stats.py    gear stat math: computed stats, rarity tiers, upgrade ladders,
              the stat-name search index
- labels.py   gear classification: category/slot/class labels, weapon damage
- craft.py    crafting recipes + jobs (craft.json / job.json)

Consumers keep the same shape as before — `from ....data import items as
idata` and `idata.<fn>(...)` — because the public API is re-exported here.
"""
from .catalog import (available, is_shop_item, item, item_effect_duration,
                     item_id_by_name, items, matched_skill_labels,
                     matches_class, matches_skill, rarities, resolve_food_info,
                     search, types, weapon_skills, weapons_for_skill)
from .craft import (craft_bill, craft_bill_many, craft_chain, craft_jobs,
                    craft_levels, first_craft_xp, is_craft_item, is_craftable,
                    jobs, recipe, recipe_unlocked_by, recipes, recipes_using)
from .labels import (affinity_color, categories, category, class_label,
                     gear_classes, gear_slots, is_gear, own_stats,
                     weapon_attack)
from .sources import (acquisition_note, armor_locked, dungeon_drop_groups,
                      drops_from_table, gear_tables, has_drops, human_loc,
                      is_rift_location, item_display_rarity,
                      item_scale_max_level, item_source_max_level,
                      item_vendor_levels, max_shown_drops, merge_drops,
                      no_source_reason, resolve_drops, shown_drops,
                      upgrade_locked)
from .stats import (RATING_STATS, class_primary_stat, corrupted_scrolls,
                    enchant_conversions, enchant_scrolls, gear_rarity_tiers,
                    gear_ratings, gear_scaling, gear_stats, gem_augments,
                    ilevel_tiers,
                    item_fixed_level, item_level, matched_stat_labels,
                    matches_stat, rating_short, set_stat_display_mode,
                    stat_display_mode, stat_rows, stat_text,
                    upgrade_costs,
                    upgrade_gains, upgrade_ladder, upgrade_material,
                    upgrade_path)

__all__ = [
    "RATING_STATS", "acquisition_note", "affinity_color", "armor_locked",
    "available", "upgrade_locked",
    "categories", "category", "class_label",
    "class_primary_stat", "corrupted_scrolls", "craft_bill",
    "craft_bill_many", "craft_chain", "craft_jobs", "craft_levels",
    "first_craft_xp",
    "drops_from_table", "enchant_conversions",
    "enchant_scrolls", "gem_augments", "gear_classes", "gear_rarity_tiers",
    "gear_ratings",
    "gear_scaling", "gear_slots", "gear_stats", "gear_tables",
    "dungeon_drop_groups", "has_drops",
    "human_loc", "ilevel_tiers", "is_craft_item", "is_craftable", "is_gear",
    "is_rift_location", "is_shop_item", "item", "item_display_rarity",
    "item_effect_duration", "item_fixed_level", "item_id_by_name",
    "item_level", "resolve_food_info",
    "item_scale_max_level",
    "item_source_max_level", "item_vendor_levels", "items", "jobs",
    "matched_skill_labels", "matched_stat_labels", "matches_class",
    "matches_skill",    "matches_stat", "max_shown_drops",
    "merge_drops", "no_source_reason", "own_stats", "rarities",
    "rating_short", "recipe", "recipe_unlocked_by",
    "recipes", "recipes_using",
    "resolve_drops", "search", "set_stat_display_mode", "shown_drops",
    "stat_display_mode", "stat_rows", "stat_text", "types",
    "upgrade_costs", "upgrade_gains", "upgrade_ladder", "upgrade_material",
    "upgrade_path",
    "weapon_attack", "weapon_skills",
]
