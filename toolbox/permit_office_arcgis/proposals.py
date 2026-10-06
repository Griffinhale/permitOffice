"""Case proposals: map selection, proposed geometry, spillover, activation."""

from __future__ import annotations

import random
import uuid

import arcpy

from .map_layers import _active_map, _feature_ring_prefixes, _is_predrawn_district_layer_name
from .messages import _log, _warn
from .rules_loader import rules
from .schema import DISTRICTS, LINES, POINTS, SUPPORT_FIELDS, ZONES
from .store import decode_json, encode_json, write_docket_item


# District geometry is fixed for the life of a game (only attributes change), so
# the SHAPE@ pull — the most expensive field on the districts table — is memoized
# per districts source and cleared whenever a fresh board is seeded.
_DISTRICT_GEOM_CACHE: dict = {}


def clear_geometry_cache():
    """Drop memoized district geometry so the next lookup re-reads the GDB."""

    _DISTRICT_GEOM_CACHE.clear()


def _summary_map(value):
    """Format a compact sorted metadata map for ArcGIS text fields."""

    if not isinstance(value, dict) or not value:
        return ""
    return ", ".join(f"{key} {amount}" for key, amount in sorted(value.items()))[:512]


def selected_cell_ids(layer):
    """Return selected district IDs from a layer, or an empty list."""

    if not layer:
        return []
    has_selection = False
    try:
        fidset = arcpy.da.Describe(layer).get("FIDSet")
        if isinstance(fidset, (list, tuple, set)):
            has_selection = bool(fidset)
        elif isinstance(fidset, str):
            has_selection = bool(fidset.strip())
        elif fidset:
            has_selection = True
    except Exception:
        try:
            fidset = getattr(arcpy.Describe(layer), "FIDSet", None)
            has_selection = bool(str(fidset).strip()) if fidset is not None else False
        except Exception:
            has_selection = False
    if not has_selection:
        return []
    # Fast path: read the canonical cell_id column directly. The schema always
    # names it "cell_id", so the common case skips the ListFields metadata scan;
    # only a legacy/renamed layer (cursor raises on the missing field) pays for
    # the case-insensitive field lookup fallback.
    try:
        return [row[0] for row in arcpy.da.SearchCursor(layer, ["cell_id"])]
    except Exception:
        fields = {field.name.lower(): field.name for field in arcpy.ListFields(layer)}
        cell_field = fields.get("cell_id")
        if not cell_field:
            return []
        return [row[0] for row in arcpy.da.SearchCursor(layer, [cell_field])]


def ensure_case_proposal(paths, item, seed, messages, target_ids=None) -> list[str]:
    """Ensure an unresolved docket item has a proposed exhibit row."""

    if item.status not in ("open", "inspected", "carried"):
        return list(item.target_cell_ids or ())
    if target_ids is not None:
        return insert_or_replace_proposal(paths, item, _exhibit_targets(item, target_ids), messages)

    existing = _case_proposal_targets(paths, item.item_id)
    if existing:
        if not item.target_cell_ids:
            item.target_cell_ids = existing
            _try_write_docket_item(paths, item, messages)
        return existing

    districts = _district_records(paths)
    targets = list(item.target_cell_ids or _suggest_targets(item, districts, seed))
    return insert_or_replace_proposal(paths, item, _exhibit_targets(item, targets), messages)


def _exhibit_targets(item, target_ids):
    """Pin a maintenance order's point exhibit to one district of its feature."""

    # Maintenance orders copy every district of the subject feature, so a line
    # or zone feature hands the point exhibit two targets. The decision resolves
    # through subject_feature_id, so the first district is enough for the map.
    targets = list(target_ids)
    if item.template_id == rules.MAINTENANCE_TEMPLATE_ID and item.geometry_type == "POINT":
        return targets[:1]
    return targets


def hide_case_proposal(paths, item_id) -> bool:
    """Remove the selected unresolved proposal without touching city features."""

    hidden = False
    where = _where_item_status(item_id, "proposed")
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.UpdateCursor(fc, ["item_id", "status"], where) as cursor:
            for row in cursor:
                if row[0] == item_id and row[1] == "proposed":
                    cursor.deleteRow()
                    hidden = True
    return hidden


def case_proposal_visible(paths, item) -> bool:
    """Return whether a docket item's unresolved proposal is present on the map."""

    item_id = getattr(item, "item_id", item)
    return bool(_case_proposal_targets(paths, item_id))


def proposal_visible_map(paths) -> dict:
    """Map every item ID that owns a live proposed exhibit to True in one pass.

    Replaces N per-item ``case_proposal_visible`` calls during a dashboard reload
    (each of which scanned all three support classes) with three cursor opens.
    """

    visible: dict = {}
    where = _where_equals("status", "proposed")
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.SearchCursor(fc, ["item_id", "status"], where) as cursor:
            for row in cursor:
                if row[0] and row[1] == "proposed":
                    visible[row[0]] = True
    return visible


def select_case_context(paths, district_layer, item, seed, messages) -> None:
    """Select the docket item's proposal, target districts, and referenced feature."""

    targets = ensure_case_proposal(paths, item, seed, messages)
    _select_district_targets(district_layer, targets, messages)
    _select_support_context(paths, item, messages)


def district_geometry_lookup(paths):
    """Read district geometries keyed by cell ID (memoized for the session).

    Callers treat the result as read-only; the same dict is shared across the
    game until ``clear_geometry_cache`` is called when a new board is seeded.
    """

    key = paths["districts"]
    cached = _DISTRICT_GEOM_CACHE.get(key)
    if cached is not None:
        return cached
    lookup = {}
    with arcpy.da.SearchCursor(paths["districts"], ["cell_id", "SHAPE@"]) as cursor:
        for cid, geom in cursor:
            lookup[cid] = geom
    _DISTRICT_GEOM_CACHE[key] = lookup
    return lookup


def _case_proposal_targets(paths, item_id):
    """Return stored target IDs for the first proposed row matching an item."""

    where = _where_item_status(item_id, "proposed")
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.SearchCursor(fc, ["item_id", "status", "target_cell_ids"], where) as cursor:
            for row in cursor:
                if row[0] == item_id and row[1] == "proposed":
                    return [part for part in (row[2] or "").split(",") if part]
    return []


def _try_write_docket_item(paths, item, messages):
    """Persist target IDs when proposal rows reveal older docket state."""

    try:
        write_docket_item(paths, item)
    except Exception as exc:
        _warn(messages, "PREVIEW", f"could not persist targets for {item.item_id}: {exc}")


def _select_district_targets(district_layer, target_ids, messages):
    """Select target districts in ArcGIS using their cell IDs."""

    where = _where_in("cell_id", target_ids)
    candidates = _district_selection_candidates(district_layer)
    for candidate in candidates:
        if _select_layer(candidate, "NEW_SELECTION", where, messages, "district targets", warn=False):
            return
    label = candidates[-1] if candidates else district_layer
    _select_layer(label, "NEW_SELECTION", where, messages, "district targets")


def _district_selection_candidates(district_layer):
    """Return district layers to try for map selection, preferring visible ring slots."""

    candidates = []
    try:
        active_map = _active_map()
        layers = active_map.listLayers() if active_map is not None else []
    except Exception:
        layers = []
    for layer in layers:
        name = getattr(layer, "name", "")
        if not _is_predrawn_district_layer_name(name):
            continue
        if not bool(getattr(layer, "visible", True)):
            continue
        if name not in candidates:
            candidates.append(name)
    if district_layer and district_layer != DISTRICTS:
        candidates.insert(0, district_layer)
    elif district_layer and district_layer not in candidates:
        candidates.append(district_layer)
    if DISTRICTS not in candidates:
        candidates.append(DISTRICTS)
    return candidates


def _select_support_context(paths, item, messages):
    """Select proposal/support rows owned by this case and its subject feature."""

    parts = []
    if item.item_id:
        parts.append(_where_equals("item_id", item.item_id))
    if item.subject_feature_id:
        parts.append(_where_equals("feature_id", item.subject_feature_id))
    where = " OR ".join(parts) if parts else None
    for layer_name, path in ((POINTS, paths["points"]), (LINES, paths["lines"]), (ZONES, paths["zones"])):
        selected = False
        for candidate in _support_selection_candidates(layer_name):
            if _select_layer(candidate, "NEW_SELECTION", where, messages, layer_name, warn=False):
                selected = True
                break
        if not selected:
            _select_layer(path, "NEW_SELECTION", where, messages, path)


def _support_selection_candidates(layer_name):
    """Return visible support display layers before the hidden/base layer."""

    candidates = []
    prefix = _feature_ring_prefixes().get(layer_name, "")
    try:
        active_map = _active_map()
        layers = active_map.listLayers() if active_map is not None else []
    except Exception:
        layers = []
    for layer in layers:
        name = getattr(layer, "name", "")
        if prefix and name.startswith(prefix) and bool(getattr(layer, "visible", True)):
            candidates.append(name)
    if layer_name not in candidates:
        candidates.append(layer_name)
    return candidates


def _select_layer(layer, selection_type, where, messages, label, warn=True) -> bool:
    """Apply a selection expression, returning whether ArcPy accepted it."""

    try:
        if where:
            arcpy.management.SelectLayerByAttribute(layer, selection_type, where)
        else:
            arcpy.management.SelectLayerByAttribute(layer, "CLEAR_SELECTION")
        return True
    except Exception as exc:
        if warn:
            _warn(messages, "SELECT", f"{label} selection failed: {exc}")
        return False


def _where_in(field, values):
    """Build a simple ArcGIS SQL IN expression for text IDs."""

    clean = [str(value) for value in values or () if value]
    if not clean:
        return None
    if len(clean) == 1:
        return _where_equals(field, clean[0])
    return f"{field} IN ({', '.join(_sql_text(value) for value in clean)})"


def _where_equals(field, value):
    """Build a simple ArcGIS SQL equality expression for a text ID."""

    return f"{field} = {_sql_text(value)}"


def _where_item_status(item_id, status):
    """SQL match for a support row owned by ``item_id`` in a given ``status``."""

    return f"{_where_equals('item_id', item_id)} AND {_where_equals('status', status)}"


def _sql_text(value):
    """Quote a text literal for simple ArcGIS SQL expressions."""

    return "'{0}'".format(str(value).replace("'", "''"))


def insert_or_replace_proposal(paths, item, target_ids, messages):
    """Create the proposed point, line, or polygon feature for a docket item."""

    # Each docket item owns one proposed exhibit. Other open docket proposals
    # stay visible so the map reads as a real in-tray instead of a single preview.
    where = _where_item_status(item.item_id, "proposed")
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.UpdateCursor(fc, ["item_id", "status"], where) as cursor:
            for row in cursor:
                if row[0] == item.item_id and row[1] == "proposed":
                    cursor.deleteRow()

    template = rules.TEMPLATES[item.template_id]
    archetype = rules.feature_archetype_for_template(template)
    metadata = rules.feature_metadata_for_template(template)
    geoms = district_geometry_lookup(paths)
    valid = [cid for cid in target_ids if cid in geoms]
    if item.geometry_type == "POINT" and len(valid) != 1:
        raise ValueError("Point permits require exactly one selected district.")
    if item.geometry_type == "LINE" and len(valid) != 2:
        raise ValueError("Line permits require exactly two selected districts.")
    if item.geometry_type == "POLYGON" and not valid:
        raise ValueError("Zone permits require one or more selected districts.")

    attrs = _support_attrs(
        item=item,
        template=template,
        archetype=archetype,
        target_ids=valid,
        status="proposed",
        display_state="proposed",
        report=template.preview,
        metadata=metadata,
    )
    if item.geometry_type == "POINT":
        geom = arcpy.PointGeometry(geoms[valid[0]].centroid, geoms[valid[0]].spatialReference)
        fc = paths["points"]
    elif item.geometry_type == "LINE":
        p1 = geoms[valid[0]].centroid
        p2 = geoms[valid[1]].centroid
        geom = arcpy.Polyline(arcpy.Array([p1, p2]), geoms[valid[0]].spatialReference)
        fc = paths["lines"]
    else:
        # Zone proposals use a smaller display polygon within the first selected
        # district because the gameplay target list carries the full selection.
        geom = inset_polygon(geoms[valid[0]])
        fc = paths["zones"]

    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + [field[0] for field in SUPPORT_FIELDS]) as cursor:
        cursor.insertRow([geom] + attrs)
    item.target_cell_ids = valid
    write_docket_item(paths, item)
    _log(messages, "PREVIEW", f"created proposed {item.geometry_type.lower()} for {item.item_id}: {','.join(valid)}")
    return valid


def seed_docket_proposals(paths, items, seed, messages):
    """Create a deterministic proposed map exhibit for each open docket item."""

    purge_proposed_features(paths)
    districts = _district_records(paths)
    for item in items:
        if item.status not in ("open", "inspected", "carried"):
            continue
        target_ids = item.target_cell_ids or _suggest_targets(item, districts, seed)
        try:
            insert_or_replace_proposal(paths, item, _exhibit_targets(item, target_ids), messages)
        except Exception as exc:
            _warn(messages, "PREVIEW", f"could not seed proposal for {item.item_id}: {exc}")


def purge_proposed_features(paths):
    """Remove unresolved proposed features without touching active city rows."""

    where = _where_equals("status", "proposed")
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.UpdateCursor(fc, ["status"], where) as cursor:
            for row in cursor:
                if row[0] == "proposed":
                    cursor.deleteRow()


def _district_records(paths):
    """Read district geometry and placement hints used for generated exhibits."""

    records = {}
    fields = ["cell_id", "district_type", "SHAPE@"]
    with arcpy.da.SearchCursor(paths["districts"], fields) as cursor:
        for cell_id, district_type, geom in cursor:
            records[cell_id] = {"district_type": district_type or "", "geom": geom}
    return records


def _suggest_targets(item, districts, seed):
    """Choose deterministic target districts matching the docket geometry."""

    template = rules.TEMPLATES[item.template_id]
    archetype = rules.feature_archetype_for_template(template)
    rng = random.Random(f"{seed}:{item.item_id}:proposal")
    scored = []
    for cell_id, record in districts.items():
        district_type = record["district_type"]
        score = 0
        if district_type in template.good_fit_types:
            score += 4
        if district_type in archetype.allowed_district_types:
            score += 3
        if district_type in template.bad_fit_types:
            score -= 4
        if district_type in archetype.conflict_district_types:
            score -= 3
        scored.append((-score, rng.random(), cell_id))
    ordered = [cell_id for _score, _tie, cell_id in sorted(scored)]
    if item.geometry_type == "POINT":
        return ordered[:1]
    if item.geometry_type == "LINE":
        if not ordered:
            return []
        first = ordered[0]
        adjacent = _adjacent_ids(first, districts)
        for cell_id in ordered[1:]:
            if cell_id in adjacent:
                return [first, cell_id]
        return ordered[:2]
    count = 2 if len(ordered) > 1 else 1
    return ordered[:count]


def _adjacent_ids(cell_id, districts):
    """Return generated-grid four-way neighbors present in the current board."""

    try:
        row = int(cell_id[1:3])
        col = int(cell_id[3:5])
    except Exception:
        return set()
    candidates = (
        f"D{row - 1:02d}{col:02d}",
        f"D{row + 1:02d}{col:02d}",
        f"D{row:02d}{col - 1:02d}",
        f"D{row:02d}{col + 1:02d}",
    )
    return {candidate for candidate in candidates if candidate in districts}


def _support_attrs(item, template, archetype, target_ids, status, display_state, report, metadata):
    """Build the shared support-feature attribute payload."""

    feature_id = str(uuid.uuid4())
    target_text = ",".join(target_ids)
    expires_turn = item.turn + template.expires_after if template.expires_after else -1
    feature_state = rules.FeatureInstance(
        feature_id=feature_id,
        item_id=item.item_id,
        template_id=item.template_id,
        archetype_id=archetype.archetype_id,
        family=archetype.family,
        service_type=archetype.service_type,
        network_type=archetype.network_type or archetype.service_type,
        owner_group=item.stakeholder or template.stakeholder,
        target_cell_ids=list(target_ids),
        capacity=archetype.capacity,
        intensity=max(1, archetype.capacity or 1),
        status=status,
        turn_created=item.turn,
        expires_turn=expires_turn,
        display_state=display_state,
        project_id=item.project_id,
        chain_step_id=item.chain_step_id,
        metadata=metadata,
    )
    rules.normalize_feature_instance(feature_state, item.turn)
    feature_state.status = status
    feature_state.display_state = display_state
    return [
        feature_id,
        item.item_id,
        item.template_id,
        item.project_id,
        item.chain_step_id,
        item.title,
        template.category,
        archetype.archetype_id,
        archetype.family,
        archetype.service_type,
        archetype.network_type or archetype.service_type,
        float(archetype.coverage_radius_m),
        archetype.capacity,
        archetype.land_use,
        archetype.incident_type,
        item.stakeholder or template.stakeholder,
        max(1, archetype.capacity or 1),
        encode_json(metadata, limit=2048),
        _summary_map(metadata.get("hazard_effects")),
        _summary_map(metadata.get("mitigation_effects")),
        status,
        item.turn,
        expires_turn,
        target_text,
        display_state,
        report,
        feature_state.condition,
        feature_state.maintenance_due_turn,
        feature_state.last_maintained_turn,
        encode_json(feature_state.state_json),
    ]


def inset_polygon(geom):
    """Return a smaller polygon centered inside a district geometry."""

    extent = geom.extent
    width = max(10.0, (extent.XMax - extent.XMin) * 0.58)
    height = max(10.0, (extent.YMax - extent.YMin) * 0.58)
    cx = (extent.XMin + extent.XMax) / 2.0
    cy = (extent.YMin + extent.YMax) / 2.0
    arr = arcpy.Array([
        arcpy.Point(cx - width / 2, cy - height / 2),
        arcpy.Point(cx + width / 2, cy - height / 2),
        arcpy.Point(cx + width / 2, cy + height / 2),
        arcpy.Point(cx - width / 2, cy + height / 2),
        arcpy.Point(cx - width / 2, cy - height / 2),
    ])
    return arcpy.Polygon(arr, geom.spatialReference)


def proposal_spillover(paths, item):
    """Find districts touched by the proposed feature's coverage buffer.

    Buffers the proposal geometry in memory and tests it against the cached
    district shapes, so no geoprocessing tool or temp layer is involved.
    """

    proposed_fc = {"POINT": paths["points"], "LINE": paths["lines"], "POLYGON": paths["zones"]}[item.geometry_type]
    archetype = rules.feature_archetype_for_template(item.template_id)
    radius = max(1, int(archetype.coverage_radius_m or 125))
    districts = district_geometry_lookup(paths)
    touched = set()
    # ArcGIS does the geometric spillover calculation; the rules layer only sees
    # the resulting district ids.
    where = _where_item_status(item.item_id, "proposed")
    with arcpy.da.SearchCursor(proposed_fc, ["item_id", "status", "SHAPE@"], where) as cursor:
        for row_item_id, status, geom in cursor:
            if row_item_id != item.item_id or status != "proposed" or geom is None:
                continue
            # The radius rule is in meters; buffer() uses the feature class unit.
            meters_per_unit = getattr(geom.spatialReference, "metersPerUnit", None) or 1.0
            coverage = geom.buffer(radius / meters_per_unit)
            touched.update(cid for cid, district in districts.items() if not coverage.disjoint(district))
    return sorted(touched)


def activate_proposal(paths, item, report):
    """Convert a proposed support feature into its resolved gameplay status."""
    fc = {"POINT": paths["points"], "LINE": paths["lines"], "POLYGON": paths["zones"]}[item.geometry_type]
    fields = ["item_id", "feature_id", "archetype_id", "project_id", "chain_step_id", "turn_created", "expires_turn", "status", "display_state", "report", "condition", "maintenance_due_turn", "last_maintained_turn", "state_json"]
    status = item.status if item.status in ("active", "failed", "enforced", "settled", "responded", "maintained") else "active"
    activated = 0
    where = _where_item_status(item.item_id, "proposed")
    with arcpy.da.UpdateCursor(fc, fields, where) as cursor:
        for row in cursor:
            if row[0] == item.item_id and row[7] == "proposed":
                # Rehydrate the lifecycle fields before writing status so
                # approval, failure, and maintenance state stay normalized.
                feature = rules.FeatureInstance(
                    feature_id=row[1],
                    archetype_id=row[2],
                    project_id=item.project_id or row[3] or "",
                    chain_step_id=item.chain_step_id or row[4] or "",
                    status=status,
                    turn_created=int(row[5] or item.turn),
                    expires_turn=int(row[6] if row[6] not in (None, "") else -1),
                    condition=int(row[10] if row[10] not in (None, "") else 100),
                    maintenance_due_turn=int(row[11] if row[11] not in (None, "") else -1),
                    last_maintained_turn=int(row[12] or 0),
                    state_json=decode_json(row[13]),
                )
                rules.normalize_feature_instance(feature, item.turn)
                feature.status = status
                feature.display_state = status
                row[3] = feature.project_id
                row[4] = feature.chain_step_id
                row[7] = feature.status
                row[8] = feature.display_state
                row[9] = report[:1024]
                row[10] = feature.condition
                row[11] = feature.maintenance_due_turn
                row[12] = feature.last_maintained_turn
                row[13] = encode_json(feature.state_json)
                cursor.updateRow(row)
                activated += 1
    return activated


def mark_proposals(paths, item_id, status, report=""):
    """Mark unresolved proposal features as denied, deferred, or otherwise closed."""

    where = _where_item_status(item_id, "proposed")
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.UpdateCursor(fc, ["item_id", "status", "display_state", "report"], where) as cursor:
            for row in cursor:
                if row[0] == item_id and row[1] == "proposed":
                    row[1] = status
                    row[2] = status
                    row[3] = report[:1024]
                    cursor.updateRow(row)
