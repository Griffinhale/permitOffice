# ArcGIS Pro smoke checklist (beta)

Run this in a live ArcGIS Pro session before tagging a beta. It validates the
ArcGIS-side behavior the offline pytest suite cannot (real `.gdb` writes, layer
repaint, symbology, resume). Each item lists the action and the pass condition.
Capture a screenshot for the starred (*) items.

Toolbox: `toolbox/arcpy_permit_office.pyt` -> "Permit Office Prototype".
Keep the Geoprocessing message log open; several checks read its tagged
lines (`[WORKSPACE]`, `[REBUILD]`, `[DASH]`, `[SYM]`).

## 0. Pre-flight
- [ ] ArcGIS Pro module cache is fresh: the `.pyt` reload loop at the top of
      `arcpy_permit_office.pyt` lists every `permit_office_arcgis/*` module in use
      (`test_toolbox_reload_list_matches_modules_on_disk` checks this offline).
- [ ] `uv run --with pytest pytest -q` is green on the dev box (offline baseline).

## 1. Startup: main menu and save location *
- [ ] The tool dialog shows no parameters; **Run** is the only action.
- [ ] In a fresh project with no save, Run opens the main menu with **New Game**
      and **Help** only, the line "No saved game in this project yet." and the
      new-map note. Log shows
      `[WORKSPACE] no workspace given; using project default: ...\data\permit_office.gdb`.
- [ ] In a map with World Topographic Map / World Hillshade, the menu names
      them. New Game opens an empty `Permit Office` map and the game layers
      land there (Contents shows only Permit Office layers).
- [ ] New Game with a save present says it replaces the saved game; Cancel
      keeps the menu and the save.
- [ ] Play a week, close the dashboard, Run again: the menu says
      "Saved game: week N of 12." and **Continue** resumes the same game.
- [ ] Remove the Permit Office layers from the map, Run, Continue: the layers
      come back and the same game resumes.
- [ ] Reopen a finished season: the menu says "season over"; Continue shows
      the Week Closed report and the Final Audit in Reports.
- [ ] With `PERMIT_OFFICE_WORKSPACE=<folder or .gdb>` set before Pro starts,
      the log shows `[WORKSPACE] PERMIT_OFFICE_WORKSPACE is set` and
      `using provided game workspace: <that path>`; the project save is untouched.

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
- [ ] End the week. Districts whose state changed repaint; converted/contested
      districts show their new fill + name.
- [ ] Confirm the board never goes blank / lines-only after an action. The visible
      district ring slot should stay intact while the prepare slot updates; on
      failure the old visible slot should remain visible.

## 4. Symbology (#9) *
- [ ] On a new game the log emits six `[SYM] styled ... from ....lyrx` lines,
      one per layer (districts, points, lines, zones, prosperity, identity), and
      no `could not point` or `missing toolbox/layers` warning.
- [ ] District base renders by `district_type`; the Prosperity overlay by
      `prosperity_band`; the Identity overlay by `identity_state`.
- [ ] Feature layers render by `display_state`; proposed-feature exhibits stay
      visible when toggled on and hide when toggled off.

## 5. Week-close pressure and the audit ladder
- [ ] Leave a case open and End Week: the week report names heat added to the
      unresolved case and the grievance update for its targets.
- [ ] Close week 4: the week report carries a checkpoint audit and the
      patience ladder moves (FAIL up, PASS down, CONDITIONAL holds).

## 6. District identity / buyouts (#6) legibility
- [ ] Play several weeks. When a low-prosperity district is contested/converted,
      the ticker + filed report name the district and the rival bidder(s) in plain
      names (no raw `D0000` ids in any player-facing text).
- [ ] A converted district shows its new type-flavored name and new fill, and its
      docket starts surfacing new-type-flavored proposals over time.

## 7. Audit-grade cache (item B)
- [ ] Make a decision: the header AUDIT line updates mid-week to reflect the
      new grade (cache invalidates on decision/turn writes).
- [ ] Select different docket rows / retarget from the map without deciding: the
      Audit grade stays put and the desk still feels responsive (selection-only
      reloads reuse the cached grade instead of recomputing the scorecard).

## 8. Legacy .gdb migration
- [ ] Open an older save (pre-rename `.gdb` if available). It loads without error;
      legacy city-health columns backfill (see `migrate_legacy_city_health_fields`).
      The new state keys (`mandate`, `initiatives`, `audit_rung`, `outcome`) are
      key/value rows, so an old save loads with no goal filed and the desk shows
      no goal cards (start a New Game to draw them).

## 9. End-of-game flow (#7)
- [ ] Close week 12. The final-audit report auto-shows with PASS/CONDITIONAL/FAIL
      flavor, the week report says Season won or lost and lists achievements,
      and the week label reads `12/12 CLOSED`. Repeated End Week after close is
      idempotent (no duplicate receipts).

## 10. Pacing and redraw budget
- [ ] Start a game with `PERMIT_OFFICE_PERF=1`. Confirm the banner has no
      clock and the week stays open until End Week (or the queue auto-close
      after the last case).
- [ ] Record timings for a normal decision and a week close:
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
- [ ] (after AR14) After the first district-changing decision, Zones still draw
      above the district fill, and District Prosperity and District Identity stay
      on with updated colors. In Contents, `Permit Office Predrawn N` sits just
      above `PermitDistricts`, below Zones.
- [ ] (after AR14, Pro 3.7+) On a brand-new game, make the first
      district-changing decision. The log shows `path=district-flip
      target='PermitDistricts'` with no `seed_visible_slot` or `RefreshLayer`;
      the map does not flash white, and both overlays stay on with new colors.
- [ ] (after AR16, Pro 3.7+) Make three decisions that change only points. Each
      logs `[REDRAW] feature-query target=...` and no `district-flip`; the map
      background never flashes white and the city stays drawn. Then make one
      decision that changes a district's type, display state, prosperity, or
      identity: it logs `district-flip`. Every changed feature and district
      shows its new color, and nothing unchanged looks stale.
- [ ] (after AR16) Show and hide the selected case's exhibit, then use Update
      from Map. Each logs `feature-query` and no `RefreshLayer`; the map never
      flashes white.
- [ ] (after AR18, Pro 3.7+) With `PERMIT_OFFICE_PERF=1` and no other setup,
      close a week where the new docket has a line or zone case. The log shows `district-flip` and
      `feature-query target='PermitLines'` (or zones), with no ring seed and no
      `RefreshLayer`; the background never flashes white. Note whether the
      city shapes blink, and which layers.
- [ ] (after AR18) Drag `PermitPoints` (and its `Permit Office Predrawn Points`
      slots) above `PermitLines` in Contents, then make a points-only
      decision. Note whether the lines still blink.
- [ ] (after AR18) With `PERMIT_OFFICE_PERF=1` set, make a points-only
      decision whose next case is also a point case. Only the points layer blinks; districts and lines stay put. The log shows
      `[REBUILD] targeted=['PermitPoints']`, one `feature-query target='PermitPoints'`,
      then one `[SELECT] clear kept=[...] cleared=[...]` line that never lists
      `PermitDistricts` under cleared. Points drop once and no district goes white. On a map
      with no `Permit Office Predrawn Points` slot, none is created and the base
      `PermitPoints` stays visible. The next case's targets and proposal point
      are highlighted afterwards.
- [ ] (after AR18) New Game: Contents lists `District Underlay` directly below
      `PermitDistricts`, with no labels of its own (each district name drawn
      once). Close a week: district interiors never go white; at most they
      show the previous fill for a moment. Zones, lines and points each drop at
      most once, for well under a second.
- [ ] (after AR21, Pro 3.7+) With `PERMIT_OFFICE_PERF=1`, close a week whose
      `[REBUILD]` line names `PermitLines`, `PermitPoints` and `PermitZones`
      (in seed 2034 only the week 12 close did). The log shows the district
      flip's `feature-query target='PermitDistricts'` line, then one
      `feature-query` line per feature layer, then the
      `path=district-flip status=ok` summary that closes the block. No ring
      seed or `RefreshLayer` appears in the block. On the recording, lines, points
      and zones each drop once, and the near-empty map lasts no longer than
      AR18 run11 D3's (about 1 s); district interiors never go white. Pro
      redraws the layers one after another, about 0.13 s apart, even though the
      queries are written within 2 ms, so the drops need not overlap (AR21 v8:
      0.43 s gap).
- [ ] (after AR19, Pro 3.7) In `ar_probe.aprx` on `probe_save.gdb`, save,
      then restart Pro. Open the dashboard, close it right away with no
      decisions, then run one `arcpy.management.GetCount(r"...\probe_save.gdb\PermitPoints")` in the
      Python window. Pro must not crash with `Tcl_AsyncDelete`. If it does,
      keep event 1000 and the dump, restart Pro, and stop after two crashes.
- [ ] (after AR6) Swap the district ring a few times. Every slot keeps its full
      style from the shipped `.lyrx` files: base type, prosperity, and identity
      look right, with no default single-symbol layer.

## 13. Dashboard look (narrow pane) *
Linux previews use substitute fonts and Tk 9; only Pro (Tk 8.6, Segoe UI,
Windows DPI) proves the look. Run each item at the default pane size
(480 x 820) and at the minimum (400 x 560), on a throwaway workspace, never a
real save. Screenshot each tab at both sizes and note the Windows display scale.
- [ ] New Game: the Desk tab shows three goal cards; clicking one (or 1-3)
      files it and the header GOAL line shows its progress.
- [ ] Header: week, AP, money and net, goal, next checkpoint audit with points
      to PASS, and the patience ladder are readable and never overlap.
- [ ] The window resizes freely between the two sizes; at the minimum the tab
      body scrolls with the mouse wheel and nothing draws over the header or
      footer.
- [ ] Desk tab: open cases list what happens if ignored; filed cases sit below;
      the brief's four buttons show hotkeys and costs inside their boxes.
- [ ] Initiative card: pick a district type and Earmark; Civic action and
      Market push act on the districts selected on the map; after one, the card
      says it is filed for the week.
- [ ] End Week stays in the footer at 0 AP and shows the close forecast; after
      week 12 the footer offers New Game and the Desk tab names the result.
- [ ] Reports tab: a week report and a decision report show headed sections
      with no `...` cut-off. City tab: stats show their change since the
      week began; district types mark an active earmark.
- [ ] Status ticker: scrolling text never shows outside its strip. Open the
      menu and the help card and wait 10+ seconds: the ticker stays under them.
