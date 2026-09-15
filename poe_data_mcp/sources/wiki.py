import re
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from poe_data_mcp.sources.common import HEADERS, GAME

# poewiki's article HTML sits behind an anti-bot challenge; its MediaWiki API does not.
_WIKI_HOST = "www.poe2wiki.net" if GAME == "poe2" else "www.poewiki.net"
_WIKI_API = f"https://{_WIKI_HOST}/w/api.php"

# Identify honestly. The shared HEADERS spoof a browser User-Agent, which is exactly what
# poewiki's anti-bot layer challenges with proof-of-work — a spoofed browser UA gets a
# "Making sure you're not a bot!" page, while a descriptive tool UA is served normally.
# This also matches MediaWiki API etiquette (identify your client, provide contact info).
_WIKI_HEADERS = {
    "User-Agent": "poe-data-mcp/0.3 (+https://github.com/charleslucas/poe-data-mcp) python-httpx",
    "Accept": "application/json",
}


def _page_title_from_url(wiki_url: str) -> str | None:
    """Extract the MediaWiki page title from a poewiki.net URL."""
    path = urlparse(wiki_url).path
    if "/wiki/" not in path:
        return None
    title = path.split("/wiki/", 1)[1].split("#", 1)[0].strip("/")
    return unquote(title).replace("_", " ") or None

# Sections worth extracting from poewiki.net pages
_USEFUL_SECTIONS = {
    "Mechanics", "Item acquisition", "Recipes",
    "Skill functions and interactions",
    "Foulborn modifiers", "Alternate artwork",
}
# Sections to skip
_SKIP_SECTIONS = {
    "Contents", "See also", "References", "Item skins",
    "Version history", "External links",
}


def _clean_wiki_text(el) -> str:
    """Extract text from a wiki element with proper spacing and punctuation."""
    text = el.get_text(" ", strip=True)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def _extract_section_text(heading, content_div) -> list[str]:
    """Extract text from elements following a heading until the next heading of same or higher level."""
    lines = []
    level = int(heading.name[1])  # h2 -> 2, h3 -> 3
    for sibling in heading.find_next_siblings():
        if sibling.name and sibling.name in ("h2", "h3"):
            sib_level = int(sibling.name[1])
            if sib_level <= level:
                break
            if level == 2 and sib_level == 3:
                break
        if sibling.name == "p":
            text = _clean_wiki_text(sibling)
            if text:
                lines.append(text)
        elif sibling.name == "ul":
            for li in sibling.find_all("li", recursive=False):
                text = _clean_wiki_text(li)
                if text:
                    lines.append(f"- {text}")
        elif sibling.name == "table":
            rows = []
            for tr in sibling.find_all("tr"):
                cells = []
                for td in tr.find_all(["th", "td"]):
                    cells.append(td.get_text(" ", strip=True))
                if cells:
                    rows.append(cells)
            if rows:
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
                for row in rows[1:]:
                    padded = row + [""] * (len(rows[0]) - len(row))
                    lines.append("| " + " | ".join(padded[:len(rows[0])]) + " |")
        elif sibling.name == "div" and "navbox" in " ".join(sibling.get("class", [])):
            break
    return lines


def fetch_wiki_page(wiki_url: str) -> str:
    """Fetch article content from the configured game's community wiki.

    Use this to get detailed mechanics, acquisition info, and recipes from the
    Community Wiki link returned by get_item_detail or get_gem_detail.

    Args:
        wiki_url: Full article URL on the configured game wiki (poe2wiki.net for PoE2)
    """
    if urlparse(wiki_url).hostname not in {_WIKI_HOST, _WIKI_HOST.removeprefix("www.")}:
        raise ValueError(f"For {GAME}, use a {_WIKI_HOST} article URL.")

    title_hint = _page_title_from_url(wiki_url)
    if not title_hint:
        return f"Could not parse a page title from {wiki_url!r} (expected .../wiki/Page_Name)."

    # poewiki serves its ARTICLE HTML behind an anti-bot proof-of-work interstitial, so
    # requesting the page directly returns HTTP 200 with a "Making sure you're not a bot!"
    # document and no article markup. The MediaWiki API is not gated the same way, so parse
    # through it and hand the rendered HTML to the same extraction logic as before.
    try:
        resp = httpx.get(
            _WIKI_API,
            params={
                "action": "parse",
                "page": title_hint,
                "prop": "text",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
            },
            headers=_WIKI_HEADERS,
            follow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        return f"Failed to fetch wiki page (HTTP {e.response.status_code})."
    except ValueError:
        return (
            "The wiki returned a non-JSON response — poewiki may be serving an anti-bot "
            "challenge. Try again shortly, or read the page in a browser."
        )

    if isinstance(payload, dict) and "error" in payload:
        info = payload["error"].get("info", "unknown error")
        return f'Wiki API error for "{title_hint}": {info}'

    parsed = (payload or {}).get("parse") or {}
    page_html = parsed.get("text") or ""
    if not page_html:
        return f'No wiki content returned for "{title_hint}".'

    soup = BeautifulSoup(page_html, "html.parser")
    content = soup.select_one("div.mw-parser-output") or soup
    api_title = parsed.get("title")

    # Strip inline tooltip popups (keep only the visible activator text)
    for popup in content.select(".hoverbox__display"):
        popup.decompose()

    sections = []

    # Page title — the API response carries no <h1>, so prefer the title it reports
    # (which also reflects any redirect that was followed).
    title = api_title or title_hint
    sections.append(f"# {title}")
    sections.append("")

    # Intro paragraphs (before first h2)
    for child in content.children:
        if hasattr(child, "name") and child.name == "h2":
            break
        if hasattr(child, "name") and child.name == "p":
            text = _clean_wiki_text(child)
            if text:
                sections.append(text)
                sections.append("")

    # Extract useful sections
    for heading in content.find_all(["h2", "h3"]):
        span = heading.select_one("span.mw-headline")
        section_name = span.get_text(strip=True) if span else heading.get_text(strip=True)

        if section_name in _SKIP_SECTIONS:
            continue

        lines = _extract_section_text(heading, content)
        if lines:
            prefix = "##" if heading.name == "h2" else "###"
            sections.append(f"{prefix} {section_name}")
            sections.extend(lines)
            sections.append("")

    sections.append(f"**Source:** {wiki_url}")
    return "\n".join(sections)


def wiki_cargo_query(tables: str, fields: str, where: str = "", limit: int = 50, offset: int = 0) -> str:
    """Query the configured game's wiki Cargo tables for item/mod data
    instead of fetching wiki pages one at a time.

    Returns a bounded page of rows. Uses poe2wiki.net with POE_GAME=poe2;
    poewiki.net requires explicit POE_GAME=poe1. Each wiki has its own schema.

    Args:
        tables: Cargo table(s). Most useful: "items" (name, class, description,
            drop_text, base_item, required_level), "mods" (id, name, domain,
            generation_type, stat_text), "skill_gems". See Special:CargoTables.
        fields: Table-qualified, comma-separated: "items.name,items.class".
        where: SQL-ish filter, e.g. items.class="Currency Item" or
            items.name="Divine Orb". Strongly recommended.
        limit: 1-500 rows (default 50).  offset: pagination for larger sets.

    Returns a count line then one " | "-joined row per line.
    Wiki lags new leagues days-to-weeks: empty result != nonexistent in game.
    Etiquette: honest UA, cache results locally, keep queries modest.
    """
    import httpx

    params = {
        "action": "cargoquery",
        "tables": tables,
        "fields": fields,
        "limit": max(1, min(int(limit), 500)),
        "offset": max(0, int(offset)),
        "format": "json",
    }
    if where:
        params["where"] = where
    try:
        resp = httpx.get(_WIKI_API, params=params, headers=_WIKI_HEADERS,
                         timeout=30, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return "Cargo query failed: {} — tables={} where={}".format(e, tables, where)
    if "error" in data:
        info = data["error"].get("info", str(data["error"]))
        return "Cargo API error: {} (check names at Special:CargoTables)".format(info)
    rows = data.get("cargoquery", [])
    if not rows:
        return ("0 rows. NOTE: empty != nonexistent in game — the wiki lags new leagues. "
                "Verify table/field spelling (Special:CargoTables); try a LIKE filter.")
    field_names = [f.split(".")[-1].strip() for f in fields.split(",")]
    out = ["{} row(s) (limit {}, offset {}):".format(len(rows), params["limit"], params["offset"])]
    for r in rows:
        t = r.get("title", {})
        out.append(" | ".join(str(t.get(fn, "") or "-") for fn in field_names))
    return chr(10).join(out)
