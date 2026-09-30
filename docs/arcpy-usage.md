# How Permit Office Uses Stock arcpy

A map of which ArcPy APIs we lean on, the params we pass, and the patterns we
follow. All ArcPy lives in the adapter layer (`toolbox/permit_office_arcgis/` +
the `.pyt`); the pure rules never import arcpy. For *why* the refresh/cursor
choices were made, see `decisions.md`.

## 1. Cursors (`arcpy.da`) — game I/O

All persistence is `arcpy.da` cursors, always under a `with` block. We pass an
explicit field list (never `"*"`) and use the `SHAPE@` token for geometry.

- **SearchCursor** — `store.py` reads (state, districts, docket, projects, active
  features) and `geometry.py` lookups. District geometry uses
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

## 4. Map / display & refresh-redraw (`geometry.py`, `dashboard.py`)

Map access is via `arcpy.mp.ArcGISProject("CURRENT").activeMap` (None-checked —
the dashboard must tolerate no open map).

- **Add:** `active_map.addDataFromPath(path)` then set `.name`; idempotent —
  guarded by `if name not in existing` over `active_map.listLayers()`. Layers are
  tuned (`transparency`, labels via `showLabels`/`listLabelClasses`/
  `label_class.expression`), symbolized, and ordered with
  `active_map.moveLayer(ref, layer, "AFTER")`.
- **Remove:** `active_map.removeLayer(layer)` for our known output names.
- **Refresh:** `arcpy.RefreshLayer(name)` per layer.

**Refresh/redraw strategy.** `arcpy.RefreshLayer` only redraws a layer's cached
renderer - it does **not** reliably reload changed GDB attributes by itself. So
`rebuild_output_layers()` now defaults to a **district display ring** for
district-dirty work: it keeps numeric `Permit Office Predrawn 0/1/2` district
slots in the map, prepares one hidden slot from the authoritative
`PermitDistricts` feature class, applies district symbology, swaps visibility
after successful preparation, and leaves the old visible slot intact on failure.
This keeps the district re-add correctness requirement without rebuilding the
whole district family every decision.

Support features use the same reusable-ring idea with
`Permit Office Predrawn Points/Lines/Zones 0/1/2`. If a visible support ring slot
already exists, the adapter first tries a cheap `RefreshLayer` on that slot; if
ArcGIS does not repaint correctly or raises, it falls back to rehydrating a ring
slot from the GDB, then to the legacy remove/add path. `force_readd=True` still
does the full remove -> add -> refresh of every in-scope layer, used when the
layer set or symbology changes (New Game) or when a ring path reports failure.
Timings are visible under `PERMIT_OFFICE_PERF=1` as `ring_redraw`,
with phase labels such as `feature_PermitPoints_ring_refresh` and
`feature_PermitPoints_ring_rehydrate`.

**Symbology** (`symbology_config.py` + `geometry.py`): a `UniqueValueRenderer` set
via `sym.updateRenderer("UniqueValueRenderer")`, with the render field assigned
through multiple fallbacks (`renderer.fields` list → `renderer.field` string →
CIM `getDefinition("V3"/"V2")` + `setDefinition`) because the accessible property
varies by ArcGIS build. Districts render by `district_type`, support layers by
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
  direction=)`: an optional `DEWorkspace`, a derived `GPFeatureLayer` output, and
  an optional `GPBoolean` perf toggle.
- `execute` resolves workspace → `ensure_schema` → add layers → open the
  dashboard → `arcpy.SetParameterAsText(P_OUTPUT, paths["districts"])`.
- Messages go through `messages.py` helpers wrapping `AddMessage`/`AddWarning`/
  `AddError`.

## 7. Environment / workspace (`schema.py: resolve_workspace`)

Prefers an explicit `.gdb` arg; else the active project's
`homeFolder/data/permit_office.gdb`; else
`arcpy.env.scratchWorkspace or scratchFolder or os.getcwd()`. We do **not** set
`arcpy.env.workspace` — all paths are absolute and passed in a `paths` dict.

## Patterns worth keeping

- One `paths` dict threads every absolute GDB path through the adapter.
- Cursors are always `with`-scoped, field-explicit, and where-narrowed for
  single-row ops (with a Python guard).
- ArcPy calls that touch the live map are wrapped in try/except + a `messages`
  warning, so a missing map or layer degrades gracefully instead of crashing.
- We avoid Feature Set drawing and any ArcGIS Pro pane automation (see
  `decisions.md`); proposals are seeded geometry, retargeted from selections.
