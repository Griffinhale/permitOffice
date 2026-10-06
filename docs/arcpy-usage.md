# How Permit Office Uses Stock arcpy

Which ArcPy APIs we use, the parameters we pass, and the patterns we follow. All ArcPy lives in the adapter layer (`toolbox/permit_office_arcgis/` +
the `.pyt`); the pure rules never import arcpy. For *why* the refresh/cursor
choices were made, see `decisions.md`.

## 1. Cursors (`arcpy.da`) — game I/O

All persistence is `arcpy.da` cursors, always under a `with` block. We pass an
explicit field list (never `"*"`) and use the `SHAPE@` token for geometry.

- **SearchCursor** — `store.py` reads (state, districts, docket, projects, active
  features) and `proposals.py` lookups. District geometry uses
  `["cell_id", "SHAPE@"]`.
- **UpdateCursor** — district/feature updates (`updateRow`), proposal deletes
  (`deleteRow`), proposal status changes, and `_delete_all_rows` (`deleteRow`
  on every row) before state, project, and docket rewrites. Those run on every
  save, so they avoid the `DeleteRows` GP tool.
- **InsertCursor** — board creation, feature inserts, state/docket/command/log
  rows; geometry rows pass `["SHAPE@"] + attrs` to `insertRow`.

**where_clause as a hint, guard as the truth.** Single-row ops pass a where
clause (built from `_where_equals` / `_where_item_status` / `_sql_quote`, e.g.
`item_id = 'CASE-1' AND status = 'proposed'`) *and* keep an in-Python row check.
The SQL narrows the scan on a real geodatabase; the Python guard guarantees
correctness even if a backend ignores the predicate (see `decisions.md`).

**Read once.** `reload()` opens a fixed set of cursors (and the batch
`proposal_visible_map` instead of per-item scans); command handlers hand already-
persisted `state`/`districts` back to `reload()` to skip re-reads. Immutable
district `SHAPE@` geometry is memoized per session (`district_geometry_lookup`,
cleared on New Game).

## 2. Schema / geodatabase (`schema.py`)

Idempotent creation guarded by `arcpy.Exists`:

- `arcpy.management.CreateFileGDB(folder, name)`
- `arcpy.management.CreateTable(gdb_path, name)` — non-spatial tables.
- `arcpy.management.CreateFeatureclass(gdb_path, name, geometry_type, spatial_reference=sr)`
  — `POINT` / `POLYLINE` / `POLYGON`.
- `arcpy.management.AddFields(table, [[name, type, alias, length], ...])` via
  `add_missing_fields`: one `ListFields` and at most one `AddFields` call per
  table. Types are `TEXT/LONG/DOUBLE/SHORT/DATE`.
  JSON fields are `TEXT` with `JSON_TEXT_LENGTH` (32768) in new saves. Older
  saves keep their old widths (e.g. `state_json` 4000), since `AddFields` never
  widens a field. `store.encode_json` never slices JSON: an oversized payload
  logs a `[STORE]` warning naming the field and size, and stores `{}`.
- `arcpy.ListFields(table)` to diff existing fields (case-insensitive), and once
  per table per run to read real JSON field widths (`text_field_length`).
- `arcpy.ListIndexes(table)` + `arcpy.management.AddIndex(table, [field], name)`
  (`ensure_lookup_indexes`) index `commands.command_id` and `docket.item_id`,
  the columns single-row where clauses match on.
- `arcpy.management.DeleteRows(table)` to reset gameplay while keeping schema
  (`clear_game_rows`, New Game only); `DeleteField` for the legacy city-health
  field migration.

## 3. Geometry construction

District board and proposals are built by hand from coordinates:

- `arcpy.Point(x, y)` → `arcpy.Array([...])` → `arcpy.Polygon(arr, sr)` /
  `arcpy.Polyline(arr, sr)`.
- `arcpy.PointGeometry(geom.centroid, geom.spatialReference)` for point permits
  (placed at a district centroid); lines connect two centroids.
- `geom.extent` (XMin/XMax/YMin/YMax) + normalized hint offsets place seeded
  city-detail features inside a district.
- Spatial reference comes from `arcpy.Describe(fc).spatialReference`. New games
  always create feature classes in `arcpy.SpatialReference(3857)` (Web Mercator),
  whatever the active map uses; Pro reprojects them for display.

## 4. Map / display & refresh-redraw (`map_layers.py`, `map_redraw.py`)

Map access is via `arcpy.mp.ArcGISProject("CURRENT").activeMap` (None-checked —
the dashboard must tolerate no open map).

- **Add:** `active_map.addLayer(arcpy.mp.LayerFile(lyrx))` from the shipped
  style (see **Symbology** below), then set `.name`; idempotent — guarded by
  `if name not in existing` over `active_map.listLayers()`. Layers are tuned
  (`transparency`, labels via `showLabels`/`listLabelClasses`/
  `label_class.expression`) and ordered with
  `active_map.moveLayer(ref, layer, "AFTER")`.
- **Remove:** `active_map.removeLayer(layer)` for our known output names.
- **Refresh:** `arcpy.RefreshLayer(name)` per layer, only where a query toggle
  cannot be used (below Pro 3.7, or as a fallback). On a visible layer it
  repaints the whole map (AR15).

**Refresh/redraw strategy.** `arcpy.RefreshLayer` only redraws a layer's cached
renderer; by itself it does not reliably reload changed GDB attributes. So
`rebuild_output_layers()` now defaults to a district display ring for
district-dirty work: it keeps numeric `Permit Office Predrawn 0/1/2` district
slots in the map, prepares one hidden slot from the authoritative
`PermitDistricts` feature class, applies district symbology, swaps visibility
after successful preparation, and leaves the old visible slot intact on failure.
Districts are still re-added for correctness, but the whole district family is
no longer rebuilt on every decision.

On Pro 3.7 and newer (`QUERY_FLIP_MIN_PRO` in `map_layers.py`), a district-dirty
redraw first flips the visible slot's `definitionQuery` between `1=1` and `2=2`.
A live probe on 3.7 showed this makes Pro show new attribute values in about
0.5 s. The flipped layer still drops its shapes for about 0.2 s (labels stay, no
white flash), and a `District Underlay` copy drawn below it shows the old fill
during the drop. `RefreshLayer` also updated the colors there but flashed the
whole map white. The flip only runs when the visible slot reads the current save's
`PermitDistricts`. Otherwise, and on older Pro, the ring rehydrate runs. The
version check reads `arcpy.GetInstallInfo()` once per session.

Support features (points, lines, zones) use the same reusable-ring idea with
`Permit Office Predrawn Points/Lines/Zones 0/1/2`. On Pro 3.7+, a redraw toggles
a no-op marker (`AND 271828=271828`) on the visible slot's `definitionQuery`, or
on the visible base layer when there is no slot yet, so Pro requeries only that
layer (AR16). The toggles for all in-scope layers run back to back so their
drops overlap. Below 3.7, or when no visible layer reads this save, the adapter
rehydrates a ring slot from the GDB, then falls back to the legacy remove/add
path and `RefreshLayer`. `force_readd=True` still
does the full remove -> add -> refresh of every in-scope layer, used when the
layer set or symbology changes (New Game) or when a ring path reports failure.
Timings are visible under `PERMIT_OFFICE_PERF=1` as `ring_redraw`,
with phase labels such as `feature_PermitPoints_ring_refresh` and
`feature_PermitPoints_ring_rehydrate`.

**Symbology** (`toolbox/layers/*.lyrx`, `map_layers._add_styled_layer`): every
Permit Office layer and ring slot is added with `Map.addLayer(arcpy.mp.LayerFile(...))`
from a style exported from Pro, then pointed at the save with
`updateConnectionProperties`; the new `dataSource` is checked because Pro can skip
an invalid update without raising. A missing or unrepointable `.lyrx` is an error,
not a code-styled fallback (ruling D7). Districts render by `district_type`, the
overlays by `prosperity_band` and `identity_state`, support layers by
`display_state`.

## 5. Selection & analysis

- `SelectLayerByAttribute(layer, "NEW_SELECTION"|"CLEAR_SELECTION", where)` — drives
  district/support selection from docket context and clears it after commands.
- Spillover: read the proposal's `SHAPE@` with a where-narrowed `SearchCursor`,
  `geom.buffer(r)` in memory (radius converted from meters via
  `spatialReference.metersPerUnit`), then keep the cached district shapes that are
  not `disjoint` from the buffer. No GP tools or temp layers.
- Reading a selection: `arcpy.da.Describe(layer).get("FIDSet")` (fallback to
  `arcpy.Describe(layer).FIDSet`), then a `SearchCursor` over the cell-id field.

## 6. Geoprocessing tool plumbing (`arcpy_permit_office.pyt`)

- `Toolbox` (label/alias/tools) + `PermitOfficePrototype` tool with
  `getParameterInfo` / `execute`, and `canRunInBackground = False`.
- Parameters via `arcpy.Parameter(displayName=, name=, datatype=, parameterType=,
  direction=)`: only a derived `GPFeatureLayer` output, so the dialog shows no
  inputs (ND14).
- `execute` resolves workspace (`resolve_game_workspace`) → `ensure_schema` →
  open the dashboard on its main menu → `arcpy.SetParameterAsText(P_OUTPUT, paths["districts"])`.
- Messages go through `messages.py` helpers wrapping `AddMessage`/`AddWarning`/
  `AddError`.

## 7. Environment / workspace (`schema.py: resolve_workspace`)

`resolve_game_workspace` passes `PERMIT_OFFICE_WORKSPACE` (testing only) to
`resolve_workspace`, which prefers an explicit `.gdb` or folder; else the active project's
`homeFolder/data/permit_office.gdb`; else
`arcpy.env.scratchWorkspace or scratchFolder or os.getcwd()`. We do **not** set
`arcpy.env.workspace` — all paths are absolute and passed in a `paths` dict.

## Patterns worth keeping

- One `paths` dict threads every absolute GDB path through the adapter.
- Cursors are always `with`-scoped, field-explicit, and where-narrowed for
  single-row ops (with a Python guard).
- ArcPy calls that touch the live map are wrapped in try/except + a `messages`
  warning, so a missing map or layer produces a warning instead of a crash.
- We avoid Feature Set drawing and any ArcGIS Pro pane automation (see
  `decisions.md`); proposals are seeded geometry, retargeted from selections.
