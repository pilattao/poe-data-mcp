"""poe.ninja economy data — price checking and currency overview.

Uses two poe.ninja API endpoints:
  Exchange: /poe1/api/economy/exchange/current/overview  — bulk tradeable items
            (currencies, div cards, scarabs, essences, oils, fossils, etc.)
            Response: {"lines": [{id, primaryValue, sparkline}], "items": [{id, name}]}

  Stash:    /poe1/api/economy/stash/current/item/overview — unique items, gems, maps, etc.
            Response: {"lines": [{name, chaosValue, divineValue, sparkLine, listingCount}]}
"""

import html
import re
import time

import httpx

from poe_data_mcp.sources.common import HEADERS


class AmbiguousLeagueError(Exception):
    """Raised when auto-detection finds more than one non-permanent league
    currently live (e.g. a challenge league running alongside an event
    league) and no explicit league was given. Callers must not guess which
    one the user means -- pass league= explicitly."""

    def __init__(self, candidates: list[str]):
        self.candidates = candidates
        super().__init__(
            "Multiple active leagues detected (" + ", ".join(candidates) +
            ") -- pass league= explicitly rather than relying on auto-detection."
        )

NINJA_EXCHANGE_URL = "https://poe.ninja/poe1/api/economy/exchange/current/overview"
NINJA_STASH_URL    = "https://poe.ninja/poe1/api/economy/stash/current/item/overview"
NINJA_HOME         = "https://poe.ninja/"

# Bulk-tradeable items — served by the exchange endpoint
EXCHANGE_TYPES = {
    "currency":   ["Currency"],
    "fragment":   ["Fragment"],
    "divcard":    ["DivinationCard"],
    "scarab":     ["Scarab"],
    "essence":    ["Essence"],
    "oil":        ["Oil"],
    "fossil":     ["Fossil"],
    "omen":       ["Omen"],
    "tattoo":     ["Tattoo"],
    "allflame":   ["AllflameEmber"],
    "artifact":   ["Artifact"],
    "delirium":   ["DeliriumOrb"],
    "astrolabe":  ["Astrolabe"],
    "resonator":  ["Resonator"],
    "wombgift":   ["Wombgift"],
    "incubator":  ["Incubator"],
}

# Equipment, gems, maps — served by the stash endpoint
STASH_TYPES = {
    "unique":          ["UniqueWeapon", "UniqueArmour", "UniqueAccessory", "UniqueFlask", "UniqueJewel"],
    "forbiddenjewel":  ["ForbiddenJewel"],
    "shrinebelt":      ["ShrineBelt"],
    "tincture":        ["UniqueTincture"],
    "relic":           ["UniqueRelic"],
    "gem":             ["SkillGem"],
    "cluster":         ["ClusterJewel"],
    "map":             ["Map", "BlightedMap", "BlightRavagedMap", "UniqueMap"],
    "invitation":      ["Invitation"],
    "base":            ["BaseType"],
    "beast":           ["Beast"],
    "vial":            ["Vial"],
}

# All known categories (for help text and validation)
ALL_TYPES = {**EXCHANGE_TYPES, **STASH_TYPES}

# Categories searched when no category hint is given (most common first)
DEFAULT_SEARCH_ORDER = ["currency", "unique", "gem", "divcard", "map"]

# --- Caches ---
_league_cache: str | None = None
_league_cache_ts: float = 0
_league_ambiguous_cache: list[str] | None = None
_league_ambiguous_cache_ts: float = 0
_LEAGUE_TTL = 3600  # 1 hour

# Cache keyed by (endpoint, league, type_name)
_ninja_cache: dict[tuple[str, str, str], tuple[float, list, list]] = {}
_NINJA_TTL = 900  # 15 minutes


def _extract_league_candidates(page_html: str) -> list[str]:
    """Extract ordered league URL slugs from poe.ninja's homepage.

    poe.ninja embeds its current league list as escaped JSON inside a
    hydration payload (props.poe1IndexState.economyLeagues), not as plain
    <a href> links -- e.g. {"name":"Mirage","url":"mirage",...}. Falls back
    to a legacy anchor-scrape in case the site ever reverts.
    """
    text = html.unescape(page_html)
    m = re.search(r'"economyLeagues":\[1,\[(.*?)\]\],"oldEconomyLeagues"', text)
    if m:
        return re.findall(r'"url":\[0,"([a-z0-9.\-]+)"\]', m.group(1))
    return re.findall(r'/(?:economy|challenge)/([a-z][a-z0-9-]+)', text.lower())


def _get_current_league() -> str:
    """Auto-detect the current temp league from poe.ninja's homepage.

    Raises AmbiguousLeagueError if more than one non-permanent league is
    currently live (e.g. a challenge league running alongside a shorter
    event league) -- do not silently guess which one the caller means.
    """
    global _league_cache, _league_cache_ts, _league_ambiguous_cache, _league_ambiguous_cache_ts

    if _league_cache and (time.time() - _league_cache_ts) < _LEAGUE_TTL:
        return _league_cache
    if _league_ambiguous_cache and (time.time() - _league_ambiguous_cache_ts) < _LEAGUE_TTL:
        raise AmbiguousLeagueError(_league_ambiguous_cache)

    permanent = {"standard", "hardcore"}
    validated: list[str] = []
    try:
        resp = httpx.get(NINJA_HOME, headers=HEADERS, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        candidates = _extract_league_candidates(resp.text)
        seen = set()
        for c in candidates:
            c = c.lower()
            if c in permanent or c in seen:
                continue
            seen.add(c)
            league_name = c.capitalize()
            lines, _ = _fetch_exchange_raw(league_name, "Currency")
            if lines:
                validated.append(league_name)
    except Exception:
        pass

    if len(validated) == 1:
        _league_cache = validated[0]
        _league_cache_ts = time.time()
        return _league_cache
    if len(validated) > 1:
        _league_ambiguous_cache = validated
        _league_ambiguous_cache_ts = time.time()
        raise AmbiguousLeagueError(validated)

    _league_cache = "Standard"
    _league_cache_ts = time.time()
    return _league_cache


def _resolve_league(league: str) -> str:
    return league if league else _get_current_league()


def _resolve_league_for_tool(league: str) -> tuple[str | None, str | None]:
    """Resolve a league for an MCP tool call. Returns (resolved_league, None) on
    success, or (None, error_message) if auto-detection is ambiguous -- callers
    should return the error_message directly rather than guessing a league."""
    try:
        return _resolve_league(league), None
    except AmbiguousLeagueError as e:
        candidates_str = ", ".join(f'"{c}"' for c in e.candidates)
        return None, (
            f"Multiple leagues are currently active ({candidates_str}) and no `league` was "
            f"specified, so I can't guess which one you mean -- likely a main challenge league "
            f"running alongside a shorter event league. Re-call with an explicit league= from "
            f"the list above (e.g. league={e.candidates[0]!r})."
        )


def _fetch_exchange_raw(league: str, type_name: str) -> tuple[list, list]:
    """Fetch from exchange endpoint. Returns (lines, items) — items maps id->name."""
    try:
        resp = httpx.get(
            NINJA_EXCHANGE_URL,
            params={"league": league, "type": type_name},
            headers=HEADERS,
            follow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("lines", []), data.get("items", [])
    except Exception:
        return [], []


def _fetch_stash_raw(league: str, type_name: str) -> list:
    """Fetch from stash endpoint. Returns lines with name/chaosValue/divineValue directly."""
    try:
        resp = httpx.get(
            NINJA_STASH_URL,
            params={"league": league, "type": type_name},
            headers=HEADERS,
            follow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("lines", [])
    except Exception:
        return []


def _fetch_exchange(league: str, type_name: str) -> tuple[list, list]:
    """Cached exchange fetch."""
    key = ("exchange", league, type_name)
    cached = _ninja_cache.get(key)
    if cached and (time.time() - cached[0]) < _NINJA_TTL:
        return cached[1], cached[2]
    lines, items = _fetch_exchange_raw(league, type_name)
    _ninja_cache[key] = (time.time(), lines, items)
    return lines, items


def _fetch_stash(league: str, type_name: str) -> list:
    """Cached stash fetch."""
    key = ("stash", league, type_name)
    cached = _ninja_cache.get(key)
    if cached and (time.time() - cached[0]) < _NINJA_TTL:
        return cached[1]
    lines = _fetch_stash_raw(league, type_name)
    _ninja_cache[key] = (time.time(), lines, [])
    return lines


def _match_score(query: str, name: str) -> int:
    """Score match quality: 3=exact, 2=startswith, 1=contains, 0=no match."""
    q = query.lower()
    n = name.lower()
    if q == n:
        return 3
    if n.startswith(q):
        return 2
    if q in n:
        return 1
    return 0


def _format_trend(sparkline: dict | None) -> str:
    """Format 7-day price trend from sparkline data."""
    if not sparkline:
        return "n/a"
    change = sparkline.get("totalChange", 0)
    if change is None:
        return "n/a"
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.1f}%"


def _search_exchange(query: str, league: str, categories: list[str]) -> list[dict]:
    """Search exchange-endpoint categories (currencies, div cards, scarabs, etc.)."""
    results = []
    for cat in categories:
        for type_name in EXCHANGE_TYPES.get(cat, []):
            lines, items = _fetch_exchange(league, type_name)
            id_to_name = {item["id"]: item["name"] for item in items}
            for line in lines:
                name = id_to_name.get(line.get("id", ""), "")
                if not name:
                    continue
                score = _match_score(query, name)
                if score > 0:
                    chaos = line.get("primaryValue", 0)
                    results.append({
                        "name": name,
                        "chaos_value": chaos,
                        "divine_value": None,
                        "listing_count": line.get("volumePrimaryValue", 0),
                        "trend_7d": _format_trend(line.get("sparkline")),
                        "category": type_name,
                        "_score": score,
                    })
    return results


def _search_stash(query: str, league: str, categories: list[str]) -> list[dict]:
    """Search stash-endpoint categories (uniques, gems, maps, etc.)."""
    results = []
    for cat in categories:
        for type_name in STASH_TYPES.get(cat, []):
            lines = _fetch_stash(league, type_name)
            for line in lines:
                name = line.get("name", "")
                variant = line.get("variant", "")
                display = f"{name} ({variant})" if variant else name
                score = _match_score(query, name)
                if score > 0:
                    results.append({
                        "name": display,
                        "chaos_value": line.get("chaosValue", 0),
                        "divine_value": line.get("divineValue"),
                        "listing_count": line.get("listingCount", 0),
                        "trend_7d": _format_trend(line.get("sparkLine")),
                        "category": type_name,
                        "links": line.get("links"),
                        "gem_level": line.get("gemLevel"),
                        "gem_quality": line.get("gemQuality"),
                        "_score": score,
                    })
    return results


def _format_results(results: list[dict], max_results: int = 10) -> str:
    """Format matched results into readable output."""
    results.sort(key=lambda r: (-r["_score"], -(r["chaos_value"] or 0)))
    results = results[:max_results]

    if not results:
        return "No results found."

    lines = []
    for r in results:
        parts = [f"**{r['name']}**"]
        parts.append(f"  Chaos: {r['chaos_value']:,.1f}")
        if r.get("divine_value") is not None:
            parts.append(f"  Divine: {r['divine_value']:,.2f}")
        if r.get("links"):
            parts.append(f"  Links: {r['links']}")
        if r.get("gem_level"):
            parts.append(f"  Level: {r['gem_level']}")
        if r.get("gem_quality"):
            parts.append(f"  Quality: {r['gem_quality']}%")
        parts.append(f"  Listings: {r['listing_count']:,}")
        parts.append(f"  7d trend: {r['trend_7d']}")
        parts.append(f"  Category: {r['category']}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


async def price_check(query: str, league: str = "", category: str = "") -> str:
    """Search poe.ninja for the current price of any item or currency.

    Args:
        query: Item name to search (partial match supported).
        league: League name. Defaults to current temp league (auto-detected).
        category: Optional hint to narrow search. Options: currency, fragment,
                  divcard, scarab, essence, oil, fossil, omen, tattoo, allflame,
                  artifact, delirium, astrolabe, resonator, wombgift, incubator,
                  unique, forbiddenjewel, shrinebelt, tincture, relic, gem,
                  cluster, map, invitation, base, beast, vial.
    """
    league, err = _resolve_league_for_tool(league)
    if err:
        return err
    results: list[dict] = []

    if category:
        cat = category.lower()
        if cat in EXCHANGE_TYPES:
            results = _search_exchange(query, league, [cat])
        elif cat in STASH_TYPES:
            results = _search_stash(query, league, [cat])
        else:
            return (
                f"Unknown category '{category}'. "
                f"Available: {', '.join(sorted(ALL_TYPES.keys()))}"
            )
    else:
        # Search common categories in order, stop when we have enough
        for cat in DEFAULT_SEARCH_ORDER:
            if cat in EXCHANGE_TYPES:
                results.extend(_search_exchange(query, league, [cat]))
            elif cat in STASH_TYPES:
                results.extend(_search_stash(query, league, [cat]))
            if len(results) >= 10:
                break

    header = f"**Price Check** — League: {league}\n\n"
    return header + _format_results(results)


async def currency_overview(league: str = "") -> str:
    """Returns top currency exchange rates for quick reference.

    Args:
        league: League name. Defaults to current temp league (auto-detected).
    """
    league, err = _resolve_league_for_tool(league)
    if err:
        return err
    lines, items = _fetch_exchange(league, "Currency")

    if not lines:
        return f"No currency data found for league '{league}'."

    id_to_name = {item["id"]: item["name"] for item in items}
    lines_with_names = [
        (id_to_name.get(l["id"], l["id"]), l.get("primaryValue", 0))
        for l in lines
    ]
    lines_with_names.sort(key=lambda x: x[1], reverse=True)
    top = lines_with_names[:20]

    rows = [f"**Currency Overview** — League: {league}\n"]
    rows.append(f"{'Name':<30} {'Chaos Value':>12}")
    rows.append("-" * 44)
    for name, chaos in top:
        rows.append(f"{name:<30} {chaos:>12,.1f}")

    return "\n".join(rows)

# Game-specific schemas and units must not be shared with PoE1.
from poe_data_mcp.sources.common import GAME
if GAME == 'poe2':
    from poe_data_mcp.sources.economy.poe2 import price_check, currency_overview
