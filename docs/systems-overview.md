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
- `turns.py` - turn advancement, week-close pressure, scorecards, audit results.
- `systems.py` - projects, feature lifecycle, economy, networks, hazards, housing.
- `helpers.py` - district math, population, stakeholder heat, effect math.
- `expiration.py`, `type_pressure.py`, `buyouts.py`, `city_detail.py` - unattended
  item policy, hidden district-type ledger, buyout transitions, civic texture.

**ArcGIS adapter - `toolbox/permit_office_arcgis/`** (ArcPy + Tkinter)
- `schema.py` - geodatabase/table/field declarations; idempotent schema creation.
- `store.py` - read/write game rows to/from pure-rule dataclasses (the only
  cursor I/O for game state).
- `proposals.py` - map selection, proposed geometry, spillover buffers, and
  feature activation.
- `city_features.py` - baseline city detail seeded onto a new board.
- `map_layers.py` - map layer add/remove, refresh, and the district/support
  display rings.
- `geometry.py` - re-exports the three modules above for one release.
- `toolbox/layers/*.lyrx` - the six layer styles exported from Pro; every layer
  and ring slot is added from one of them.
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
| **Services** | local service capacity (district only) | service/network/utility features | reduces service gaps and dissatisfaction |

The city holds the first four as running totals in `PermitGameState`, moved by
the averaged deltas of each decision, flat deny/defer deltas, feature failures
and new incidents. They are not recomputed from districts. Services exists only
per district.

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

Written from the code on 2026-10-03 (rules in `toolbox/permit_office/`, the
controller in `dashboard.py`). Each turn is one office week; the persisted field
is still `turn`.

**Season.** 12 weeks. A new game starts at week 1 with 2 AP, $60, activity 50,
friction 20, trust 35, exposure 25. AP refills to 2 at every week close and
does not carry over.

**Audit ladder.** The week 4, 8 and 12 closes run a checkpoint audit
(`AUDIT_WEEKS`). A FAIL moves the council's patience one rung up the ladder
(attended, warning, sanctioned, dismissed), a PASS moves it one rung down, and
a CONDITIONAL holds it. While sanctioned, AP refills to one less. Reaching
dismissed ends the season at once (`outcome = "dismissed"`). When week 12
closes the full week-close simulation runs, the game is marked `complete`, and
the season is won if the filed mandate is met and the office was not
dismissed (`outcome = "won"` or `"lost"`). The week report names the verdict
and the round's achievements. No new docket is generated after the season
ends; End Week then only repeats the final grade.

**Mandate.** New Game offers three season goals drawn from the seed
(`offer_mandates`); the player picks one (`choose_mandate`). The catalog has
six: grow a district type by three districts, quiet streets (no group at
grievance 4, no open incident), public confidence (trust 55+), close the gaps
(critical service gaps cut to a third), balanced books ($80+, non-negative net,
no failed features) and an even-handed city (no type over 8 districts, every
starting type keeps one). Every mandate met at the end also counts as an
achievement for the round (`season_achievements`). The thresholds are first
guesses.

**No clock.** A week lasts until End Week, or until the queue auto-close two
seconds after the last open case is filed. There is no real-time deadline.
At week close, `advance_daily_pressure` builds a full week (four days) of
district pressure: +1 a day, capped at 4, for each open case that targets the
district, for an incident or a high grievance, service gap, hazard or
displacement, and for each feature on it that is due for maintenance or
degraded. Pressure feeds the unresolved-case step below and then resets.

**Docket.** Two new cases a week (`DRAWN_CASES_PER_WEEK`), plus follow-up work, up to
four in all, filled in this order: project steps that
are due, carried cases, pending momentum follow-ups, one maintenance order, one
civic incident, one stakeholder-heat follow-up (at most once per stakeholder
cooldown, recorded at week close), then two cases from a weighted draw over
the twelve ordinary templates. The draw is weighted by district type, dominant
citizen groups, population and district stats, so the same seed gives the same
docket. The docket table is replaced every week; only `carried` cases come
back. A case awaits a decision while it is `open`, `inspected` or `carried`
(`OPEN_DOCKET_STATUSES`); any other status is filed and the rules refuse a
second decision on it.

**Actions.** Every template costs 1 AP to inspect, issue, or issue with
conditions. Issuing also costs the template's money ($3-24), and conditions add
the mitigation cost ($3-10).

- *Select* runs `select_case_context`: it makes sure the case has a proposed
  exhibit and selects its target districts and features on the map. Toggle
  Exhibit hides or shows the proposal; Retarget Map rebuilds it from the
  current map selection. Neither costs AP.
- *Inspect* (1 AP) reveals the risk band and evidence, and opens violations
  with deadlines on medium or high risk. An inspected high-risk case is more
  likely to fail on approval than an uninspected one.
- *Issue / Add Conditions* needs at least one target and the AP and money. It
  applies target and spillover deltas, adjusted for land-use fit, rolls failure
  (conditions lower the chance and soften the deltas), then applies population,
  housing, hazard, project and buyout effects. It activates the exhibit on the
  map.
- *Deny* an ordinary permit costs 0 AP and $0: friction +1, activity -1,
  stakeholder heat, project delay. *Defer* on an enforcement, maintenance or
  incident case costs 1 AP.

Points need exactly one target district, lines two, polygons one or more.
Maintenance cases skip spillover.

**Week close** (End Week, or the queue auto-close two seconds after the last
open case is filed):

1. Open cases build a full week of district pressure.
2. Overdue violations add heat and priority.
3. Each case still open or inspected adds heat from its targets' daily
   pressure and a grievance reaction, then follows its template's expiration
   policy: mandatory follow-ups are carried, missed windows expire, momentum
   cases expire and raise buyout pressure, some queue an enforcement or
   incident follow-up.
4. Feature lifecycle: expiry, decay, maintenance due, degraded, failed.
5. Economy: district and feature revenue minus upkeep. Money can go negative.
6. Networks and hazards (when there are features or hazards), housing,
   population drift, grievance floors, new incidents.
7. Contested buyouts resolve, then a new buyout round picks targets.
8. Stakeholders who gained no heat this week lose 1 heat.
9. Turn +1, AP refill, audit grade, new docket, map redraw, week report.

**Audit grade.** Score = activity + trust - friction - exposure + money/3 +
last net (clamped to ±10) minus finding penalties (negative or low money, high
friction or exposure, service gaps, incidents, hazards, displacement, failed or
worn features, overdue violations). PASS needs 70+, money of zero or more and no
critical finding; CONDITIONAL needs 45+ and at most one critical; anything else
is FAIL. A new game grades CONDITIONAL (score 60). Only the checkpoint
audits move the ladder; the dashboard recomputes the grade after each write so
the player can see where the next checkpoint stands.

**Command flow.** Every action runs on the Tk thread: insert a command row,
read game rows (`store.py`), resolve through `rules`, write the results to the
geodatabase once, plan the redraw from the actual `DecisionResult`
(`redraw_plan.py`), redraw only the changed layers (`map_redraw.py`,
`map_layers.py`), file a report, reload the desk (see `decisions.md`).

**Launch, resume, new game.** The `.pyt` resolves the workspace, runs
`ensure_schema`, adds output layers and opens the controller. A saved board is
resumed (its docket regenerated if missing); otherwise the dashboard opens on a
start screen. New Game clears the game rows, builds the board, seeds city
features, writes the state, generates the docket and re-adds the layers.

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
- No seed is locked as a 12-week balance route yet; balance tuning is open.
- Live ArcGIS run: launch the GP tool, New Game with seed `2034` (its week 1
  docket is a line case then a point case, pinned by
  `test_probe_seed_2034_opens_with_a_line_and_a_point_case`), then walk
  point/line/polygon approvals, inspect, deny, toggle exhibit, retarget, and End
  Week through week 12 — confirming exhibits draw, metrics/`display_state` update,
  reports file, and buyout/identity stays legible from map + text.
- Refresh diagnostics: enable **Log Refresh Timings** in the toolbox or set
  `PERMIT_OFFICE_PERF=1`; output is a nested `[PERF] turn=... total=...` tree.
- Probe runs: set `PERMIT_OFFICE_LOG_FILE=<path>` before launching Pro to append
  every tagged message (`PERF`, `REDRAW`, `SELECT`, warnings) to that file with
  a UTC timestamp. Pro can offload GP messages out of reach; the file stays.
