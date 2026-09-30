"""Workspace-resolution coverage for the Permit Office toolbox schema layer."""

from __future__ import annotations

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
