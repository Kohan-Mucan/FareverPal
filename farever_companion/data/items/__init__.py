"""Item database package (`item_drops.json` scan, offline, no game).

The former single-module data layer was split into focused modules mirroring
the UI page split (catalog / sources / stats / labels):

- catalog.py  raw item_drops.json access: the item catalog + search
- sources.py  drop sources: resolve_drops, rift flags, gear loot-table groups
- stats.py    gear stat math: computed stats, rarity tiers, upgrade ladders,
              the stat-name search index
- labels.py   gear classification: category/slot/class labels, weapon damage
- craft.py    crafting recipes + jobs (craft.json / job.json)
- sources.py  also owns vendor stock: the per-vendor grouping the Merchants
              view reads, derived from the drops index's own npc rows

Consumers keep the same shape as before — `from ....data import items as
idata` and `idata.<fn>(...)` — because the public API is re-exported here.
`__all__` below is that API: it is what tells the linter a deliberate
re-export apart from a stray import, and the hygiene suite resolves every
entry against this module.
"""
from .catalog import (available, is_shop_item, item, item_effect_duration,
                     item_id_by_name, item_special_traits, items, matched_skill_labels,
                     matches_class, matches_skill, rarities, resolve_food_info,
                     search, types, weapon_skills, weapon_upgrade_passive,
                     weapon_upgrade_trait, weapons_for_skill)
from .craft import (craft_bill, craft_bill_many, craft_chain, craft_jobs,
                    craft_levels, first_craft_xp, is_craft_item, is_craftable,
                    jobs, recipe, recipe_unlocked_by, recipes, recipes_using)
from .labels import (affinity_color, categories, category, class_label,
                     gear_classes, gear_slots, is_gear, is_shield, own_stats,
                     weapon_attack)
from .sources import (acquisition_note, armor_locked, dungeon_drop_groups,
                      drops_from_table, gear_tables, has_drops,
                      heroic_infusions,
                      infusion_skill_notes, human_loc,
                      cache_is_unreleased, cache_vendor, cache_vendors,
                      cache_display_name, cache_via,
                      container_contents, container_spec,
                      currency_label, faction_caches,
                      heroic_cache_contents,
                      heroic_cache_spec,
                      unreleased_caches, vendor_caches,
                      is_rift_location, item_display_rarity,
                      item_scale_max_level, item_source_max_level,
                      item_vendor_levels, max_shown_drops, merge_drops,
                      epic_pieces,
                      no_source_reason, resolve_drops,
                      shared_source, shown_drops, upgrade_locked,
                      with_recorded_shop,
                      CRITTER_KIND, codex_entry_for, has_vendor_stock,
                      unresolved_vendor_lists, unresolved_vendor_stock,
                      vendor_charge, vendor_display_name, vendor_name,
                      vendor_names, vendor_scan_date, vendor_sections,
                      vendor_stock, vendor_stock_for, vendor_stock_lists)
from .stats import (CRIT_CHANCE_DIVISOR, LOADOUT_ROLES, RATING_STATS,
                    class_can_role, class_primary_stat, class_roles,
                    corrupted_scrolls, crit_chance_pct, enchant_conversions,
                    enchant_scrolls, equipped_stats, format_item_stats_summary,
                    gear_rarity_tiers, gear_ratings, gear_scaling, gear_stats,
                    gem_augments, ilevel_tiers, item_fixed_level, item_level,
                    loadout_stat_totals, matched_stat_labels, matches_stat,
                    optimize_loadout_build, rank_offhand_shields, rating_short,
                    role_label, set_stat_display_mode,
                    stat_display_mode, stat_rows, stat_text, suggest_arsenal_weapon,
                    suggest_augment_plan, suggest_main_weapon,
                    suggest_offhand_shield,
                    granted_rank, upgrade_cap, upgrade_cost_levels,
                    upgrade_costs, upgrade_gains,
                    upgrade_ladder, upgrade_material,
                    upgrade_materials, upgrade_path, weapon_primary_stat,
                    weapon_role_fit, weapon_role_reasons)

__all__ = [
    "CRIT_CHANCE_DIVISOR", "RATING_STATS", "LOADOUT_ROLES",
    "acquisition_note", "affinity_color",
    "armor_locked", "crit_chance_pct", "equipped_stats",
    "available", "upgrade_locked",    "categories", "category", "class_can_role", "class_label",
    "class_primary_stat", "class_roles", "corrupted_scrolls", "craft_bill",
    "craft_bill_many", "craft_chain", "craft_jobs", "craft_levels",
    "first_craft_xp",
    "drops_from_table", "enchant_conversions",
    "enchant_scrolls", "gem_augments", "gear_classes", "gear_rarity_tiers",
    "gear_ratings",
    "gear_scaling", "gear_slots", "gear_stats", "gear_tables",
    "dungeon_drop_groups", "has_drops",
    "human_loc", "ilevel_tiers", "is_craft_item", "is_craftable", "is_gear",
    "is_shield",
    "is_rift_location", "is_shop_item", "item", "item_display_rarity",
    "item_effect_duration", "item_fixed_level", "item_id_by_name",
    "item_level", "loadout_stat_totals", "resolve_food_info",
    "item_scale_max_level",
    "item_source_max_level", "item_vendor_levels", "items", "jobs",
    "matched_skill_labels", "matched_stat_labels", "matches_class",
    "rank_offhand_shields", "role_label",
    "matches_skill",    "matches_stat", "max_shown_drops", "suggest_arsenal_weapon",
    "suggest_augment_plan", "suggest_main_weapon", "suggest_offhand_shield",
    "epic_pieces", "merge_drops",
    "no_source_reason", "own_stats", "rarities", "shared_source",
    "rating_short", "recipe", "recipe_unlocked_by",
    "recipes", "recipes_using",
    "resolve_drops", "search", "set_stat_display_mode", "shown_drops",
    "with_recorded_shop",
    "stat_display_mode", "stat_rows", "stat_text", "types",
    "granted_rank", "upgrade_cap", "upgrade_cost_levels", "upgrade_costs",
    "upgrade_gains",
    "upgrade_ladder", "upgrade_material",
    "upgrade_materials", "upgrade_path",    "weapon_attack", "weapon_role_fit", "weapon_role_reasons", "weapon_skills",
    "weapon_upgrade_passive", "weapon_upgrade_trait",
    # Reached through this hub but written after the list above, so declared
    # here rather than re-sorted in. pyflakes reads each one as the re-export
    # it is, and the hygiene suite resolves every entry against this module.
    "cache_display_name", "cache_is_unreleased", "cache_vendor",
    "cache_vendors", "cache_via",
    "container_contents", "container_spec",
    "currency_label", "faction_caches", "format_item_stats_summary",
    "heroic_cache_contents", "heroic_cache_spec", "heroic_infusions",
    "infusion_skill_notes", "item_special_traits", "optimize_loadout_build",
    "unreleased_caches", "vendor_caches", "weapon_primary_stat",
    "weapons_for_skill",
    # vendor stock — the drops index's own npc rows, grouped per vendor
    "CRITTER_KIND",
    "has_vendor_stock", "codex_entry_for", "unresolved_vendor_lists", "unresolved_vendor_stock",
    "vendor_charge",
    "vendor_display_name", "vendor_name", "vendor_names",
    "vendor_scan_date", "vendor_sections",
    "vendor_stock", "vendor_stock_for", "vendor_stock_lists",
]
