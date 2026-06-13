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


def test_active_spatial_reference_uses_real_map_sr(monkeypatch):
    """Verify a map with a real WKID is normalized to a fresh SR."""
    real = _sr(3857)
    fresh = _sr(3857)
    monkeypatch.setattr(schema, "arcpy", _sr_arcpy(SimpleNamespace(spatialReference=real), fresh))

    assert schema.active_spatial_reference(None) is fresh


def test_active_spatial_reference_falls_back_on_unknown_sr(monkeypatch):
    """Verify an Unknown map SR (factoryCode 0) falls back to Web Mercator.

    Regression: an Unknown SR is truthy, so it used to propagate into feature-class
    creation and break the first linear Buffer with a RuntimeError on machines
    whose active map had no coordinate system.
    """
    sentinel = _sr(3857)
    monkeypatch.setattr(schema, "arcpy", _sr_arcpy(SimpleNamespace(spatialReference=_sr(0)), sentinel))

    assert schema.active_spatial_reference(None) is sentinel


def test_active_spatial_reference_falls_back_when_no_active_map(monkeypatch):
    """Verify no active map falls back to Web Mercator."""
    sentinel = _sr(3857)
    monkeypatch.setattr(schema, "arcpy", _sr_arcpy(None, sentinel))

    assert schema.active_spatial_reference(None) is sentinel


def test_active_spatial_reference_falls_back_on_error(monkeypatch):
    """Verify any probe failure falls back to Web Mercator."""
    sentinel = _sr(3857)
    monkeypatch.setattr(schema, "arcpy", _sr_arcpy(None, sentinel, raise_err=True))

    assert schema.active_spatial_reference(None) is sentinel


def test_active_spatial_reference_returns_fresh_wkid_sr(monkeypatch):
    """Verify feature-class creation never receives the raw active-map SR."""

    problematic = _sr(26910)
    fresh = _sr(26910)
    constructed = []
    fake_arcpy = _sr_arcpy(SimpleNamespace(spatialReference=problematic), _sr(3857))
    fake_arcpy.SpatialReference = lambda wkid: constructed.append(wkid) or fresh
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)

    assert schema.active_spatial_reference(None) is fresh
    assert constructed == [26910]


def test_active_spatial_reference_unknown_uses_fresh_web_mercator(monkeypatch):
    """Verify Unknown active-map SR falls back to a fresh Web Mercator object."""

    fallback = _sr(3857)
    constructed = []
    fake_arcpy = _sr_arcpy(SimpleNamespace(spatialReference=_sr(0)), fallback)
    fake_arcpy.SpatialReference = lambda wkid: constructed.append(wkid) or fallback
    monkeypatch.setattr(schema, "arcpy", fake_arcpy)

    assert schema.active_spatial_reference(None) is fallback
    assert constructed == [3857]


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
