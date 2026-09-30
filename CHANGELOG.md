# Changelog

All notable changes to Permit Office are recorded here. This project uses
[semantic versioning](https://semver.org/); pre-1.0 releases may change
behavior between minor versions.

## Unreleased

Work since v0.95.0. The rules tests pass; none of it has had the live ArcGIS Pro
smoke test yet.

### Added
- A public pressure model: a small set of player-facing city meters derived from
  the hidden internals.
- Evidence widgets and live trend symbology on the dashboard.

### Changed
- Redesigned the dashboard desk: layout, action cards, map key, report and help
  reference.
- Startup is more resilient: schema versioning and repair on open, and ArcPy-free
  precompute on a worker thread from copied rows, with a timeout that falls back
  to running it on the main thread.

### Removed
- Redraw experiment and benchmark controls from the public toolbox. The display
  ring is the production redraw path (ADR-16); retired probes are recorded in
  `docs/failed-experiments.md`.

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
