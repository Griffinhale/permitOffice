"""Workspace-resolution coverage for the Permit Office toolbox schema layer."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
import sys


sys.modules.setdefault(
    "arcpy",
    SimpleNamespace(
        AddMessage=lambda text: None,
        AddWarning=lambda text: None,
        AddError=lambda text: None,
    ),
)

import arcpy

from toolbox.permit_office_arcgis import schema


def _raise(*_args, **_kwargs):
    """Stand-in that fails if the default-resolution path is ever consulted."""

    raise AssertionError("default workspace resolution should not be consulted")


def test_resolve_workspace_uses_provided_gdb_verbatim_without_default():
    """A provided .gdb is honored exactly; the default chain is never consulted.

    arcpy.mp / arcpy.env are absent on the test stub, so touching the project-home
    or scratch fallback would raise -- proving the provided workspace wins.
    """

    path = schema.resolve_workspace("C:/games/save.gdb", None)

    assert path == "C:/games/save.gdb"


def test_resolve_workspace_provided_folder_appends_default_gdb_name():
    """A provided folder resolves to <folder>/permit_office.gdb, not the default."""

    path = schema.resolve_workspace("C:/games/board", None)

    assert path == os.path.join("C:/games/board", schema.DEFAULT_GDB_NAME)


def test_resolve_workspace_honors_provided_workspace_with_empty_contents():
    """A provided workspace is used even when it has no game rows yet.

    This is the regression the live run flagged: an empty/new target workspace
    must not fall back to the default project geodatabase.
    """

    path = schema.resolve_workspace("D:/fresh/new_game.gdb", None)

    assert path == "D:/fresh/new_game.gdb"


def test_resolve_workspace_blank_value_falls_back_to_default(monkeypatch):
    """A blank/whitespace value is treated as 'not provided' and falls back."""

    monkeypatch.setattr(arcpy, "mp", SimpleNamespace(ArcGISProject=_raise), raising=False)
    monkeypatch.setattr(
        arcpy, "env", SimpleNamespace(scratchWorkspace="C:/scratch.gdb", scratchFolder=None), raising=False
    )

    path = schema.resolve_workspace("   ", None)

    assert path == "C:/scratch.gdb"


def test_resolve_workspace_hash_sentinel_falls_back_to_default(monkeypatch):
    """The ArcGIS '#' unspecified sentinel is treated as 'not provided'."""

    monkeypatch.setattr(arcpy, "mp", SimpleNamespace(ArcGISProject=_raise), raising=False)
    monkeypatch.setattr(
        arcpy, "env", SimpleNamespace(scratchWorkspace="C:/scratch.gdb", scratchFolder=None), raising=False
    )

    path = schema.resolve_workspace("#", None)

    assert path == "C:/scratch.gdb"


def test_resolve_workspace_none_falls_back_to_project_home(monkeypatch):
    """With no value, the project home data folder is used when available."""

    monkeypatch.setattr(
        arcpy, "mp", SimpleNamespace(ArcGISProject=lambda _name: SimpleNamespace(homeFolder="C:/proj")), raising=False
    )
    monkeypatch.setattr(arcpy, "env", SimpleNamespace(scratchWorkspace=None, scratchFolder=None), raising=False)

    path = schema.resolve_workspace(None, None)

    assert path == os.path.join("C:/proj", "data", schema.DEFAULT_GDB_NAME)


def _sr(factory_code):
    """Fake SpatialReference exposing only factoryCode."""
    return SimpleNamespace(factoryCode=factory_code)


def _sr_arcpy(active_map, sentinel, raise_err=False):
    """ArcPy stub: an active map (or None) plus a Web Mercator fallback factory."""

    def _project(_name):
        if raise_err:
            raise RuntimeError("no project")
        return SimpleNamespace(activeMap=active_map)

    return SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=_project),
        SpatialReference=lambda wkid: sentinel,
        AddMessage=lambda text: None,
        AddWarning=lambda text: None,
    )


class _Messages:
    """Collect tool messages so tests can read the logged lines."""

    def __init__(self):
        self.lines = []

    def addMessage(self, line):
        self.lines.append(line)

    def addWarningMessage(self, line):
        self.lines.append(line)


def _board_sr_arcpy(active_map, raise_err=False):
    """ArcPy stub that records every WKID used to build a SpatialReference."""

    constructed = []
    fake_arcpy = _sr_arcpy(active_map, None, raise_err=raise_err)
    fake_arcpy.SpatialReference = lambda wkid: constructed.append(wkid) or _sr(wkid)
    return fake_arcpy, constructed


def test_board_spatial_reference_is_web_mercator_in_geographic_map(monkeypatch):
    """Verify a WGS84 map still yields a fresh 3857 board SR and says why."""

    fake_arcpy, constructed = _board_sr_arcpy(SimpleNamespace(spatialReference=_sr(4326)))
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)
    messages = _Messages()

    sr = schema.board_spatial_reference(messages)

    assert sr.factoryCode == 3857
    assert constructed == [3857]
    assert any("4326" in line and "3857" in line for line in messages.lines)


def test_board_spatial_reference_is_web_mercator_in_foot_map(monkeypatch):
    """Verify a foot-based State Plane map does not shrink the board."""

    fake_arcpy, constructed = _board_sr_arcpy(SimpleNamespace(spatialReference=_sr(2227)))
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)

    assert schema.board_spatial_reference(None).factoryCode == 3857
    assert constructed == [3857]


def test_board_spatial_reference_quiet_when_map_matches(monkeypatch):
    """Verify no mismatch note is logged when the map is already Web Mercator."""

    fake_arcpy, _constructed = _board_sr_arcpy(SimpleNamespace(spatialReference=_sr(3857)))
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)
    messages = _Messages()

    assert schema.board_spatial_reference(messages).factoryCode == 3857
    assert messages.lines == []


def test_board_spatial_reference_without_usable_map(monkeypatch):
    """Verify Unknown SR, no active map, and probe errors all give 3857."""

    for active_map, raise_err in (
        (SimpleNamespace(spatialReference=_sr(0)), False),
        (None, False),
        (None, True),
    ):
        fake_arcpy, constructed = _board_sr_arcpy(active_map, raise_err=raise_err)
        monkeypatch.setattr(schema, "arcpy", fake_arcpy)

        assert schema.board_spatial_reference(None).factoryCode == 3857
        assert constructed == [3857]


def test_ensure_schema_creates_feature_classes_in_web_mercator_regardless_of_map(monkeypatch):
    """Verify new saves use 3857 for all four feature classes in a 4326 map."""

    fake_arcpy, _constructed = _board_sr_arcpy(SimpleNamespace(spatialReference=_sr(4326)))
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)
    feature_class_srs = {}

    def ensure_feature_class(gdb_path, name, geometry_type, fields, spatial_ref, messages):
        feature_class_srs[name] = spatial_ref.factoryCode
        return f"{gdb_path}/{name}"

    monkeypatch.setattr(schema, "ensure_gdb", lambda *_args: None)
    monkeypatch.setattr(schema, "_schema_fast_path_current", lambda *_args: False)
    monkeypatch.setattr(schema, "ensure_feature_class", ensure_feature_class)
    monkeypatch.setattr(schema, "ensure_table", lambda gdb_path, name, *_args: f"{gdb_path}/{name}")
    monkeypatch.setattr(schema, "migrate_legacy_city_health_fields", lambda *_args: None)
    monkeypatch.setattr(schema, "_mark_schema_current", lambda *_args: None)

    schema.ensure_schema("C:/game.gdb", None)

    assert feature_class_srs == {
        schema.DISTRICTS: 3857,
        schema.POINTS: 3857,
        schema.LINES: 3857,
        schema.ZONES: 3857,
    }


def test_ensure_feature_class_passes_fresh_spatial_reference_to_create(monkeypatch):
    """Regression: ArcGIS Pro 3.3.2 crashes on the raw active-map SR object."""

    problematic = _sr(26910)
    fresh = _sr(26910)
    created = []

    def create_featureclass(gdb_path, name, geometry_type, spatial_reference=None):
        created.append((gdb_path, name, geometry_type, spatial_reference))
        assert spatial_reference is fresh
        assert spatial_reference is not problematic

    fake_arcpy = SimpleNamespace(
        Exists=lambda _path: False,
        ListFields=lambda _path: [],
        management=SimpleNamespace(
            CreateFeatureclass=create_featureclass,
            AddField=lambda *_args, **_kwargs: None,
        ),
        SpatialReference=lambda wkid: fresh,
    )
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)

    schema.ensure_feature_class("C:/game.gdb", "PermitDistricts", "POLYGON", [], problematic, None)

    assert created == [("C:/game.gdb", "PermitDistricts", "POLYGON", fresh)]


def test_ensure_schema_fast_path_skips_field_scans_when_marker_current(monkeypatch):
    """Verify a current schema marker avoids repeated ListFields scans."""

    gdb_path = "C:/game.gdb"
    paths = schema.schema_paths(gdb_path)
    calls = []

    class FakeSearchCursor:
        def __init__(self, path, fields, where_clause=None):
            assert path == paths["schema_meta"]
            assert fields == ["key", "value_text"]
            assert where_clause is None

        def __enter__(self):
            return iter([["other", "old"], [schema.SCHEMA_VERSION_KEY, schema.SCHEMA_VERSION]])

        def __exit__(self, *_args):
            return False

    fake_arcpy = SimpleNamespace(
        Exists=lambda path: calls.append(("exists", path)) or True,
        ListFields=lambda _path: (_ for _ in ()).throw(AssertionError("fast path should not scan fields")),
        da=SimpleNamespace(SearchCursor=FakeSearchCursor),
    )
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)

    assert schema.ensure_schema(gdb_path, None) == paths
    assert ("exists", paths["districts"]) in calls


def _store():
    """Import the store module lazily so the arcpy stub is already installed."""

    from toolbox.permit_office_arcgis import store

    return store


def test_encode_json_fits_returns_full_payload():
    """Verify a payload inside its limit is stored whole."""

    store = _store()

    assert store.encode_json({"b": 1, "a": 2}, limit=64) == '{"a": 2, "b": 1}'


def test_encode_json_overflow_warns_and_stays_valid(monkeypatch):
    """Verify an oversized payload is named in a warning and never cut mid-JSON."""

    store = _store()
    warnings = []
    monkeypatch.setattr(store, "_warn", lambda messages, tag, text: warnings.append((tag, text)))
    payload = {"history": "x" * 500}

    text = store.encode_json(payload, limit=100, field="case_json")

    assert json.loads(text) == {}
    assert len(text) <= 100
    assert len(warnings) == 1
    tag, line = warnings[0]
    assert tag == "STORE"
    assert "case_json" in line and "100" in line and str(len(json.dumps(payload, sort_keys=True))) in line


def test_json_field_limit_uses_real_width_of_old_saves(monkeypatch):
    """Verify an old save's narrower field sets the overflow limit, not the new width."""

    store = _store()
    monkeypatch.setattr(schema, "_FIELD_LENGTHS", {})
    fields = [SimpleNamespace(name="case_json", length=4000), SimpleNamespace(name="item_id", length=96)]
    monkeypatch.setattr(schema.arcpy, "ListFields", lambda _path: fields, raising=False)

    assert store.json_field_limit("C:/old.gdb/PermitDocket", "case_json") == 4000


def test_json_field_limit_falls_back_to_schema_width(monkeypatch):
    """Verify a failed field lookup falls back to the declared schema width."""

    store = _store()
    monkeypatch.setattr(schema, "_FIELD_LENGTHS", {})
    monkeypatch.setattr(schema.arcpy, "ListFields", _raise, raising=False)

    assert store.json_field_limit("C:/game.gdb/PermitDocket", "case_json") == schema.JSON_TEXT_LENGTH


def test_json_fields_are_widened_for_new_saves():
    """Verify every JSON-bearing text field uses the wide JSON length."""

    json_fields = {
        (table, name): length
        for table, fields in (
            ("districts", schema.DISTRICT_FIELDS),
            ("support", schema.SUPPORT_FIELDS),
            ("docket", schema.DOCKET_FIELDS),
            ("state", schema.STATE_FIELDS),
            ("projects", schema.PROJECT_FIELDS),
            ("commands", schema.COMMAND_FIELDS),
            ("action_log", schema.ACTION_LOG_FIELDS),
        )
        for name, _type, _alias, length in fields
        if name.endswith("_json") or (table, name) in {("state", "value_text"), ("action_log", "city_delta")}
    }

    assert ("docket", "case_json") in json_fields
    assert ("support", "state_json") in json_fields
    assert ("districts", "hazard_json") in json_fields
    assert set(json_fields.values()) == {schema.JSON_TEXT_LENGTH}
    assert schema.JSON_TEXT_LENGTH > 4000


class _NoGpManagement:
    """Fake arcpy.management that fails on any GP tool call."""

    def __getattr__(self, name):
        raise AssertionError(f"write path called GP tool {name}")


class _RowTable:
    """In-memory table whose cursors support insert, update, and delete."""

    def __init__(self, rows=None):
        self.rows = [list(row) for row in rows or []]
        self.inserted = []

    def insert_cursor(self, _path, _fields):
        table = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def insertRow(self, row):
                table.inserted.append(list(row))

        return Cursor()

    def update_cursor(self, _path, _fields, where_clause=None):
        table = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                for row in list(table.rows):
                    self.current = row
                    yield row

            def deleteRow(self):
                table.rows.remove(self.current)

        return Cursor()


def _write_path_arcpy(monkeypatch, store, table):
    """Point store at cursor fakes where every GP tool call fails."""

    monkeypatch.setattr(store.arcpy, "management", _NoGpManagement(), raising=False)
    monkeypatch.setattr(store.arcpy, "Exists", lambda _path: True, raising=False)
    monkeypatch.setattr(
        store.arcpy,
        "da",
        SimpleNamespace(InsertCursor=table.insert_cursor, UpdateCursor=table.update_cursor),
        raising=False,
    )
    monkeypatch.setattr(schema, "_FIELD_LENGTHS", {"state": {}, "projects": {}, "docket": {}})


def test_write_state_clears_rows_with_cursor_not_delete_rows(monkeypatch):
    """Verify write_state replaces rows without the DeleteRows GP tool."""

    store = _store()
    table = _RowTable([["turn", "1", 1], ["money", "5", 5]])
    _write_path_arcpy(monkeypatch, store, table)

    store.write_state({"state": "state"}, store.rules.CityState())

    assert table.rows == []
    assert any(row[0] == "turn" for row in table.inserted)


def test_write_projects_clears_rows_with_cursor_not_delete_rows(monkeypatch):
    """Verify write_projects replaces rows without the DeleteRows GP tool."""

    store = _store()
    table = _RowTable([["old-project"]])
    _write_path_arcpy(monkeypatch, store, table)

    store.write_projects({"projects": "projects"}, {})

    assert table.rows == []


def test_generate_docket_rows_clears_rows_with_cursor_not_delete_rows(monkeypatch):
    """Verify docket regeneration clears the table without DeleteRows."""

    store = _store()
    table = _RowTable([["old-item"]])
    _write_path_arcpy(monkeypatch, store, table)
    state = store.rules.CityState()
    state.status = "complete"
    monkeypatch.setattr(store, "read_state", lambda _paths: state)
    monkeypatch.setattr(store, "read_districts", lambda _paths: {})
    monkeypatch.setattr(store, "read_active_features", lambda _paths: [])
    monkeypatch.setattr(store, "read_projects", lambda _paths: {})
    monkeypatch.setattr(store, "read_docket", lambda _paths: [])
    monkeypatch.setattr(store, "_log", lambda *args: None)

    assert store.generate_docket_rows({"docket": "docket"}, 2026, None) == []
    assert table.rows == []


def _schema_setup_arcpy(existing_fields, indexes=()):
    """Fake arcpy for schema setup that records AddFields and AddIndex calls."""

    calls = {"list_fields": 0, "add_fields": [], "add_index": []}

    def list_fields(_path):
        calls["list_fields"] += 1
        return [SimpleNamespace(name=name) for name in existing_fields]

    fake = SimpleNamespace(
        Exists=lambda _path: True,
        ListFields=list_fields,
        ListIndexes=lambda _path: [
            SimpleNamespace(fields=[SimpleNamespace(name=name)]) for name in indexes
        ],
        management=SimpleNamespace(
            AddFields=lambda table, rows: calls["add_fields"].append((table, rows)),
            AddIndex=lambda table, fields, name: calls["add_index"].append((table, fields, name)),
        ),
    )
    return fake, calls


def test_ensure_table_adds_missing_fields_in_one_call(monkeypatch):
    """Verify schema setup lists fields once and batches AddFields per table."""

    fake, calls = _schema_setup_arcpy(["key"])
    monkeypatch.setattr(schema, "arcpy", fake)

    schema.ensure_table("C:/game.gdb", schema.GAME_STATE, schema.STATE_FIELDS, None)

    assert calls["list_fields"] == 1
    assert len(calls["add_fields"]) == 1
    table, rows = calls["add_fields"][0]
    assert table == os.path.join("C:/game.gdb", schema.GAME_STATE)
    assert rows == [
        ["value_text", "TEXT", "Value Text", schema.JSON_TEXT_LENGTH],
        ["value_num", "DOUBLE", "Value Number", ""],
    ]


def test_ensure_table_skips_add_fields_when_complete(monkeypatch):
    """Verify no AddFields call runs when every field already exists."""

    fake, calls = _schema_setup_arcpy(["KEY", "value_text", "value_num"])
    monkeypatch.setattr(schema, "arcpy", fake)

    schema.ensure_table("C:/game.gdb", schema.GAME_STATE, schema.STATE_FIELDS, None)

    assert calls["add_fields"] == []


def test_ensure_lookup_indexes_adds_missing_indexes_once(monkeypatch):
    """Verify command_id and docket item_id get attribute indexes when missing."""

    fake, calls = _schema_setup_arcpy([], indexes=["item_id"])
    monkeypatch.setattr(schema, "arcpy", fake)
    paths = schema.schema_paths("C:/game.gdb")

    schema.ensure_lookup_indexes(paths, None)

    assert calls["add_index"] == [(paths["commands"], ["command_id"], "idx_command_id")]


class _DictRowTable:
    """In-memory feature table of dict rows; cursors project any field list."""

    def __init__(self):
        self.rows = []

    def _cursor(self, fields, rows):
        table = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                for current in rows:
                    self.current = current
                    yield [current.get(field) for field in fields]

            def insertRow(self, values):
                table.rows.append(dict(zip(fields, values)))

            def updateRow(self, values):
                self.current.update(zip(fields, values))

        return Cursor()

    def search_cursor(self, _path, fields, where_clause=None):
        return self._cursor(fields, list(self.rows))

    def insert_cursor(self, _path, fields):
        return self._cursor(fields, [])

    def update_cursor(self, _path, fields, where_clause=None):
        return self._cursor(fields, self.rows)


def _district_store(monkeypatch):
    """Return store wired to one in-memory district table."""

    store = _store()
    table = _DictRowTable()
    monkeypatch.setattr(
        store.arcpy,
        "da",
        SimpleNamespace(
            SearchCursor=table.search_cursor,
            InsertCursor=table.insert_cursor,
            UpdateCursor=table.update_cursor,
        ),
        raising=False,
    )
    monkeypatch.setattr(store.arcpy, "Describe", lambda _path: SimpleNamespace(spatialReference=None), raising=False)
    monkeypatch.setattr(store, "square_polygon", lambda *args: "square")
    monkeypatch.setattr(store, "_log", lambda *args: None)
    monkeypatch.setattr(schema, "_FIELD_LENGTHS", {"districts": {}})
    return store, table


def _every_field_set(store, profile):
    """Give one district a non-default value in every persisted field."""

    rules = store.rules
    profile.prior_district_type = "residential"
    profile.identity_state = "contested"
    profile.contesting_cell_id = "D0001"
    profile.contesting_type = "industrial"
    profile.transition_due_turn = 7
    profile.buyout_pressure = 3
    profile.last_buyout_report = "Buyout offer filed."
    profile.land_use = "industrial"
    profile.zoning_overlay = "flood_overlay"
    profile.network_access = {service: 5 for service in rules.SERVICE_TYPES}
    profile.hazards = {hazard: 2 for hazard in rules.HAZARD_TYPES}
    profile.housing_capacity = 900
    profile.affordability = 40
    profile.vacancy_rate = 6
    profile.displacement = {group: 1 for group in rules.CITIZEN_GROUPS}
    profile.population_mix = {group: 2 for group in rules.CITIZEN_GROUPS}
    profile.dissatisfaction = {group: 3 for group in rules.CITIZEN_GROUPS}
    return rules.normalize_profile(profile)


def test_district_codec_round_trips_every_field(monkeypatch):
    """Verify a fully set district survives create, update, and read unchanged."""

    from dataclasses import asdict

    store, table = _district_store(monkeypatch)
    paths = {"districts": "districts"}
    store.create_district_board(paths, 2026, None)
    board = store.read_districts(paths)
    target = _every_field_set(store, board["D0000"])
    expected = {cid: asdict(profile) for cid, profile in board.items()}

    store.write_district_updates(paths, board, "Decision report.", affected_ids=["D0000"])
    loaded = store.read_districts(paths)

    assert {cid: asdict(profile) for cid, profile in loaded.items()} == expected
    assert loaded["D0000"].name == target.name
    reports = {row["cell_id"]: row["last_report"] for row in table.rows}
    assert reports["D0000"] == "Decision report."
    assert reports["D0001"] == "New district profile generated."
    assert all(row["SHAPE@"] == "square" for row in table.rows)


def test_district_read_applies_defaults_to_blank_rows(monkeypatch):
    """Verify blank district columns read back with the documented defaults."""

    store, table = _district_store(monkeypatch)
    table.rows.append({"cell_id": "D0000", "district_name": "Blank"})

    profile = store.read_districts({"districts": "districts"})["D0000"]

    assert profile.name == "Blank"
    assert profile.population == 0
    assert profile.district_type == "mercantile"
    assert profile.identity_state == "stable"
    assert profile.incident_state == "none"
    assert profile.adjacent_cell_ids == []


def test_district_codec_covers_every_schema_district_field():
    """Verify the codec and DISTRICT_FIELDS list the same columns."""

    store = _store()
    schema_names = {name for name, *_rest in schema.DISTRICT_FIELDS}

    assert set(store.DISTRICT_FIELD_NAMES) | {"last_report"} == schema_names
    assert len(store.DISTRICT_FIELD_NAMES) == len(set(store.DISTRICT_FIELD_NAMES))
