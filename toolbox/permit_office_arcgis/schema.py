"""ArcGIS geodatabase schema creation for the Permit Office prototype."""

from __future__ import annotations

import os

import arcpy

from .messages import _log, _warn

TOOLBOX_LABEL = "Permit Office Prototype"
TOOLBOX_ALIAS = "permit_office"

DEFAULT_GDB_NAME = "permit_office.gdb"
DISTRICTS = "PermitDistricts"
POINTS = "PermitPoints"
LINES = "PermitLines"
ZONES = "PermitZones"
DOCKET = "PermitDocket"
GAME_STATE = "PermitGameState"
PROJECTS = "PermitProjects"
COMMANDS = "PermitUICommand"
ACTION_LOG = "PermitActionLog"
SCHEMA_META = "PermitSchemaMeta"
SCHEMA_VERSION = "2026-06-12-startup-schema-v1"
SCHEMA_VERSION_KEY = "schema_version"
WEB_MERCATOR_WKID = 3857

P_WORKSPACE = 0
P_OUTPUT = 1
P_PERF = 2
P_REDRAW_EXPERIMENT = 3
P_REDRAW_BENCHMARK_RUNS = 4
P_STARTUP_EXPERIMENT = 5

DISTRICT_FIELDS = [
    ("cell_id", "TEXT", "District ID", 32),
    ("district_name", "TEXT", "District Name", 96),
    ("population", "LONG", "Population", None),
    ("activity", "LONG", "Activity", None),
    ("friction", "LONG", "Civic Friction", None),
    ("trust", "LONG", "Trust", None),
    ("exposure", "LONG", "Exposure", None),
    ("services", "LONG", "Services", None),
    ("district_type", "TEXT", "Hidden District Type", 32),
    ("prior_district_type", "TEXT", "Prior District Type", 32),
    ("identity_state", "TEXT", "Identity State", 32),
    ("contesting_cell_id", "TEXT", "Contesting District ID", 32),
    ("contesting_type", "TEXT", "Contesting Type", 32),
    ("transition_due_turn", "LONG", "Transition Due Week", None),
    ("buyout_pressure", "LONG", "Buyout Pressure", None),
    ("last_buyout_report", "TEXT", "Last Buyout Report", 512),
    ("land_use", "TEXT", "Land Use", 32),
    ("zoning_overlay", "TEXT", "Zoning Overlay", 32),
    ("display_state", "TEXT", "Display State", 32),
    ("prosperity_band", "TEXT", "Prosperity Band", 16),
    ("service_gap_json", "TEXT", "Service Gaps", 1024),
    ("adjacent_cell_ids", "TEXT", "Adjacent District IDs", 512),
    ("network_access_json", "TEXT", "Network Access", 1024),
    ("hazard_json", "TEXT", "Hazards", 1024),
    ("housing_capacity", "LONG", "Housing Capacity", None),
    ("affordability", "LONG", "Affordability", None),
    ("vacancy_rate", "SHORT", "Vacancy Rate", None),
    ("displacement_json", "TEXT", "Displacement", 1024),
    ("population_mix_json", "TEXT", "Population Mix", 1024),
    ("dissatisfaction_json", "TEXT", "Dissatisfaction", 1024),
    ("incident_state", "TEXT", "Civic Incident State", 32),
    ("incident_group", "TEXT", "Civic Incident Group", 32),
    ("public_profile", "TEXT", "Public Profile", 512),
    ("last_report", "TEXT", "Last Report", 512),
]

SUPPORT_FIELDS = [
    ("feature_id", "TEXT", "Feature ID", 64),
    ("item_id", "TEXT", "Docket Item ID", 96),
    ("template_id", "TEXT", "Template ID", 64),
    ("project_id", "TEXT", "Project ID", 96),
    ("chain_step_id", "TEXT", "Project Step ID", 64),
    ("feature_name", "TEXT", "Feature Name", 128),
    ("feature_type", "TEXT", "Feature Type", 32),
    ("archetype_id", "TEXT", "Archetype ID", 64),
    ("family", "TEXT", "Feature Family", 32),
    ("service_type", "TEXT", "Service Type", 32),
    ("network_type", "TEXT", "Network Type", 32),
    ("coverage_radius_m", "DOUBLE", "Coverage Radius Meters", None),
    ("capacity", "LONG", "Capacity", None),
    ("land_use", "TEXT", "Land Use", 32),
    ("incident_type", "TEXT", "Incident Type", 32),
    ("owner_group", "TEXT", "Owner Group", 64),
    ("intensity", "LONG", "Intensity", None),
    ("metadata_json", "TEXT", "Metadata JSON", 2048),
    ("hazard_summary", "TEXT", "Hazard Summary", 512),
    ("mitigation_summary", "TEXT", "Mitigation Summary", 512),
    ("status", "TEXT", "Status", 32),
    ("turn_created", "LONG", "Turn Created", None),
    ("expires_turn", "LONG", "Expires Turn", None),
    ("target_cell_ids", "TEXT", "Target District IDs", 512),
    ("display_state", "TEXT", "Display State", 32),
    ("report", "TEXT", "Report", 1024),
    ("condition", "LONG", "Condition", None),
    ("maintenance_due_turn", "LONG", "Maintenance Due Turn", None),
    ("last_maintained_turn", "LONG", "Last Maintained Turn", None),
    ("state_json", "TEXT", "Feature State JSON", 4000),
]

DOCKET_FIELDS = [
    ("item_id", "TEXT", "Docket Item ID", 96),
    ("turn", "LONG", "Turn", None),
    ("template_id", "TEXT", "Template ID", 64),
    ("title", "TEXT", "Title", 128),
    ("geometry_type", "TEXT", "Geometry Type", 16),
    ("status", "TEXT", "Status", 32),
    ("inspected", "SHORT", "Inspected", None),
    ("target_cell_ids", "TEXT", "Target District IDs", 512),
    ("preview_text", "TEXT", "Preview Text", 2048),
    ("risk_band", "TEXT", "Risk Band", 32),
    ("carryover", "TEXT", "Carryover Rule", 64),
    ("stakeholder", "TEXT", "Stakeholder", 64),
    ("origin_item_id", "TEXT", "Origin Item ID", 96),
    ("target_rule", "TEXT", "Target Rule", 512),
    ("project_id", "TEXT", "Project ID", 96),
    ("chain_step_id", "TEXT", "Project Step ID", 64),
    ("scenario_tags", "TEXT", "Scenario Tags", 256),
    ("priority", "LONG", "Priority", None),
    ("due_turn", "LONG", "Due Turn", None),
    ("subject_feature_id", "TEXT", "Subject Feature ID", 64),
    ("case_json", "TEXT", "Case JSON", 4000),
]

STATE_FIELDS = [
    ("key", "TEXT", "Key", 64),
    ("value_text", "TEXT", "Value Text", 2048),
    ("value_num", "DOUBLE", "Value Number", None),
]

PROJECT_FIELDS = [
    ("project_id", "TEXT", "Project ID", 96),
    ("chain_template_id", "TEXT", "Chain Template ID", 96),
    ("current_step_id", "TEXT", "Current Step ID", 64),
    ("status", "TEXT", "Status", 32),
    ("turn_started", "LONG", "Turn Started", None),
    ("due_turn", "LONG", "Due Turn", None),
    ("stakeholder", "TEXT", "Stakeholder", 64),
    ("target_cell_ids", "TEXT", "Target District IDs", 512),
    ("payload_json", "TEXT", "Payload JSON", 4000),
    ("last_report", "TEXT", "Last Report", 1024),
]

COMMAND_FIELDS = [
    ("command_id", "TEXT", "Command ID", 64),
    ("created_utc", "DATE", "Created UTC", None),
    ("finished_utc", "DATE", "Finished UTC", None),
    ("action", "TEXT", "Action", 64),
    ("item_id", "TEXT", "Docket Item ID", 96),
    ("status", "TEXT", "Status", 32),
    ("attempt_count", "LONG", "Attempt Count", None),
    ("target_cell_ids", "TEXT", "Target District IDs", 512),
    ("payload_json", "TEXT", "Payload JSON", 4000),
    ("message", "TEXT", "Message", 1024),
    ("error_message", "TEXT", "Error Message", 1024),
]

ACTION_LOG_FIELDS = [
    ("created_utc", "DATE", "Created UTC", None),
    ("turn", "LONG", "Turn", None),
    ("action", "TEXT", "Action", 64),
    ("item_id", "TEXT", "Docket Item ID", 96),
    ("target_cell_ids", "TEXT", "Target District IDs", 512),
    ("result", "TEXT", "Result", 2048),
    ("city_delta", "TEXT", "City Delta", 512),
]

SCHEMA_META_FIELDS = [
    ("key", "TEXT", "Key", 64),
    ("value_text", "TEXT", "Value Text", 2048),
]

LEGACY_CITY_HEALTH_FIELD_MIGRATIONS = {
    "prosperity": "activity",
    "unrest": "friction",
    "culture": "trust",
    "risk": "exposure",
}


def schema_paths(gdb_path):
    """Return the canonical dataset paths for a Permit Office geodatabase."""

    return {
        "districts": os.path.join(gdb_path, DISTRICTS),
        "points": os.path.join(gdb_path, POINTS),
        "lines": os.path.join(gdb_path, LINES),
        "zones": os.path.join(gdb_path, ZONES),
        "docket": os.path.join(gdb_path, DOCKET),
        "state": os.path.join(gdb_path, GAME_STATE),
        "projects": os.path.join(gdb_path, PROJECTS),
        "commands": os.path.join(gdb_path, COMMANDS),
        "action_log": os.path.join(gdb_path, ACTION_LOG),
        "schema_meta": os.path.join(gdb_path, SCHEMA_META),
    }


def resolve_workspace(value, messages):
    """Resolve the geodatabase path from user input, project home, or scratch.

    A workspace supplied via the optional tool parameter is honored verbatim and
    the project/scratch default chain is NEVER consulted -- even when the target
    has no game rows yet (an empty workspace starts a new game there). The default
    chain is reached only when no real workspace was given. ``""``, whitespace,
    and the ArcGIS ``"#"`` unspecified sentinel all count as "not provided".
    """

    text = str(value).strip() if value is not None else ""
    if text and text != "#":
        if not text.lower().endswith(".gdb"):
            text = os.path.join(text, DEFAULT_GDB_NAME)
        _log(messages, "WORKSPACE", f"using provided game workspace: {text}")
        return text
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        if aprx.homeFolder:
            home = os.path.join(aprx.homeFolder, "data", DEFAULT_GDB_NAME)
            _log(messages, "WORKSPACE", f"no workspace given; using project default: {home}")
            return home
    except Exception as exc:
        _warn(messages, "WORKSPACE", f"ArcGISProject('CURRENT') failed: {exc}")
    scratch = arcpy.env.scratchWorkspace or arcpy.env.scratchFolder or os.getcwd()
    fallback = str(scratch) if str(scratch).lower().endswith(".gdb") else os.path.join(str(scratch), DEFAULT_GDB_NAME)
    _log(messages, "WORKSPACE", f"no workspace or project home; using scratch: {fallback}")
    return fallback


def ensure_gdb(gdb_path, messages):
    """Create the target file geodatabase when it does not already exist."""

    folder = os.path.dirname(gdb_path)
    name = os.path.basename(gdb_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if not arcpy.Exists(gdb_path):
        arcpy.management.CreateFileGDB(folder or os.getcwd(), name)
        _log(messages, "SCHEMA", f"created gdb: {gdb_path}")
    return gdb_path


def add_field_if_missing(table, name, field_type, alias=None, length=None):
    """Add one ArcGIS field if the table does not already contain it."""

    existing = {field.name.lower() for field in arcpy.ListFields(table)}
    if name.lower() in existing:
        return False
    kwargs = {}
    if alias:
        kwargs["field_alias"] = alias
    if length is not None and field_type.upper() == "TEXT":
        kwargs["field_length"] = length
    arcpy.management.AddField(table, name, field_type, **kwargs)
    return True


def _spatial_reference_wkid(spatial_ref):
    """Return a usable WKID from an ArcPy SpatialReference-like object."""

    for attr in ("factoryCode", "wkid", "latestWkid"):
        value = getattr(spatial_ref, attr, 0)
        try:
            wkid = int(value or 0)
        except (TypeError, ValueError):
            wkid = 0
        if wkid:
            return wkid
    return 0


def normalized_spatial_reference(spatial_ref, messages=None):
    """Return a freshly constructed SR safe for CreateFeatureclass.

    ArcGIS Pro 3.3.2 can crash when the active-map SpatialReference COM object is
    passed straight into CreateFeatureclass. Use only its stable WKID and build a
    new arcpy.SpatialReference instance, falling back to Web Mercator.
    """

    wkid = _spatial_reference_wkid(spatial_ref) or WEB_MERCATOR_WKID
    if wkid == WEB_MERCATOR_WKID and _spatial_reference_wkid(spatial_ref) == 0:
        _log(messages, "MAP", "using Web Mercator (3857)")
    return arcpy.SpatialReference(wkid)


def ensure_table(gdb_path, name, fields, messages):
    """Create or update a non-spatial table with the configured fields."""

    path = os.path.join(gdb_path, name)
    if not arcpy.Exists(path):
        arcpy.management.CreateTable(gdb_path, name)
        _log(messages, "SCHEMA", f"created table: {name}")
    for field in fields:
        add_field_if_missing(path, *field)
    return path


def ensure_feature_class(gdb_path, name, geometry_type, fields, spatial_ref, messages):
    """Create or update a feature class with the configured fields."""

    path = os.path.join(gdb_path, name)
    if not arcpy.Exists(path):
        arcpy.management.CreateFeatureclass(
            gdb_path,
            name,
            geometry_type,
            spatial_reference=normalized_spatial_reference(spatial_ref, messages),
        )
        _log(messages, "SCHEMA", f"created feature class: {name}")
    for field in fields:
        add_field_if_missing(path, *field)
    return path


def migrate_legacy_city_health_fields(table, messages):
    """Backfill renamed city-health fields from legacy district columns."""

    existing = {field.name.lower(): field.name for field in arcpy.ListFields(table)}
    legacy_fields = [
        (legacy, current)
        for legacy, current in LEGACY_CITY_HEALTH_FIELD_MIGRATIONS.items()
        if legacy in existing and current in existing
    ]
    if not legacy_fields:
        return
    for legacy, current in legacy_fields:
        with arcpy.da.UpdateCursor(table, [existing[current], existing[legacy]]) as cursor:
            for row in cursor:
                if row[0] in (None, "") and row[1] not in (None, ""):
                    row[0] = row[1]
                    cursor.updateRow(row)
    try:
        arcpy.management.DeleteField(table, [existing[legacy] for legacy, _current in legacy_fields])
    except Exception as exc:
        _warn(messages, "SCHEMA", f"legacy city health fields retained: {exc}")


def active_spatial_reference(messages):
    """Use the active ArcGIS map spatial reference, falling back to Web Mercator.

    Only a map SR with a real WKID (factoryCode != 0) is used; a blank/Unknown
    map coordinate system (factoryCode 0), a missing property, or any probe
    failure falls back to Web Mercator (3857). An Unknown SR is otherwise truthy
    and would propagate into feature-class creation, then crash the first linear
    `arcpy.analysis.Buffer` with a spatial-reference RuntimeError on machines
    whose active map has no coordinate system. Logs which path was taken.
    """

    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        active_map = aprx.activeMap
        sr = getattr(active_map, "spatialReference", None) if active_map else None
        wkid = _spatial_reference_wkid(sr)
        if wkid:
            _log(messages, "MAP", f"using active map spatial reference (wkid {wkid})")
            return normalized_spatial_reference(sr, messages)
        _log(messages, "MAP", "active map has no usable spatial reference; using Web Mercator (3857)")
    except Exception as exc:
        _warn(messages, "MAP", f"active map SR unavailable ({exc}); using Web Mercator (3857)")
    return arcpy.SpatialReference(WEB_MERCATOR_WKID)


def _schema_marker_current(state_path):
    """Return True when the additive schema marker matches this code version."""

    with arcpy.da.SearchCursor(state_path, ["key", "value_text"]) as cursor:
        for key, value_text in cursor:
            if key == SCHEMA_VERSION_KEY:
                return str(value_text or "") == SCHEMA_VERSION
    return False


def _schema_fast_path_current(paths, messages):
    """Return True when expected datasets and the schema marker are present."""

    if not all(arcpy.Exists(path) for path in paths.values()):
        return False
    try:
        if _schema_marker_current(paths["schema_meta"]):
            _log(messages, "SCHEMA", f"fast path schema={SCHEMA_VERSION}")
            return True
    except Exception as exc:
        _warn(messages, "SCHEMA", f"fast path unavailable: {exc}")
    return False


def _mark_schema_current(paths, messages):
    """Persist the current additive schema marker in the game-state table."""

    try:
        with arcpy.da.UpdateCursor(paths["schema_meta"], ["key", "value_text"]) as cursor:
            for row in cursor:
                if row[0] == SCHEMA_VERSION_KEY:
                    row[1] = SCHEMA_VERSION
                    cursor.updateRow(row)
                    return
        with arcpy.da.InsertCursor(paths["schema_meta"], ["key", "value_text"]) as cursor:
            cursor.insertRow([SCHEMA_VERSION_KEY, SCHEMA_VERSION])
    except Exception as exc:
        _warn(messages, "SCHEMA", f"schema marker not written: {exc}")


def ensure_schema(gdb_path, messages):
    """Ensure all active feature classes and tables exist in the game geodatabase."""

    ensure_gdb(gdb_path, messages)
    paths = schema_paths(gdb_path)
    if _schema_fast_path_current(paths, messages):
        return paths
    sr = active_spatial_reference(messages)
    paths = {
        "districts": ensure_feature_class(gdb_path, DISTRICTS, "POLYGON", DISTRICT_FIELDS, sr, messages),
        "points": ensure_feature_class(gdb_path, POINTS, "POINT", SUPPORT_FIELDS, sr, messages),
        "lines": ensure_feature_class(gdb_path, LINES, "POLYLINE", SUPPORT_FIELDS, sr, messages),
        "zones": ensure_feature_class(gdb_path, ZONES, "POLYGON", SUPPORT_FIELDS, sr, messages),
        "docket": ensure_table(gdb_path, DOCKET, DOCKET_FIELDS, messages),
        "state": ensure_table(gdb_path, GAME_STATE, STATE_FIELDS, messages),
        "projects": ensure_table(gdb_path, PROJECTS, PROJECT_FIELDS, messages),
        "commands": ensure_table(gdb_path, COMMANDS, COMMAND_FIELDS, messages),
        "action_log": ensure_table(gdb_path, ACTION_LOG, ACTION_LOG_FIELDS, messages),
        "schema_meta": ensure_table(gdb_path, SCHEMA_META, SCHEMA_META_FIELDS, messages),
    }
    migrate_legacy_city_health_fields(paths["districts"], messages)
    _mark_schema_current(paths, messages)
    return paths


def clear_game_rows(paths):
    """Delete gameplay rows while preserving the existing geodatabase schema."""

    for key in ("districts", "points", "lines", "zones", "docket", "state", "projects", "commands", "action_log"):
        if arcpy.Exists(paths[key]):
            arcpy.management.DeleteRows(paths[key])
