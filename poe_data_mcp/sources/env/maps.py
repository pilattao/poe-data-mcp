import httpx

from poe_data_mcp.sources.common import Cache, fetch_page

BASE_URL = "https://poedb.tw/us"

_map_cache = Cache()

ATLAS_URL = "https://poedb.tw/us/Atlas_of_Worlds"


def _get_all_maps() -> list[dict]:
    """Scrape Atlas_of_Worlds and return all maps with tier/boss/link info."""
    cached = _map_cache.get()
    if cached is not None:
        return cached

    soup = fetch_page(ATLAS_URL)
    maps = []

    for entry in soup.select("div.d-flex.border-top.rounded"):
        body = entry.select_one("div.flex-grow-1")
        if not body:
            continue

        # Map name: first div with no class inside the body
        name_div = body.find("div", class_="")
        if not name_div:
            continue
        name = name_div.get_text(strip=True)
        if not name:
            continue

        tier = ""
        boss = ""
        linked = []

        for prop in body.select("div.property"):
            text = prop.get_text(separator=" ", strip=True)
            if text.startswith("Tier:"):
                tier = text.replace("Tier:", "").strip()
            elif text.startswith("Boss Fights:"):
                boss = text.replace("Boss Fights:", "").strip()
            elif text.startswith("Link:"):
                linked = [a.get_text(strip=True) for a in prop.find_all("a")]

        # Map detail URL — guess from name (poedb uses underscores)
        slug = name.replace(" ", "_")
        url = f"https://poedb.tw/us/{slug}"

        maps.append({
            "name": name,
            "url": url,
            "tiers": tier,
            "boss": boss,
            "linked": linked,
        })

    _map_cache.set(maps)
    return maps


def format_map(m: dict) -> str:
    parts = [f"- **{m['name']}**"]
    if m["tiers"]:
        parts.append(f"(T{m['tiers']})")
    if m["boss"]:
        parts.append(f"— Boss: {m['boss']}")
    return " ".join(parts)


def get_map_detail(name: str) -> str:
    maps = _get_all_maps()

    cached = None
    name_lower = name.lower()
    for m in maps:
        if m["name"] == name:
            cached = m
            break
    if not cached:
        for m in maps:
            if m["name"].lower() == name_lower:
                cached = m
                break

    if cached:
        url = cached["url"]
    else:
        url = f"{BASE_URL}/{name.replace(' ', '_')}"

    sections = []
    if cached:
        sections.append(f"# {cached['name']}")
        if cached["tiers"]:
            sections.append(f"**Tier:** {cached['tiers']}")
        if cached["boss"]:
            sections.append(f"**Boss:** {cached['boss']}")
        if cached["linked"]:
            sections.append(f"**Connected maps:** {', '.join(cached['linked'])}")
        sections.append("")

    try:
        soup = fetch_page(url)
    except httpx.HTTPStatusError:
        if cached:
            sections.append(f"**Full details:** {url}")
            return "\n".join(sections)
        return f"Could not find map '{name}'. Try env_search to find the correct name."

    if not cached:
        sections.append(f"# {name}\n")

    tables = soup.find_all("table")
    if tables:
        for tr in tables[0].find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            key = tds[0].get_text(strip=True)
            if key == "Level":
                sections.append(f"**Area level:** {tds[1].get_text(strip=True)}")
            elif key == "Vaal Area":
                sections.append(f"**Vaal area:** {tds[1].get_text(strip=True)}")

    wiki_link = soup.find("a", string="Community Wiki")
    if wiki_link and wiki_link.get("href"):
        sections.append(f"**Community Wiki:** {wiki_link['href']}")

    sections.append(f"**Full details:** {url}")
    return "\n".join(sections)
