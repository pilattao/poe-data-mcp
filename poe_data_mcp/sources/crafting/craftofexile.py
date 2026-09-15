"""Game-aware Craft of Exile tools (PoE2 by default)."""
from importlib import import_module
from .game import selected_game


def _backend():
    return import_module(f'poe_data_mcp.sources.crafting.{selected_game()}')


def craftofexile_cache_status() -> str:
    """Show the selected game's Craft of Exile cache, patch, source and freshness."""
    return _backend().craftofexile_cache_status()


def update_craftofexile_cache(force: bool = False) -> str:
    """Refresh the selected game's Craft of Exile cache; check at most every 12h.

    PoE2 discovers current patch JSON and English localization on the beta site.
    Downloads are rate limited; a failed refresh preserves the previous snapshot.
    force bypasses freshness checks, but does not bypass source rate limits.
    """
    return _backend().update_craftofexile_cache(force)


def search_craft_mods(query: str, item_class: str = '') -> str:
    """Search localized crafting mods, optionally filtered by their actual item class.

    Examples: query='reload speed', item_class='crossbow'; query='maximum life'.
    PoE2 source weights are estimates, not guaranteed in-game probabilities.
    """
    return _backend().search_craft_mods(query, item_class)


def get_craft_tiers(base_type: str, query: str) -> str:
    """Show crafting mod tiers, level requirements, ranges and source weights.

    base_type is a class, e.g. 'crossbow', 'ring', 'helmet str', 'quarterstaff'.
    Special mod pools are labeled separately; weights do not establish odds.
    """
    return _backend().get_craft_tiers(base_type, query)


def get_fossil_info(fossil_name: str) -> str:
    """Look up PoE1 fossil affinities; report the documented unsupported gap in PoE2."""
    return _backend().get_fossil_info(fossil_name)


def get_essence_mods(essence_name: str, item_type: str = '') -> str:
    """Look up essence modifier outcomes by item class from the selected game's data.

    PoE2 examples: 'Abrasion', 'the Mind', 'Enhancement', item_type='crossbow'.
    Multiple outcomes indicate a choice from the listed pool, not all guaranteed.
    """
    return _backend().get_essence_mods(essence_name, item_type)


def get_craft_base_items(query: str = '', item_class: str = '') -> str:
    """Find crafting base names and drop levels, joined to their item classes."""
    return _backend().get_craft_base_items(query, item_class)
