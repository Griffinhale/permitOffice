"""Tests for redraw planning."""

from __future__ import annotations

from types import SimpleNamespace

from toolbox import arcpy_permit_office_rules as rules
from toolbox.permit_office_arcgis import redraw_plan


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
