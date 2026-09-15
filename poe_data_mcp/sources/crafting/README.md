# PoE2 crafting, modifiers and environment adapters

Verified against live sources on **2026-09-15**. `POE_GAME` defaults to `poe2`.
An explicitly unsupported value raises `ValueError`; it cannot select PoE1.
The seven existing Craft of Exile tool names, `search_mods`, `env_search` and
`env_detail` retain their signatures. The PoE1 implementations live in `poe1.py`
modules and are selected only by `POE_GAME=poe1`.

## Craft of Exile source and schema

The [original PoE2 page](https://www.craftofexile.com/?game=poe2) directs users to
[the beta site](https://beta.craftofexile.com/?game=poe2) for current data.
The adapter discovers the **matching PoE2 patch** and these script URLs from that
page, rather than replacing strings in PoE1 URLs:

- `json/poe2/<patch>/data.json?v=<version>`: `coedata={...};`
- `json/poe2/<patch>/localization/english.json?v=<version>`: `coelang=[...];`

These are public assets used by the site, not a documented API. The site's
[developer page](https://beta.craftofexile.com/developpers) states that it provides
no API endpoints. The adapter performs anonymous GETs only. It parses JSON
assignments and never executes downloaded JavaScript.

The verified source patch was **4.5.5.1.5**, labeled **0.5.5.1.5 / Forbidden Rites**
by the site. A cold adapter refresh obtained HTTP 200 for the page and both files.
The snapshot contains 104 classes, 2,488 item entries, 3,183 modifier entries,
95 essences and 6,307 localized strings. The source patch identifier is reported
verbatim; it is not a claim that all source definitions are currently obtainable.

Verified relationships:

- `classes/items/mods/modgroups/essences.entries` contain records keyed by `id`.
- `label` and `stats[].label` index the zero-based localization array.
- `items[].class` joins **CoE class IDs**; `classes[].class` is a distinct game enum.
- `classmods[CoE class ID][mod ID]` supplies class-specific source weights.
- `essences.classmods[game class enum][mod ID]` and
  `essences.basemods[CoE class ID][mod ID]` jointly map to essence IDs.
- Multiple stats can share one label. The formatter deduplicates labels and
  inserts individual stat ranges into successive `#` placeholders, matching the
  site's `ModsManager.buildModName` / `applyValuesToMod` behavior.
- Tier ranking follows the site's class/group ordering by minimum level, then
  power, descending. Querying one tier still returns its whole eligible group.
  Zero-weight entries are omitted. These are source tier rankings.
- `items.maps[item ID].tier` is the Waystone tier; tablet effects come from item
  `implicits` joined to mods and localization.

The site's `packages/files/package_poe2.js?v=1789404199` was inspected to verify
these joins and the method-specific pool labels. Weights are labeled estimates;
Desecrated, Genesis Tree and Otherworldly pools have **unknown weights**, so their
numeric source markers are not presented as probabilities. No crafting simulator,
cost estimator or probability calculator is added by this port.

## Cache behavior

PoE2 uses `<cache root>/poe2/bundle.json`, isolated from the old PoE1 cache.
`POE_DATA_MCP_CACHE_DIR` overrides the root; otherwise platformdirs supplies the
user cache root plus `craftofexile`. Data and English localization are validated
and published as one atomic snapshot. The adapter verifies provenance, matching
patch URLs, labels and the required relationships before replacing a snapshot.

The default update interval is 12 hours, with at least 1.5 seconds between request
starts. Calls are serialized within a process. HTTP 429/503 `Retry-After` is
honored, including forced refreshes; there is no immediate retry loop. Refresh
errors keep the previous validated snapshot and are shown in tool output.
Without a valid snapshot a crafting lookup raises a source-unavailable error.
Cold downloads require network access; subsequent valid cached reads work offline.
Caches contain data fetched by the user and are not redistributed with this code.

## Local PoB2 and environment

`search_mods` uses the confirmed shared `sources.poe2_data.get_data()` interface:
`.bases()`, `.load(relative, seed=False)`, `.root`, and `.metadata(relative)`.
Set `POB_INSTALL_DIR` to the installed **Path of Building Community (PoE2)** folder.
It separates `ModItem`, `ModFlask`, `ModCharm`, `ModJewel`, and `ModIncursionLimb`
pools, and labels `ModCorrupted` separately. Ordered first-matching spawn tags are
applied independently to each actual base, so an earlier zero weight cannot be
overridden by a later generic tag. Quarterstaves use the native Warstaff subtype.
PoB2 eligibility flags are not measured spawn probabilities. These lookups show
local source definitions and file modification times, not a confirmed patch age.

Environment categories:

| Category | Source | Meaning |
|---|---|---|
| `maps` | Local `Data/WorldAreas.lua`, seeded load, `isMap=true` | Map area definitions, bosses, monsters, tags |
| `areas` | Same table, positive level, not maps/hideouts | Campaign and other playable area definitions |
| `tablets` | Craft of Exile item classes, items and implicit mods | Eight actual map-content tablet types |
| `waystones` | Craft of Exile items and `items.maps` | Waystone items and their source tiers |

Map definition levels are labeled separately from actual Waystone-determined map
levels. No PoE1 fixed atlas connections are invented. Duplicate area names list
full names and native IDs for disambiguation. Unavailable or empty source
categories produce explicit coverage-gap messages, including during cross-category
searches; they are not reported as successfully loaded empty datasets.

## Documented gaps

- **Fossils:** no fossil-affinity system exists in the verified CoE PoE2 dataset.
  `get_fossil_info` reports unsupported. No fossil-to-omen/essence mapping is invented.
- **Scarabs:** unsupported in PoE2. Tablets serve the broad map-content role but
  have different mechanics; `scarabs` is not silently aliased to `tablets`.
- **Perfect Essence of the Infinite:** the verified CoE snapshot includes the
  item but has no modifier mapping. Its lookup explicitly reports the missing
  outcome mapping; it does not claim the item has no effect.
- `search_mods` covers the listed native normal/corrupted pools. It does not merge
  `ModItemExclusive`, which contains special and legacy definitions. Use the CoE
  tools for its labeled special pools; neither source is silently substituted for
  the other. Missing source data does not prove a modifier is absent in the game.
- Tablet default effects are source definitions, not the state of a player's used
  or crafted tablet. Source data does not describe the player's generated atlas.

## Verification

`../.venv/bin/python -m pytest -q tests/test_poe2_crafting*.py tests/test_poe2_env*.py`

The 36 offline tests cover schema joins, ranges, real spawn-tag ordering, game
selection, cache rollback, source rate limits, tablet/Waystone relationships,
ambiguous area names, explicit coverage gaps, and the retained PoE1 mod endpoint.
Fixtures are hand-authored schema examples, not redistributed data snapshots.

Manual source checks used the live downloaded patch and the installed PoB2 tables:
Abrasion on crossbows (all four essence tiers), crossbow crafting tiers, Makeshift
Crossbow bases, charge modifiers on charms, quarterstaff attack speed, the Arbiter
map search, Burning Monolith details, all eight tablets and Waystone Tier 15.
All 3,183 CoE modifier records were rendered without parse errors.
