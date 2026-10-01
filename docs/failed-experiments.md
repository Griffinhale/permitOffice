# Failed Experiments

This log records probes that should not be reintroduced without new live ArcGIS
evidence. Keep concise entries here; keep current decisions in `docs/decisions.md`.

## 2026-06 - Threaded ArcPy read probe

**Tried:** move ArcPy/GDB reads into a startup worker.

**Finding:** unsafe in live Pro. ArcPy-bound worker probes can freeze or behave
unpredictably.

**Decision:** retired. ArcPy cursor work, GDB writes, map/layer operations, and
Tk widget mutation stay on the main Tk/ArcPy thread. Worker threads may only run
pure Python work from already-captured row snapshots.

**Removed:** GP-facing `Startup/Threading Diagnostics` parameter and stale
`threaded-arcpy-read-probe`/`threaded-cache` selectors.

## 2026-06 - Predrawn rehydrate as selectable redraw path

**Tried:** expose `predrawn-rehydrate` beside `district-ring` as a GP-selectable
redraw experiment.

**Finding:** rehydrate was useful during the transition from broad remove/add,
but the three-slot district/support ring became the correctness-safe production
path. Keeping both selectable made the toolbox look experimental and invited
stale-symbology paths back into normal play.

**Decision:** retired as a runtime option. The public toolbox no longer exposes
redraw experiments or benchmark runs. Ring redraw is production behavior; failure
falls back narrowly to legacy remove/add/refresh.

## 2026-06 - Pure predrawn visibility swap

**Tried:** swap already-drawn district snapshots for very fast redraws.

**Finding:** fast but not correctness-safe. District renderer-driving attributes
can stale out, and `predrawn-swap-refresh` produced red/gray close-state map
corruption in live runs.

**Decision:** rejected. Do not accept stale symbology or corrupted close-state
rendering as a performance win.

## 2026-06 - Volatile overlay redraw

**Tried:** render only volatile/changed district overlays.

**Finding:** cheaper than full district-family remove/add, but it could leave the
board visually incomplete by filtering away baseline districts.

**Decision:** rejected as a default or runtime option.

## 2026-06 - SDK display-cache add-in

**Tried:** C# ArcGIS Pro SDK display-cache control.

**Finding:** slower than the stock ArcPy path in the spike and added build,
install, deployment, and UI burden outside the dashboard command flow.

**Decision:** retired as a capability proof. Keep the Python toolbox on stock
ArcPy unless a future live measurement proves the SDK burden buys enough.

## 2026-06 - Materialized-view cache primitive

**Tried:** a standalone dependency-keyed `materialized.py` cache primitive from
the precomputed-decision-cache spike.

**Finding:** no active runtime caller after the spike. Keeping the file preserved
an abstraction without leverage.

**Decision:** removed. The one-ply decision-future cache (`futures.py`,
`cache_keys.py`, `dirty.py`) was later removed too: each click resolved the
decision once for the cache and again for real, and the hint never changed what
was redrawn. Redraw planning now comes from the actual result alone.
