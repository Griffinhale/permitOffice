# Permit Office — Systems Overview

The canonical "how the whole thing works" reference. Read this first; the other
docs drill into specific areas (`docket-items.md`, `arcpy-usage.md`,
`writing-and-tone.md`, `decisions.md`).

## Concept

A turn-based city-planning sim played *through* ArcGIS Pro, framed as a dryly
absurd municipal permit office. The player is an audit-facing clerk, not a mayor:
you shape a strange city by filing, approving, inspecting, denying, and
explaining permits. Feature classes are the save file, map selections are the
input device, and ArcPy geometry/selection operations are part of the rules.

Design north star: a simple sim loop with **surprising but legible** city
consequences — not a GIS demo and not a deduction puzzle. Information is partial
(visible district traits + uncertain side effects); fun comes from event variety
and watching the city react.

## Two-Layer Architecture

The codebase is split so the game is testable without ArcGIS Pro.

**Pure rules - `toolbox/permit_office/`** (ArcPy-free, importable in plain Python)
- `models.py` - dataclasses (`CityState`, `DistrictProfile`, `DocketItem`,
  `FeatureInstance`, `ProjectRecord`, `DecisionResult`) and shared constants.
- `catalogs/` - typed catalogs: `templates.py` (docket templates, scenarios,
  governance) and `features.py` (feature archetypes / operating rules).
- `profiles.py` - district generation, docket generation, inspection cases.
- `decisions.py` - inspect / approve / mitigate / deny resolution.
- `turns.py` - turn advancement, scorecards, audit results, deadlines.
- `systems.py` - projects, feature lifecycle, economy, networks, hazards, housing.
- `helpers.py` - district math, population, stakeholder heat, effect math.
- `expiration.py`, `type_pressure.py`, `buyouts.py`, `city_detail.py` - unattended
  item policy, hidden district-type ledger, buyout transitions, civic texture.

**ArcGIS adapter - `toolbox/permit_office_arcgis/`** (ArcPy + Tkinter)
- `schema.py` - geodatabase/table/field declarations; idempotent schema creation.
- `store.py` - read/write game rows to/from pure-rule dataclasses (the only
  cursor I/O for game state).
- `geometry.py` - map selection, proposed geometry, spillover buffers, feature
  activation, map refresh, symbology, and district/support display rings.
- `redraw_plan.py` - ArcPy-free redraw planning: which layers a command
  changes and whether they are refreshed or re-added.
- `map_redraw.py` - `rebuild_output_layers`, which carries out a redraw plan on
  the map.
- `layer_ring.py` - reusable ArcGIS layer-ring helpers.
- `dashboard.py` - the Tkinter `DashboardController`, per-action command flow,
  and authoritative resolve/write.
- `desk_view.py` / `desk_model.py` - canvas rendering and its pure data model.
- `symbology_config.py`, `messages.py`, `rules_loader.py`, `_perf.py` - support.

**Entry point — `toolbox/arcpy_permit_office.pyt`**: the GP tool. Stays thin —
parameter definitions, schema setup, and launching the dashboard only.

`toolbox/arcpy_permit_office_rules.py` is a compatibility facade re-exporting the
pure rules so tests and the toolbox import the same `rules` module.

## Persisted State (the save file *is* the geodatabase)

| Feature class / table | Holds | Dataclass |
| --- | --- | --- |
| `PermitDistricts` (polygon) | 5×5 district board: metrics, population mix, hazards, housing, identity/buyout, display state | `DistrictProfile` |
| `PermitPoints` / `PermitLines` / `PermitZones` | proposed/active/denied/failed/incident/maintenance features, split by geometry type | `FeatureInstance` |
| `PermitGameState` | turn, AP, money, audit stage, city metrics, stakeholder heat, last report | `CityState` |
| `PermitDocket` | current week's docket rows | `DocketItem` |
| `PermitProjects` | multi-turn project chains | `ProjectRecord` |
| `PermitUICommand` | recoverable command lifecycle rows | — |
| `PermitActionLog` | player-facing audit trail | — |

## City-Health Stat Model

Public audit backbone (0–100 scale, mid-band ≈ 45):

| Stat | Meaning | Raised by | Lowered / risk |
| --- | --- | --- | --- |
| **Activity** | local economic activity, growth pressure | business/transit/development | worsens displacement when affordability/vacancy low |
| **Friction** | visible civic friction | incidents, unresolved dockets, failed approvals | raises failure chance, population loss, audit findings |
| **Trust** | civic trust, cohesion | parks, education, public art, reserves | lightly dampens grievance drift |
| **Exposure** | safety/infra/environmental exposure | hazards, unsafe work, degraded features | raises failure chance, hazard pressure, incidents |
| **Services** | local service capacity | service/network/utility features | reduces service gaps and dissatisfaction |

**Dissatisfaction** is a group-specific grievance band (0–4), local until band 4
opens a civic incident (which adds citywide friction *once*). Display-state
priority for the map: `incident` > `grievance` > `service_gap` > `hazard` >
`housing_pressure` > `economic_growth` > `stable`.

Internal-only systems (affect the public stats but aren't part of the core
model): population mix, grievance bands, hazards, housing pressure, district
identity, buyout pressure, maintenance condition, stakeholder heat, inspection
risk bands. See `docs/decisions.md` for why the model was kept this small.

### Population & districts

District **archetype** (residential, mercantile, industrial, civic, academic,
natural) is the static land-use-fit layer. Population composition is dynamic:
`population`, `population_mix_json` (group bands 0–3), `dissatisfaction_json`
(0–4), `incident_state`, `incident_group`, `public_profile`. Turn advance applies
small deterministic drift: high-activity/low-exposure/low-friction districts grow;
high-exposure/high-friction/incident districts shrink; low services adds grievance.

### District identity & buyouts

District-type *mix* influences docket generation. Low-activity districts can
enter deterministic contested buyout transitions from stronger neighbors
(refuse → contested → stabilize-or-convert). On conversion a district is
relabeled with a type-flavored name and shifts `district_type` fill. Office
attention can stabilize a contested district before conversion. The raw ledger
stays hidden; legibility comes from names, colors, the news ticker, and reports.

## The Turn Loop

Each "turn" is one office week (the persisted field is still `turn`). Target
season is **12 weeks**, **2 AP/week**, ordinary permit denials cost 0 AP, week 6
files the mid-season audit, week 12 files the final audit.

The shared command flow for every action (`dashboard.py`): insert a command row ->
read game rows (`store.py`) -> resolve via `rules` against current state -> write
authoritative results to the GDB once -> build a redraw plan from the actual
`DecisionResult` (`redraw_plan.py`) -> refresh/rehydrate affected display-ring
layers (`map_redraw.py`, `geometry.py`) -> file a report -> reload the desk. All synchronous, on the Tk thread (see
`decisions.md`).

**Launch / resume / new game.** The `.pyt` resolves the workspace, runs
`ensure_schema`, adds output layers, and opens the controller. Saved districts+
state rows are resumed (docket regenerated if missing); otherwise the dashboard
opens on a start screen. New Game runs `clear_game_rows`, `create_district_board`,
`seed_city_features`, `write_state`, `generate_docket_rows`, then re-adds layers.

**Select.** Selecting a docket row calls `select_case_context` — ensures a
proposal exists, selects target districts, selects the proposal/subject feature.
`Toggle Exhibit` deletes/recreates only the selected proposal; `Retarget Map`
replaces the proposal from the current map selection.

**Inspect.** Reads state/districts/features; `resolve_decision(..., "inspect")`
spends AP and enriches the `DocketItem` with risk band + evidence. No geometry
changes.

**Issue / Add Conditions (approve).** Ensures a proposed exhibit, computes
spillover via an in-memory geometry buffer (except maintenance), reads state/districts/features/
projects, resolves effects (targets, spillover, mitigation, failure risk,
population, incidents, projects), writes everything, activates the proposed
exhibit into its support layer, and does a targeted layer rebuild for
`PermitDistricts` + the changed support layer.

**Deny.** Same resolver path; records disposition, friction, heat, and project
delay without approval benefits. Proposed rows are marked denied/deferred.

**End Week.** Resolves unresolved items by template expiration policy; advances
feature lifecycle, economy, networks, hazards, housing, population; resolves
buyout transitions; increments the turn (week 6/12 audits); regenerates the
docket via `generate_docket_rows`; rebuilds all output layers.

Edge cases: points need exactly 1 target district, lines exactly 2, polygons ≥1.
Maintenance items reference an existing active feature and skip spillover. Failed
approvals can create failed-feature state + audit risk. Unresolved items create
future pressure rather than vanishing.

## Status

A playable prototype: generated districts, seeded city detail, weighted docket
templates, inspections, approvals/mitigation, no-AP denials, incidents,
maintenance follow-ups, recurring economy, projects, audits, symbology, district
identity/buyout pressure, start/help flow, exhibit controls, and an inline final
audit receipt.

Verified live during the v0.95 spike (ArcGIS Pro): district layers render on
launch and **repaint their evolving state across decisions and End Week** through
the promoted district-ring redraw path. Support feature rings now share the same
display-ring strategy, with cheap visible-slot refresh before rehydrate fallback.
A manual **End Week** control is available from the desk utility menu regardless
of AP.

Outstanding evidence (not code): a fuller recorded **live ArcGIS Pro smoke test**
on the target machine - support-ring repaint across points/lines/zones,
cold-start resume, legacy field migration on an existing `.gdb`, and 12-week
balance tuning toward a fair PASS.

## Validation

- Pure rules + adapter shims: `python3 -m pytest -q` (install
  `requirements-dev.txt`, or `uv run pytest -q`, if `pytest` is missing).
- Seed `2026` is locked as the 12-week balance route via pure-Python regression.
- Live ArcGIS run: launch the GP tool, New Game with seed `2026`, then walk
  point/line/polygon approvals, inspect, deny, toggle exhibit, retarget, and End
  Week through week 12 — confirming exhibits draw, metrics/`display_state` update,
  reports file, and buyout/identity stays legible from map + text.
- Refresh diagnostics: enable **Log Refresh Timings** in the toolbox or set
  `PERMIT_OFFICE_PERF=1`; output is a nested `[PERF] turn=... total=...` tree.
- Probe runs: set `PERMIT_OFFICE_HOLD_DAY=<0-4>` before launching Pro to hold
  the office clock on that day (0 = Monday). The clock runs until mid-way
  through that day, then stops; the banner shows `HELD`. No later day ticks or
  Friday deadline fire. End Week still advances by hand.
