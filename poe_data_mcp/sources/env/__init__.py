"""Environment tools for the selected game (PoE2 by default)."""
from importlib import import_module
from poe_data_mcp.sources.crafting.game import selected_game


def env_search(query: str, category: str = '') -> str:
    """Search PoE2 maps/areas from local PoB2 and tablets/waystones from Craft of Exile.

    Categories: maps, areas, tablets, waystones. Empty searches all available
    sources; unavailable categories are reported explicitly. PoE1 scarabs have
    no equivalent mapping; use tablets for PoE2's distinct map-content items.
    """
    module = import_module(f'poe_data_mcp.sources.env.{selected_game()}')
    return module.env_search(query, category)


def env_detail(name: str) -> str:
    """Show a PoE2 map/area, tablet or waystone by exact name or native ID.

    Includes source data and freshness. Ambiguous names list choices. Area
    definition level is separate from the level determined by a Waystone.
    """
    module = import_module(f'poe_data_mcp.sources.env.{selected_game()}')
    return module.env_detail(name)
