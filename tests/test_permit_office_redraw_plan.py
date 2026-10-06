"""Tests for redraw planning."""

from __future__ import annotations

from types import SimpleNamespace
import sys

import pytest

sys.modules.setdefault(
    "arcpy",
    SimpleNamespace(
        AddMessage=lambda text: None,
        AddWarning=lambda text: None,
        AddError=lambda text: None,
    ),
)

from toolbox import arcpy_permit_office_rules as rules
from toolbox.permit_office_arcgis import map_layers, redraw_plan


def test_hydrated_redraw_plan_uses_actual_result():
    actual = rules.DecisionResult(
        True,
        "approve",
        "CASE-1",
        "approved",
        affected_cell_ids=["D0001"],
        district_deltas={"D0001": {"trust": 1}},
        feature_updates={"F-1": {"status": "active"}},
    )

    plan = redraw_plan.hydrate_decision_redraw_plan(actual, feature_layer_key="points")

    assert plan.feature_layer_key == "points"
    assert plan.feature_ids == ("F-1",)
    assert plan.affected_cell_ids == ("D0001",)
    assert plan.refresh_names == frozenset({"PermitPoints"})
    assert plan.remove_readd_names == frozenset({"PermitDistricts", "PermitPoints"})


def test_hydrated_redraw_plan_routes_line_and_zone_feature_layers():
    line = redraw_plan.hydrate_decision_redraw_plan(
        rules.DecisionResult(True, "approve", "L", "ok", affected_cell_ids=["D0000"]),
        feature_layer_key="lines",
    )
    zone = redraw_plan.hydrate_decision_redraw_plan(
        rules.DecisionResult(True, "approve", "Z", "ok", affected_cell_ids=["D0000"]),
        feature_layer_key="zones",
    )

    assert line.refresh_names == frozenset({"PermitLines"})
    assert zone.refresh_names == frozenset({"PermitZones"})
    assert line.remove_readd_names == frozenset({"PermitDistricts", "PermitLines"})
    assert zone.remove_readd_names == frozenset({"PermitDistricts", "PermitZones"})


def test_hydrated_redraw_plan_keeps_clean_feature_layer_out_of_readd_scope():
    """Verify a decision that left its feature layer alone redraws districts only."""

    actual = rules.DecisionResult(
        True,
        "approve",
        "CASE-1",
        "approved",
        affected_cell_ids=["D0001"],
        district_deltas={"D0001": {"trust": 1}},
    )

    plan = redraw_plan.hydrate_decision_redraw_plan(actual, feature_layer_key="points", feature_layer_dirty=False)

    assert plan.refresh_names == frozenset()
    assert plan.remove_readd_names == frozenset({"PermitDistricts"})
    assert redraw_plan.layer_names_for_plan(plan) == {"PermitDistricts"}


def test_redraw_plan_scopes_desk_district_and_feature_only_work():
    desk = redraw_plan._redraw_plan(dirty_scope=redraw_plan.DIRTY_DESK_ONLY)
    district = redraw_plan._redraw_plan({redraw_plan.DISTRICTS, redraw_plan.POINTS}, dirty_scope=redraw_plan.DIRTY_DISTRICTS)
    feature = redraw_plan._redraw_plan({redraw_plan.LINES})
    forced = redraw_plan._redraw_plan(force_readd=True)

    assert desk == redraw_plan.RedrawPlan("desk-only", frozenset(), clear_selections=False)
    assert district.mode == "district-readd"
    assert district.remove_scope == frozenset({redraw_plan.DISTRICTS, redraw_plan.POINTS})
    assert feature == redraw_plan.RedrawPlan("refresh-only", frozenset({redraw_plan.LINES}))
    assert forced == redraw_plan.RedrawPlan("force-readd", frozenset(), None)


def test_decision_and_week_close_layer_scopes():
    assert redraw_plan._decision_layer_names(SimpleNamespace(geometry_type="LINE")) == {redraw_plan.DISTRICTS, redraw_plan.LINES}
    assert redraw_plan._decision_layer_names(SimpleNamespace(geometry_type="OTHER")) is None
    assert redraw_plan._feature_layer_key_for_item(SimpleNamespace(geometry_type="POLYGON")) == "zones"
    assert redraw_plan._week_close_redraw_layers(None) == redraw_plan.WEEK_CLOSE_READD_LAYERS
    assert redraw_plan._week_close_redraw_layers([SimpleNamespace(geometry_type="zone")]) == frozenset(
        {redraw_plan.DISTRICTS, redraw_plan.ZONES}
    )


def test_dashboard_uses_the_shared_redraw_planners():
    from toolbox.permit_office_arcgis import dashboard, map_redraw

    assert dashboard._decision_layer_names is redraw_plan._decision_layer_names
    assert dashboard._week_close_redraw_layers is redraw_plan._week_close_redraw_layers
    assert dashboard.rebuild_output_layers is map_redraw.rebuild_output_layers
    assert map_redraw._redraw_plan is redraw_plan._redraw_plan


def test_selection_layers_for_item_names_targets_and_the_proposal_layer():
    """A case selects districts for its targets and the layer holding its proposal."""

    point_case = rules.DocketItem("CASE-p", "street_vendor_compact", "Vendor", "POINT", 1, target_cell_ids=["D0001"])
    line_case = rules.DocketItem("CASE-l", "connector_corridor", "Road", "LINE", 1, target_cell_ids=["D0001", "D0002"])
    zone_case = rules.DocketItem("CASE-z", "market_square", "Square", "POLYGON", 1, target_cell_ids=["D0003"])

    assert redraw_plan.selection_layers_for_item(point_case) == frozenset({"PermitDistricts", "PermitPoints"})
    assert redraw_plan.selection_layers_for_item(line_case) == frozenset({"PermitDistricts", "PermitLines"})
    assert redraw_plan.selection_layers_for_item(zone_case) == frozenset({"PermitDistricts", "PermitZones"})
    assert redraw_plan.selection_layers_for_item(None) == frozenset()


def test_selection_layers_for_item_keeps_districts_for_an_unresolved_case_without_targets():
    """An open case with no targets yet still gets a proposal with suggested targets."""

    open_case = rules.DocketItem("CASE-o", "street_vendor_compact", "Vendor", "POINT", 1)
    done_case = rules.DocketItem("CASE-d", "street_vendor_compact", "Vendor", "POINT", 1, status="approved")
    odd_case = rules.DocketItem("CASE-x", "street_vendor_compact", "Vendor", "MULTIPATCH", 1, target_cell_ids=["D0001"])

    assert redraw_plan.selection_layers_for_item(open_case) == frozenset({"PermitDistricts", "PermitPoints"})
    assert redraw_plan.selection_layers_for_item(done_case) == frozenset({"PermitPoints"})
    assert redraw_plan.selection_layers_for_item(odd_case) == frozenset({"PermitDistricts"})


class _RecordingLayer:
    """Fake map layer that logs every read of its source and every write."""

    def __init__(self, events, name, source, visible=True, query=""):
        self._events = events
        self.name = name
        self._source = source
        self._visible = visible
        self._query = query

    @property
    def dataSource(self):
        self._events.append(("source", self.name))
        return self._source

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._events.append(("visible", self.name, value))
        self._visible = value

    @property
    def definitionQuery(self):
        return self._query

    @definitionQuery.setter
    def definitionQuery(self, value):
        self._events.append(("query", self.name, value))
        self._query = value


def _week_close_map(monkeypatch, *, lines_visible=True, underlays=(), version=(3, 7)):
    """Install a Pro 3.7 map: district slot, points ring slot, ringless lines and zones.

    ``underlays`` adds (copy name, source) feature copies beneath the live layers.
    """

    events = []
    layers = [
        _RecordingLayer(events, redraw_plan.LINES, "lines", visible=lines_visible),
        _RecordingLayer(events, redraw_plan.POINTS, "points", visible=False),
        _RecordingLayer(events, "Permit Office Predrawn Points 0", "points", query="1=1"),
        _RecordingLayer(events, redraw_plan.ZONES, "zones"),
        _RecordingLayer(events, "Permit Office Predrawn 0", "districts", query="1=1"),
        _RecordingLayer(events, redraw_plan.DISTRICTS, "districts", visible=False),
    ] + [_RecordingLayer(events, name, source) for name, source in underlays]

    def list_layers():
        events.append(("listLayers",))
        return list(layers)

    def current_project(_name):
        events.append(("project",))
        return SimpleNamespace(activeMap=active_map)

    active_map = SimpleNamespace(listLayers=list_layers)
    fake = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=current_project),
        RefreshLayer=lambda name: events.append(("refresh", name)),
        GetInstallInfo=lambda: {"Version": ".".join(str(part) for part in version)},
        AddMessage=lambda text: None,
        AddWarning=lambda text: None,
    )
    monkeypatch.setattr(map_layers, "arcpy", fake)
    monkeypatch.setitem(map_layers._PRO_VERSION_CACHE, "version", version)
    return events


_FEATURE_TARGETS = {
    redraw_plan.LINES,
    redraw_plan.ZONES,
    "Permit Office Predrawn Points 0",
}


def _feature_toggle_indexes(events):
    return [i for i, event in enumerate(events) if event[0] == "query" and event[1] in _FEATURE_TARGETS]


def test_week_close_resolves_every_feature_layer_before_toggling_them_back_to_back(monkeypatch):
    """AR21: the three feature requeries run together so their drops overlap."""

    events = _week_close_map(monkeypatch)
    rehydrated = []
    monkeypatch.setattr(map_layers, "_rehydrate_feature_display_ring", lambda paths, messages, name: rehydrated.append(name) or True)
    scope = redraw_plan._week_close_redraw_layers([SimpleNamespace(geometry_type=kind) for kind in ("POINT", "LINE", "POLYGON")])

    handled = map_layers.apply_ring_redraw(
        {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"},
        None,
        layer_names=set(scope),
        remove_scope=set(scope),
    )

    assert handled is True
    assert rehydrated == []
    toggles = _feature_toggle_indexes(events)
    assert sorted(events[i][1] for i in toggles) == sorted(_FEATURE_TARGETS)
    assert toggles == list(range(toggles[0], toggles[0] + len(toggles)))
    feature_resolution = [
        i for i, event in enumerate(events)
        if event[0] in ("project", "listLayers") or (event[0] == "source" and event[1] in _FEATURE_TARGETS)
    ]
    assert feature_resolution and max(feature_resolution) < toggles[0]


_UNDERLAYS = (("Lines Underlay", "lines"), ("Points Underlay", "points"), ("Zones Underlay", "zones"))


def _underlay_toggle_indexes(events):
    names = {name for name, _source in _UNDERLAYS}
    return [i for i, event in enumerate(events) if event[0] == "query" and event[1] in names]


def test_week_close_requeries_every_feature_copy_before_the_live_batch(monkeypatch):
    """AR24: the copies redraw while the live layers still show the old picture.

    A copy is never covered, so it must not keep showing proposals the close
    removed; the live toggles stay back to back (AR21) and no RefreshLayer runs.
    """

    events = _week_close_map(monkeypatch, underlays=_UNDERLAYS)
    monkeypatch.setattr(map_layers, "_rehydrate_feature_display_ring", lambda *args: pytest.fail("no rehydrate"))
    scope = redraw_plan._week_close_redraw_layers([SimpleNamespace(geometry_type=kind) for kind in ("POINT", "LINE", "POLYGON")])

    map_layers.apply_ring_redraw(
        {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"},
        None,
        layer_names=set(scope),
        remove_scope=set(scope),
    )

    copies = _underlay_toggle_indexes(events)
    toggles = _feature_toggle_indexes(events)
    assert sorted(events[i][1] for i in copies) == sorted(name for name, _source in _UNDERLAYS)
    assert max(copies) < toggles[0]
    assert toggles == list(range(toggles[0], toggles[0] + len(toggles)))
    assert not any(event[0] == "refresh" for event in events)


def test_feature_copy_reading_another_save_is_left_alone(monkeypatch):
    """A copy left over from another workspace is not requeried as if it were this save's."""

    events = _week_close_map(monkeypatch, underlays=(("Points Underlay", "old_save_points"),))
    monkeypatch.setattr(map_layers, "_rehydrate_feature_display_ring", lambda *args: True)

    map_layers.apply_ring_redraw(
        {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"},
        None,
        layer_names={redraw_plan.DISTRICTS, redraw_plan.POINTS},
        remove_scope={redraw_plan.DISTRICTS, redraw_plan.POINTS},
    )

    assert _underlay_toggle_indexes(events) == []


def test_feature_copies_are_requeried_below_pro_3_7_too(monkeypatch):
    """Older Pro skips the live query flip, but the copies still must not hold removed proposals."""

    events = _week_close_map(monkeypatch, underlays=_UNDERLAYS, version=(3, 6))
    monkeypatch.setattr(map_layers, "_rehydrate_feature_display_ring", lambda *args: True)

    map_layers._refresh_feature_scope(
        {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"},
        None,
        set(redraw_plan.WEEK_CLOSE_READD_LAYERS),
        remove_scope=set(redraw_plan.WEEK_CLOSE_READD_LAYERS),
    )

    assert sorted(events[i][1] for i in _underlay_toggle_indexes(events)) == sorted(name for name, _source in _UNDERLAYS)
    assert _feature_toggle_indexes(events) == []


def test_week_close_rehydrates_an_unresolved_feature_layer_after_the_batched_toggles(monkeypatch):
    """A layer with no visible slot or base keeps the ring seed, after the overlap."""

    events = _week_close_map(monkeypatch, lines_visible=False)
    monkeypatch.setattr(
        map_layers,
        "_rehydrate_feature_display_ring",
        lambda paths, messages, name: events.append(("rehydrate", name)) or True,
    )

    map_layers.apply_ring_redraw(
        {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"},
        None,
        layer_names=set(redraw_plan.WEEK_CLOSE_READD_LAYERS),
        remove_scope=set(redraw_plan.WEEK_CLOSE_READD_LAYERS),
    )

    toggles = _feature_toggle_indexes(events)
    assert sorted(events[i][1] for i in toggles) == sorted(_FEATURE_TARGETS - {redraw_plan.LINES})
    assert toggles == list(range(toggles[0], toggles[0] + len(toggles)))
    assert events.index(("rehydrate", redraw_plan.LINES)) > toggles[-1]


def test_decision_requery_toggles_only_its_feature_layer_once(monkeypatch):
    """Decision redraws carry one feature layer, so batching leaves them as landed."""

    events = _week_close_map(monkeypatch)
    monkeypatch.setattr(map_layers, "_rehydrate_feature_display_ring", lambda *args: pytest.fail("no rehydrate"))
    plan = redraw_plan.hydrate_decision_redraw_plan(rules.DecisionResult(True, "approve", "CASE-1", "approved"), feature_layer_key="points")

    map_layers.apply_ring_redraw(
        {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"},
        None,
        layer_names=redraw_plan.layer_names_for_plan(plan),
        remove_scope=set(plan.remove_readd_names),
    )

    assert [events[i][1] for i in _feature_toggle_indexes(events)] == ["Permit Office Predrawn Points 0"]
