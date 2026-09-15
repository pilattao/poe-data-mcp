from urllib.parse import urlparse
import base64
import json
import zlib
import xml.etree.ElementTree as ET

import httpx

from poe_data_mcp.sources.common import HEADERS

# Stats worth showing in the summary
_KEY_STATS = {
    "Life": "Life",
    "Mana": "Mana",
    "Spirit": "Spirit",
    "SpiritUnreserved": "Unreserved Spirit",
    "ManaUnreserved": "Unreserved Mana",
    "EnergyShield": "Energy Shield",
    "Armour": "Armour",
    "Evasion": "Evasion",
    "Ward": "Ward",
    "TotalDPS": "Total DPS",
    "AverageDamage": "Avg Damage",
    "Speed": "Attacks/Casts per Second",
    "HitChance": "Hit Chance",
}

# Resistance stats
_RESIST_STATS = {
    "FireResist": "Fire",
    "ColdResist": "Cold",
    "LightningResist": "Lightning",
    "ChaosResist": "Chaos",
}

# Defense stats (%, shown with % suffix) — use PoB's "Effective" keys
_DEFENSE_STATS = {
    "EffectiveSpellSuppressionChance": "Spell Suppression",
    "EffectiveBlockChance": "Block",
    "EffectiveSpellBlockChance": "Spell Block",
    "AttackDodgeChance": "Attack Dodge",
    "SpellDodgeChance": "Spell Dodge",
}

# Charge maximums
_CHARGE_STATS = {
    "EnduranceChargesMax": "Endurance",
    "FrenzyChargesMax": "Frenzy",
    "PowerChargesMax": "Power",
}

# Slot display order (using real PoB slot names)
_SLOT_ORDER = [
    "Helmet", "Amulet", "Weapon 1", "Weapon 2", "Body Armour",
    "Gloves", "Belt", "Boots", "Ring 1", "Ring 2",
    "Flask 1", "Flask 2", "Charm 1", "Charm 2", "Charm 3", "Flask 3", "Flask 4", "Flask 5",
    "Graft 1", "Graft 2",
    "Weapon 1 Swap", "Weapon 2 Swap",
]

# Item text metadata lines to skip (not mods)
_SKIP_PREFIXES = (
    "Unique ID:", "Item Level:", "LevelReq:", "Quality:", "Sockets:",
    "Energy Shield:", "EnergyShieldBasePercentile:",
    "Armour:", "ArmourBasePercentile:",
    "Evasion:", "EvasionBasePercentile:",
    "Ward:", "WardBasePercentile:",
    "Physical Damage:", "Elemental Damage:", "Chaos Damage:",
    "Critical Strike Chance:", "Attacks per Second:", "Weapon Range:",
    "Stack Size:", "Hunter Item", "Crusader Item", "Redeemer Item",
    "Warlord Item", "Elder Item", "Shaper Item", "Synthesised Item",
)

# Regex to strip ALL PoB annotation tags like {crafted}, {tags:...}, {range:0.5}, {exarch}, etc.
import re as _re
_POB_TAG_RE = _re.compile(r'\{[^}]*\}')


def _fetch_raw(url: str) -> str:
    """Resolve a share URL to a raw PoB base64 code string."""
    url = url.strip().rstrip("/")

    if "pobb.in" in url:
        # https://pobb.in/{id}            → https://pobb.in/{id}/raw
        # https://pobb.in/u/{user}/{id}   → https://pobb.in/u/{user}/{id}/raw
        raw_url = url + "/raw"
    elif urlparse(url).hostname in {"poedb.tw", "poe2db.tw"}:
        # https://poedb.tw/{locale}/PathOfBuilding?id={id} → https://poedb.tw/pob/{id}/raw
        import re as _urlre
        m = _urlre.search(r"[?&]id=([^&]+)", url)
        if not m:
            raise ValueError(f"Could not extract build ID from poedb.tw URL: {url}")
        raw_url = f"https://{urlparse(url).hostname}/pob/{m.group(1)}/raw"
    elif "pastebin.com" in url:
        # https://pastebin.com/{id} → https://pastebin.com/raw/{id}
        paste_id = url.rstrip("/").split("/")[-1]
        raw_url = f"https://pastebin.com/raw/{paste_id}"
    else:
        raise ValueError("Unsupported URL. Supported: pobb.in, poedb.tw, pastebin.com")

    resp = httpx.get(raw_url, headers=HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.text.strip()


def _decode_pob(code: str) -> ET.Element:
    """Decode a PoB base64 export string → XML root element."""
    rem = len(code) % 4
    if rem:
        code += "=" * (4 - rem)
    try:
        data = base64.urlsafe_b64decode(code)
        xml_bytes = zlib.decompress(data)
        root = ET.fromstring(xml_bytes)
        from poe_data_mcp.sources.common import GAME
        expected = "PathOfBuilding2" if GAME == "poe2" else "PathOfBuilding"
        if root.tag != expected:
            raise ValueError(f"Expected {expected} XML for {GAME}, got {root.tag}")
        return root
    except Exception as e:
        raise ValueError(f"Could not decode PoB code: {e}")


def _clean_mod(mod: str) -> str:
    """Strip all PoB annotation tags like {crafted}, {exarch}, {tags:...}, {range:0.5} etc."""
    return _POB_TAG_RE.sub("", mod).strip()


def _parse_build_info(root: ET.Element) -> dict:
    build = root.find("Build")
    if build is None:
        return {}

    info = {
        "class": build.get("className", "Unknown"),
        "ascendancy": build.get("ascendClassName", ""),
        "level": build.get("level", "?"),
        "bandit": build.get("bandit", ""),
        "pantheon_major": build.get("pantheonMajorGod", ""),
        "pantheon_minor": build.get("pantheonMinorGod", ""),
    }

    if root.tag == "PathOfBuilding2":
        info.update(bandit="", pantheon_major="", pantheon_minor="")
    all_tracked = {**_KEY_STATS, **_RESIST_STATS, **_DEFENSE_STATS, **_CHARGE_STATS}
    stats = {}
    for stat in build.findall("PlayerStat"):
        key = stat.get("stat", "")
        if key in all_tracked:
            try:
                stats[key] = float(stat.get("value", "0"))
            except ValueError:
                pass
    info["stats"] = stats
    return info


def _parse_skills(root: ET.Element) -> list[dict]:
    skills_el = root.find("Skills")
    if skills_el is None:
        return []

    # Identify the active SkillSet — fall back to first if not found
    active_id = skills_el.get("activeSkillSet", "1")
    skill_sets = skills_el.findall("SkillSet")

    if skill_sets:
        active_set = next(
            (s for s in skill_sets if s.get("id") == active_id),
            skill_sets[0],
        )
        skill_elements = active_set.findall("Skill")
    else:
        # Older PoB format: <Skill> directly under <Skills>
        skill_elements = skills_el.findall("Skill")

    groups = []
    for skill in skill_elements:
        if skill.get("enabled", "true").lower() == "false":
            continue

        # PoB serializes a missing main skill as the literal string "nil"
        # (and the attribute may be absent on older exports). Default to the
        # first gem in either case rather than crashing on int("nil").
        try:
            main_idx = int(skill.get("mainActiveSkill", "1")) - 1  # 1-based → 0-based
        except ValueError:
            main_idx = 0

        gems = []
        for i, gem in enumerate(skill.findall("Gem")):
            if gem.get("enabled", "true").lower() == "false":
                continue
            # nameSpec is the display name; skillId is internal
            name = gem.get("nameSpec") or gem.get("skillId", "")
            if not name:
                continue
            lvl = gem.get("level", "")
            qual = gem.get("quality", "0")
            is_main = (i == main_idx)
            gems.append({
                "name": name,
                "level": lvl,
                "quality": qual,
                "is_main": is_main,
            })

        if gems:
            groups.append({
                "slot": skill.get("slot", ""),
                "label": skill.get("label", ""),
                "gems": gems,
            })

    return groups


def _parse_item_text(text: str) -> dict:
    """Parse a PoB item text block (no --- separators, uses Implicits: N)."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    result = {"rarity": "", "name": "", "base": "", "implicits": [], "mods": []}

    i = 0
    # Extract rarity
    while i < len(lines):
        if lines[i].startswith("Rarity:"):
            result["rarity"] = lines[i].removeprefix("Rarity:").strip()
            i += 1
            break
        i += 1

    # Extract name (line 1) and base type (line 2 for rare/unique)
    name_count = 0
    while i < len(lines):
        line = lines[i]
        if any(line.startswith(p) for p in _SKIP_PREFIXES) or line.startswith("Implicits:"):
            break
        if name_count == 0:
            result["name"] = line
        elif name_count == 1 and result["rarity"] in ("RARE", "UNIQUE", "MAGIC"):
            result["base"] = line
        else:
            break
        name_count += 1
        i += 1

    # Skip metadata lines until Implicits:
    while i < len(lines) and not lines[i].startswith("Implicits:"):
        i += 1

    # Parse implicits
    if i < len(lines) and lines[i].startswith("Implicits:"):
        try:
            n = int(lines[i].removeprefix("Implicits:").strip())
        except ValueError:
            n = 0
        i += 1
        for _ in range(n):
            if i < len(lines):
                result["implicits"].append(_clean_mod(lines[i]))
                i += 1

    # Remaining lines are explicit mods
    while i < len(lines):
        result["mods"].append(_clean_mod(lines[i]))
        i += 1

    return result


def _parse_items(root: ET.Element) -> list[dict]:
    items_el = root.find("Items")
    if items_el is None:
        return []

    # Build id → parsed item dict
    item_map: dict[str, dict] = {}
    for item in items_el.findall("Item"):
        iid = item.get("id", "")
        text = (item.text or "").strip()
        if iid and text:
            item_map[iid] = _parse_item_text(text)

    # Collect Slot elements — they live inside <ItemSet>, not directly under <Items>
    item_sets = items_el.findall("ItemSet")
    if item_sets:
        active_id = items_el.get("activeItemSet", "1")
        active_set = next((it for it in item_sets if it.get("id") == active_id), item_sets[0])
        slot_sources = list(active_set.findall("Slot"))
    else:
        slot_sources = list(items_el.findall("Slot"))

    # Map slot name → item (skip abyssal sockets and empty slots)
    slot_items: dict[str, dict] = {}
    for slot_el in slot_sources:
        slot_name = slot_el.get("name", "")
        iid = slot_el.get("itemId", "")
        if "Abyssal Socket" in slot_name:
            continue
        if iid and iid != "0" and iid in item_map:
            slot_items[slot_name] = item_map[iid]

    # Return in display order
    equipped: list[dict] = []
    seen: set[str] = set()
    for slot in _SLOT_ORDER:
        if slot in slot_items:
            equipped.append({"slot": slot, **slot_items[slot]})
            seen.add(slot)
    # Append any remaining slots not in the predefined order
    for slot, item in slot_items.items():
        if slot not in seen:
            equipped.append({"slot": slot, **item})

    return equipped


def _split_camel(name: str) -> str:
    """'TheBrineKing' → 'The Brine King'"""
    return _re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)


def _strip_pob_colors(text: str) -> str:
    """Remove PoB color codes: ^x RRGGBB and ^N (single digit)."""
    text = _re.sub(r'\^x[0-9A-Fa-f]{6}', '', text)
    text = _re.sub(r'\^[0-9]', '', text)
    return text.strip()


def _extract_stage_indices(title: str) -> list[int]:
    """Extract stage indices from a title like 'Level 1-20 {1}' or '{6,7}'."""
    m = _re.search(r'\{([\d,\s]+)\}', title)
    if not m:
        return []
    return [int(x.strip()) for x in m.group(1).split(',') if x.strip().isdigit()]


def _clean_title(title: str) -> str:
    """Strip color codes and stage index tags from a layout title."""
    title = _strip_pob_colors(title)
    title = _re.sub(r'\s*\{[\d,\s]+\}\s*$', '', title)
    return title.strip()


def _parse_layouts(root: ET.Element) -> list[dict]:
    """
    Extract progression stages by grouping ItemSets, Tree Specs, and SkillSets
    that share the same {N} index tag in their titles.
    Returns a list of stage dicts sorted by stage index.
    """
    # Collect all components keyed by stage index
    stages: dict[int, dict] = {}

    def ensure(idx: int) -> dict:
        if idx not in stages:
            stages[idx] = {"index": idx, "items_title": "", "skills_title": "",
                           "tree_title": "", "tree_nodes": 0}
        return stages[idx]

    # ItemSets
    items_el = root.find("Items")
    if items_el is not None:
        for iset in items_el.findall("ItemSet"):
            raw = iset.get("title", "")
            for idx in _extract_stage_indices(raw):
                ensure(idx)["items_title"] = _clean_title(raw)

    # Tree Specs
    tree_el = root.find("Tree")
    if tree_el is not None:
        for spec in tree_el.findall("Spec"):
            raw = spec.get("title", "")
            indices = _extract_stage_indices(raw)
            nodes = len([n for n in spec.get("nodes", "").split(",") if n.strip()])
            for idx in indices:
                s = ensure(idx)
                s["tree_title"] = _clean_title(raw)
                s["tree_nodes"] = nodes

    # SkillSets
    skills_el = root.find("Skills")
    if skills_el is not None:
        for sset in skills_el.findall("SkillSet"):
            raw = sset.get("title", "")
            for idx in _extract_stage_indices(raw):
                ensure(idx)["skills_title"] = _clean_title(raw)

    if not stages:
        return []

    return sorted(stages.values(), key=lambda s: s["index"])


def _parse_notes(root: ET.Element) -> str:
    """Extract the Notes section text, stripping PoB color codes and whitespace."""
    notes_el = root.find("Notes")
    if notes_el is None or not notes_el.text:
        return ""
    return _strip_pob_colors(notes_el.text).strip()


def _parse_passives(root: ET.Element) -> dict:
    tree_el = root.find("Tree")
    if tree_el is None:
        return {"keystones": [], "notables": [], "total": 0}

    specs = tree_el.findall("Spec")
    if not specs:
        return {"keystones": [], "notables": [], "total": 0}

    # Tree Specs have no id; activeSpec is a 1-based index into the Spec list
    # (a leveling guide can carry many stage trees — default to the first if
    # the index is missing or out of range rather than the wrong tree).
    try:
        active_idx = int(tree_el.get("activeSpec", "1")) - 1
    except ValueError:
        active_idx = 0
    if not 0 <= active_idx < len(specs):
        active_idx = 0
    spec = specs[active_idx]

    nodes_str = spec.get("nodes", "")
    if not nodes_str:
        return {"keystones": [], "notables": [], "total": 0}

    allocated_ids = set(nodes_str.split(","))
    total = len(allocated_ids)

    try:
        from poe_data_mcp.sources.player.passives import _load_tree
        by_id = _load_tree(spec.get("treeVersion", ""))["by_id"]
        keystones, notables = [], []
        for nid in allocated_ids:
            node = by_id.get(nid)
            if not node:
                continue
            ntype = node.get("_type", "")
            name = node.get("name", "").strip()
            if not name:
                continue
            if ntype == "keystone":
                keystones.append(name)
            elif ntype == "notable":
                notables.append(name)
    except Exception as exc:
        # A missing source is not evidence of an empty tree.
        raise ValueError(f"Unable to resolve allocated passives: {exc}") from exc

    return {
        "keystones": sorted(keystones),
        "notables": sorted(notables),
        "total": total,
    }


def parse_pob(code_or_url: str) -> str:
    """Parse a Path of Building export code or share URL.

    Accepts:
    - Raw PoB export code (base64 string from PoB's Export button)
    - pobb.in URL (https://pobb.in/xxxxx or https://pobb.in/u/username/xxxxx)
    - Pastebin URL (https://pastebin.com/xxxxx)

    Returns a build summary including class, level, key stats, skill links,
    equipped items with mods, and allocated keystones and notable passives.

    Args:
        code_or_url: PoB export code, pobb.in share URL, or pastebin URL.
    """
    code = code_or_url.strip()

    if code.startswith("http"):
        try:
            code = _fetch_raw(code)
        except httpx.HTTPStatusError as e:
            return f"Failed to fetch PoB data: HTTP {e.response.status_code} from {e.request.url}"
        except Exception as e:
            return f"Failed to fetch PoB data: {e}"

    try:
        root = _decode_pob(code)
    except Exception as e:
        return str(e)

    build = _parse_build_info(root)
    skills = _parse_skills(root)
    items = _parse_items(root)
    passives = _parse_passives(root)
    notes = _parse_notes(root)
    layouts = _parse_layouts(root)

    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────
    char_class = build.get("class", "Unknown")
    asc = build.get("ascendancy", "")
    level = build.get("level", "?")

    title = f"# {char_class}"
    # PoB sets ascendClassName to the literal "None" for un-ascended characters.
    if asc == "None":
        asc = "Unascended"
    if asc and asc != char_class:
        title += f" ({asc})"
    title += f"  —  Level {level}"
    lines += [title, ""]

    # ── Notes ────────────────────────────────────────────────────
    if notes:
        lines.append("## Notes")
        lines.append(notes)
        lines.append("")

    # ── Progression Stages ───────────────────────────────────────
    if layouts:
        lines.append("## Progression Stages")
        for stage in layouts:
            idx = stage["index"]
            # Pick the most descriptive title across the three components
            title = stage["items_title"] or stage["tree_title"] or stage["skills_title"]
            lines.append(f"**Stage {idx}** — {title}")
            if stage["tree_title"] and stage["tree_nodes"]:
                lines.append(f"  - Tree: {stage['tree_title']} ({stage['tree_nodes']} nodes)")
            if stage["skills_title"]:
                lines.append(f"  - Skills: {stage['skills_title']}")
            if stage["items_title"]:
                lines.append(f"  - Items: {stage['items_title']}")
        lines.append("")

    # ── Build Details ────────────────────────────────────────────
    details = []
    bandit = build.get("bandit", "")
    if bandit:
        details.append(f"**Bandit:** {bandit}")
    major = build.get("pantheon_major", "")
    minor = build.get("pantheon_minor", "")
    if major or minor:
        pantheon = " / ".join(_split_camel(p) for p in [major, minor] if p)
        details.append(f"**Pantheon:** {pantheon}")
    if details:
        lines += details + [""]

    # ── Key Stats ────────────────────────────────────────────────
    stats = build.get("stats", {})
    if stats:
        lines.append("## Key Stats")
        for key, label in _KEY_STATS.items():
            val = stats.get(key)
            if val is None or val == 0:
                continue
            if key in ("TotalDPS", "AverageDamage"):
                lines.append(f"- **{label}:** {val:,.0f}")
            elif key == "HitChance":
                lines.append(f"- **{label}:** {val:.1f}%")
            elif key == "Speed":
                lines.append(f"- **{label}:** {val:.2f}")
            else:
                lines.append(f"- **{label}:** {int(val):,}")
        lines.append("")

    # ── Resistances ──────────────────────────────────────────────
    resist_parts = []
    for key, label in _RESIST_STATS.items():
        val = stats.get(key)
        if val is not None:
            resist_parts.append(f"{label}: {int(val)}%")
    if resist_parts:
        lines.append("## Resistances")
        lines.append("  ".join(resist_parts))
        lines.append("")

    # ── Defense ──────────────────────────────────────────────────
    defense_parts = []
    for key, label in _DEFENSE_STATS.items():
        val = stats.get(key)
        if val is not None and val > 0:
            defense_parts.append(f"{label}: {val:.1f}%")
    if defense_parts:
        lines.append("## Defense")
        lines.append("  ".join(defense_parts))
        lines.append("")

    # ── Charges ──────────────────────────────────────────────────
    charge_parts = []
    for key, label in _CHARGE_STATS.items():
        val = stats.get(key)
        if val is not None and val > 0:
            charge_parts.append(f"{label}: {int(val)}")
    if charge_parts:
        lines.append("## Max Charges")
        lines.append("  ".join(charge_parts))
        lines.append("")

    # ── Skill Links ──────────────────────────────────────────────
    if skills:
        lines.append("## Skill Links (Active Set)")
        for group in skills:
            slot = group["slot"] or group["label"] or "—"
            gem_parts = []
            for g in group["gems"]:
                name = g["name"]
                lvl = g["level"]
                qual = g["quality"]
                tag = f"L{lvl}" if lvl else ""
                if qual and qual != "0":
                    tag += f"/Q{qual}"
                label = f"{name} ({tag})" if tag else name
                if g.get("is_main"):
                    label = f"**{label}**"
                gem_parts.append(label)
            lines.append(f"- **{slot}:** " + " — ".join(gem_parts))
        lines.append("")

    # ── Equipped Items ───────────────────────────────────────────
    if items:
        lines.append("## Equipped Items")
        for item in items:
            slot = item["slot"]
            name = item.get("name", "")
            base = item.get("base", "")
            rarity = item.get("rarity", "")
            implicits = item.get("implicits", [])
            mods = item.get("mods", [])

            # Slot header
            display_name = name
            if base and base != name:
                display_name += f" *({base})*"
            rarity_tag = f" [{rarity}]" if rarity else ""
            lines.append(f"**{slot}:** {display_name}{rarity_tag}")

            # Show implicits then explicit mods
            all_mods = implicits + mods
            for mod in all_mods[:10]:
                lines.append(f"  - {mod}")
            if len(all_mods) > 10:
                lines.append(f"  - *... and {len(all_mods) - 10} more*")
        lines.append("")

    # ── Passives ─────────────────────────────────────────────────
    total = passives.get("total", 0)
    keystones = passives.get("keystones", [])
    notables = passives.get("notables", [])

    if total > 0:
        lines.append(f"## Passive Tree  ({total} nodes)")
        if keystones:
            lines.append(f"**Keystones:** {', '.join(keystones)}")
        if notables:
            lines.append(f"**Notables ({len(notables)}):** {', '.join(notables)}")
        lines.append("")

    return "\n".join(lines)


def _is_support(gem: ET.Element) -> bool:
    """A gem is a support if its skillId/gemId marks it as one."""
    return gem.get("skillId", "").startswith("Support") or "SupportGem" in gem.get("gemId", "")


def _skill_groups_for_set(sset: ET.Element) -> list[dict]:
    """Structured socket/link groups for one <SkillSet> (or <Skills>) element."""
    groups = []
    for skill in sset.findall("Skill"):
        try:
            main_idx = int(skill.get("mainActiveSkill", "1")) - 1
        except ValueError:
            main_idx = 0
        gems = []
        for i, gem in enumerate(skill.findall("Gem")):
            name = gem.get("nameSpec") or gem.get("skillId", "")
            if not name:
                continue
            gems.append({
                "name": name,
                "skillId": gem.get("skillId", ""),
                "level": gem.get("level", ""),
                "quality": gem.get("quality", "0"),
                "support": _is_support(gem),
                "enabled": gem.get("enabled", "true").lower() != "false",
                "is_main": i == main_idx,
            })
        if not gems:
            continue
        groups.append({
            "slot": skill.get("slot") or None,
            "label": _strip_pob_colors(skill.get("label", "")).strip(),
            "enabled": skill.get("enabled", "true").lower() != "false",
            "gem_count": len(gems),
            "gems": gems,
        })
    return groups


def _sockets_line(item_text: str) -> str:
    """Extract the 'Sockets:' colour layout from a PoB item text block (e.g. 'B-B-R G')."""
    for line in item_text.splitlines():
        line = line.strip()
        if line.startswith("Sockets:"):
            return line.removeprefix("Sockets:").strip()
    return ""


def _itemset_slot_sockets(root: ET.Element, stage_idx: int | None) -> dict:
    """Map slot name -> the item's actual socket-colour layout, for the ItemSet that
    matches the given progression-stage index (falls back to the active/first set).
    The item's real colours are stored in the export; this surfaces them per slot."""
    items_el = root.find("Items")
    if items_el is None:
        return {}
    id_text = {it.get("id", ""): (it.text or "") for it in items_el.findall("Item")}
    item_sets = items_el.findall("ItemSet")

    target = None
    if stage_idx is not None:
        target = next((s for s in item_sets if stage_idx in _extract_stage_indices(s.get("title", ""))), None)
    if target is None:
        active = items_el.get("activeItemSet")
        target = next((s for s in item_sets if s.get("id") == active), None)
        if target is None:
            target = item_sets[0] if item_sets else None

    slot_sockets = {}
    if target is not None:
        for slot in target.findall("Slot"):
            iid = slot.get("itemId", "")
            if iid and iid != "0" and iid in id_text:
                layout = _sockets_line(id_text[iid])
                if layout:
                    slot_sockets[slot.get("name", "")] = layout
    return slot_sockets


def parse_pob_skill_groups(code_or_url: str, skill_set: str = "") -> str:
    """Read structured skills and supports from the selected PoB skill set.

    In PoE2, skill support groups are independent from equipment rune sockets.
    The result lists equipment_sockets separately. skill_set selects an exact
    ID or an unambiguous title substring; omission selects the active set.
    """
    code = code_or_url.strip()
    if code.startswith("http"):
        try:
            code = _fetch_raw(code)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code} from {e.request.url}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch PoB data: {e}"})

    try:
        root = _decode_pob(code)
    except Exception as e:
        return json.dumps({"error": str(e)})

    build = _parse_build_info(root)
    skills_el = root.find("Skills")
    if skills_el is None:
        return json.dumps({"error": "No <Skills> section in this build."})

    sets = skills_el.findall("SkillSet")
    active_id = skills_el.get("activeSkillSet", "1")

    stage_idx = None
    if not sets:
        # Older single-set format: <Skill> directly under <Skills>.
        index = [{"id": "1", "title": "(default)", "active": True}]
        showing, groups = "(default)", _skill_groups_for_set(skills_el)
    else:
        index = [{
            "id": s.get("id", ""),
            "title": _clean_title(s.get("title", "")),
            "active": s.get("id") == active_id,
        } for s in sets]

        target = None
        if skill_set:
            q = skill_set.strip().lower()
            exact = [s for s in sets if s.get("id") == skill_set.strip()]
            matches = exact or [s for s in sets if q in _clean_title(s.get("title", "")).lower()]
            if len(matches) != 1:
                raise ValueError(f"Unknown or ambiguous skill set {skill_set!r}; select an exact ID")
            target = matches[0]
        if target is None:
            target = next((s for s in sets if s.get("id") == active_id), sets[0])
        showing, groups = _clean_title(target.get("title", "")), _skill_groups_for_set(target)
        idxs = _extract_stage_indices(target.get("title", ""))
        stage_idx = idxs[0] if idxs else None

    # Attach the item's ACTUAL socket colours (stored in the export) to slot-bound
    # groups, matched to the stage's item set. Unassigned groups have no item, so no
    # item_sockets — derive their required colours from the gems (via get_gem_detail).
    slot_sockets = _itemset_slot_sockets(root, stage_idx)
    if root.tag != "PathOfBuilding2":
        for g in groups:
            if g["slot"] and g["slot"] in slot_sockets:
                g["item_sockets"] = slot_sockets[g["slot"]]

    return json.dumps({
        "game": "poe2" if root.tag == "PathOfBuilding2" else "poe1",
        "equipment_sockets": slot_sockets,
        "class": build.get("class"),
        "ascendancy": build.get("ascendancy"),
        "level": build.get("level"),
        "active_skill_set": active_id,
        "skill_sets": index,
        "showing": showing,
        "groups": groups,
    }, indent=2)
