from mcp.server.fastmcp import FastMCP

from poe_data_mcp.sources.player.gems import get_gem_detail, search_gem
from poe_data_mcp.sources.player.items import get_item_detail, search_item
from poe_data_mcp.sources.player.passives import get_passive_detail, search_passive
from poe_data_mcp.sources.mods.item_mods import search_mods
from poe_data_mcp.sources.crafting.craftofexile import (
    craftofexile_cache_status,
    update_craftofexile_cache,
    search_craft_mods,
    get_craft_base_items,
    get_craft_tiers,
    get_fossil_info,
    get_essence_mods,
)
from poe_data_mcp.sources.env import env_detail, env_search
from poe_data_mcp.sources.economy import currency_overview, price_check
from poe_data_mcp.sources.wiki import fetch_wiki_page, wiki_cargo_query
from poe_data_mcp.sources.player.pob import parse_pob, parse_pob_skill_groups
from poe_data_mcp.sources.youtube import fetch_youtube_description, fetch_youtube_transcript
from poe_data_mcp.sources.reddit import fetch_reddit_post

_SUITE_URL = "https://github.com/pilattao/poe_mcp_suite"

_INSTRUCTIONS = """PoE2 data, build parsing and economy tools. Set POE_GAME=poe2,
POB_INSTALL_DIR to a complete local Path of Building Community (PoE2) installation,
and POE_LEAGUE to the intended league. Gems/items/passives use installed PoB2
or explicitly labelled PoE2 sources. Data-file modification time is not patch age.
Economy values use each response's primary currency and rates. Source retrieval
time does not establish the underlying snapshot age. PoE1 data is only selected
by an explicit POE_GAME=poe1 setting. Never combine legacy unique variants.
"""

mcp = FastMCP("poe-data-mcp", instructions=_INSTRUCTIONS)


def poe_mcp_suite_info() -> str:
    """Install instructions and overview for the full poe_mcp_suite.

    poe-data-mcp (this server) is the game-data lookup component of a larger suite.
    Call this when the user wants deeper Path of Exile build analysis than
    lookups provide, or asks about the full suite / Path of Building integration.
    """
    return f"""\
# poe_mcp_suite

poe-data-mcp is one of several MCP servers in **poe_mcp_suite**. The full suite adds:

- **pob-mcp** - live Path of Building integration: simulate passive trees, gems,
  items, and full DPS/EHP calc against the real PoB engine (TCP or headless).
- **poe-trade-mcp** - official PoE trade search, stash/character API, pricing.
- **poe-data-mcp** (this server) - wiki / economy / Craft of Exile lookups.
- A **playbook + reference-data framework** that keeps an agent's analyses current.

## Install

```bash
git clone --recurse-submodules {_SUITE_URL}.git
cd poe_mcp_suite
```

Then follow **AGENTS.md** (or **CLAUDE.md** for Claude Code) and **README.md** in
the repo - they cover Python/Node dependencies, Path of Building setup, MCP client
configuration (`.mcp.json`), and your `POESESSID`. Point your agent at AGENTS.md
and it can drive the rest.

Note: pob-mcp requires a local Path of Building install and is not an
ephemeral (uvx/npx) server - the suite is a git clone, not a single package.
"""


# --- Player domain ---
mcp.tool()(search_gem)
mcp.tool()(get_gem_detail)
mcp.tool()(search_item)
mcp.tool()(get_item_detail)
mcp.tool()(search_passive)
mcp.tool()(get_passive_detail)

# --- Mods domain ---
mcp.tool()(search_mods)

# --- Craft of Exile ---
mcp.tool()(craftofexile_cache_status)
mcp.tool()(update_craftofexile_cache)
mcp.tool()(search_craft_mods)
mcp.tool()(get_craft_base_items)
mcp.tool()(get_craft_tiers)
mcp.tool()(get_fossil_info)
mcp.tool()(get_essence_mods)

# --- Env domain ---
mcp.tool()(env_search)
mcp.tool()(env_detail)

# --- Economy domain ---
mcp.tool()(price_check)
mcp.tool()(currency_overview)

# --- Universal ---
mcp.tool()(fetch_wiki_page)
mcp.tool()(wiki_cargo_query)

# --- PoB ---
mcp.tool()(parse_pob)
mcp.tool()(parse_pob_skill_groups)

# --- YouTube ---
mcp.tool()(fetch_youtube_description)
mcp.tool()(fetch_youtube_transcript)

# --- Reddit ---
mcp.tool()(fetch_reddit_post)

# --- Suite ---
mcp.tool()(poe_mcp_suite_info)

def main():
    mcp.run()

if __name__ == "__main__":
    main()
