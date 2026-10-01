"""Baseline city detail features seeded onto a new board."""

from __future__ import annotations

import arcpy

from .messages import _log, _warn
from .proposals import (
    _summary_map,
    _support_attrs,
    clear_geometry_cache,
    district_geometry_lookup,
    inset_polygon,
)
from .rules_loader import rules
from .schema import SUPPORT_FIELDS
from .store import encode_json, read_districts


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
