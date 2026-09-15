"""Craft of Exile data cache and crafting lookup tools.

Downloads JSON data from craftofexile.com on first use and caches it locally in
the platform user-cache dir (see ``_default_cache_dir``; overridable via
``POE_DATA_MCP_CACHE_DIR``). Each user fetches their own copy — nothing is redistributed.

Freshness: the homepage embeds ?v=<unix_timestamp> on every data file URL.
We compare stored timestamps against the current homepage on each process start
(at most once per CHECK_INTERVAL seconds to avoid hammering the site).
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

SITE = "https://www.craftofexile.com"


def _default_cache_dir() -> Path:
    """Where to cache craftofexile data.

    A single canonical location is shared by every install method — a standalone
    ``uvx``/``pipx`` install and the full poe_mcp_suite both land here, so the
    cache is built once and reused regardless of how the server was installed:

    1. ``POE_DATA_MCP_CACHE_DIR`` env var, if set (optional override for advanced use).
    2. The platform user-cache dir (the default), e.g.
       ``%LOCALAPPDATA%\\poe-data-mcp\\Cache`` on Windows or
       ``~/.cache/poe-data-mcp`` elsewhere.
    """
    env = os.environ.get("POE_DATA_MCP_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    try:
        from platformdirs import user_cache_dir

        return Path(user_cache_dir("poe-data-mcp", appauthor=False)) / "craftofexile"
    except Exception:
        return Path.home() / ".cache" / "poe-data-mcp" / "craftofexile"


CACHE_DIR = _default_cache_dir()
MANIFEST_FILE = CACHE_DIR / "manifest.json"

# Only re-check the homepage for version updates this often (12 hours)
CHECK_INTERVAL = 43200

# Polite pause between successive file downloads
DOWNLOAD_DELAY = 1.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Local filename → URL path (without ?v=)
_DATA_FILES: dict[str, str] = {
    "poec_data.json":       "/json/data/main/poec_data.json",
    "poec_lang.us.json":    "/json/data/lang/poec_lang.us.json",
    "poec_prices.json":     "/json/data/prices/poec_prices.json",
    "poec_common.json":     "/json/data/poec_common.json",
    "poec_affinities.json": "/json/data/affinities/poec_affinities.json",
    "poec_exdata.json":     "/json/data/exdata/poec_exdata.json",
}

# In-memory cache of loaded JSON
_mem: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_manifest(manifest: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def _fetch_remote_versions() -> dict[str, str]:
    """Fetch craftofexile homepage and extract ?v= timestamps for each data file."""
    resp = httpx.get(SITE, headers=HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    versions: dict[str, str] = {}
    for filename, path in _DATA_FILES.items():
        # Homepage uses relative URLs (no leading slash)
        rel = path.lstrip("/")
        m = re.search(re.escape(rel) + r'\?v=(\d+)', html)
        if m:
            versions[filename] = m.group(1)
    return versions


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_file(filename: str, version: str | None) -> None:
    path = _DATA_FILES[filename]
    url = SITE + path + (f"?v={version}" if version else "")
    resp = httpx.get(url, headers=HEADERS, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / filename).write_bytes(resp.content)
    _mem.pop(filename, None)  # invalidate in-memory cache


def ensure_cache(force: bool = False) -> dict:
    """Download missing or outdated data files from craftofexile.com.

    Checks the homepage for updated ?v= timestamps at most every CHECK_INTERVAL
    seconds so we don't hammer the site. Pass force=True to skip the interval
    guard and re-check immediately.

    Returns a status dict with keys: checked (bool), downloaded (list[str]),
    skipped (list[str]), errors (dict[str, str]).
    """
    manifest = _load_manifest()
    last_check = manifest.get("_last_check", 0)
    now = time.time()

    result: dict = {"checked": False, "downloaded": [], "skipped": [], "errors": {}}

    all_present = all((CACHE_DIR / f).exists() for f in _DATA_FILES)
    due_for_check = (now - last_check) > CHECK_INTERVAL

    if not force and all_present and not due_for_check:
        result["skipped"] = list(_DATA_FILES)
        return result

    # Fetch remote versions
    try:
        remote = _fetch_remote_versions()
        result["checked"] = True
    except Exception as e:
        # If we can't reach the site and have local files, carry on
        if all_present:
            result["skipped"] = list(_DATA_FILES)
            result["errors"]["homepage"] = str(e)
            return result
        raise RuntimeError(
            f"Could not reach craftofexile.com to download data: {e}\n"
            "Check your internet connection and try again."
        ) from e

    time.sleep(DOWNLOAD_DELAY)

    for filename in _DATA_FILES:
        local = CACHE_DIR / filename
        remote_v = remote.get(filename)
        cached_v = manifest.get(filename)

        needs = force or not local.exists() or (remote_v and remote_v != cached_v)
        if not needs:
            result["skipped"].append(filename)
            continue

        try:
            _download_file(filename, remote_v)
            manifest[filename] = remote_v or "unknown"
            result["downloaded"].append(filename)
            # Polite gap between successive downloads
            if filename != list(_DATA_FILES)[-1]:
                time.sleep(DOWNLOAD_DELAY)
        except Exception as e:
            result["errors"][filename] = str(e)

    manifest["_last_check"] = now
    _save_manifest(manifest)
    return result


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

_JS_ASSIGN_RE = re.compile(r'^\s*[A-Za-z_]\w*\s*=\s*')


def _load(filename: str) -> Any:
    """Return parsed JSON for a cached file, triggering download if missing.

    craftofexile serves files as JS assignments (e.g. ``poecd={...}``) rather
    than bare JSON, so we strip the leading ``varname=`` before parsing.
    """
    if filename not in _mem:
        local = CACHE_DIR / filename
        if not local.exists():
            ensure_cache()
        text = (CACHE_DIR / filename).read_text(encoding="utf-8")
        text = _JS_ASSIGN_RE.sub("", text, count=1)
        _mem[filename] = json.loads(text)
    return _mem[filename]


def _lang() -> dict:
    return _load("poec_lang.us.json")


def _data() -> dict:
    return _load("poec_data.json")


def _common() -> dict:
    return _load("poec_common.json")


# ---------------------------------------------------------------------------
# Internal helpers for the richer tools
# ---------------------------------------------------------------------------

def _require_cache() -> str | None:
    """Return an error string if any data file is missing, else None."""
    missing = [f for f in _DATA_FILES if not (CACHE_DIR / f).exists()]
    if missing:
        return (
            "Craft of Exile data not cached yet.\n"
            "Run `update_craftofexile_cache()` to download the required files first.\n\n"
            f"Missing: {', '.join(missing)}"
        )
    return None


def _parse_pipe_ids(pipe_str: str | None) -> list[str]:
    """Parse '|5|16|17|' into ['5', '16', '17']."""
    if not pipe_str:
        return []
    return [x for x in pipe_str.split("|") if x.strip()]


def _build_base_id_map() -> dict[str, str]:
    """Return {id_base_str: name_base} from bases.seq."""
    return {
        str(e.get("id_base", "")): e.get("name_base", "")
        for e in _data().get("bases", {}).get("seq", [])
    }


def _build_mtype_id_map() -> dict[str, str]:
    """Return {id_mtype_str: name_mtype} from mtypes.seq."""
    return {
        str(e.get("id_mtype", "")): e.get("name_mtype", "")
        for e in _data().get("mtypes", {}).get("seq", [])
    }


# ---------------------------------------------------------------------------
# Public MCP tools
# ---------------------------------------------------------------------------


def craftofexile_cache_status() -> str:
    """Show the status of the local Craft of Exile data cache.

    Reports which files are present, their version timestamps, and when the
    site was last checked for updates. Run this before crafting queries to
    confirm data is available, or to see if an update is needed.
    """
    manifest = _load_manifest()
    last_check = manifest.get("_last_check", 0)
    check_str = (
        f"{int((time.time() - last_check) / 60)} minutes ago"
        if last_check else "never"
    )

    lines = [
        f"## Craft of Exile Cache Status",
        f"Cache directory: `{CACHE_DIR}`",
        f"Last version check: {check_str}",
        "",
        "| File | Cached | Version |",
        "|------|--------|---------|",
    ]

    for filename in _DATA_FILES:
        local = CACHE_DIR / filename
        present = "yes" if local.exists() else "MISSING"
        version = manifest.get(filename, "—")
        lines.append(f"| {filename} | {present} | {version} |")

    if not any((CACHE_DIR / f).exists() for f in _DATA_FILES):
        lines += [
            "",
            "**No data cached yet.** Call `update_craftofexile_cache()` to download.",
        ]

    return "\n".join(lines)


def update_craftofexile_cache(force: bool = False) -> str:
    """Download or refresh Craft of Exile data files from craftofexile.com.

    On first call this downloads all files (~a few MB total). Subsequent calls
    only re-download files whose version has changed on the site (checked via
    the ?v= timestamps embedded in the craftofexile homepage).

    The site is checked for updates at most every 12 hours automatically.
    Pass force=True to bypass the interval and check immediately.

    Files are cached in reference_data/craftofexile/ which is gitignored —
    each user fetches their own copy and nothing is redistributed.

    Args:
        force: Re-check the site immediately even if checked recently.
    """
    result = ensure_cache(force=force)

    lines = ["## Craft of Exile Cache Update"]
    if result.get("checked"):
        lines.append("Checked craftofexile.com for updates.")
    else:
        lines.append("Skipped version check (checked recently; pass force=True to override).")

    if result["downloaded"]:
        lines.append(f"\n**Downloaded ({len(result['downloaded'])}):**")
        for f in result["downloaded"]:
            lines.append(f"  - {f}")
    if result["skipped"]:
        lines.append(f"\n**Already up to date ({len(result['skipped'])}):**")
        for f in result["skipped"]:
            lines.append(f"  - {f}")
    if result["errors"]:
        lines.append(f"\n**Errors:**")
        for f, e in result["errors"].items():
            lines.append(f"  - {f}: {e}")

    if not result["downloaded"] and not result["errors"]:
        lines.append("\nAll files are current.")

    return "\n".join(lines)


def search_craft_mods(query: str, item_class: str = "") -> str:
    """Search Craft of Exile mod data by keyword.

    Searches mod text for the given keyword and returns matching mods with
    their IDs and text. Optionally narrow by item class (e.g. 'helmet',
    'ring', 'bow').

    This data comes from craftofexile.com's mod pool — it reflects the full
    list of rollable mods with their weight groupings as craftofexile tracks
    them, which is more crafting-focused than raw game data.

    Args:
        query: Keyword to search mod text for (e.g. 'life', 'fire resistance',
               'attack speed', 'chaos').
        item_class: Optional item class filter to narrow results.
    """
    ensure_cache()
    lang = _lang()

    # poec_lang.us.json structure: top-level key "mod" mapping id -> text
    # (possibly nested under a game-version key — handle both)
    mod_map: dict = {}
    raw = lang
    if "mod" in raw:
        mod_map = raw["mod"]
    else:
        # Try one level of nesting (poe1 / poe2 keys)
        for v in raw.values():
            if isinstance(v, dict) and "mod" in v:
                mod_map = v["mod"]
                break

    if not mod_map:
        return "Could not parse mod data from cached poec_lang.us.json. Try `update_craftofexile_cache(force=True)`."

    q = query.lower()
    matches = []
    for mod_id, mod_entry in mod_map.items():
        # mod_entry may be a string or a dict with a "text" / "name" key
        if isinstance(mod_entry, str):
            text = mod_entry
        elif isinstance(mod_entry, dict):
            text = mod_entry.get("text") or mod_entry.get("name") or str(mod_entry)
        else:
            continue

        if q not in text.lower():
            continue

        if item_class and item_class.lower() not in str(mod_entry).lower():
            continue

        matches.append((mod_id, text))

    if not matches:
        qualifier = f" on '{item_class}'" if item_class else ""
        return f"No mods matching '{query}'{qualifier} found in Craft of Exile data."

    lines = [
        f"## Craft of Exile mods matching '{query}'"
        + (f" (filtered by '{item_class}')" if item_class else ""),
        f"Found {len(matches)} mod(s).\n",
    ]
    for mod_id, text in matches[:100]:
        lines.append(f"- **{mod_id}**: {text}")

    if len(matches) > 100:
        lines.append(f"\n*Showing first 100 of {len(matches)}. Narrow with a more specific query.*")

    return "\n".join(lines)


def get_craft_tiers(base_type: str, query: str) -> str:
    """Show tier value ranges and spawn weights for a craftable mod on a specific item class.

    Looks up mods matching the keyword query and returns every tier's minimum item level,
    value range, and spawn weight — the raw numbers behind crafting probability. Tiers are
    shown highest-first (T1 = best values, lowest weight / hardest to roll).

    Only tiers with non-zero weight are shown; zero-weight entries are unrollable
    (removed or debug mods) and are silently excluded.

    Args:
        base_type: Item class to look up tiers for. Partial, case-insensitive match.
                   Examples: 'staff', 'ring', 'helmet', 'bow', 'glove'.
                   Use 'warstaff' for warstaves. Matches against the class name,
                   not individual base item names.
        query: Keyword to search mod text for. Examples: 'increased physical damage',
               'maximum life', 'attack speed', 'critical strike multiplier'.
    """
    err = _require_cache()
    if err:
        return err

    d = _data()
    lang_mods = _lang().get("mod", {})
    tiers_data = d.get("tiers", {})
    mods_seq = d.get("modifiers", {}).get("seq", [])

    bases_by_id = _build_base_id_map()

    bt = base_type.lower()
    target_base_ids = {bid for bid, name in bases_by_id.items() if bt in name.lower()}
    if not target_base_ids:
        sample = sorted(set(bases_by_id.values()))[:25]
        return (
            f"No item class matching '{base_type}'.\n"
            f"Known classes (sample): {', '.join(sample)}"
        )

    # Index modifiers for affix/modgroup lookup
    mod_detail: dict[str, dict] = {}
    for m in mods_seq:
        mid = str(m.get("id_modifier", ""))
        if mid:
            mod_detail[mid] = m

    q = query.lower()
    results = []

    for mod_id, tier_by_base in tiers_data.items():
        relevant = target_base_ids & set(tier_by_base.keys())
        if not relevant:
            continue

        # Get display text from lang, fall back to modifier name
        text = lang_mods.get(mod_id) or lang_mods.get(int(mod_id) if str(mod_id).isdigit() else mod_id, "")
        if isinstance(text, dict):
            text = text.get("text", str(text))
        if not isinstance(text, str):
            text = ""
        info = mod_detail.get(str(mod_id), {})
        fallback_name = info.get("name_modifier", "")

        if q not in (text + " " + fallback_name).lower():
            continue

        display = text or fallback_name or f"mod {mod_id}"
        affix = info.get("affix", "?")
        modgroup = info.get("modgroup", "?")

        for bid in sorted(relevant):
            # Exclude zero-weight (unrollable) tiers
            tier_entries = [
                t for t in tier_by_base[bid]
                if str(t.get("weighting", "0")) != "0"
            ]
            if not tier_entries:
                continue
            results.append((display, mod_id, bid, bases_by_id[bid], affix, modgroup, tier_entries))

    if not results:
        return (
            f"No rollable tiers found for '{query}' on '{base_type}'.\n"
            "Try a simpler keyword (e.g. 'physical' rather than 'increased physical damage')."
        )

    lines = [f"## Craft of Exile — '{query}' tiers on {base_type}", ""]

    for display, mod_id, bid, base_name, affix, modgroup, tier_entries in results:
        lines.append(f"### {display}")
        lines.append(
            f"ID: {mod_id} | {affix} | group: {modgroup} | item class: {base_name} (id {bid})"
        )
        lines.append("")
        lines.append("| Tier | Min iLvl | Values | Weight |")
        lines.append("|------|----------|--------|--------|")

        sorted_tiers = sorted(tier_entries, key=lambda t: int(t.get("ilvl", 0)), reverse=True)
        for i, t in enumerate(sorted_tiers):
            ilvl = t.get("ilvl", "?")
            raw_nv = t.get("nvalues", "?")
            weight = t.get("weighting", "?")
            try:
                vals = json.loads(raw_nv)
                parts = []
                for v in vals:
                    if isinstance(v, list) and len(v) == 2:
                        parts.append(f"{v[0]}–{v[1]}")
                    else:
                        parts.append(str(v))
                formatted = ", ".join(parts)
            except Exception:
                formatted = raw_nv
            lines.append(f"| T{i + 1} | {ilvl} | {formatted} | {weight} |")

        lines.append("")

    return "\n".join(lines)


def get_fossil_info(fossil_name: str) -> str:
    """Look up a fossil's mod type affinities: what it boosts, reduces, and blocks.

    Returns the mod type categories it makes more likely, less likely, and blocked —
    useful for planning fossil-crafting strategies (e.g. which fossil boosts physical
    mods without blocking life mods).

    Args:
        fossil_name: Fossil name to look up. Partial, case-insensitive match.
                     Examples: 'pristine', 'aberrant', 'jagged', 'dense'.
                     Use a partial name to list all matching fossils.
    """
    err = _require_cache()
    if err:
        return err

    d = _data()
    fossils = d.get("fossils", {}).get("seq", [])
    mtype_names = _build_mtype_id_map()

    fl = fossil_name.lower()
    matches = [f for f in fossils if fl in f.get("name_fossil", "").lower()]

    if not matches:
        all_names = sorted(f.get("name_fossil", "") for f in fossils)
        return (
            f"No fossil matching '{fossil_name}'.\n"
            f"Known fossils: {', '.join(all_names)}"
        )

    lines = []
    for fossil in matches:
        name = fossil.get("name_fossil", "?")
        lines.append(f"## {name} Fossil")
        lines.append("")

        more = _parse_pipe_ids(fossil.get("more_list"))
        less = _parse_pipe_ids(fossil.get("less_list"))
        block = _parse_pipe_ids(fossil.get("block_list"))

        if more:
            names = [mtype_names.get(x, f"type {x}") for x in more]
            lines.append(f"**More likely** (boosted): {', '.join(names)}")
        if less:
            names = [mtype_names.get(x, f"type {x}") for x in less]
            lines.append(f"**Less likely** (reduced): {', '.join(names)}")
        if block:
            names = [mtype_names.get(x, f"type {x}") for x in block]
            lines.append(f"**Blocked**: {', '.join(names)}")
        if not more and not less and not block:
            lines.append("No affinity data found for this fossil.")

        # mod_data gives numeric weight multipliers per mod category
        raw_mod_data = fossil.get("mod_data")
        if raw_mod_data:
            try:
                mod_data: dict = json.loads(raw_mod_data)
                lines.append("")
                lines.append("**Raw affinity weights** (0 = blocked; higher = more weight in the mod pool):")
                lines.append("")
                lines.append("| Mod Category | Weight |")
                lines.append("|--------------|--------|")
                for cat, weight in sorted(mod_data.items(), key=lambda x: -x[1]):
                    w_str = "blocked" if weight == 0 else str(weight)
                    lines.append(f"| {cat} | {w_str} |")
            except Exception:
                pass

        lines.append("")

    return "\n".join(lines)


def get_essence_mods(essence_name: str, item_type: str = "") -> str:
    """Look up the guaranteed mod an essence provides on each item type.

    Essences guarantee one specific mod when used on an item, replacing a random
    mod with a fixed one. This tool shows those guaranteed mods, optionally filtered
    to a specific item slot.

    Args:
        essence_name: Essence name to look up. Partial, case-insensitive match.
                      Examples: 'anger', 'delirium', 'dread', 'woe'.
                      Omit the 'Screaming'/'Shrieking'/etc. tier prefix — search
                      by the base name and all tiers will be shown.
        item_type: Optional slot filter. Partial, case-insensitive.
                   Examples: 'staff', 'ring', 'helmet', 'glove', 'amulet'.
    """
    err = _require_cache()
    if err:
        return err

    d = _data()
    essences = d.get("essences", {}).get("seq", [])

    el = essence_name.lower()
    matches = [e for e in essences if el in e.get("name_essence", "").lower()]

    if not matches:
        all_names = sorted(e.get("name_essence", "") for e in essences)
        return (
            f"No essence matching '{essence_name}'.\n"
            f"Known essences: {', '.join(all_names)}"
        )

    it = item_type.lower()
    lines = []

    for essence in matches:
        name = essence.get("name_essence", "?")
        lines.append(f"## {name} Essence — Guaranteed Mods")
        lines.append("")

        raw_tooltip = essence.get("tooltip", "[]")
        try:
            tooltip: list = json.loads(raw_tooltip)
        except Exception:
            lines.append("Could not parse mod data for this essence.")
            lines.append("")
            continue

        filtered = [t for t in tooltip if not it or it in t.get("lbl", "").lower()]

        if not filtered:
            qualifier = f" for item type '{item_type}'" if it else ""
            lines.append(f"No guaranteed mods found{qualifier}.")
            lines.append("")
            continue

        lines.append("| Slot | Guaranteed Mod |")
        lines.append("|------|----------------|")
        for entry in filtered:
            slot = entry.get("lbl", "?")
            val = entry.get("val", "?")
            lines.append(f"| {slot} | {val} |")
        lines.append("")

    return "\n".join(lines)


def get_craft_base_items(query: str = "", item_class: str = "") -> str:
    """Look up base items from Craft of Exile data.

    Returns base item names with their drop levels. Use this to find the
    exact base name to use when setting up a crafting scenario.

    Args:
        query: Optional name filter (e.g. 'sword', 'vaal', 'hubris').
        item_class: Optional class filter (e.g. 'helmet', 'ring', 'bow').
    """
    ensure_cache()
    data = _data()

    # poec_data.json structure: top-level may have poe1/poe2 split or direct bitems
    items_list: list = []
    raw = data
    if "bitems" in raw:
        items_list = raw["bitems"].get("seq", [])
    else:
        for v in raw.values():
            if isinstance(v, dict) and "bitems" in v:
                items_list = v["bitems"].get("seq", [])
                break

    if not items_list:
        return "Could not parse base item data from cached poec_data.json. Try `update_craftofexile_cache(force=True)`."

    q = query.lower()
    cls = item_class.lower()

    matches = []
    for item in items_list:
        name = item.get("name_bitem") or item.get("name") or ""
        if q and q not in name.lower():
            continue
        if cls and cls not in str(item).lower():
            continue
        drop = item.get("drop_level", "?")
        matches.append((name, drop))

    if not matches:
        qualifier = f" in '{item_class}'" if item_class else ""
        return f"No base items matching '{query}'{qualifier} found."

    lines = [
        f"## Craft of Exile base items" + (f" matching '{query}'" if query else ""),
        f"Found {len(matches)} item(s).\n",
        "| Base | Drop Level |",
        "|------|-----------|",
    ]
    for name, drop in sorted(matches, key=lambda x: str(x[0]))[:200]:
        lines.append(f"| {name} | {drop} |")

    if len(matches) > 200:
        lines.append(f"\n*Showing first 200 of {len(matches)}. Use a more specific query.*")

    return "\n".join(lines)
