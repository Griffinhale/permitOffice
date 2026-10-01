"""ArcGIS geometry operations for previews, activation, and map refresh."""

from __future__ import annotations

import json
import os
import random
import time
import uuid

import arcpy

from .messages import _log, _warn
from .layer_ring import DisplayLayerRing, DistrictLayerRing
from .rules_loader import rules
from .schema import DISTRICTS, LINES, POINTS, SUPPORT_FIELDS, ZONES
from .store import decode_json, encode_json, read_districts, write_docket_item
from .symbology_config import LAYER_TRANSPARENCY, RENDER_FIELD_BY_LAYER_KEY, SYMBOLS_BY_FIELD, apply_default_symbol_style, apply_symbol_style

# District overlay layers reuse the PermitDistricts feature class with different
# render fields so land-use type, prosperity, and buyout identity each get their
# own visual channel without one fill having to encode all three.
DISTRICT_PROSPERITY = "District Prosperity"
DISTRICT_IDENTITY = "District Identity"
PREDRAWN_LAYER_PREFIX = "Permit Office Predrawn"
PREDRAWN_POINTS_PREFIX = "Permit Office Predrawn Points"
PREDRAWN_LINES_PREFIX = "Permit Office Predrawn Lines"
PREDRAWN_ZONES_PREFIX = "Permit Office Predrawn Zones"

# Lowest Pro version where flipping a district slot's definition query was seen
# to show new GDB attribute values without flicker (AR5 spike, Pro 3.7). Older
# builds keep the ring rehydrate. Lower this only after the probe passes there.
QUERY_FLIP_MIN_PRO = (3, 7)
QUERY_FLIP_VALUES = ("1=1", "2=2")
_PRO_VERSION_CACHE: dict = {}

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
        return insert_or_replace_proposal(paths, item, list(target_ids), messages)

    existing = _case_proposal_targets(paths, item.item_id)
    if existing:
        if not item.target_cell_ids:
            item.target_cell_ids = existing
            _try_write_docket_item(paths, item, messages)
        return existing

    districts = _district_records(paths)
    targets = list(item.target_cell_ids or _suggest_targets(item, districts, seed))
    return insert_or_replace_proposal(paths, item, targets, messages)


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
            insert_or_replace_proposal(paths, item, target_ids, messages)
        except Exception as exc:
            _warn(messages, "PREVIEW", f"could not seed proposal for {item.item_id}: {exc}")


def purge_proposed_features(paths):
    """Remove unresolved proposed features without touching active city rows."""

    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.UpdateCursor(fc, ["status"]) as cursor:
            for row in cursor:
                if row[0] == "proposed":
                    cursor.deleteRow()


def seed_city_features(paths, seed, messages):
    """Seed deterministic baseline city detail so the map starts populated."""

    # A fresh board has just been written by create_district_board, so any
    # memoized geometry from a prior game is stale and must be dropped.
    clear_geometry_cache()
    if any(_feature_count(paths[key]) for key in ("points", "lines", "zones")):
        return
    districts = read_districts(paths)
    descriptors = rules.generate_city_detail_features(districts, seed=seed)
    inserted = 0
    for descriptor in descriptors:
        try:
            _insert_city_detail_feature(paths, descriptor)
            inserted += 1
        except Exception as exc:
            _warn(messages, "CITY", f"could not seed {descriptor.name}: {exc}")
    if inserted:
        active = sum(1 for feature in descriptors if feature.status == "active")
        context = sum(1 for feature in descriptors if feature.status == "context")
        _log(messages, "CITY", f"seeded {inserted} city detail feature(s): {active} active, {context} context")


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
        json.dumps(metadata, sort_keys=True)[:2048],
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


def _insert_baseline_feature(paths, archetype_id, target_ids, name):
    """Insert one active baseline feature using an existing archetype shape."""

    archetype = rules.FEATURE_ARCHETYPES[archetype_id]
    template_id = _template_for_archetype(archetype_id)
    template = rules.TEMPLATES[template_id]
    item = rules.DocketItem(
        item_id=f"BASE-{archetype_id}",
        template_id=template_id,
        title=name,
        geometry_type=archetype.geometry_type,
        turn=0,
        status="active",
        target_cell_ids=list(target_ids),
        stakeholder="city",
    )
    geoms = district_geometry_lookup(paths)
    metadata = rules.feature_metadata_for_template(template)
    attrs = _support_attrs(
        item=item,
        template=template,
        archetype=archetype,
        target_ids=target_ids,
        status="active",
        display_state=archetype.display_state or "active",
        report="Existing city feature.",
        metadata=metadata,
    )
    if archetype.geometry_type == "POINT":
        geom = arcpy.PointGeometry(geoms[target_ids[0]].centroid, geoms[target_ids[0]].spatialReference)
        fc = paths["points"]
    elif archetype.geometry_type == "LINE":
        p1 = geoms[target_ids[0]].centroid
        p2 = geoms[target_ids[-1]].centroid
        geom = arcpy.Polyline(arcpy.Array([p1, p2]), geoms[target_ids[0]].spatialReference)
        fc = paths["lines"]
    else:
        geom = inset_polygon(geoms[target_ids[0]])
        fc = paths["zones"]
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + [field[0] for field in SUPPORT_FIELDS]) as cursor:
        cursor.insertRow([geom] + attrs)


def _insert_city_detail_feature(paths, descriptor):
    """Insert one generated city-detail descriptor into a support layer."""

    geoms = district_geometry_lookup(paths)
    targets = [cid for cid in descriptor.target_cell_ids if cid in geoms]
    if not targets:
        raise ValueError("city detail descriptor has no valid target district")
    archetype = rules.FEATURE_ARCHETYPES.get(descriptor.archetype_id)
    metadata = {
        "archetype_id": descriptor.archetype_id,
        "seeded_city_detail": True,
        **dict(descriptor.metadata or {}),
    }
    feature_state = rules.FeatureInstance(
        feature_id=descriptor.feature_id,
        item_id="",
        template_id="",
        archetype_id=descriptor.archetype_id,
        family=archetype.family if archetype else "",
        service_type=archetype.service_type if archetype else "",
        network_type=(archetype.network_type or archetype.service_type) if archetype else "",
        owner_group=descriptor.owner_group,
        target_cell_ids=targets,
        capacity=descriptor.capacity,
        intensity=descriptor.intensity,
        status=descriptor.status,
        turn_created=0,
        expires_turn=-1,
        display_state=descriptor.display_state,
        metadata=metadata,
        state_json=dict(descriptor.state or {}),
    )
    rules.normalize_feature_instance(feature_state, 0)
    feature_state.status = descriptor.status
    feature_state.display_state = descriptor.display_state
    if descriptor.geometry_type == "POINT":
        geom = _point_from_hint(geoms[targets[0]], descriptor.geometry_hint)
        fc = paths["points"]
    elif descriptor.geometry_type == "LINE":
        geom = _line_from_hint([geoms[cid] for cid in targets], descriptor.geometry_hint)
        fc = paths["lines"]
    else:
        geom = _rect_from_hint(geoms[targets[0]], descriptor.geometry_hint)
        fc = paths["zones"]
    attrs = [
        descriptor.feature_id,
        "",
        "",
        "",
        "",
        descriptor.name,
        "city_context",
        descriptor.archetype_id,
        feature_state.family,
        feature_state.service_type,
        feature_state.network_type,
        float(archetype.coverage_radius_m if archetype else 0),
        feature_state.capacity,
        archetype.land_use if archetype else "",
        archetype.incident_type if archetype else "",
        descriptor.owner_group,
        feature_state.intensity,
        encode_json(feature_state.metadata, limit=2048),
        _summary_map(archetype.hazard_effects if archetype else {}),
        _summary_map(archetype.mitigation_effects if archetype else {}),
        feature_state.status,
        feature_state.turn_created,
        feature_state.expires_turn,
        ",".join(targets),
        feature_state.display_state,
        "Seeded city detail.",
        feature_state.condition,
        feature_state.maintenance_due_turn,
        feature_state.last_maintained_turn,
        encode_json(feature_state.state_json),
    ]
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + [field[0] for field in SUPPORT_FIELDS]) as cursor:
        cursor.insertRow([geom] + attrs)


def _point_from_hint(geom, hint):
    """Create a point inside a district from normalized offsets."""

    extent = geom.extent
    x = extent.XMin + (extent.XMax - extent.XMin) * float(hint.get("x", 0.5))
    y = extent.YMin + (extent.YMax - extent.YMin) * float(hint.get("y", 0.5))
    return arcpy.PointGeometry(arcpy.Point(x, y), geom.spatialReference)


def _line_from_hint(geoms, hint):
    """Create a corridor or short street line from district geometry hints."""

    if len(geoms) == 1 and hint.get("shape") == "stub":
        extent = geoms[0].extent
        orientation = hint.get("orientation", "horizontal")
        offset = float(hint.get("offset", 0.5))
        if orientation == "vertical":
            x = extent.XMin + (extent.XMax - extent.XMin) * offset
            points = [arcpy.Point(x, extent.YMin + (extent.YMax - extent.YMin) * 0.18), arcpy.Point(x, extent.YMax - (extent.YMax - extent.YMin) * 0.18)]
        else:
            y = extent.YMin + (extent.YMax - extent.YMin) * offset
            points = [arcpy.Point(extent.XMin + (extent.XMax - extent.XMin) * 0.18, y), arcpy.Point(extent.XMax - (extent.XMax - extent.XMin) * 0.18, y)]
        return arcpy.Polyline(arcpy.Array(points), geoms[0].spatialReference)
    points = [geom.centroid for geom in geoms]
    return arcpy.Polyline(arcpy.Array(points), geoms[0].spatialReference)


def _rect_from_hint(geom, hint):
    """Create a rectangle inside a district from normalized center and size."""

    extent = geom.extent
    width = max(8.0, (extent.XMax - extent.XMin) * float(hint.get("w", 0.25)))
    height = max(8.0, (extent.YMax - extent.YMin) * float(hint.get("h", 0.20)))
    cx = extent.XMin + (extent.XMax - extent.XMin) * float(hint.get("cx", 0.5))
    cy = extent.YMin + (extent.YMax - extent.YMin) * float(hint.get("cy", 0.5))
    x0 = max(extent.XMin + 2.0, min(extent.XMax - width - 2.0, cx - width / 2))
    y0 = max(extent.YMin + 2.0, min(extent.YMax - height - 2.0, cy - height / 2))
    arr = arcpy.Array([
        arcpy.Point(x0, y0),
        arcpy.Point(x0 + width, y0),
        arcpy.Point(x0 + width, y0 + height),
        arcpy.Point(x0, y0 + height),
        arcpy.Point(x0, y0),
    ])
    return arcpy.Polygon(arr, geom.spatialReference)


def _template_for_archetype(archetype_id):
    """Return a docket template that spawns the requested archetype."""

    for template in rules.TEMPLATES.values():
        if template.spawn_archetype_id == archetype_id:
            return template.template_id
    raise KeyError(archetype_id)


def _feature_count(path):
    """Return the row count for an ArcGIS feature class."""

    return int(arcpy.management.GetCount(path)[0])


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


def refresh_all(paths, messages, layer_names=None):
    """Refresh known map layers; layer_names limits to a subset when given."""
    targets = [n for n in (DISTRICTS, POINTS, LINES, ZONES) if layer_names is None or n in layer_names]
    for name in targets:
        try:
            arcpy.RefreshLayer(name)
            _log(messages, "REFRESH", f"RefreshLayer({name!r}) OK")
        except Exception as exc:
            _warn(messages, "REFRESH", f"RefreshLayer({name!r}) failed: {exc}")


def output_layers_present():
    """Return True if any Permit Office output layer is on the active map.

    Used to decide whether to auto-resume a saved board: a map with none of our
    layers is treated as a fresh-start session even when the .gdb still holds a
    save. Conservative -- returns True unless it can positively confirm an active
    map that lacks every output layer, so a probe failure never suppresses a
    normal resume.
    """

    names = {DISTRICTS, POINTS, LINES, ZONES, DISTRICT_PROSPERITY, DISTRICT_IDENTITY}
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        active_map = aprx.activeMap
        if active_map is None:
            return True
        return any(layer.name in names for layer in active_map.listLayers())
    except Exception:
        return True


def ensure_active_map(messages, map_name="Permit Office"):
    """Ensure the project has an open map for Permit Office layer operations."""

    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
    except Exception as exc:
        _warn(messages, "MAP", f"ArcGISProject('CURRENT') unavailable; cannot ensure map: {exc}")
        return None
    active_map = getattr(aprx, "activeMap", None)
    if active_map is not None:
        return active_map
    try:
        maps = list(aprx.listMaps()) if hasattr(aprx, "listMaps") else []
    except Exception as exc:
        _warn(messages, "MAP", f"could not list project maps: {exc}")
        maps = []
    if maps:
        target = maps[0]
        _open_map_view(target, messages)
        _log(messages, "MAP", f"opened existing map: {getattr(target, 'name', 'Map')}")
        return target
    create_map = getattr(aprx, "createMap", None)
    if create_map is None:
        _warn(messages, "MAP", "project has no active map and ArcGISProject.createMap is unavailable")
        return None
    try:
        target = create_map(map_name)
        _open_map_view(target, messages)
        _log(messages, "MAP", f"created and opened map: {getattr(target, 'name', map_name)}")
        return target
    except Exception as exc:
        _warn(messages, "MAP", f"could not create map: {exc}")
        return None


def _open_map_view(map_obj, messages):
    opener = getattr(map_obj, "openView", None)
    if opener is None:
        return
    try:
        opener()
    except Exception as exc:
        _warn(messages, "MAP", f"could not open map view: {exc}")


def add_outputs_to_map(paths, messages, layer_names=None):
    """Add active game outputs to the map; layer_names limits to a subset when given."""
    try:
        active_map = ensure_active_map(messages)
        if active_map is None:
            return
        existing = {lyr.name: lyr for lyr in active_map.listLayers()}
        for name, key in [p for p in ((DISTRICTS, "districts"), (POINTS, "points"), (LINES, "lines"), (ZONES, "zones")) if layer_names is None or p[0] in layer_names]:
            if name not in existing:
                lyr = active_map.addDataFromPath(paths[key])
                lyr.name = name
                existing[name] = lyr
                _log(messages, "MAP", f"added {name}")
            _tune_layer_visibility(existing[name], key)
            _configure_labels(existing[name], key)
            apply_simple_symbology(existing[name], key, messages)
        # Add the prosperity and identity overlays from the same districts source.
        if layer_names is None or DISTRICTS in layer_names:
            for name, key in ((DISTRICT_PROSPERITY, "district_prosperity"), (DISTRICT_IDENTITY, "district_identity")):
                if name not in existing:
                    lyr = active_map.addDataFromPath(paths["districts"])
                    lyr.name = name
                    existing[name] = lyr
                    _log(messages, "MAP", f"added {name}")
                _tune_layer_visibility(existing[name], key)
                apply_simple_symbology(existing[name], key, messages)
        _order_output_layers(active_map, existing)
    except Exception as exc:
        _warn(messages, "MAP", f"add outputs failed: {exc}")


def remove_outputs_from_map(messages, layer_names=None):
    """Remove stale Permit Office layers; layer_names limits to a subset when given."""
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        active_map = aprx.activeMap
        if active_map is None:
            return
        base_outputs = {DISTRICTS, POINTS, LINES, ZONES}
        # The district overlays are tied to the districts source, so they clear
        # whenever districts (or a full refresh) are being removed.
        overlay_outputs = {DISTRICT_PROSPERITY, DISTRICT_IDENTITY}
        if layer_names is None:
            output_names = base_outputs | overlay_outputs
        else:
            output_names = (base_outputs & set(layer_names))
            if DISTRICTS in layer_names:
                output_names |= overlay_outputs
        removed = 0
        for layer in list(active_map.listLayers()):
            layer_name = getattr(layer, "name", None)
            remove_predrawn = (layer_names is None or DISTRICTS in set(layer_names or ())) and _is_predrawn_district_layer_name(layer_name or "")
            remove_feature_ring = _should_remove_predrawn_feature_layer(layer_name or "", layer_names)
            if layer_name in output_names or remove_predrawn or remove_feature_ring:
                active_map.removeLayer(layer)
                removed += 1
        if removed:
            _log(messages, "MAP", f"removed {removed} stale Permit Office layer(s)")
    except Exception as exc:
        _warn(messages, "MAP", f"remove stale outputs failed: {exc}")


def _active_map():
    """Return the current active ArcGIS map, or None when unavailable."""

    aprx = arcpy.mp.ArcGISProject("CURRENT")
    return aprx.activeMap


def _layers_by_name(active_map):
    """Return active map layers keyed by their display name."""

    if active_map is None:
        return {}
    return {getattr(layer, "name", ""): layer for layer in active_map.listLayers()}


def _log_redraw(messages, path, status, started, detail=""):
    elapsed = time.perf_counter() - started
    suffix = f" {detail}" if detail else ""
    _log(messages, "REDRAW", f"path={path} status={status} elapsed={elapsed:.3f}{suffix}")


class _PhaseTimer:
    def __init__(self):
        self._last = time.perf_counter()
        self.parts = []

    def mark(self, name):
        now = time.perf_counter()
        self.parts.append((name, now - self._last))
        self._last = now

    def summary(self):
        return " ".join(f"{name}={elapsed:.3f}" for name, elapsed in self.parts)


def _set_definition_query(layer, definition_query):
    if definition_query is None:
        return
    try:
        layer.definitionQuery = definition_query
    except Exception:
        pass


def _district_display_style_hash(definition_query):
    query = definition_query or ""
    return f"districts|labels=districts|query={query}|transparency=10"


def _prepare_district_display_layer(layer, messages, definition_query=None, skip_if_style_matches=False):
    _set_definition_query(layer, definition_query)
    style_hash = _district_display_style_hash(definition_query)
    if skip_if_style_matches and getattr(layer, "_permit_office_style_hash", "") == style_hash:
        return True
    _tune_layer_visibility(layer, "districts")
    _configure_labels(layer, "districts")
    apply_simple_symbology(layer, "districts", messages)
    try:
        layer._permit_office_style_hash = style_hash
    except Exception:
        pass
    return False


def _prepare_feature_display_layer(layer, messages, layer_key, definition_query=None):
    _set_definition_query(layer, definition_query)
    _tune_layer_visibility(layer, layer_key)
    _configure_labels(layer, layer_key)
    apply_simple_symbology(layer, layer_key, messages)


def _is_predrawn_district_layer_name(name):
    return name.startswith(PREDRAWN_LAYER_PREFIX) and not _is_predrawn_feature_layer_name(name)


def _is_predrawn_feature_layer_name(name):
    return any(name.startswith(prefix) for prefix in _feature_ring_prefixes().values())


def _should_remove_predrawn_feature_layer(name, layer_names):
    if not name:
        return False
    if layer_names is None:
        return _is_predrawn_feature_layer_name(name)
    scoped = set(layer_names or ())
    return any(layer in scoped and name.startswith(prefix) for layer, prefix in _feature_ring_prefixes().items())


def _is_ring_slot_name(name):
    suffix = name.removeprefix(PREDRAWN_LAYER_PREFIX).strip()
    return suffix.isdigit()


def _feature_layer_scope(layer_names):
    feature_layers = frozenset((POINTS, LINES, ZONES))
    if layer_names is None:
        return set(feature_layers)
    return set(layer_names) & feature_layers


def _refresh_feature_scope(paths, messages, layer_names, remove_scope=None, phase_marker=None):
    phase_marker = phase_marker or (lambda _name: None)
    feature_scope = _feature_layer_scope(layer_names)
    if not feature_scope:
        return
    readd_scope = feature_scope & set(remove_scope or ())
    fallback_readd = set()
    for layer_name in sorted(readd_scope):
        if _refresh_visible_feature_display_ring(paths, messages, layer_name):
            phase_marker(f"feature_{layer_name}_ring_refresh")
            continue
        if not _rehydrate_feature_display_ring(paths, messages, layer_name):
            fallback_readd.add(layer_name)
        phase_marker(f"feature_{layer_name}_ring_rehydrate")
    if fallback_readd:
        remove_outputs_from_map(messages, layer_names=fallback_readd)
        add_outputs_to_map(paths, messages, layer_names=fallback_readd)
        phase_marker("feature_fallback_readd")
    refresh_scope = (feature_scope - readd_scope) | fallback_readd
    if refresh_scope:
        refresh_all(paths, messages, layer_names=refresh_scope)
        for layer_name in sorted(refresh_scope):
            phase_marker(f"feature_{layer_name}_refresh")


def _rehydrate_feature_display_ring(paths, messages, layer_name):
    prefix = _feature_ring_prefixes().get(layer_name)
    path_key = _feature_path_keys().get(layer_name)
    layer_key = _feature_layer_keys().get(layer_name)
    if not prefix or not path_key or not layer_key:
        return False
    active_map = ensure_active_map(messages)
    if active_map is None:
        return False

    def _copy_style(layer):
        _prepare_feature_display_layer(layer, messages, layer_key, "1=1")

    try:
        ring = DisplayLayerRing(active_map, prefix=prefix, arcpy_module=arcpy, style_copier=_copy_style)
        ring.prepare_and_swap(paths[path_key])
        _hide_base_feature_layer(active_map, layer_name)
        return True
    except Exception as exc:
        _warn(messages, "REDRAW", f"feature-ring {layer_name} failed: {exc}")
        return False


def _refresh_visible_feature_display_ring(paths, messages, layer_name):
    prefix = _feature_ring_prefixes().get(layer_name)
    path_key = _feature_path_keys().get(layer_name)
    if not prefix or not path_key or not _query_flip_supported():
        return False
    active_map = ensure_active_map(messages)
    if active_map is None:
        return False
    visible = None
    for layer in active_map.listLayers():
        name = getattr(layer, "name", "")
        suffix = name.removeprefix(prefix + " ")
        if name.startswith(prefix + " ") and suffix.isdigit() and bool(getattr(layer, "visible", False)):
            if not _same_source(layer, paths[path_key]):
                return False
            visible = layer
            break
    if visible is None:
        return False
    _hide_base_feature_layer(active_map, layer_name)
    try:
        # RefreshLayer invalidates every visible layer in the containing view.
        # Change only this layer's query, preserving any existing filter.
        current = visible.definitionQuery or ""
        marker = " AND 271828=271828"
        if current.endswith(marker) and current.startswith("("):
            visible.definitionQuery = current[1:-len(marker)-1]
        else:
            visible.definitionQuery = f"({current or '1=1'}){marker}"
        _log(messages, "REDRAW", f"feature-query target={visible.name!r}")
        return True
    except Exception as exc:
        _warn(messages, "REDRAW", f"feature-ring refresh {layer_name} failed: {exc}")
        return False


def _feature_ring_prefixes():
    return {
        POINTS: PREDRAWN_POINTS_PREFIX,
        LINES: PREDRAWN_LINES_PREFIX,
        ZONES: PREDRAWN_ZONES_PREFIX,
    }


def _feature_path_keys():
    return {
        POINTS: "points",
        LINES: "lines",
        ZONES: "zones",
    }


def _feature_layer_keys():
    return {
        POINTS: "points",
        LINES: "lines",
        ZONES: "zones",
    }


def _hide_base_feature_layer(active_map, layer_name):
    for layer in active_map.listLayers():
        if getattr(layer, "name", "") == layer_name:
            try:
                layer.visible = False
            except Exception:
                pass


def _pro_version():
    """Return the running Pro version as an int tuple, or () when unknown."""

    if "version" not in _PRO_VERSION_CACHE:
        try:
            text = str(arcpy.GetInstallInfo().get("Version", ""))
            _PRO_VERSION_CACHE["version"] = tuple(int(part) for part in text.split(".")[:2] if part.isdigit())
        except Exception:
            _PRO_VERSION_CACHE["version"] = ()
    return _PRO_VERSION_CACHE["version"]


def _query_flip_supported():
    """Return True when this Pro build is proven to requery on a query flip."""

    version = _pro_version()
    return bool(version) and version >= QUERY_FLIP_MIN_PRO


def _same_source(layer, path):
    """Return True when a layer reads the given feature class."""

    try:
        source = layer.dataSource
    except Exception:
        return False
    return os.path.normcase(os.path.normpath(source or "")) == os.path.normcase(os.path.normpath(path))


def _flip_visible_district_slot(active_map, district_path, messages):
    """Flip the visible ring slot's query so Pro redraws new attribute values.

    Returns the flipped layer, or None when there is no visible slot reading
    this save's districts (callers then use the ring rehydrate).
    """

    for layer in active_map.listLayers():
        name = getattr(layer, "name", "")
        if not (_is_ring_slot_name(name) and bool(getattr(layer, "visible", False))):
            continue
        if not _same_source(layer, district_path):
            return None
        try:
            current = layer.definitionQuery or ""
            layer.definitionQuery = QUERY_FLIP_VALUES[1] if current == QUERY_FLIP_VALUES[0] else QUERY_FLIP_VALUES[0]
            return layer
        except Exception as exc:
            _warn(messages, "REDRAW", f"district query flip failed on {name!r}: {exc}")
            return None
    return None


def _apply_district_ring_redraw(paths, messages, layer_names, remove_scope=None):
    started = time.perf_counter()
    phases = _PhaseTimer()
    active_map = ensure_active_map(messages)
    if active_map is None:
        _log_redraw(messages, "district-ring", "no-active-map", started)
        return

    def _copy_style(layer):
        _prepare_district_display_layer(layer, messages, "1=1")

    if _query_flip_supported():
        flipped = _flip_visible_district_slot(active_map, paths["districts"], messages)
        phases.mark("query_flip")
        if flipped is not None:
            _hide_non_ring_district_family(active_map)
            phases.mark("hide_base_districts")
            _refresh_feature_scope(paths, messages, layer_names, remove_scope=remove_scope, phase_marker=phases.mark)
            phases.mark("feature_layers")
            _log_redraw(messages, "district-flip", "ok", started, f"target={getattr(flipped, 'name', '')!r} {phases.summary()}")
            return

    ring = DistrictLayerRing(active_map, arcpy_module=arcpy, style_copier=_copy_style, phase_marker=phases.mark)
    phases.mark("ring_discovery")
    prepared = ring.prepare_and_swap(paths["districts"])
    phases.mark("prepare_visibility_swap")
    _hide_non_ring_district_family(active_map)
    phases.mark("hide_base_districts")
    _refresh_feature_scope(paths, messages, layer_names, remove_scope=remove_scope, phase_marker=phases.mark)
    phases.mark("feature_layers")
    _log_redraw(messages, "district-ring", "ok", started, f"target={getattr(prepared, 'name', '')!r} {phases.summary()}")


def apply_ring_redraw(paths, messages, layer_names=None, remove_scope=None):
    """Apply production display-ring redraw without raising into gameplay.

    Returns True when the path handled the redraw request, or False when callers
    should fall back to the legacy remove/add/refresh path.
    """

    try:
        if layer_names is not None and DISTRICTS not in layer_names:
            _refresh_feature_scope(paths, messages, layer_names, remove_scope=set(layer_names))
            return True
        _apply_district_ring_redraw(paths, messages, layer_names, remove_scope=remove_scope)
        return True
    except Exception as exc:
        _warn(messages, "REDRAW", f"ring redraw failed: {exc}")
        return False


def _hide_non_ring_district_family(active_map):
    """Hide district layers superseded by the active predrawn ring slot."""

    names = {DISTRICTS, DISTRICT_PROSPERITY, DISTRICT_IDENTITY}
    for layer in active_map.listLayers():
        layer_name = getattr(layer, "name", "")
        legacy_predrawn = _is_predrawn_district_layer_name(layer_name) and not _is_ring_slot_name(layer_name)
        if layer_name in names or legacy_predrawn:
            try:
                layer.visible = False
            except Exception:
                pass


def apply_simple_symbology(layer, key, messages):
    """Apply unique-value symbology when the layer supports it."""

    try:
        if not layer.supports("SYMBOLOGY"):
            return
        field_name = RENDER_FIELD_BY_LAYER_KEY.get(key, "display_state")
        sym = layer.symbology
        sym.updateRenderer("UniqueValueRenderer")
        field_set_via_cim = False
        field_set, last_error = _set_unique_value_renderer_field(sym.renderer, field_name)
        if not field_set:
            direct_error = last_error
            field_set, cim_error = _set_unique_value_renderer_field_with_cim(layer, sym, field_name)
            field_set_via_cim = field_set
            last_error = cim_error if field_set else direct_error or cim_error
        if not field_set:
            _warn(
                messages,
                "SYM",
                "unique-value symbology skipped for "
                f"{getattr(layer, 'name', key)}: renderer field API unavailable"
                f" ({last_error})",
            )
            return
        try:
            sym.renderer.useDefaultSymbol = True
        except Exception:
            pass
        if not field_set_via_cim:
            _configure_unique_value_renderer(sym.renderer, field_name, key)
            layer.symbology = sym
        else:
            _try_configure_layer_unique_value_items(layer, field_name, key)
        _log(messages, "SYM", f"set {field_name} unique-value symbology on {getattr(layer, 'name', key)}")
    except Exception as exc:
        _warn(messages, "SYM", f"symbology setup skipped for {getattr(layer, 'name', key)}: {exc}")


def _set_unique_value_renderer_field(renderer, field_name):
    """Set a unique-value renderer field across ArcGIS Pro API variants."""

    last_error = None
    attempts = (
        ("fields", [field_name]),
        ("field", field_name),
        ("fields", (field_name,)),
    )
    for attr, value in attempts:
        if attr == "field" and not _renderer_has_attr(renderer, attr):
            continue
        try:
            setattr(renderer, attr, value)
            return True, None
        except Exception as exc:
            last_error = exc
    return False, last_error or "no supported field setter"


def _renderer_has_attr(renderer, attr):
    """Return whether an ArcGIS renderer exposes an attribute safely."""

    try:
        getattr(renderer, attr)
    except Exception:
        return False
    return True


def _set_unique_value_renderer_field_with_cim(layer, sym, field_name):
    """Fallback through CIM when arcpy.mp renderer properties are unavailable."""

    last_error = None
    if not hasattr(layer, "getDefinition") or not hasattr(layer, "setDefinition"):
        return False, "CIM definition API unavailable"
    try:
        layer.symbology = sym
    except Exception as exc:
        last_error = exc
    for cim_version in ("V3", "V2"):
        try:
            # ArcGIS Pro has shipped renderer field setters under different
            # arcpy.mp surfaces; CIM keeps this path working across versions.
            definition = layer.getDefinition(cim_version)
            renderer = getattr(definition, "renderer", None)
            if renderer is None:
                last_error = "CIM renderer unavailable"
                continue
            _set_cim_attr(renderer, ("fields", "Fields"), [field_name])
            _try_set_cim_attr(renderer, ("useDefaultSymbol", "UseDefaultSymbol"), True)
            _try_set_cim_attr(renderer, ("isDefaultSymbolVisible", "IsDefaultSymbolVisible"), True)
            layer.setDefinition(definition)
            return True, None
        except Exception as exc:
            last_error = exc
    return False, last_error or "CIM renderer field setter unavailable"


def _set_cim_attr(target, names, value):
    """Set the first matching CIM attribute, or the first name as a fallback."""

    for name in names:
        try:
            getattr(target, name)
            setattr(target, name, value)
            return
        except AttributeError:
            continue
    setattr(target, names[0], value)


def _try_set_cim_attr(target, names, value):
    """Best-effort CIM attribute write used for optional renderer flags."""

    try:
        _set_cim_attr(target, names, value)
    except Exception:
        pass


def _try_configure_layer_unique_value_items(layer, field_name, key):
    """Best-effort item styling after a CIM renderer-field fallback."""

    try:
        sym = layer.symbology
        if not _renderer_uses_field(sym.renderer, field_name):
            return
        _configure_unique_value_renderer(sym.renderer, field_name, key)
        layer.symbology = sym
    except Exception:
        pass


def _configure_unique_value_renderer(renderer, field_name, key=None):
    """Seed and style known unique-value classes when ArcGIS exposes item APIs."""
    try:
        renderer.useDefaultSymbol = True
    except Exception:
        pass
    _add_unique_values(renderer, field_name)
    _style_default_symbol(renderer, key)
    _style_unique_value_items(renderer, field_name, key)


def _add_unique_values(renderer, field_name):
    """Seed expected unique-value classes when the renderer supports it."""

    if not hasattr(renderer, "addValues"):
        return
    heading = field_name
    try:
        groups = getattr(renderer, "groups", None)
        if groups:
            heading = groups[0].heading or heading
    except Exception:
        pass
    try:
        renderer.addValues({heading: list(SYMBOLS_BY_FIELD.get(field_name, {}))})
    except Exception:
        pass


def _style_default_symbol(renderer, key):
    """Apply the configured fallback style to a renderer default symbol."""

    for attr in ("defaultSymbol", "default_symbol"):
        try:
            symbol = getattr(renderer, attr)
        except Exception:
            continue
        if symbol is not None:
            apply_default_symbol_style(symbol, key)
            return


def _style_unique_value_items(renderer, field_name, key=None):
    """Apply configured labels and symbols to known unique-value items."""

    symbol_map = SYMBOLS_BY_FIELD.get(field_name, {})
    try:
        groups = renderer.groups
    except Exception:
        return
    for group in groups or []:
        for item in getattr(group, "items", []) or []:
            value = _unique_value_item_value(item)
            if value not in symbol_map:
                continue
            color, label = symbol_map[value]
            try:
                item.label = label
            except Exception:
                pass
            try:
                item.symbol.color = {"RGB": color}
            except Exception:
                pass
            apply_symbol_style(item.symbol, key, value)


def _unique_value_item_value(item):
    """Extract the string value from an ArcGIS unique-value item."""

    try:
        values = item.values
        if values and values[0]:
            return str(values[0][0])
    except Exception:
        pass
    return ""


def _renderer_uses_field(renderer, field_name):
    """Return whether a renderer is already keyed by the requested field."""

    try:
        fields = renderer.fields
        return field_name in list(fields or [])
    except Exception:
        pass
    try:
        return renderer.field == field_name
    except Exception:
        return False


def _tune_layer_visibility(layer, key):
    """Apply configured transparency when the ArcGIS layer allows it."""

    try:
        layer.transparency = LAYER_TRANSPARENCY[key]
    except Exception:
        pass


def _configure_labels(layer, key):
    """Enable district-name labels when label APIs are available."""

    if key != "districts":
        return
    try:
        layer.showLabels = True
    except Exception:
        pass
    try:
        label_classes = layer.listLabelClasses()
    except Exception:
        return
    for label_class in label_classes or []:
        try:
            label_class.expression = "$feature.district_name"
        except Exception:
            pass
        try:
            label_class.visible = True
        except Exception:
            pass


def _order_output_layers(active_map, existing):
    """Draw district colors as the base, identity/prosperity overlays just above
    it, and feature lines/points/zones on top."""
    ordered_names = [LINES, POINTS, ZONES, DISTRICT_IDENTITY, DISTRICT_PROSPERITY, DISTRICTS]
    if not all(existing.get(name) for name in (LINES, POINTS, ZONES, DISTRICTS)):
        return
    present = [existing[name] for name in ordered_names if existing.get(name) is not None]
    try:
        for reference_layer, move_layer in zip(present, present[1:]):
            active_map.moveLayer(reference_layer, move_layer, "AFTER")
    except Exception:
        pass
