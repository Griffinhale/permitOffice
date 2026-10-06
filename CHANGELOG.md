# Changelog

All notable changes to Permit Office are recorded here. This project uses
[semantic versioning](https://semver.org/); pre-1.0 releases may change
behavior between minor versions.

## Unreleased

Work since v0.95.0. The rules tests and dashboard previews pass. The ArcPy
review items below were checked live in ArcGIS Pro; the loop and pane changes
are waiting on their live check.

### Added
- Season goals: New Game offers three goals drawn from the seed and the player
  files one; week 12 decides the season by it. Goals met along the way count as
  achievements.
- An audit ladder: checkpoint audits at weeks 4, 8 and 12 move council patience
  (warning, sanctioned with one AP less, dismissed).
- Weekly initiatives: Earmark a district type (buyouts and the case draw lean
  its way), Civic action, and Market push on map-selected districts.
- Hotkeys for every action shown on the desk, and a probe log file
  (`PERMIT_OFFICE_LOG_FILE`).
- `tools/desk_preview.py`, which renders the dashboard without ArcGIS Pro.

### Changed
- The dashboard is a narrow, resizable pane (480 x 820, down to 400 x 560) with
  Desk, Reports and City tabs and an End Week footer that forecasts the close.
- Two new cases a week instead of four, on top of follow-up work.
- Weeks end on End Week. The 150-second office clock is gone; ignored cases
  build a full week of pressure at the close.
- Stakeholder heat fades by one a week when nothing feeds it, and heat
  follow-ups respect their cooldown.
- Layers load from shipped `.lyrx` styles; map redraws requery layers in place,
  keep selections the next case replaces, and draw a district underlay, so a
  week close drops each feature layer once with no white flash.
- Lines, points and zones each draw over an unlabeled copy that shows the old
  picture while the live layer redraws, so a week close no longer leaves the
  map near-empty.
- `geometry.py` is split by job into `proposals.py`, `city_features.py`,
  `map_layers.py`, and `symbology.py`; `geometry.py` re-exports them for one
  release.

### Fixed
- Approved cases stayed in the inbox and could be approved a second time,
  paying twice, and the queue auto-close never fired after an approval.
- Deferring an enforcement, maintenance or incident case showed 0 AP while the
  rules charged 1.
- The dashboard window no longer frees Tk on another thread (a Pro crash).
- After the season ended, clicking Desk or a week report jumped straight back
  to the final audit.
- A maintenance order for a line or zone feature never placed its map point,
  and approving it failed until the player picked one district by hand.

### Removed
- Office Standing, threat-track and other standalone meters; their causes now
  appear as report findings.
- The unused demo docket sequence.
- Redraw experiment and benchmark controls from the public toolbox. The display
  ring is the production redraw path (ADR-16); retired probes are recorded in
  `docs/failed-experiments.md`.
- The one-ply decision-future cache (`futures.py`, `cache_keys.py`, `dirty.py`).

## v0.95.0 - 2026-06-10

Performance/refactor spike release. The gameplay rules and save format remain
GDB-authoritative, but the redraw and decision-preview architecture has been
reshaped around explicit cache primitives and reusable display layers.

### Added
- Pure cache primitives under `toolbox/permit_office/`: stable cache/state
  hashing, generation tokens, dirty district/layer bitsets, one-ply decision
  future nodes, and materialized-view cache structures.
- Hydrated redraw planning in the ArcGIS adapter. Actual `DecisionResult` data is
  combined with cache hints to pick precise district/support layer dirty scopes.
- A three-slot district display ring (`Permit Office Predrawn 0/1/2`) and support
  rings for points, lines, and zones. The visible slot stays on screen while a
  prepare slot is refreshed or rehydrated.
- More detailed performance instrumentation for cache lookup, resolve/write,
  redraw hydration, district-ring phases, support-ring refresh/rehydrate, and
  reload/materialized reuse.

### Changed
- `district-ring` is now the promoted redraw path. The older
  `predrawn-rehydrate` path remains available as a diagnostic fallback.
- Dashboard command flow now consults the one-ply future cache before approval
  when useful, but authoritative decisions still resolve against current state
  and write the GDB once before map redraw work.
- Startup/new-game handling creates and opens a map when the project has none,
  then uses the active map spatial reference or Web Mercator fallback.
- Reload/materialized views are reused only when dependency hashes match.

### Fixed
- District and support redraws avoid one layer stack per speculative branch; the
  map now uses a small reusable display ring.
- Support feature layers can refresh an existing visible ring slot before falling
  back to rehydrate/remove-add work.
- Speculative future-cache branch failures are contained as invalid cache nodes;
  they no longer abort the authoritative command path.

### Validation
- Merged-main verification for this release: `313 passed, 1 skipped` with
  `pytest -q`.

## v0.9.0 — 2026-06-04

First tagged public release. Permit Office is playable end to end as a 12-week
civic season inside ArcGIS Pro.

### Highlights
- Pure-Python rules engine (districts, dockets, inspections, decisions, turns,
  scorecards, audits) that runs and tests without ArcGIS.
- ArcGIS adapter: geodatabase-backed save state, generated district geometry,
  proposal/activation, map repaint, and a Tkinter desk dashboard.
- Content systems: weighted docket templates (shaped by district type **and**
  citizen-culture mix), stakeholder heat, population mix and dissatisfaction,
  civic incidents, service gaps, housing, hazards, projects, maintenance,
  recurring economy, and deepened district identity / multi-bidder buyouts.
- Deterministic seeded generation; seed `2026` is a locked 12-week balance route.

### Added
- **District identity deepening (#6):** human-readable district names everywhere
  in player-facing text (audit findings, dashboard, reports, previews; `cell_id`
  stays the stable key); district type + citizen-culture distribution shape which
  proposals appear; multi-bidder buyout negotiation with per-bidder willingness
  rolls; a successful buyout shifts the district's type, culture, and resources
  with overextension risk. See ADR-14.
- **Empty-map fresh start:** launching against a workspace whose `.gdb` holds a
  save but whose map has no Permit Office layers now opens a New Game prompt
  instead of silently resuming the old board (the save is left untouched).
- **ArcGIS Pro smoke checklist** (`docs/arcgis-pro-smoke-checklist.md`) for live
  beta validation.

### Changed
- The optional **Game Workspace** is honored verbatim — even when its `.gdb` is
  empty — and the project/scratch default is used only when no workspace is
  given; each resolution logs its source.
- City-health vocabulary unified on the four vitals (Activity / Friction / Trust
  / Exposure) with Heat + Pressure signals (#5, ADR-10); revenue/upkeep/net shown
  on inspected revenue cases, and action lanes read per case family (#1, #2).

### Fixed
- District layers render their evolving state across an End Week (live-verified
  2026-06-04). `arcpy.RefreshLayer` does not reload GDB attribute writes, so
  `rebuild_output_layers()` removes + re-adds the district family every rebuild
  (feature layers stay refresh-only). See ADR-4.
- Resuming a save no longer crashes when a selected case targets several
  aggrieved districts: grievance bands now aggregate by max (staying within the
  label range) instead of summing — was an `IndexError` in
  `target_population_hint`.
- A manual **End Week** control is always available from the desk utility menu
  (previously it only appeared once the queue was empty, trapping players out of
  AP with unaffordable cases queued).
- A blank/Unknown active-map coordinate system no longer breaks New Game: a map
  SR without a real WKID (factoryCode 0) now falls back to Web Mercator (3857)
  instead of seeding feature classes with an Unknown SR and crashing the first
  linear `arcpy.analysis.Buffer` with a spatial-reference `RuntimeError`
  (reproduced on a 3.3.2 project whose active map had no coordinate system).

### Performance
- Controller-side **audit-grade cache:** the scorecard (and its district
  `deepcopy`) recomputes only on game-data writes, not on selection-only redraws.
- Desk view caches per-model lookup maps and memoizes text-fit across redraws.
- Fewer geodatabase cursor opens per action (batch proposal-visibility scan,
  read-once reload, memoized district geometry; `selected_cell_ids` reads the
  `cell_id` column directly, falling back to a field scan only on error).
- A "skip the district re-add when no rendered field changed" experiment was
  tried and **reverted**: `RefreshLayer` reloads neither symbology nor
  attributes, and attribute tables change nearly every action, so the district
  family must re-add unconditionally (ADR-4).

### Known gaps
- Developed and tested on **ArcGIS Pro 3.6**; **3.3+** is the practical floor
  (the only version-gated arcpy call, `arcpy.RefreshLayer`, arrived at 3.3 and is
  guarded). Spatial-reference handling is not version-gated.
- Live ArcGIS Pro smoke-test results on a target machine are not yet recorded
  (see `docs/arcgis-pro-smoke-checklist.md`; open issues #9, #11).
- 12-week balance tuning toward a reliable PASS is ongoing.
- Map symbology legibility (#9) and the cursor/IO live-verification items (#11)
  still need a live session.
