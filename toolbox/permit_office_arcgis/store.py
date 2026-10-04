"""ArcGIS persistence helpers for Permit Office state, features, and commands."""

from __future__ import annotations

import datetime as _datetime
import json
import uuid
from typing import Callable, NamedTuple
from typing import Callable, NamedTuple

import arcpy

from ._perf import perf_traced
from .messages import _log, _warn
from .rules_loader import rules
from .schema import DOCKET_FIELDS, JSON_TEXT_LENGTH, PROJECT_FIELDS, text_field_length


DOCKET_FIELD_NAMES = [field[0] for field in DOCKET_FIELDS]
DISTRICT_JSON_FIELDS = ("network_access_json", "hazard_json", "displacement_json")
DOCKET_UPDATE_FIELDS = [
    name
    for name in DOCKET_FIELD_NAMES
    if name not in {"turn", "template_id", "title", "geometry_type"}
]

LEGACY_STATE_METRIC_KEYS = {
    "prosperity": "activity",
    "unrest": "friction",
    "culture": "trust",
    "risk": "exposure",
}


def now_utc():
    """Return the current UTC timestamp for command and action rows."""

    return _datetime.datetime.utcnow()


def _sql_quote(value):
    """Quote a text literal for a simple file-geodatabase SQL where clause."""

    return "'{0}'".format(str(value).replace("'", "''"))


def _delete_all_rows(path):
    """Empty a table with an UpdateCursor instead of the DeleteRows GP tool.

    A GP tool call costs far more than a cursor on these small tables, and
    these writes run on every decision. clear_game_rows (New Game) keeps
    DeleteRows.
    """

    with arcpy.da.UpdateCursor(path, ["OID@"]) as cursor:
        for _row in cursor:
            cursor.deleteRow()


def square_polygon(x0, y0, size, sr):
    """Build a square district polygon in the target spatial reference."""

    arr = arcpy.Array([
        arcpy.Point(x0, y0),
        arcpy.Point(x0 + size, y0),
        arcpy.Point(x0 + size, y0 + size),
        arcpy.Point(x0, y0 + size),
        arcpy.Point(x0, y0),
    ])
    return arcpy.Polygon(arr, sr)


def create_district_board(paths, seed, messages):
    """Generate and persist a fresh deterministic district board."""

    sr = arcpy.Describe(paths["districts"]).spatialReference
    profiles = rules.generate_district_profiles(rows=5, cols=5, seed=seed)
    # The generated rule profiles are flattened into ArcGIS field values so the
    # map layer remains the persisted source for the current board.
    fields = ["SHAPE@"] + DISTRICT_FIELD_NAMES + ["last_report"]
    limits = _json_limits(paths["districts"], DISTRICT_JSON_FIELDS)
    with arcpy.da.InsertCursor(paths["districts"], fields) as cursor:
        for profile in profiles:
            row = int(profile.cell_id[1:3])
            col = int(profile.cell_id[3:5])
            values = _encode_district(profile, DISTRICT_CODEC, limits)
            values["SHAPE@"] = square_polygon(col * 100.0, row * 100.0, 96.0, sr)
            values["last_report"] = "New district profile generated."
            cursor.insertRow([values[field] for field in fields])
    _log(messages, "NEW", f"inserted {len(profiles)} districts")


@perf_traced("write_state")
def write_state(paths, state):
    """Persist city state as key/value rows for ArcGIS-friendly storage."""

    limit = json_field_limit(paths["state"], "value_text")

    def as_json(key, value):
        """Return a (value_text, value_num) pair for one JSON state key."""

        return (encode_json(value, limit=limit, field=f"state.{key}"), None)

    values = {
        "turn": (str(state.turn), state.turn),
        "max_turns": (str(state.max_turns), state.max_turns),
        "ap": (str(state.ap), state.ap),
        "max_ap": (str(state.max_ap), state.max_ap),
        "money": (str(state.money), state.money),
        "audit_rung": (str(state.audit_rung), state.audit_rung),
        "status": (state.status, None),
        "outcome": (state.outcome, None),
        "last_report": (state.last_report, None),
        "activity": (str(state.activity), state.activity),
        "friction": (str(state.friction), state.friction),
        "trust": (str(state.trust), state.trust),
        "exposure": (str(state.exposure), state.exposure),
        "scenario_id": (state.scenario_id, None),
        "stakeholder_heat": as_json("stakeholder_heat", state.stakeholder_heat),
        "last_revenue": (str(state.last_revenue), state.last_revenue),
        "last_upkeep": (str(state.last_upkeep), state.last_upkeep),
        "last_net": (str(state.last_net), state.last_net),
        "maintenance_backlog": (str(state.maintenance_backlog), state.maintenance_backlog),
        "stakeholder_memory": as_json("stakeholder_memory", state.stakeholder_memory),
        "type_ledger": as_json("type_ledger", state.type_ledger),
        "pending_followups": as_json("pending_followups", state.pending_followups),
        "week_day": (str(getattr(state, "week_day", 0)), getattr(state, "week_day", 0)),
        "daily_pressure": as_json("daily_pressure", getattr(state, "daily_pressure", {})),
        "mandate": as_json("mandate", getattr(state, "mandate", {})),
        "initiatives": as_json("initiatives", getattr(state, "initiatives", {})),
    }
    _delete_all_rows(paths["state"])
    with arcpy.da.InsertCursor(paths["state"], ["key", "value_text", "value_num"]) as cursor:
        for key, (text, num) in values.items():
            cursor.insertRow([key, text, num])


def read_state(paths):
    """Rehydrate city state from key/value rows."""

    values = {}
    with arcpy.da.SearchCursor(paths["state"], ["key", "value_text", "value_num"]) as cursor:
        for key, text, num in cursor:
            values[key] = (text, num)
    for legacy, current in LEGACY_STATE_METRIC_KEYS.items():
        if current not in values and legacy in values:
            values[current] = values[legacy]
    state = rules.CityState()
    for key in ("turn", "max_turns", "ap", "max_ap", "money", "audit_rung", "activity", "friction", "trust", "exposure", "last_revenue", "last_upkeep", "last_net", "maintenance_backlog", "week_day"):
        entry = values.get(key)
        if entry is not None and entry[1] is not None:
            setattr(state, key, int(entry[1]))
    for key in ("status", "last_report", "scenario_id", "outcome"):
        entry = values.get(key)
        if entry is not None:
            setattr(state, key, entry[0] or ("default" if key == "scenario_id" else ""))
    default_max_turns = rules.CityState().max_turns
    if state.status == "playing" and state.max_turns < default_max_turns:
        state.max_turns = default_max_turns
    if "stakeholder_heat" in values and values["stakeholder_heat"][0]:
        try:
            parsed = json.loads(values["stakeholder_heat"][0])
            state.stakeholder_heat = {str(key): int(value) for key, value in parsed.items()}
        except Exception:
            state.stakeholder_heat = {}
    if "stakeholder_memory" in values and values["stakeholder_memory"][0]:
        try:
            parsed = json.loads(values["stakeholder_memory"][0])
            state.stakeholder_memory = {str(key): int(value) for key, value in parsed.items()}
        except Exception:
            state.stakeholder_memory = {}
    if "type_ledger" in values and values["type_ledger"][0]:
        try:
            parsed = json.loads(values["type_ledger"][0])
            rules.write_type_ledger(state, parsed)
        except Exception:
            state.type_ledger = {}
    if "pending_followups" in values and values["pending_followups"][0]:
        try:
            parsed = json.loads(values["pending_followups"][0])
            state.pending_followups = {str(key): str(value) for key, value in parsed.items()}
        except Exception:
            state.pending_followups = {}
    if "daily_pressure" in values and values["daily_pressure"][0]:
        try:
            parsed = json.loads(values["daily_pressure"][0])
            state.daily_pressure = {str(key): max(0, min(4, int(value))) for key, value in parsed.items() if int(value) > 0}
        except Exception:
            state.daily_pressure = {}
    if "mandate" in values and values["mandate"][0]:
        try:
            parsed = json.loads(values["mandate"][0])
            state.mandate = parsed if isinstance(parsed, dict) else {}
        except Exception:
            state.mandate = {}
    if "initiatives" in values and values["initiatives"][0]:
        try:
            parsed = json.loads(values["initiatives"][0])
            state.initiatives = parsed if isinstance(parsed, dict) else {}
        except Exception:
            state.initiatives = {}
    return state


def encode_group_bands(value, maximum=4):
    """Encode population or dissatisfaction bands as compact clamped JSON."""

    return json.dumps(_clamped_int_map(value, rules.CITIZEN_GROUPS, maximum=maximum), sort_keys=True)


def decode_group_bands(text, maximum=4):
    """Decode group band JSON while discarding unknown groups."""

    return _clamped_int_map(_decode_json_dict(text), rules.CITIZEN_GROUPS, maximum=maximum)


def encode_service_gap(value):
    """Encode service gaps as compact JSON for text fields."""

    return json.dumps(_clamped_int_map(value, rules.SERVICE_TYPES), sort_keys=True)


def decode_service_gap(text):
    """Decode service-gap JSON while discarding unknown services."""

    return _clamped_int_map(_decode_json_dict(text), rules.SERVICE_TYPES)


def encode_json(value, limit=JSON_TEXT_LENGTH, field="json", messages=None):
    """Encode a dictionary for a bounded ArcGIS text field, never cutting it.

    Slicing JSON leaves invalid text that decodes to {} with no trace. An
    oversized payload is instead logged by field and size and stored as {}.
    """

    text = json.dumps(value or {}, sort_keys=True)
    if len(text) <= limit:
        return text
    _warn(messages, "STORE", f"{field} payload is {len(text)} chars, over its {limit}-char field; stored {{}} instead")
    return "{}"


def json_field_limit(table, field):
    """Return the real width of a JSON text field, or the new-save width."""

    return text_field_length(table, field, JSON_TEXT_LENGTH)


def _json_limits(table, fields):
    """Return {field: width} for the JSON fields a writer fills on one table."""

    return {field: json_field_limit(table, field) for field in fields}


def decode_json(text):
    """Decode optional JSON text, returning an empty dict for invalid values."""

    return _decode_json_dict(text)


def _decode_json_dict(text):
    """Decode ArcGIS text JSON, accepting only dictionary payloads."""

    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clamped_int_map(value, allowed, maximum=None):
    """Return sparse non-negative ints for allow-listed text-field maps."""

    allowed_keys = set(allowed)
    out = {}
    for key, raw in (value or {}).items():
        if key not in allowed_keys:
            continue
        try:
            amount = int(raw or 0)
        except (TypeError, ValueError):
            continue
        amount = max(0, amount)
        if maximum is not None:
            amount = min(maximum, amount)
        if amount:
            out[key] = amount
    return dict(sorted(out.items()))


class DistrictField(NamedTuple):
    """One district column: its rule attribute and how it crosses the cursor."""

    field: str
    attr: str
    encode: Callable
    decode: Callable
    default: object = None
    on_update: bool = True


def _same(value, _limit=None):
    """Pass a value through unchanged."""

    return value


def _as_int(value):
    """Decode a numeric column as an int."""

    return int(value)


def _json_codec(field):
    """Return an encoder that writes a JSON map within the field's real width."""

    return lambda value, limit: encode_json(value, limit=limit, field=field)


def _cell_list(value):
    """Decode a comma-separated district id list."""

    return [part for part in value.split(",") if part]


# One row per persisted district column. read_districts, write_district_updates,
# and create_district_board all run over this table, so a new column is one
# line. A blank or null cell reads as `default` (if any) before decoding. cell_id is
# fixed after New Game; district_name changes when a buyout converts a district
# (ADR-9). last_report is written separately because updates change it only for
# affected districts.
DISTRICT_CODEC = (
    DistrictField("cell_id", "cell_id", _same, _same, on_update=False),
    DistrictField("district_name", "name", _same, _same),
    DistrictField("population", "population", _same, _as_int, 0),
    DistrictField("activity", "activity", _same, _as_int, 0),
    DistrictField("friction", "friction", _same, _as_int, 0),
    DistrictField("trust", "trust", _same, _as_int, 0),
    DistrictField("exposure", "exposure", _same, _as_int, 0),
    DistrictField("services", "services", _same, _as_int, 0),
    DistrictField("district_type", "district_type", _same, _same, "mercantile"),
    DistrictField("prior_district_type", "prior_district_type", _same, _same, ""),
    DistrictField("identity_state", "identity_state", _same, _same, "stable"),
    DistrictField("contesting_cell_id", "contesting_cell_id", _same, _same, ""),
    DistrictField("contesting_type", "contesting_type", _same, _same, ""),
    DistrictField("transition_due_turn", "transition_due_turn", _same, _as_int, 0),
    DistrictField("buyout_pressure", "buyout_pressure", _same, _as_int, 0),
    DistrictField("last_buyout_report", "last_buyout_report", lambda value, _limit: value[:512], _same, ""),
    DistrictField("land_use", "land_use", _same, _same, ""),
    DistrictField("zoning_overlay", "zoning_overlay", _same, _same, ""),
    DistrictField("display_state", "display_state", _same, _same, "stable"),
    DistrictField("service_gap_json", "service_gap", lambda value, _limit: encode_service_gap(value), decode_service_gap, ""),
    DistrictField("adjacent_cell_ids", "adjacent_cell_ids", lambda value, _limit: ",".join(value), _cell_list, ""),
    DistrictField("network_access_json", "network_access", _json_codec("network_access_json"), decode_json, ""),
    DistrictField("hazard_json", "hazards", _json_codec("hazard_json"), decode_json, ""),
    DistrictField("housing_capacity", "housing_capacity", _same, _as_int, 0),
    DistrictField("affordability", "affordability", _same, _as_int, 0),
    DistrictField("vacancy_rate", "vacancy_rate", _same, _as_int, 0),
    DistrictField("displacement_json", "displacement", _json_codec("displacement_json"), decode_json, ""),
    DistrictField(
        "population_mix_json",
        "population_mix",
        lambda value, _limit: encode_group_bands(value, maximum=3),
        lambda text: decode_group_bands(text, maximum=3),
        "",
    ),
    DistrictField(
        "dissatisfaction_json",
        "dissatisfaction",
        lambda value, _limit: encode_group_bands(value, maximum=4),
        lambda text: decode_group_bands(text, maximum=4),
        "",
    ),
    DistrictField("incident_state", "incident_state", _same, _same, "none"),
    DistrictField("incident_group", "incident_group", _same, _same, ""),
    DistrictField("public_profile", "public_profile", _same, _same, ""),
    DistrictField("prosperity_band", "prosperity_band", _same, _same, "stable"),
)
DISTRICT_CODEC_FIELDS = [spec.field for spec in DISTRICT_CODEC]


def _encode_district(profile, specs, limits):
    """Return {field: cursor value} for a profile over the given codec rows."""

    return {spec.field: spec.encode(getattr(profile, spec.attr), limits.get(spec.field)) for spec in specs}


def _decode_district_value(spec, raw):
    """Decode one district cell, reading blank or null as the codec default.

    A default of None means the column has none and passes through raw.
    """

    if spec.default is not None and raw in (None, ""):
        raw = spec.default
    return spec.decode(raw)


class DistrictField(NamedTuple):
    """One district column: its profile attribute, codec, and blank default.

    encode(value, limit) gets the save's real field width, which only the JSON
    encoders use. read() swaps a blank cell for default before decoding.
    """

    field: str
    attr: str
    encode: Callable
    decode: Callable
    default: object = None
    on_update: bool = True

    def read(self, raw):
        """Decode one cell, using default when it is None or empty."""

        return self.decode(self.default if raw in (None, "") else raw)


def _same(value, _limit=None):
    """Pass a value through unchanged."""

    return value


def _json_codec(field):
    """Return an encoder that fits dict JSON to the field's real width."""

    return lambda value, limit: encode_json(value, limit=limit, field=field)


def _cell_ids(value):
    """Split a comma-joined district id list."""

    return [part for part in value.split(",") if part]


# One row per persisted district column. A new column is one line here plus its
# schema entry. cell_id is fixed after New Game; district_name is rewritten
# because a buyout conversion renames the district (ADR-9). last_report is
# written separately (per-decision report text).
DISTRICT_CODEC = (
    DistrictField("cell_id", "cell_id", _same, _same, on_update=False),
    DistrictField("district_name", "name", _same, _same),
    DistrictField("population", "population", _same, int, 0),
    DistrictField("activity", "activity", _same, int, 0),
    DistrictField("friction", "friction", _same, int, 0),
    DistrictField("trust", "trust", _same, int, 0),
    DistrictField("exposure", "exposure", _same, int, 0),
    DistrictField("services", "services", _same, int, 0),
    DistrictField("district_type", "district_type", _same, _same, "mercantile"),
    DistrictField("prior_district_type", "prior_district_type", _same, _same, ""),
    DistrictField("identity_state", "identity_state", _same, _same, "stable"),
    DistrictField("contesting_cell_id", "contesting_cell_id", _same, _same, ""),
    DistrictField("contesting_type", "contesting_type", _same, _same, ""),
    DistrictField("transition_due_turn", "transition_due_turn", _same, int, 0),
    DistrictField("buyout_pressure", "buyout_pressure", _same, int, 0),
    DistrictField("last_buyout_report", "last_buyout_report", lambda value, _limit: value[:512], _same, ""),
    DistrictField("land_use", "land_use", _same, _same, ""),
    DistrictField("zoning_overlay", "zoning_overlay", _same, _same, ""),
    DistrictField("display_state", "display_state", _same, _same, "stable"),
    DistrictField("service_gap_json", "service_gap", lambda value, _limit: encode_service_gap(value), decode_service_gap, ""),
    DistrictField("adjacent_cell_ids", "adjacent_cell_ids", lambda value, _limit: ",".join(value), _cell_ids, ""),
    DistrictField("network_access_json", "network_access", _json_codec("network_access_json"), decode_json, ""),
    DistrictField("hazard_json", "hazards", _json_codec("hazard_json"), decode_json, ""),
    DistrictField("housing_capacity", "housing_capacity", _same, int, 0),
    DistrictField("affordability", "affordability", _same, int, 0),
    DistrictField("vacancy_rate", "vacancy_rate", _same, int, 0),
    DistrictField("displacement_json", "displacement", _json_codec("displacement_json"), decode_json, ""),
    DistrictField(
        "population_mix_json",
        "population_mix",
        lambda value, _limit: encode_group_bands(value, maximum=3),
        lambda text: decode_group_bands(text, maximum=3),
        "",
    ),
    DistrictField(
        "dissatisfaction_json",
        "dissatisfaction",
        lambda value, _limit: encode_group_bands(value, maximum=4),
        lambda text: decode_group_bands(text, maximum=4),
        "",
    ),
    DistrictField("incident_state", "incident_state", _same, _same, "none"),
    DistrictField("incident_group", "incident_group", _same, _same, ""),
    DistrictField("public_profile", "public_profile", _same, _same, ""),
    DistrictField("prosperity_band", "prosperity_band", _same, _same, "stable"),
)
DISTRICT_FIELD_NAMES = [spec.field for spec in DISTRICT_CODEC]
DISTRICT_UPDATE_CODEC = tuple(spec for spec in DISTRICT_CODEC if spec.on_update)


def _encode_district(profile, codec, limits):
    """Return {field: stored value} for one profile over the given codec rows."""

    return {spec.field: spec.encode(getattr(profile, spec.attr), limits.get(spec.field)) for spec in codec}


def read_districts(paths):
    """Read district feature rows into normalized rule profiles."""

    out = {}
    # ArcGIS stores nested fields as delimited or JSON text; decode them back
    # into rule dataclasses before normalizing derived fields.
    with arcpy.da.SearchCursor(paths["districts"], DISTRICT_FIELD_NAMES) as cursor:
        for row in cursor:
            values = dict(zip(DISTRICT_FIELD_NAMES, row))
            profile = rules.DistrictProfile(**{spec.attr: spec.read(values[spec.field]) for spec in DISTRICT_CODEC})
            rules.normalize_profile(profile)
            out[profile.cell_id] = profile
    return out


@perf_traced("write_district_updates")
def write_district_updates(paths, districts, report, affected_ids=None):
    """Persist profiles and return whether built-in district display fields changed."""

    affected = set(affected_ids or districts)
    # Every district row is refreshed from normalized state, while last_report is
    # only changed for affected districts so unrelated map notes survive.
    fields = ["cell_id"] + [spec.field for spec in DISTRICT_UPDATE_CODEC] + ["last_report"]
    limits = _json_limits(paths["districts"], DISTRICT_JSON_FIELDS)
    display_changed = False
    display_fields = ("district_name", "district_type", "display_state", "prosperity_band", "identity_state")
    with arcpy.da.UpdateCursor(paths["districts"], fields) as cursor:
        for row in cursor:
            values = dict(zip(fields, row))
            cid = values["cell_id"]
            if cid not in districts:
                continue
            profile = districts[cid]
            rules.normalize_profile(profile)
            encoded = _encode_district(profile, DISTRICT_UPDATE_CODEC, limits)
            display_changed |= any(values.get(field) != encoded.get(field) for field in display_fields)
            values.update(encoded)
            if cid in affected:
                values["last_report"] = report[:512]
            cursor.updateRow([values[field] for field in fields])
    return display_changed




def read_active_features(paths):
    """Read active support features from point, line, and polygon classes."""

    features = []
    # Support features live in separate geometry classes but share the same
    # attribute contract, so collect them into one lifecycle list.
    fields = [
        "feature_id",
        "item_id",
        "template_id",
        "project_id",
        "chain_step_id",
        "archetype_id",
        "family",
        "service_type",
        "network_type",
        "owner_group",
        "target_cell_ids",
        "capacity",
        "intensity",
        "status",
        "turn_created",
        "expires_turn",
        "display_state",
        "metadata_json",
        "hazard_summary",
        "mitigation_summary",
        "condition",
        "maintenance_due_turn",
        "last_maintained_turn",
        "state_json",
    ]
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        with arcpy.da.SearchCursor(fc, fields) as cursor:
            for row in cursor:
                feature = rules.FeatureInstance(
                    feature_id=row[0],
                    item_id=row[1] or "",
                    template_id=row[2] or "",
                    project_id=row[3] or "",
                    chain_step_id=row[4] or "",
                    archetype_id=row[5] or "",
                    family=row[6] or "",
                    service_type=row[7] or "",
                    network_type=row[8] or "",
                    owner_group=row[9] or "",
                    target_cell_ids=[part for part in (row[10] or "").split(",") if part],
                    capacity=int(row[11] or 0),
                    intensity=int(row[12] or 1),
                    status=row[13] or "active",
                    turn_created=int(row[14] or 1),
                    expires_turn=int(row[15] if row[15] not in (None, "") else -1),
                    display_state=row[16] or "",
                    metadata=decode_json(row[17]),
                    condition=int(row[20] if row[20] not in (None, "") else 100),
                    maintenance_due_turn=int(row[21] if row[21] not in (None, "") else -1),
                    last_maintained_turn=int(row[22] or 0),
                    state_json=decode_json(row[23]),
                )
                rules.normalize_feature_instance(feature)
                features.append(feature)
    return features


@perf_traced("write_active_features")
def write_active_features(paths, features):
    """Persist lifecycle fields for existing support features."""

    by_id = {feature.feature_id: feature for feature in features}
    # Lifecycle writes intentionally update only mutable runtime fields, leaving
    # geometry and immutable permit metadata untouched.
    fields = [
        "feature_id",
        "status",
        "display_state",
        "condition",
        "maintenance_due_turn",
        "last_maintained_turn",
        "state_json",
        "metadata_json",
        "report",
    ]
    for fc in (paths["points"], paths["lines"], paths["zones"]):
        limits = _json_limits(fc, ("state_json", "metadata_json"))
        with arcpy.da.UpdateCursor(fc, fields) as cursor:
            for row in cursor:
                feature = by_id.get(row[0])
                if not feature:
                    continue
                rules.normalize_feature_instance(feature)
                row[1] = feature.status
                row[2] = feature.display_state
                row[3] = feature.condition
                row[4] = feature.maintenance_due_turn
                row[5] = feature.last_maintained_turn
                row[6] = encode_json(feature.state_json, limit=limits["state_json"], field="state_json")
                row[7] = encode_json(feature.metadata, limit=limits["metadata_json"], field="metadata_json")
                row[8] = f"Feature {feature.feature_id}: {feature.status}, condition {feature.condition}"[:1024]
                cursor.updateRow(row)


def read_projects(paths):
    """Read project records from the optional projects table."""

    projects = {}
    project_path = paths.get("projects")
    if not project_path or not arcpy.Exists(project_path):
        return projects
    fields = [
        "project_id",
        "chain_template_id",
        "current_step_id",
        "status",
        "turn_started",
        "due_turn",
        "stakeholder",
        "target_cell_ids",
        "payload_json",
        "last_report",
    ]
    with arcpy.da.SearchCursor(project_path, fields) as cursor:
        for row in cursor:
            project = rules.ProjectRecord(
                project_id=row[0],
                chain_template_id=row[1] or "",
                current_step_id=row[2] or "",
                status=row[3] or "active",
                turn_started=int(row[4] or 1),
                due_turn=int(row[5] or 1),
                stakeholder=row[6] or "",
                target_cell_ids=[part for part in (row[7] or "").split(",") if part],
                payload=decode_json(row[8]),
                last_report=row[9] or "",
            )
            projects[project.project_id] = project
    return projects


@perf_traced("write_projects")
def write_projects(paths, projects):
    """Replace persisted project rows with the current in-memory records."""

    project_path = paths.get("projects")
    if not project_path or not arcpy.Exists(project_path):
        return
    _delete_all_rows(project_path)
    fields = [field[0] for field in PROJECT_FIELDS]
    limit = json_field_limit(project_path, "payload_json")
    with arcpy.da.InsertCursor(project_path, fields) as cursor:
        for project in sorted(projects.values(), key=lambda item: item.project_id):
            cursor.insertRow([
                project.project_id,
                project.chain_template_id,
                project.current_step_id,
                project.status,
                project.turn_started,
                project.due_turn,
                project.stakeholder,
                ",".join(project.target_cell_ids),
                encode_json(project.payload, limit=limit, field="payload_json"),
                project.last_report[:1024],
            ])


@perf_traced("generate_docket_rows")
def generate_docket_rows(paths, seed, messages):
    """Generate the turn docket and replace the persisted docket table."""

    from .proposals import seed_docket_proposals

    state = read_state(paths)
    districts = read_districts(paths)
    active_features = read_active_features(paths)
    projects = read_projects(paths)
    carried_items = [item for item in read_docket(paths) if item.status == "carried"]
    _delete_all_rows(paths["docket"])
    if state.status == "complete" or state.turn > state.max_turns:
        _log(messages, "DOCKET", f"final audit complete; no week {state.turn + 1} docket generated")
        return []
    items = rules.generate_docket(
        turn=state.turn,
        seed=seed,
        state=state,
        districts=districts,
        projects=projects,
        active_features=active_features,
        carried_items=carried_items,
    )
    # Docket rows mirror rule items exactly enough for the dashboard to reload
    # without recomputing follow-up priority or case metadata.
    limit = json_field_limit(paths["docket"], "case_json")
    with arcpy.da.InsertCursor(paths["docket"], DOCKET_FIELD_NAMES) as cursor:
        for item in items:
            cursor.insertRow([
                item.item_id,
                item.turn,
                item.template_id,
                item.title,
                item.geometry_type,
                item.status,
                1 if item.inspected else 0,
                ",".join(item.target_cell_ids),
                item.preview_text,
                item.risk_band,
                item.carryover,
                item.stakeholder,
                item.origin_item_id,
                item.target_rule,
                item.project_id,
                item.chain_step_id,
                ",".join(rules.TEMPLATES[item.template_id].scenario_tags),
                item.priority,
                item.due_turn,
                item.subject_feature_id,
                encode_json(item.case_json, limit=limit, field="case_json", messages=messages),
            ])
    seed_docket_proposals(paths, items, seed, messages)
    write_state(paths, state)
    _log(messages, "DOCKET", f"generated {len(items)} docket item(s) for turn {state.turn}")
    return items


def read_docket(paths):
    """Read persisted docket rows into rule docket items."""

    items = []
    with arcpy.da.SearchCursor(paths["docket"], DOCKET_FIELD_NAMES) as cursor:
        for row in cursor:
            item = rules.DocketItem(
                item_id=row[0],
                template_id=row[2],
                title=row[3],
                geometry_type=row[4],
                turn=int(row[1] or 1),
                status=row[5] or "open",
                inspected=bool(row[6]),
                target_cell_ids=[part for part in (row[7] or "").split(",") if part],
                preview_text=row[8] or "",
                risk_band=row[9] or "unknown",
                carryover=row[10] or "expire_or_return",
                stakeholder=row[11] or "",
                origin_item_id=row[12] or "",
                target_rule=row[13] or "",
                project_id=row[14] or "",
                chain_step_id=row[15] or "",
                priority=int(row[17] or 0),
                due_turn=int(row[18] or 0),
                subject_feature_id=row[19] or "",
                case_json=decode_json(row[20]),
            )
            items.append(item)
    return items


@perf_traced("write_docket_item")
def write_docket_item(paths, item):
    """Persist mutable fields for one docket item."""

    # The item ID field is DOCKET_UPDATE_FIELDS[0]; push the row match into SQL so
    # the cursor scans one row instead of the whole docket table.
    where = "{0} = {1}".format(DOCKET_UPDATE_FIELDS[0], _sql_quote(item.item_id))
    with arcpy.da.UpdateCursor(paths["docket"], DOCKET_UPDATE_FIELDS, where) as cursor:
        for row in cursor:
            if row[0] != item.item_id:
                continue
            row[1] = item.status
            row[2] = 1 if item.inspected else 0
            row[3] = ",".join(item.target_cell_ids)
            row[4] = item.preview_text
            row[5] = item.risk_band
            row[6] = item.carryover
            row[7] = item.stakeholder
            row[8] = item.origin_item_id
            row[9] = item.target_rule
            row[10] = item.project_id
            row[11] = item.chain_step_id
            row[12] = ",".join(rules.TEMPLATES[item.template_id].scenario_tags)
            row[13] = item.priority
            row[14] = item.due_turn
            row[15] = item.subject_feature_id
            row[16] = encode_json(item.case_json, limit=json_field_limit(paths["docket"], "case_json"), field="case_json")
            cursor.updateRow(row)
            return


@perf_traced("command_insert")
def command_insert(paths, action, item_id, target_ids, payload=None):
    """Create a command row before a dashboard action begins."""

    command_id = str(uuid.uuid4())
    payload_text = encode_json(payload, limit=json_field_limit(paths["commands"], "payload_json"), field="command.payload_json")
    fields = ["command_id", "created_utc", "action", "item_id", "status", "attempt_count", "target_cell_ids", "payload_json", "message"]
    with arcpy.da.InsertCursor(paths["commands"], fields) as cursor:
        cursor.insertRow([command_id, now_utc(), action, item_id, "created", 0, ",".join(target_ids), payload_text, "created"])
    return command_id


@perf_traced("command_finish")
def command_finish(paths, command_id, status, message="", error=""):
    """Mark a command row finished with status and diagnostic text."""

    fields = ["command_id", "finished_utc", "status", "attempt_count", "message", "error_message"]
    # The commands table grows once per action across a 12-week game. The SQL
    # match uses the command_id attribute index that ensure_schema adds, so it
    # does not scan every prior command row.
    where = "{0} = {1}".format(fields[0], _sql_quote(command_id))
    with arcpy.da.UpdateCursor(paths["commands"], fields, where) as cursor:
        for row in cursor:
            if row[0] != command_id:
                continue
            row[1] = now_utc()
            row[2] = status
            row[3] = (row[3] or 0) + 1
            row[4] = message[:1024]
            row[5] = error[:1024]
            cursor.updateRow(row)
            return


@perf_traced("action_log")
def action_log(paths, state, result):
    """Append a compact audit trail entry for a resolved decision."""

    city_delta = encode_json(result.city_delta, limit=json_field_limit(paths["action_log"], "city_delta"), field="city_delta")
    with arcpy.da.InsertCursor(paths["action_log"], ["created_utc", "turn", "action", "item_id", "target_cell_ids", "result", "city_delta"]) as cursor:
        cursor.insertRow([
            now_utc(),
            state.turn,
            result.action,
            result.item_id,
            ",".join(result.affected_cell_ids),
            result.report[:2048],
            city_delta,
        ])
