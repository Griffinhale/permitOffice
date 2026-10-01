# ArcGIS Pro smoke checklist (beta)

Run this in a live ArcGIS Pro session before tagging a beta. It validates the
ArcGIS-side behavior the offline pytest suite cannot (real `.gdb` writes, layer
repaint, symbology, resume). Each item lists the action and the pass condition.
Capture a screenshot for the starred (*) items.

Toolbox: `toolbox/arcpy_permit_office.pyt` -> "Permit Office Prototype".
Tip: keep the Geoprocessing message log open; several checks read its tagged
lines (`[WORKSPACE]`, `[REBUILD]`, `[DASH]`, `[SYM]`).

## 0. Pre-flight
- [ ] ArcGIS Pro module cache is fresh: the `.pyt` reload loop at the top of
      `arcpy_permit_office.pyt` lists every `permit_office_arcgis/*` module in use
      (no new modules were added this round, so no change is expected).
- [ ] `uv run --with pytest pytest -q` is green on the dev box (offline baseline).

## 1. Workspace resolution (respects the optional param)
- [ ] Run with **Game Workspace empty**. Log shows
      `[WORKSPACE] no workspace given; using project default: ...\data\permit_office.gdb`
      and the existing save resumes (or a start screen appears if none).
- [ ] Run with **Game Workspace = a fresh/empty folder or .gdb**. Log shows
      `[WORKSPACE] using provided game workspace: <that path>` and a NEW game is
      offered/started there. It must NOT load the project-default game.
- [ ] Re-run pointing at the same provided workspace: it resumes that game (not
      the default).

## 2. Cold-start resume *
- [ ] Close Pro entirely, reopen, run the tool against an existing save.
- [ ] The dashboard resumes at the correct week (e.g. `11/12`), the docket is
      populated, and the map shows the full board (districts + features).
- [ ] **Regression (grievance crash):** resume a save whose selected case targets
      two or more districts that share an aggrieved citizen group. The desk opens
      without an `IndexError` in `target_population_hint`; the case brief reads
      e.g. "Existing grievance file: renters are aggrieved."

## 3. District + feature repaint *
- [ ] Approve a case. The affected district family repaints (base
      `district_type`, prosperity overlay, identity overlay) and any new/updated
      feature shows. Log shows `[REBUILD] ... mode=district-readd ...` and, with
      perf enabled, `ring_redraw=...`.
- [ ] Advance a week (or let the deadline fire). Districts whose state changed
      repaint; converted/contested districts show their new fill + name.
- [ ] Confirm the board never goes blank / lines-only after an action. The visible
      district ring slot should stay intact while the prepare slot updates; on
      failure the old visible slot should remain visible.

## 4. Symbology (#9) *
- [ ] On launch the log emits the six `[SYM] set ... unique-value symbology on ...`
      lines (PermitDistricts/Points/Lines/Zones + District Prosperity + District
      Identity).
- [ ] District base renders by `district_type`; the Prosperity overlay by
      `prosperity_band`; the Identity overlay by `identity_state`.
- [ ] Feature layers render by `display_state`; proposed-feature exhibits stay
      visible when toggled on and hide when toggled off.

## 5. Daily-pressure channel
- [ ] Advance through a work-week's days. Daily pressure writes `display_state`
      to districts; confirm the chosen daily overlay reads as intended (or note
      it as a #9 follow-up if no dedicated daily channel renders yet).

## 6. District identity / buyouts (#6) legibility
- [ ] Play several weeks. When a low-prosperity district is contested/converted,
      the ticker + filed report name the district and the rival bidder(s) in plain
      names (no raw `D0000` ids in any player-facing text).
- [ ] A converted district shows its new type-flavored name and new fill, and its
      docket starts surfacing new-type-flavored proposals over time.

## 7. Audit-grade cache (item B)
- [ ] Make a decision: the City Pulse **Audit** row updates mid-week to reflect
      the new grade (cache invalidates on decision/turn writes).
- [ ] Select different docket rows / retarget from the map without deciding: the
      Audit grade stays put and the desk still feels responsive (selection-only
      reloads reuse the cached grade instead of recomputing the scorecard).

## 8. Legacy .gdb migration
- [ ] Open an older save (pre-rename `.gdb` if available). It loads without error;
      legacy city-health columns backfill (see `migrate_legacy_city_health_fields`)
      and no new persisted fields are required (this round added none).

## 9. End-of-game flow (#7)
- [ ] Reach week 12 / deadline. The inline final-audit receipt auto-shows with
      PASS/CONDITIONAL/FAIL flavor; the week label reads `12/12 CLOSED`. Repeated
      End Week after close is idempotent (no duplicate receipts).

## 10. Pacing and redraw budget
- [ ] Start a game with `PERMIT_OFFICE_PERF=1`. Confirm the filing deadline is
      2:30 and each office day lasts about 30 seconds.
- [ ] Let the week advance from Monday to Tuesday. Confirm the desk status names
      rising pressure, and the log does not show a district re-add unless a
      configured checkpoint is reached.
- [ ] Let the week advance to a configured checkpoint. Confirm the log includes
      `[REBUILD] targeted=['PermitDistricts'] mode=district-readd dirty=districts`.
- [ ] Record timings for a normal decision and a checkpoint tick:
      `ring_redraw`, `feature_PermitPoints_ring_refresh` /
      `feature_PermitPoints_ring_rehydrate`, and total `rebuild`. If the ring
      path reports a warning and falls back, record `remove`, `add`, `refresh`
      too.

## 11. Retired redraw probes
- [ ] Confirm the public toolbox has no **Redraw Experiment** or benchmark
      parameter.
- [ ] If live redraw looks wrong, reproduce with the production ring path first.
      Use `docs/failed-experiments.md` before reviving any retired probe.
- [ ] `district-ring`: current promoted path. Confirm point features still
      appear/update after approvals while the district board keeps correct
      type/identity/prosperity visuals.
- [ ] Pre-drawn rehydrate path: record seed cost, hot-path cost, resume behavior,
      Contents clutter, and runtime swap timing. It remains a diagnostic fallback
      because district-ring preserved correctness with a smaller reusable display
      surface.
- [ ] Promote no experiment unless it preserves visual correctness after GDB
      writes and reduces live redraw time versus the baseline.

## 12. ArcPy review follow-through
Offline fakes cannot prove these. Drop the `(after ARn)` tag once that change
lands and this check passes live.
- [ ] (after AR2) Open a map whose coordinate system is WGS84 (4326) and start a
      new game in a fresh workspace. All four feature classes report EPSG 3857
      and the board draws as even 100 m squares. An existing save still opens
      unchanged.
- [ ] (after AR1) Approve one point case and one line case. The districts that
      receive spillover match the ones the old GP path picked for the same
      geometry (compare against a pre-change save or a screenshot).
- [ ] (after AR1, AR4) With `PERMIT_OFFICE_PERF=1`, record `spillover`,
      `resolve`, and total decision time for one normal decision, next to the
      section 10 numbers.
- [ ] (after AR3) Start a new game. The JSON text fields are the new wider
      length, and a long case history saves without a truncation warning.
- [ ] (after AR4) On that new `.gdb`, the lookup-column indexes exist
      (Catalog > table Properties > Indexes).
- [ ] (after AR14, Pro 3.7+) With `PERMIT_OFFICE_PERF=1`, play a full week.
      Decisions log `district-flip`, district colors match each filed report,
      the board never flickers or blanks, and `rebuild` drops below the ring's
      section 10 numbers. On Pro older than 3.7, decisions log `district-ring`.
- [ ] (after AR16, Pro 3.7+) Make three decisions that change only points. Each
      logs `[REDRAW] feature-query target=...` and no `district-flip`; the map
      background never flashes white and the city stays drawn. Then make one
      decision that changes a district's type, display state, prosperity, or
      identity: it logs `district-flip`. Every changed feature and district
      shows its new color, and nothing unchanged looks stale.
- [ ] (after AR16) After a day tick, with some districts showing daily
      pressure, make a points-only decision. It logs no `district-flip`, and the
      daily-pressure districts keep their color until the next day tick.
- [ ] (after AR6) Swap the district ring a few times. Every slot keeps its full
      style from the shipped `.lyrx` files: base type, prosperity, and identity
      look right, with no default single-symbol layer.

---
**On any failure:** capture the Geoprocessing message log + a screenshot, note the
exact step, and file it. Items 2 (grievance), 1 (workspace), 3 (repaint), and 7
(grade cache) cover changes made this session and are the highest-priority checks.
