"""Modifier lookup for the selected game."""
from poe_data_mcp.sources.crafting.game import selected_game


def search_mods(item_type: str, query: str = '') -> str:
    """Search PoE2 item modifiers from installed PoB2 tables by class or exact base.

    Examples: crossbow, charms, quarterstaff, helmets str, life flasks, Ruby.
    Uses ordered spawn-tag rules and separate charm/flask/jewel mod domains.
    PoB2 weights indicate eligibility, not measured probabilities. Corrupted
    modifiers are labeled separately. POE_GAME=poe1 retains the old PoEDB source.
    """
    if selected_game() == 'poe1':
        from .poe1 import search_mods as lookup
    else:
        from .poe2 import search_mods as lookup
    return lookup(item_type, query)
