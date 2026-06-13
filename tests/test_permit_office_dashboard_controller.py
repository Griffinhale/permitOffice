"""Controller tests for dashboard map-update and approval sequencing."""

from __future__ import annotations

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

from toolbox import arcpy_permit_office_rules as rules
from toolbox.permit_office_arcgis import dashboard
from toolbox.permit_office_arcgis import desk_model
from toolbox.permit_office_arcgis import _perf
from toolbox.permit_office_arcgis.desk_model import build_desk_model
from toolbox.permit_office_arcgis import schema
from toolbox.permit_office_arcgis import store


def _profile(cell_id):
    """Return a normalized baseline district profile for controller tests."""

    profile = rules.DistrictProfile(cell_id, cell_id, 1000, 50, 20, 35, 25, 50, "mercantile")
    return rules.normalize_profile(profile)


def test_update_from_map_replaces_selected_case_targets(monkeypatch):
    """Verify map selections replace the selected case target list."""

    item = rules.DocketItem(
        "CASE-update",
        "connector_corridor",
        "Connector Corridor",
        "LINE",
        1,
    )
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.selected_item_id = item.item_id
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None

    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(dashboard, "selected_cell_ids", lambda layer: ["D0000", "D0001"])
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages: None)

    def insert(paths, docket_item, target_ids, messages):
        """Fake proposal replacement that records target ids on the item."""

        docket_item.target_cell_ids = list(target_ids)
        return list(target_ids)

    monkeypatch.setattr(dashboard, "insert_or_replace_proposal", insert)

    controller.update_from_map()

    assert item.target_cell_ids == ["D0000", "D0001"]
    assert controller.status_text == "Updated Connector Corridor from map selection: D0000, D0001."


def test_approval_restores_missing_proposal_before_spillover(monkeypatch):
    """Verify approval rebuilds proposal geometry before spillover lookup."""

    item = rules.DocketItem(
        "CASE-approve",
        "connector_corridor",
        "Connector Corridor",
        "LINE",
        1,
        target_cell_ids=["D0000", "D0001"],
    )
    state = rules.CityState()
    districts = {"D0000": _profile("D0000"), "D0001": _profile("D0001"), "D0002": _profile("D0002")}
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.selected_item_id = item.item_id
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    order = []

    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(dashboard, "selected_cell_ids", lambda layer: [])
    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard, "write_active_features", lambda paths, active_features: None)
    def activate(paths, docket_item, report):
        """Fake proposal activation that records call order."""

        order.append("activate")
        return 1

    monkeypatch.setattr(dashboard, "activate_proposal", activate)
    monkeypatch.setattr(controller, "_finish_decision", lambda *args: order.append("finish"))

    def ensure(paths, docket_item, seed, messages, target_ids=None):
        """Fake proposal ensure step that validates fallback targets."""

        order.append("ensure")
        assert target_ids == ["D0000", "D0001"]
        return list(target_ids)

    def spillover(paths, docket_item):
        """Fake spillover lookup that must run after proposal creation."""

        assert order == ["ensure"]
        order.append("spillover")
        return ["D0002"]

    def resolve(state_arg, docket_item, district_arg, action, targets, spillovers, **kwargs):
        """Fake rules resolver that validates target and spillover inputs."""

        assert targets == ["D0000", "D0001"]
        assert spillovers == ["D0002"]
        docket_item.status = "active"
        return rules.DecisionResult(True, action, docket_item.item_id, "approved", affected_cell_ids=list(targets) + list(spillovers))

    monkeypatch.setattr(dashboard, "ensure_case_proposal", ensure)
    monkeypatch.setattr(dashboard, "proposal_spillover", spillover)
    monkeypatch.setattr(dashboard.rules, "resolve_decision", resolve)

    controller.apply_decision("approve", False)

    assert order == ["ensure", "spillover", "activate", "finish"]


def test_decision_exception_status_uses_neutral_action_copy(monkeypatch):
    """Verify approval-path failures use visible-neutral decision copy."""

    item = rules.DocketItem("CASE-error", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.selected_item_id = item.item_id
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None

    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(dashboard, "selected_cell_ids", lambda layer: ["D0000"])
    monkeypatch.setattr(dashboard, "ensure_case_proposal", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("proposal locked")))

    controller.apply_decision("approve", False)

    assert controller.status_text == "Decision failed: proposal locked"


def test_approval_continues_when_future_cache_lookup_fails(monkeypatch):
    """Verify speculative cache failures never block authoritative decisions."""

    item = rules.DocketItem(
        "CASE-cache",
        "street_vendor_compact",
        "Street Vendor Compact",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )
    state = rules.CityState()
    districts = {"D0000": _profile("D0000")}
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.selected_item_id = item.item_id
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    calls = []
    warnings = []

    class BrokenFutureCache:
        current_state_hash = "parent"

        def __init__(self, *args, **kwargs):
            pass

        def build_one_ply(self, *args, **kwargs):
            raise TypeError("unhashable type: 'FeatureInstance'")

        def lookup(self, *args, **kwargs):
            return None

    monkeypatch.setattr(dashboard.futures, "DecisionFutureCache", BrokenFutureCache)
    monkeypatch.setattr(dashboard, "_warn", lambda messages, tag, text: warnings.append((tag, text)))
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(dashboard, "selected_cell_ids", lambda layer: [])
    monkeypatch.setattr(dashboard, "ensure_case_proposal", lambda paths, docket_item, seed, messages, target_ids=None: ["D0000"])
    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: calls.append(("command", args, kwargs)))
    monkeypatch.setattr(dashboard, "proposal_spillover", lambda paths, docket_item: [])
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard, "write_active_features", lambda paths, active_features: None)
    monkeypatch.setattr(dashboard, "activate_proposal", lambda paths, docket_item, report: 1)
    monkeypatch.setattr(controller, "_finish_decision", lambda *args: calls.append(("finish", args)))

    def resolve(state_arg, docket_item, district_arg, action, targets, spillovers, **kwargs):
        calls.append(("resolve", action, tuple(targets), tuple(spillovers)))
        docket_item.status = "active"
        return rules.DecisionResult(True, action, docket_item.item_id, "approved", affected_cell_ids=targets)

    monkeypatch.setattr(dashboard.rules, "resolve_decision", resolve)

    controller.apply_decision("approve", False)

    assert ("resolve", "approve", ("D0000",), ()) in calls
    assert any(call[0] == "finish" for call in calls)
    assert any("future cache lookup skipped" in text for _tag, text in warnings)


def test_successful_decision_reapplies_map_presentation_before_refresh(monkeypatch):
    """Verify successful decisions rebuild map layers before showing receipt."""

    item = rules.DocketItem("CASE-finish", "procession_route", "Procession Route", "LINE", 1)
    state = rules.CityState()
    districts = {"D0000": _profile("D0000")}
    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    order = []

    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: order.append("districts"))
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: order.append("state"))
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: order.append("projects"))
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: order.append("docket"))
    monkeypatch.setattr(dashboard, "action_log", lambda *args, **kwargs: order.append("log"))
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: order.append("command"))
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: order.extend(["clear", "remove", "map", "refresh"]))
    monkeypatch.setattr(controller, "_record_receipt", lambda *args, **kwargs: order.append("receipt"))

    result = rules.DecisionResult(True, "approve", item.item_id, "approved", affected_cell_ids=["D0000"])

    controller._finish_decision("CMD-1", item, state, districts, {}, result)

    assert controller.district_layer == dashboard.DISTRICTS
    assert order[-5:] == ["clear", "remove", "map", "refresh", "receipt"]


def test_rebuild_defaults_to_district_ring_for_district_scope(monkeypatch):
    """Verify district redraws use the promoted district ring path.

    Live ArcGIS testing showed predrawn rehydrate can stale out and miss
    district conversions, while the numeric district ring preserves changed
    district symbology once RefreshLayer runs after visibility swap.
    """

    calls = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: None)
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or True,
    )

    dashboard.rebuild_output_layers({}, object())

    assert calls == [
        (
            "experiment",
            dashboard.DEFAULT_REDRAW_EXPERIMENT,
            {
                "layer_names": None,
                "remove_scope": {dashboard.DISTRICTS},
                "dirty_scope": None,
                "mode": "district-readd",
            },
        ),
    ]


def test_rebuild_force_readd_removes_every_layer(monkeypatch):
    """Verify an explicit force_readd still clears the full output set."""

    calls = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: None)
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))

    dashboard.rebuild_output_layers({}, object(), force_readd=True)

    assert ("remove", None) in calls


def test_rebuild_planner_uses_default_experiment_for_district_scope(monkeypatch):
    """Verify district dirty scope routes through district ring by default."""

    calls = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or True,
    )

    plan = dashboard.rebuild_output_layers({}, object(), layer_names={dashboard.DISTRICTS})

    assert plan.mode == "district-readd"
    assert plan.remove_scope == frozenset((dashboard.DISTRICTS,))
    assert calls == [
        ("clear", None),
        (
            "experiment",
            dashboard.DEFAULT_REDRAW_EXPERIMENT,
            {
                "layer_names": {dashboard.DISTRICTS},
                "remove_scope": {dashboard.DISTRICTS},
                "dirty_scope": None,
                "mode": "district-readd",
            },
        ),
    ]


def test_rebuild_planner_readds_dirty_point_layer_when_in_scope(monkeypatch):
    """Verify point decisions can re-add points instead of relying on refresh."""

    calls = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or True,
    )

    plan = dashboard.rebuild_output_layers({}, object(), layer_names={dashboard.DISTRICTS, dashboard.POINTS}, dirty_scope=dashboard.DIRTY_DISTRICTS)

    assert plan.mode == "district-readd"
    assert plan.remove_scope == frozenset((dashboard.DISTRICTS, dashboard.POINTS))
    assert calls == [
        ("clear", None),
        (
            "experiment",
            dashboard.DEFAULT_REDRAW_EXPERIMENT,
            {
                "layer_names": {dashboard.DISTRICTS, dashboard.POINTS},
                "remove_scope": {dashboard.DISTRICTS, dashboard.POINTS},
                "dirty_scope": dashboard.DIRTY_DISTRICTS,
                "mode": "district-readd",
            },
        ),
    ]


def test_rebuild_hydrated_plan_can_override_remove_scope_and_experiment(monkeypatch):
    """Verify hydrated redraw plans can ring-swap districts while refreshing features."""

    calls = []
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or True,
    )

    plan = dashboard.rebuild_output_layers(
        {},
        object(),
        layer_names={dashboard.DISTRICTS, dashboard.POINTS},
        dirty_scope=dashboard.DIRTY_DISTRICTS,
        remove_scope_override={dashboard.DISTRICTS},
        redraw_experiment="district-ring",
    )

    assert plan.mode == "district-readd"
    assert calls == [
        ("clear", None),
        (
            "experiment",
            "district-ring",
            {
                "layer_names": {dashboard.DISTRICTS, dashboard.POINTS},
                "remove_scope": {dashboard.DISTRICTS},
                "dirty_scope": dashboard.DIRTY_DISTRICTS,
                "mode": "district-readd",
            },
        ),
    ]


def test_rebuild_prefers_explicit_toolbox_experiment_over_hydrated_override(monkeypatch):
    """Verify GP-selected experiments are not hidden by controller redraw hints."""

    calls = []
    monkeypatch.setenv(dashboard.REDRAW_EXPERIMENT_ENV, "predrawn-rehydrate")
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or True,
    )

    dashboard.rebuild_output_layers(
        {},
        object(),
        layer_names={dashboard.DISTRICTS, dashboard.POINTS},
        dirty_scope=dashboard.DIRTY_DISTRICTS,
        remove_scope_override={dashboard.DISTRICTS, dashboard.POINTS},
        redraw_experiment="district-ring",
    )

    assert calls[1][0] == "experiment"
    assert calls[1][1] == "predrawn-rehydrate"
    assert calls[1][2]["remove_scope"] == {dashboard.DISTRICTS, dashboard.POINTS}


def test_rebuild_planner_allows_desk_only_without_map_work(monkeypatch):
    """Verify desk-only dirty scopes do not touch ArcGIS map layers."""

    calls = []
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))

    plan = dashboard.rebuild_output_layers({}, object(), dirty_scope=dashboard.DIRTY_DESK_ONLY)

    assert plan.mode == "desk-only"
    assert plan.layer_names == frozenset()
    assert calls == []


def test_rebuild_planner_refreshes_feature_only_scope(monkeypatch):
    """Verify feature-only dirty scopes avoid district re-adds."""

    calls = []
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))

    plan = dashboard.rebuild_output_layers({}, object(), layer_names={dashboard.POINTS})

    assert plan.mode == "refresh-only"
    assert calls == [
        ("clear", None),
        ("add", {dashboard.POINTS}),
        ("refresh", {dashboard.POINTS}),
    ]


def test_rebuild_logs_dirty_scope_and_mode(monkeypatch):
    """Verify rebuild logs enough context for live perf comparisons."""

    messages = object()
    logs = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: None)
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "run_redraw_experiment", lambda *args, **kwargs: True)
    monkeypatch.setattr(dashboard, "_log", lambda messages_arg, tag, text: logs.append((tag, text)))

    dashboard.rebuild_output_layers({}, messages, layer_names={dashboard.DISTRICTS}, dirty_scope=dashboard.DIRTY_DISTRICTS)

    assert ("REBUILD", "targeted=['PermitDistricts'] mode=district-readd dirty=districts") in logs


def test_rebuild_uses_experiment_when_env_is_set(monkeypatch):
    """Verify redraw experiments replace normal remove/add work only when opted in."""

    calls = []
    monkeypatch.setenv(dashboard.REDRAW_EXPERIMENT_ENV, "district-ring")
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or True,
    )

    plan = dashboard.rebuild_output_layers({}, object(), layer_names={dashboard.DISTRICTS}, dirty_scope=dashboard.DIRTY_DISTRICTS)

    assert plan.mode == "district-readd"
    assert calls == [
        ("clear", None),
        (
            "experiment",
            "district-ring",
            {
                "layer_names": {dashboard.DISTRICTS},
                "remove_scope": {dashboard.DISTRICTS},
                "dirty_scope": dashboard.DIRTY_DISTRICTS,
                "mode": "district-readd",
            },
        ),
    ]


def test_rebuild_falls_back_when_default_rehydrate_fails(monkeypatch):
    """Verify promoted redraw path failure falls back to the old remove/add path."""

    calls = []
    logs = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: calls.append(("clear", None)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(
        dashboard,
        "run_redraw_experiment",
        lambda paths, messages, experiment, **kwargs: calls.append(("experiment", experiment, kwargs)) or False,
    )
    monkeypatch.setattr(dashboard, "_warn", lambda messages_arg, tag, text: logs.append((tag, text)))

    plan = dashboard.rebuild_output_layers({}, object(), layer_names={dashboard.DISTRICTS}, dirty_scope=dashboard.DIRTY_DISTRICTS)

    assert plan.mode == "district-readd"
    assert calls == [
        ("clear", None),
        (
            "experiment",
            dashboard.DEFAULT_REDRAW_EXPERIMENT,
            {
                "layer_names": {dashboard.DISTRICTS},
                "remove_scope": {dashboard.DISTRICTS},
                "dirty_scope": dashboard.DIRTY_DISTRICTS,
                "mode": "district-readd",
            },
        ),
        ("remove", {dashboard.DISTRICTS}),
        ("add", {dashboard.DISTRICTS}),
        ("refresh", {dashboard.DISTRICTS}),
    ]
    assert logs == [("REBUILD", "district-ring failed; falling back to district-readd")]


def test_standalone_rebuild_emits_perf_summary(monkeypatch):
    """Verify timer/checkpoint rebuilds log perf outside turn sessions."""

    messages = object()
    logs = []
    monkeypatch.delenv(dashboard.REDRAW_EXPERIMENT_ENV, raising=False)
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: None)
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "run_redraw_experiment", lambda *args, **kwargs: True)
    monkeypatch.setattr(_perf, "_log", lambda messages_arg, tag, text: logs.append((tag, text)))
    _perf.set_enabled(True)
    try:
        dashboard.rebuild_output_layers({}, messages, layer_names={dashboard.DISTRICTS}, dirty_scope=dashboard.DIRTY_DISTRICTS)
    finally:
        _perf.set_enabled(None)

    perf_lines = [text for tag, text in logs if tag == "PERF"]
    assert len(perf_lines) == 1
    assert perf_lines[0].startswith("rebuild=")
    assert "experiment_district-ring=" in perf_lines[0]


def test_rebuild_inside_turn_session_does_not_emit_duplicate_perf_summary(monkeypatch):
    """Verify action rebuilds stay nested under the surrounding turn perf session."""

    messages = object()
    logs = []
    monkeypatch.setattr(dashboard, "clear_output_selections", lambda paths: None)
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages, layer_names=None: None)
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages, layer_names=None: None)
    monkeypatch.setattr(_perf, "_log", lambda messages_arg, tag, text: logs.append((tag, text)))
    _perf.set_enabled(True)
    try:
        with _perf.perf_session("turn=test", messages):
            dashboard.rebuild_output_layers({}, messages, layer_names={dashboard.DISTRICTS}, dirty_scope=dashboard.DIRTY_DISTRICTS)
    finally:
        _perf.set_enabled(None)

    perf_lines = [text for tag, text in logs if tag == "PERF"]
    assert len(perf_lines) == 1
    assert perf_lines[0].startswith("turn=test=")
    assert "rebuild=" in perf_lines[0]


def test_run_redraw_benchmark_cycles_variants_and_restores_selection(monkeypatch):
    """Verify the GP benchmark emits comparable runs without stealing the selected experiment."""

    calls = []
    logs = []
    monkeypatch.setenv(dashboard.REDRAW_EXPERIMENT_ENV, "predrawn-rehydrate")
    monkeypatch.setattr(dashboard, "_log", lambda messages_arg, tag, text: logs.append((tag, text)))
    monkeypatch.setattr(dashboard.time, "perf_counter", lambda: len(calls) + len(logs) / 1000)

    def fake_rebuild(paths, messages, layer_names=None, dirty_scope=None):
        calls.append((dashboard.os.environ.get(dashboard.REDRAW_EXPERIMENT_ENV, ""), layer_names, dirty_scope))

    monkeypatch.setattr(dashboard, "rebuild_output_layers", fake_rebuild)

    dashboard.run_redraw_benchmark({"districts": "districts"}, object(), runs=2)

    assert dashboard.os.environ[dashboard.REDRAW_EXPERIMENT_ENV] == "predrawn-rehydrate"
    expected_per_run = len(dashboard.REDRAW_BENCHMARK_VARIANTS) * len(dashboard.REDRAW_BENCHMARK_SCOPES)
    assert len(calls) == expected_per_run * 2
    assert calls[0] == ("", {dashboard.DISTRICTS}, dashboard.DIRTY_DISTRICTS)
    assert calls[1] == ("", {dashboard.DISTRICTS}, dashboard.DIRTY_DISTRICTS)
    assert calls[2] == ("", {dashboard.DISTRICTS, dashboard.POINTS}, dashboard.DIRTY_DISTRICTS)
    assert any(tag == "BENCH" and text.startswith("start runs=2") for tag, text in logs)
    assert any(tag == "BENCH" and "variant=default scope=districts run=1" in text for tag, text in logs)
    assert any(tag == "BENCH" and text == "done" for tag, text in logs)


def test_final_audit_report_includes_grade_flavor():
    """Verify the inline final audit carries PASS/CONDITIONAL/FAIL ending flavor (#7)."""

    for grade in ("PASS", "CONDITIONAL", "FAIL"):
        report = dashboard._final_audit_report(grade, "Audit score summary.")
        assert report.startswith(f"Final audit: {grade}.")
        assert dashboard.FINAL_AUDIT_FLAVOR[grade] in report
        assert "Audit score summary." in report
    # An unknown/blank grade still closes the file gracefully.
    fallback = dashboard._final_audit_report("", "Audit score summary.")
    assert "Audit closes the current file." in fallback


def test_filed_report_text_includes_local_decision_changes():
    """Verify filed reports summarize local metric and feature changes."""

    result = rules.DecisionResult(
        True,
        "approve",
        "CASE-local",
        "approved",
        district_deltas={"D0000": {"activity": 2, "friction": -1, "services": 4}},
        feature_updates={"F-market": {"status": "active", "condition": 72, "maintenance_due_turn": 5}},
    )

    report = dashboard._filed_report_text(result)

    assert report.startswith("approved Local changes:")
    assert "D0000 act +2" in report
    assert "fric -1" in report
    assert "serv +4" in report
    assert "F-market active condition 72 due 5" in report


def test_start_new_game_replaces_rows_and_map_layers(monkeypatch):
    """Verify New Game rewrites rows, map layers, and controller state."""

    controller = dashboard.DashboardController({"districts": "districts"}, "old_layer", 2026, object())
    controller.selected_item_id = "CASE-old"
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    order = []

    monkeypatch.setattr(dashboard, "clear_game_rows", lambda paths: order.append("clear"))
    monkeypatch.setattr(dashboard, "create_district_board", lambda paths, seed, messages: order.append(("districts", seed)))
    monkeypatch.setattr(dashboard, "seed_city_features", lambda paths, seed, messages: order.append(("city", seed)))
    monkeypatch.setattr(dashboard, "write_state", lambda paths, state: order.append(("state", state.turn)))
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda paths, seed, messages: order.append(("docket", seed)))
    monkeypatch.setattr(dashboard, "remove_outputs_from_map", lambda messages: order.append("remove"))
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: order.append("map"))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages: order.append("refresh"))

    controller.start_new_game(99)

    assert order == ["clear", ("districts", 99), ("city", 99), ("state", 1), ("docket", 99), "remove", "map", "refresh"]
    assert controller.seed == 99
    assert controller.district_layer == dashboard.DISTRICTS
    assert controller.selected_item_id == ""
    assert controller.status_text == "New game started with seed 99."


def test_scorecard_files_report_tab_without_dialog(monkeypatch):
    """Verify Scorecard becomes a selectable dashboard report tab."""

    controller = dashboard.DashboardController({"state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    state = rules.CityState()
    districts = {"D0000": _profile("D0000")}

    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])

    controller.show_scorecard()

    assert controller.report_tabs
    assert controller.report_tabs[-1].kind == "scorecard"
    assert controller.report_tabs[-1].selected is True
    assert controller.selected_report_id == controller.report_tabs[-1].report_id


def test_dashboard_enforces_startup_geometry_after_window_maps():
    """Verify startup sizing claims the left half of the working display."""

    class FakeRoot:
        def __init__(self):
            self.calls = []
            self.width = 900
            self.height = 780
            self.screen_width = 2560
            self.screen_height = 1440

        def geometry(self, value=None):
            if value is not None:
                self.calls.append(("geometry", value))

        def minsize(self, width, height):
            self.calls.append(("minsize", width, height))

        def update_idletasks(self):
            self.calls.append(("update",))

        def winfo_width(self):
            return self.width

        def winfo_height(self):
            return self.height

        def winfo_screenwidth(self):
            return self.screen_width

        def winfo_screenheight(self):
            return self.screen_height

        def after(self, delay, callback):
            self.calls.append(("after", delay))
            callback()
            return "after-1"

    root = FakeRoot()

    dashboard._configure_dashboard_window(root)

    assert ("minsize", 1180, 860) in root.calls
    assert ("geometry", "1280x1400+0+0") in root.calls
    assert root.calls.count(("geometry", "1280x1400+0+0")) == 2


def test_selecting_application_tab_returns_to_applications_and_updates_map_context(monkeypatch):
    """Verify nested application selection owns the active case and map highlight."""

    item = rules.DocketItem("CASE-select", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)
    controller = dashboard.DashboardController({"docket": "docket"}, "district_layer", 2026, object())
    controller.selected_item_id = ""
    controller.selected_desk_tab = "reports"
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    calls = []

    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(
        dashboard,
        "select_case_context",
        lambda paths, district_layer, docket_item, seed, messages: calls.append((district_layer, docket_item.item_id, seed)),
    )
    controller.reload = lambda **kwargs: calls.append(("reload", controller.selected_desk_tab, controller.selected_item_id))

    controller.select_item(item.item_id)

    assert controller.selected_item_id == item.item_id
    assert controller.selected_desk_tab == "applications"
    assert calls == [("district_layer", item.item_id, 2026), ("reload", "applications", item.item_id)]


def test_recording_normal_decision_report_stays_on_applications():
    """Verify filed decision reports do not interrupt application triage."""

    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.selected_desk_tab = "applications"

    controller._record_receipt("Street Vendor", "approved Local changes:", ["D0000"], rules.CityState(turn=2))

    assert controller.report_tabs[-1].title == "Street Vendor"
    assert controller.selected_report_id == controller.report_tabs[-1].report_id
    assert controller.selected_desk_tab == "applications"


def test_finish_decision_selects_next_open_application_and_updates_map_context(monkeypatch):
    """Verify successful decisions advance triage to the next open app."""

    resolved = rules.DocketItem("CASE-done", "street_vendor_compact", "Done", "POINT", 1, status="approved")
    next_item = rules.DocketItem("CASE-next", "street_vendor_compact", "Next", "POINT", 1, target_cell_ids=["D0001"])
    state = rules.CityState(turn=2)
    districts = {"D0001": _profile("D0001")}
    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    calls = []

    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: calls.append("districts"))
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: calls.append("state"))
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: calls.append("projects"))
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: calls.append("docket"))
    monkeypatch.setattr(dashboard, "action_log", lambda *args, **kwargs: calls.append("log"))
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: calls.append("command"))
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: calls.append("rebuild"))
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [resolved, next_item])
    monkeypatch.setattr(
        dashboard,
        "select_case_context",
        lambda paths, district_layer, docket_item, seed, messages: calls.append(("context", docket_item.item_id)),
    )
    monkeypatch.setattr(controller, "_schedule_queue_autoclose", lambda: calls.append("autoclose"))

    result = rules.DecisionResult(True, "approve", resolved.item_id, "approved", affected_cell_ids=["D0000"])

    controller._finish_decision("CMD-1", resolved, state, districts, {}, result)

    assert controller.selected_item_id == next_item.item_id
    assert controller.selected_desk_tab == "applications"
    assert ("context", next_item.item_id) in calls
    assert "autoclose" not in calls


def test_finish_decision_schedules_queue_autoclose_when_no_open_apps(monkeypatch):
    """Verify clearing the queue arms the end-week countdown."""

    resolved = rules.DocketItem("CASE-done", "street_vendor_compact", "Done", "POINT", 1, status="approved")
    state = rules.CityState(turn=2)
    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    calls = []

    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [resolved])
    monkeypatch.setattr(controller, "_schedule_queue_autoclose", lambda: calls.append("autoclose"))

    result = rules.DecisionResult(True, "deny", resolved.item_id, "denied", affected_cell_ids=[])

    controller._finish_decision("CMD-1", resolved, state, {}, {}, result)

    assert controller.selected_item_id == ""
    assert controller.selected_desk_tab == "applications"
    assert calls == ["autoclose"]


def test_finish_decision_passes_dirty_district_scope_to_rebuild(monkeypatch):
    """Verify decisions declare their district render dirtiness explicitly."""

    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller.status_var = dashboard._StatusProxy(controller)
    item = rules.DocketItem("open", "street_vendor_compact", "Street Vendor Compact", "POINT", 1, target_cell_ids=["D0000"])
    state = rules.CityState()
    result = rules.DecisionResult(True, "approve", item.item_id, "Approved.", affected_cell_ids=["D0000"])
    calls = []

    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda paths, messages, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(controller, "_schedule_queue_autoclose", lambda: None)

    controller._finish_decision("CMD-1", item, state, {}, {}, result, layer_names={dashboard.DISTRICTS, dashboard.POINTS})

    assert calls == [{"layer_names": {dashboard.DISTRICTS, dashboard.POINTS}, "dirty_scope": dashboard.DIRTY_DISTRICTS}]


def test_cancel_queue_autoclose_cancels_scheduled_callback():
    """Verify explicit cancel clears the queue auto-close timer."""

    canceled = []
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: canceled.append("reload")
    controller._queue_autoclose_after_id = "after-1"
    controller._queue_autoclose_active = True
    controller.root = SimpleNamespace(after_cancel=lambda ident: canceled.append(("cancel", ident)))

    controller.cancel_queue_autoclose()

    assert controller._queue_autoclose_after_id is None
    assert controller._queue_autoclose_active is False
    assert ("cancel", "after-1") in canceled


def test_selecting_reports_pauses_queue_autoclose():
    """Verify reviewing reports pauses pending queue auto-close."""

    canceled = []
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.reload = lambda **kwargs: canceled.append("reload")
    controller._queue_autoclose_after_id = "after-1"
    controller._queue_autoclose_active = True
    controller.root = SimpleNamespace(after_cancel=lambda ident: canceled.append(("cancel", ident)))

    controller.select_desk_tab("reports")

    assert controller.selected_desk_tab == "reports"
    assert controller._queue_autoclose_active is False
    assert ("cancel", "after-1") in canceled


def test_week_deadline_is_two_and_half_minutes_split_across_five_days():
    """Verify the faster pacing constants keep the five office-day structure."""

    assert dashboard.WEEK_DEADLINE_SECONDS == 150
    assert len(dashboard.WORK_WEEK_DAYS) == 5
    assert dashboard.WORK_DAY_SECONDS == 30


def test_queue_autoclose_defaults_to_two_seconds():
    """Verify queue clear auto-close uses the faster default delay."""

    calls = []
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.status_var = dashboard._StatusProxy(controller)

    class FakeRoot:
        def after(self, delay, callback):
            calls.append(("after", delay, callback.__name__))
            return "after-1"

    controller.root = FakeRoot()

    controller._schedule_queue_autoclose()

    assert controller._queue_autoclose_active is True
    assert controller._queue_autoclose_seconds == 2
    assert calls == [("after", 2000, "_queue_autoclose_tick")]
    assert controller.status_text == "Queue cleared. Week closes automatically in 2 seconds."


def test_end_game_closes_dashboard_without_clearing_rows():
    """Verify End Game only closes the dashboard window."""

    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    destroyed = []
    controller.root = SimpleNamespace(destroy=lambda: destroyed.append("destroy"))

    controller.end_game()

    assert destroyed == ["destroy"]


def test_advance_turn_after_final_audit_is_idempotent(monkeypatch):
    """Verify repeated final-audit closes do not mutate gameplay rows."""

    controller = dashboard.DashboardController({"state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    state = rules.CityState(turn=12, status="complete", audit_stage=2)
    order = []

    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: order.append("command"))
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: order.append("state"))
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: order.append("projects"))
    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: order.append("districts"))
    monkeypatch.setattr(dashboard, "write_active_features", lambda *args, **kwargs: order.append("features"))
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda *args, **kwargs: order.append("docket"))
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: order.append("rebuild"))

    controller.advance_turn()

    assert order == ["command", "rebuild"]
    assert controller.status_text == "Final audit already filed. Scorecard: CONDITIONAL."
    assert controller.last_receipt is not None
    assert controller.last_receipt.title.startswith("Final Audit:")


def test_advance_turn_records_inline_final_audit_receipt(monkeypatch):
    """Verify manual week-twelve closure shows the final audit inline."""

    controller = dashboard.DashboardController({"state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    state = rules.CityState(turn=12)
    districts = {"D0000": _profile("D0000")}
    order = []

    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: order.append("command"))
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: order.append("state"))
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: order.append("projects"))
    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: order.append("districts"))
    monkeypatch.setattr(dashboard, "write_active_features", lambda *args, **kwargs: order.append("features"))
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda *args, **kwargs: order.append("docket"))
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: order.append("rebuild"))

    controller.advance_turn()

    assert state.status == "complete"
    assert "docket" not in order
    assert controller.last_receipt is not None
    assert controller.last_receipt.title.startswith("Final Audit:")
    assert "Audit" in controller.last_receipt.report
    assert controller.selected_desk_tab == "reports"
    assert controller.report_tabs[-1].kind == "scorecard"
    assert controller.report_tabs[-1].selected is True
    assert controller._deadline_running is False


def test_completed_game_reloads_inline_final_audit_receipt(monkeypatch):
    """Verify completed saves keep the final audit visible after reload."""

    controller = dashboard.DashboardController({"state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    state = rules.CityState(turn=12, status="complete", audit_stage=2)
    districts = {"D0000": _profile("D0000")}

    class FakeView:
        def __init__(self):
            self.model = None

        def render(self, model):
            self.model = model

    controller.view = FakeView()
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)

    controller.reload()

    assert controller.view.model.receipt is not None
    assert controller.view.model.receipt.title.startswith("Final Audit:")
    assert controller.view.model.selected_desk_tab == "reports"
    assert controller.view.model.report_tabs[-1].kind == "scorecard"
    rows_by_label = {row.label: row for row in controller.view.model.ledger_rows}
    assert rows_by_label["Week"].value == "12/12 CLOSED"
    assert rows_by_label["Office Standing"].label == "Office Standing"


def test_deadline_final_week_records_same_inline_final_audit_receipt(monkeypatch):
    """Verify timer-driven final closure uses the same inline ending path."""

    controller = dashboard.DashboardController({"districts": "districts", "state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    state = rules.CityState(turn=12)
    districts = {"D0000": _profile("D0000")}

    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_active_features", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: None)
    controller.reload = lambda **kwargs: None

    controller.advance_turn(auto=True)

    assert controller.status_text.startswith("Auto-deadline: Final audit:")
    assert controller.last_receipt is not None
    assert controller.selected_desk_tab == "reports"
    assert controller.report_tabs[-1].kind == "scorecard"
    assert controller.last_receipt.title.startswith("Final Audit:")


def test_advance_turn_readds_only_generated_support_layers_after_generating_new_proposals(monkeypatch):
    """Verify week close redraws only support layers that received proposals."""

    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    state = rules.CityState(turn=2)
    calls = []

    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard.rules, "advance_turn_result", lambda *args, **kwargs: SimpleNamespace(report="Week closed."))
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_active_features", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: None)
    generated = [
        rules.DocketItem("CASE-point", "street_vendor_compact", "Street Vendor Compact", "POINT", 2),
        rules.DocketItem("CASE-line", "connector_corridor", "Connector Corridor", "LINE", 2),
    ]
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda *args, **kwargs: generated)
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda paths, messages, **kwargs: calls.append(kwargs))

    controller.advance_turn()

    assert calls == [
        {
            "remove_scope_override": {
                dashboard.DISTRICTS,
                dashboard.POINTS,
                dashboard.LINES,
            },
            "layer_names": {
                dashboard.DISTRICTS,
                dashboard.POINTS,
                dashboard.LINES,
            },
            "redraw_experiment": "district-ring",
        }
    ]


def test_prepare_dashboard_session_regenerates_missing_docket_for_saved_game(monkeypatch):
    """Verify resumable games repair an empty docket table."""

    counts = {"districts": 25, "state": 1, "docket": 0}
    order = []

    monkeypatch.setattr(dashboard, "_row_count", lambda path: counts[path])
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: order.append("map"))
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda paths, seed, messages: order.append(("docket", seed)))
    monkeypatch.setattr(dashboard, "refresh_all", lambda paths, messages: order.append("refresh"))
    monkeypatch.setattr(dashboard, "_log", lambda *args: None)

    seed = dashboard.prepare_dashboard_session({"districts": "districts", "state": "state", "docket": "docket"}, 2026, object())

    assert seed == 2026
    assert order == ["map", ("docket", 2026), "refresh"]


def test_generate_docket_rows_uses_rules_default_four_item_docket(monkeypatch):
    """Verify persisted ArcGIS dockets follow the rules default row count."""

    inserted = []
    saved_states = []
    paths = {"docket": "docket", "state": "state"}
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}

    class FakeInsertCursor:
        """Minimal InsertCursor stand-in that records rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def insertRow(self, row):
            inserted.append(row)

    monkeypatch.setattr(store, "read_state", lambda _paths: state)
    monkeypatch.setattr(store, "read_districts", lambda _paths: districts)
    monkeypatch.setattr(store, "read_active_features", lambda _paths: [])
    monkeypatch.setattr(store, "read_projects", lambda _paths: {})
    monkeypatch.setattr(store, "read_docket", lambda _paths: [])
    monkeypatch.setattr(store, "write_state", lambda _paths, state_arg: saved_states.append(dict(state_arg.pending_followups)))
    monkeypatch.setattr(store, "_log", lambda *args: None)
    monkeypatch.setattr(store.arcpy, "management", SimpleNamespace(DeleteRows=lambda _path: None), raising=False)
    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(InsertCursor=FakeInsertCursor), raising=False)
    monkeypatch.setattr("toolbox.permit_office_arcgis.geometry.seed_docket_proposals", lambda *args: None)

    items = store.generate_docket_rows(paths, 2026, object())

    assert len(items) == 4
    assert len(inserted) == 4
    assert saved_states == [{}]


def test_state_persists_pending_followups_json(monkeypatch):
    """Verify ArcGIS state rows round-trip pending momentum follow-ups."""

    rows = []
    paths = {"state": "state"}
    state = rules.CityState()
    state.pending_followups = {
        "expire-vendor": rules.CIVIC_INCIDENT_TEMPLATE_ID,
        "expire-site": rules.ENFORCEMENT_TEMPLATE_ID,
    }

    class FakeInsertCursor:
        """Minimal InsertCursor stand-in for state rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def insertRow(self, row):
            rows.append(tuple(row))

    class FakeSearchCursor:
        """Minimal SearchCursor stand-in for state rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return iter(rows)

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(store.arcpy, "management", SimpleNamespace(DeleteRows=lambda _path: rows.clear()), raising=False)
    monkeypatch.setattr(
        store.arcpy,
        "da",
        SimpleNamespace(InsertCursor=FakeInsertCursor, SearchCursor=FakeSearchCursor),
        raising=False,
    )

    store.write_state(paths, state)
    restored = store.read_state(paths)

    assert restored.pending_followups == state.pending_followups


def test_state_persists_type_ledger_json(monkeypatch):
    """Verify ArcGIS state rows round-trip hidden type pressure memory."""

    rows = []
    paths = {"state": "state"}
    state = rules.CityState()
    districts = {
        profile.cell_id: profile
        for profile in rules.generate_district_profiles(rows=2, cols=2, seed=2026)
    }
    state.type_ledger = rules.rebuild_type_ledger(districts)

    class FakeInsertCursor:
        """Minimal InsertCursor stand-in for state rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def insertRow(self, row):
            rows.append(tuple(row))

    class FakeSearchCursor:
        """Minimal SearchCursor stand-in for state rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return iter(rows)

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(store.arcpy, "management", SimpleNamespace(DeleteRows=lambda _path: rows.clear()), raising=False)
    monkeypatch.setattr(
        store.arcpy,
        "da",
        SimpleNamespace(InsertCursor=FakeInsertCursor, SearchCursor=FakeSearchCursor),
        raising=False,
    )

    store.write_state(paths, state)
    restored = store.read_state(paths)

    assert restored.type_ledger == state.type_ledger


def test_schema_declares_buyout_identity_district_fields():
    """Verify district storage has columns for Task 5 buyout transition state."""

    fields = {name: (field_type, alias, length) for name, field_type, alias, length in schema.DISTRICT_FIELDS}

    assert fields["prior_district_type"] == ("TEXT", "Prior District Type", 32)
    assert fields["identity_state"] == ("TEXT", "Identity State", 32)
    assert fields["contesting_cell_id"] == ("TEXT", "Contesting District ID", 32)
    assert fields["contesting_type"] == ("TEXT", "Contesting Type", 32)
    assert fields["transition_due_turn"] == ("LONG", "Transition Due Week", None)
    assert fields["buyout_pressure"] == ("LONG", "Buyout Pressure", None)
    assert fields["last_buyout_report"] == ("TEXT", "Last Buyout Report", 512)


def test_schema_declares_renamed_city_health_district_fields():
    """Verify persisted district health fields use the renamed model."""

    fields = {name: (field_type, alias, length) for name, field_type, alias, length in schema.DISTRICT_FIELDS}

    assert fields["activity"] == ("LONG", "Activity", None)
    assert fields["friction"] == ("LONG", "Civic Friction", None)
    assert fields["trust"] == ("LONG", "Trust", None)
    assert fields["exposure"] == ("LONG", "Exposure", None)
    for legacy in ("prosperity", "unrest", "culture", "risk"):
        assert legacy not in fields


def test_schema_migrates_legacy_city_health_fields(monkeypatch):
    """Verify existing boards backfill renamed fields from legacy columns."""

    rows = [
        {"activity": None, "prosperity": 41, "friction": None, "unrest": 22, "trust": None, "culture": 55, "exposure": None, "risk": 18},
        {"activity": None, "prosperity": 62, "friction": None, "unrest": 31, "trust": None, "culture": 44, "exposure": None, "risk": 27},
    ]
    deleted = []

    class Field:
        def __init__(self, name):
            self.name = name

    class FakeUpdateCursor:
        def __init__(self, _path, fields):
            self.fields = fields
            self.index = 0
            self.current = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            if self.index >= len(rows):
                raise StopIteration
            self.current = rows[self.index]
            self.index += 1
            return [self.current.get(field) for field in self.fields]

        def updateRow(self, values):
            for field, value in zip(self.fields, values):
                self.current[field] = value

    monkeypatch.setattr(schema.arcpy, "ListFields", lambda _path: [Field(name) for name in rows[0]], raising=False)
    monkeypatch.setattr(schema.arcpy, "da", SimpleNamespace(UpdateCursor=FakeUpdateCursor), raising=False)
    monkeypatch.setattr(schema.arcpy, "management", SimpleNamespace(DeleteField=lambda _path, fields: deleted.extend(fields)), raising=False)
    monkeypatch.setattr(schema, "_warn", lambda *args: None)

    schema.migrate_legacy_city_health_fields("districts", object())

    assert rows[0]["activity"] == 41
    assert rows[0]["friction"] == 22
    assert rows[0]["trust"] == 55
    assert rows[0]["exposure"] == 18
    assert deleted == ["prosperity", "unrest", "culture", "risk"]


def test_state_storage_writes_renamed_city_health_keys(monkeypatch):
    """Verify city state persists renamed health keys without legacy rows."""

    rows = []
    paths = {"state": "state"}
    state = rules.CityState(activity=61, friction=23, trust=52, exposure=17)

    class FakeInsertCursor:
        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def insertRow(self, row):
            rows.append(tuple(row))

    monkeypatch.setattr(store.arcpy, "management", SimpleNamespace(DeleteRows=lambda _path: rows.clear()), raising=False)
    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(InsertCursor=FakeInsertCursor), raising=False)

    store.write_state(paths, state)

    keys = {row[0]: row for row in rows}
    assert keys["activity"][2] == 61
    assert keys["friction"][2] == 23
    assert keys["trust"][2] == 52
    assert keys["exposure"][2] == 17
    for legacy in ("prosperity", "unrest", "culture", "risk"):
        assert legacy not in keys


def test_state_storage_reads_legacy_city_health_keys(monkeypatch):
    """Verify legacy state rows hydrate the renamed city health fields."""

    rows = [
        ("turn", "1", 1),
        ("prosperity", "61", 61),
        ("unrest", "23", 23),
        ("culture", "52", 52),
        ("risk", "17", 17),
    ]
    paths = {"state": "state"}

    class FakeSearchCursor:
        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return iter(rows)

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(SearchCursor=FakeSearchCursor), raising=False)

    restored = store.read_state(paths)

    assert restored.activity == 61
    assert restored.friction == 23
    assert restored.trust == 52
    assert restored.exposure == 17


def test_state_storage_upgrades_active_legacy_six_week_games(monkeypatch):
    """Verify old active six-week saves resume with the current season length."""

    rows = [
        ("turn", "1", 1),
        ("max_turns", "6", 6),
        ("status", "playing", None),
    ]
    paths = {"state": "state"}

    class FakeSearchCursor:
        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return iter(rows)

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(SearchCursor=FakeSearchCursor), raising=False)

    restored = store.read_state(paths)

    assert restored.turn == 1
    assert restored.max_turns == 12


def test_district_storage_round_trips_buyout_transition_state():
    """Verify ArcGIS district rows persist Task 5 identity and conversion fields."""

    paths = {"districts": "districts"}
    rows = [
        {"cell_id": "A"},
        {"cell_id": "B"},
    ]
    converted = rules.DistrictProfile("A", "Converted Row", 1000, 46, 30, 35, 20, 40, "mercantile")
    converted.prior_district_type = "residential"
    converted.identity_state = "converted"
    converted.buyout_pressure = 3
    converted.last_buyout_report = "A converted from residential to mercantile."
    contested = rules.DistrictProfile("B", "Contested Row", 1000, 38, 25, 35, 20, 40, "residential")
    contested.identity_state = "contested"
    contested.contesting_cell_id = "A"
    contested.contesting_type = "mercantile"
    contested.transition_due_turn = 4
    contested.buyout_pressure = 6
    contested.last_buyout_report = "B entered contested buyout from A."
    districts = {"A": converted, "B": contested}

    class FakeSearchCursor:
        """Dictionary-backed SearchCursor for district storage tests."""

        def __init__(self, _path, fields):
            self.projected = [[row.get(field) for field in fields] for row in rows]

        def __enter__(self):
            return iter(self.projected)

        def __exit__(self, *_args):
            return False

    class FakeUpdateCursor:
        """Dictionary-backed UpdateCursor for district storage tests."""

        def __init__(self, _path, fields):
            self.fields = fields
            self.index = 0
            self.current = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            if self.index >= len(rows):
                raise StopIteration
            self.current = rows[self.index]
            self.index += 1
            return [self.current.get(field) for field in self.fields]

        def updateRow(self, values):
            for field, value in zip(self.fields, values):
                self.current[field] = value

    previous_da = getattr(store.arcpy, "da", None)
    store.arcpy.da = SimpleNamespace(SearchCursor=FakeSearchCursor, UpdateCursor=FakeUpdateCursor)
    try:
        store.write_district_updates(paths, districts, "Buyout report")
        restored = store.read_districts(paths)
    finally:
        if previous_da is None:
            delattr(store.arcpy, "da")
        else:
            store.arcpy.da = previous_da

    assert rows[0]["district_type"] == "mercantile"
    assert restored["A"].district_type == "mercantile"
    assert restored["A"].prior_district_type == "residential"
    assert restored["A"].identity_state == "converted"
    assert restored["A"].buyout_pressure == 3
    assert restored["A"].last_buyout_report == "A converted from residential to mercantile."
    assert restored["B"].identity_state == "contested"
    assert restored["B"].contesting_cell_id == "A"
    assert restored["B"].contesting_type == "mercantile"
    assert restored["B"].transition_due_turn == 4
    assert restored["B"].buyout_pressure == 6


def test_generate_docket_rows_persists_consumed_pending_followups(monkeypatch):
    """Verify generated momentum rows are removed from saved state."""

    inserted = []
    saved_states = []
    paths = {"docket": "docket", "state": "state"}
    state = rules.CityState()
    state.pending_followups = {
        f"expire-{idx}": rules.CIVIC_INCIDENT_TEMPLATE_ID
        for idx in range(5)
    }

    class FakeInsertCursor:
        """Minimal InsertCursor stand-in that records docket rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def insertRow(self, row):
            inserted.append(row)

    monkeypatch.setattr(store, "read_state", lambda _paths: state)
    monkeypatch.setattr(store, "read_districts", lambda _paths: {})
    monkeypatch.setattr(store, "read_active_features", lambda _paths: [])
    monkeypatch.setattr(store, "read_projects", lambda _paths: {})
    monkeypatch.setattr(store, "read_docket", lambda _paths: [])
    monkeypatch.setattr(store, "write_state", lambda _paths, state_arg: saved_states.append(dict(state_arg.pending_followups)))
    monkeypatch.setattr(store, "_log", lambda *args: None)
    monkeypatch.setattr(store.arcpy, "management", SimpleNamespace(DeleteRows=lambda _path: None), raising=False)
    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(InsertCursor=FakeInsertCursor), raising=False)
    monkeypatch.setattr("toolbox.permit_office_arcgis.geometry.seed_docket_proposals", lambda *args: None)

    items = store.generate_docket_rows(paths, 2026, object())

    assert [item.origin_item_id for item in items] == [
        "momentum:expire-0",
        "momentum:expire-1",
        "momentum:expire-2",
        "momentum:expire-3",
    ]
    assert state.pending_followups == {"expire-4": rules.CIVIC_INCIDENT_TEMPLATE_ID}
    assert saved_states == [{"expire-4": rules.CIVIC_INCIDENT_TEMPLATE_ID}]
    assert len(inserted) == 4


def test_generate_docket_rows_carries_existing_mandatory_context(monkeypatch):
    """Verify carried rows survive docket-table regeneration with context."""

    inserted = []
    paths = {"docket": "docket", "state": "state"}
    state = rules.CityState(turn=2)
    carried = rules.DocketItem(
        "fire-followup",
        "fire_budget_escalation",
        "Fire Budget Escalation",
        "POLYGON",
        1,
        status="carried",
        target_cell_ids=["D0000", "D0001"],
        preview_text="Prior fire budget review.",
        stakeholder="fire_department",
        origin_item_id="origin-fire",
        target_rule="Select fire coverage districts.",
        project_id="project-fire",
        chain_step_id="fire-step",
        priority=3,
        due_turn=4,
        subject_feature_id="F-fire",
        case_json={"inspection": {"exposure": "high"}},
    )

    class FakeInsertCursor:
        """Minimal InsertCursor stand-in that records regenerated docket rows."""

        def __init__(self, _path, _fields):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def insertRow(self, row):
            inserted.append(row)

    monkeypatch.setattr(store, "read_state", lambda _paths: state)
    monkeypatch.setattr(store, "read_districts", lambda _paths: {})
    monkeypatch.setattr(store, "read_active_features", lambda _paths: [])
    monkeypatch.setattr(store, "read_projects", lambda _paths: {})
    monkeypatch.setattr(store, "read_docket", lambda _paths: [carried])
    monkeypatch.setattr(store, "write_state", lambda *_args: None)
    monkeypatch.setattr(store, "_log", lambda *args: None)
    monkeypatch.setattr(store.arcpy, "management", SimpleNamespace(DeleteRows=lambda _path: None), raising=False)
    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(InsertCursor=FakeInsertCursor), raising=False)
    monkeypatch.setattr("toolbox.permit_office_arcgis.geometry.seed_docket_proposals", lambda *args: None)

    items = store.generate_docket_rows(paths, 2026, object())

    assert items[0].template_id == carried.template_id
    assert items[0].status == "open"
    assert items[0].turn == 2
    assert items[0].target_cell_ids == carried.target_cell_ids
    assert items[0].project_id == carried.project_id
    assert items[0].case_json == carried.case_json
    assert "Carried forward from prior week." in items[0].preview_text
    assert inserted[0][2] == carried.template_id
    assert inserted[0][5] == "open"
    assert inserted[0][7] == "D0000,D0001"
    assert inserted[0][14] == carried.project_id


def test_deadline_timer_formats_equal_office_days(monkeypatch):
    """Verify the faster filing timer divides into equal office days."""

    controller = dashboard.DashboardController({"districts": "districts", "state": "state"}, "district_layer", 2026, object())
    state = rules.CityState(turn=2)

    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: 100.0)

    controller._sync_deadline_timer(state)
    assert controller._deadline_week == 2
    assert controller._deadline_presentation() == ("MON INTAKE 0:30", 0, True)

    expected = (
        (129.0, "MON INTAKE 0:01"),
        (130.0, "TUE INSPECTION 0:30"),
        (159.0, "TUE INSPECTION 0:01"),
        (160.0, "WED COMMENT 0:30"),
        (190.0, "THU ESCALATION 0:30"),
        (220.0, "FRI CLOSE 0:30"),
        (249.0, "FRI CLOSE 0:01"),
    )
    for now, label in expected:
        monkeypatch.setattr(dashboard.time, "monotonic", lambda now=now: now)
        text, _meter, running = controller._deadline_presentation()
        assert text == label
        assert running is True


def test_deadline_ambient_status_uses_current_office_day(monkeypatch):
    """Verify idle status text follows the current office-day note."""

    controller = dashboard.DashboardController({"districts": "districts", "state": "state"}, "district_layer", 2026, object())
    state = rules.CityState(turn=1)

    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: 10.0)
    controller._sync_deadline_timer(state)

    assert controller._display_status_text() == "New applications logged. Triage high-risk packets."

    monkeypatch.setattr(dashboard.time, "monotonic", lambda: 70.0)

    assert controller._display_status_text() == "Public comment window is open. Unresolved cases may draw attention."

    controller.status_text = "Selected Connector Corridor; map context updated."

    assert controller._display_status_text() == "Selected Connector Corridor; map context updated."


def test_deadline_tick_auto_advances_when_expired(monkeypatch):
    """Verify an expired deadline triggers automatic week advance."""

    controller = dashboard.DashboardController({"districts": "districts", "state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller._deadline_running = True
    controller._deadline_started = 100.0
    calls = []

    class FakeRoot:
        """Minimal Tk root stand-in that records scheduled callbacks."""

        def after(self, delay, callback):
            """Record the next scheduled timer tick."""

            calls.append(("after", delay))
            return "after-1"

    class FakeView:
        """Minimal desk view stand-in that records deadline updates."""

        def update_deadline(self, text, meter, running, status_text=None):
            """Record a live deadline presentation update."""

            calls.append(("view", text, meter, running, status_text))

    controller.root = FakeRoot()
    controller.view = FakeView()
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: 401.0)
    monkeypatch.setattr(controller, "advance_turn", lambda auto=False: calls.append(("advance", auto)))

    controller._deadline_tick()

    assert ("advance", True) in calls
    assert ("after", dashboard.TIMER_TICK_MS) in calls


def test_daily_pressure_overlay_writer_updates_only_display_fields(monkeypatch):
    """Verify daily overlays use only display fields in the update cursor."""

    profile = _profile("D0000")
    profile.incident_state = "protest"
    pressured = rules.DistrictProfile("D0001", "D0001", 1000, 50, 20, 35, 25, 90, "civic", housing_capacity=1500, affordability=80)
    rules.normalize_profile(pressured)
    rows = [["D0000", "stable", "old"], ["D0001", "stable", "old"]]
    updated = []

    class FakeCursor:
        """Fake ArcPy update cursor exposing only overlay fields."""

        def __init__(self, _path, fields):
            """Validate the overlay writer requested a narrow field list."""

            assert fields == ["cell_id", "display_state", "last_report"]

        def __enter__(self):
            """Enter the cursor context."""

            return self

        def __exit__(self, *_args):
            """Leave the cursor context without suppressing errors."""

            return False

        def __iter__(self):
            """Iterate fake rows by reference so updates are observable."""

            return iter(rows)

        def updateRow(self, row):
            """Capture a row written by the overlay helper."""

            updated.append(list(row))

    monkeypatch.setattr(store.arcpy, "da", SimpleNamespace(UpdateCursor=FakeCursor), raising=False)

    store.write_daily_pressure_overlays({"districts": "districts"}, {"D0000": profile, "D0001": pressured}, {"D0000": 1, "D0001": 2})

    assert updated[0][1] == "incident"
    assert updated[1][1] == "daily_pressure"
    assert rows[0][0] == "D0000"


def test_deadline_tick_advances_daily_pressure_without_map_rebuild_before_checkpoint(monkeypatch):
    """Verify day ticks persist pressure and update the desk before map checkpoint days."""

    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller._deadline_running = True
    controller._deadline_started = 100.0
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    state = rules.CityState(week_day=0)
    item = rules.DocketItem("open", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1, target_cell_ids=["D0000"])
    profile = rules.DistrictProfile("D0000", "D0000", 1000, 50, 20, 35, 25, 90, "civic", housing_capacity=1500, affordability=80)
    rules.normalize_profile(profile)
    calls = []

    class FakeRoot:
        """Minimal Tk root stand-in that records scheduled callbacks."""

        def after(self, delay, callback):
            """Record the next scheduled timer tick."""

            calls.append(("after", delay))
            return "after-1"

    class FakeView:
        """Minimal desk view stand-in that records deadline updates."""

        def update_deadline(self, text, meter, running, status_text=None):
            """Record a live deadline presentation update."""

            calls.append(("view", text, meter, running, status_text))

    controller.root = FakeRoot()
    controller.view = FakeView()
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: 131.0)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {"D0000": profile})
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "write_state", lambda paths, state_arg: calls.append(("state", state_arg.week_day, dict(state_arg.daily_pressure))))
    monkeypatch.setattr(dashboard, "write_daily_pressure_overlays", lambda paths, districts, pressure: calls.append(("overlay", dict(pressure))))
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda paths, messages, **kwargs: calls.append(("rebuild", kwargs)))

    controller._deadline_tick()

    assert ("state", 1, {"D0000": 1}) in calls
    assert ("overlay", {"D0000": 1}) in calls
    assert not any(kind == "rebuild" for kind, *_rest in calls)
    view_calls = [call for call in calls if call[0] == "view"]
    assert any("1 district pressure" in str(call[4]) for call in view_calls)


def test_deadline_tick_rebuilds_districts_on_midweek_checkpoint(monkeypatch):
    """Verify checkpoint days can pay the district re-add cost deliberately."""

    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller._deadline_running = True
    controller._deadline_started = 100.0
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    state = rules.CityState(week_day=1)
    item = rules.DocketItem("open", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1, target_cell_ids=["D0000"])
    profile = rules.DistrictProfile("D0000", "D0000", 1000, 50, 20, 35, 25, 90, "civic", housing_capacity=1500, affordability=80)
    rules.normalize_profile(profile)
    calls = []

    class FakeRoot:
        """Minimal Tk root stand-in that records scheduled callbacks."""

        def after(self, delay, callback):
            """Record the next scheduled timer tick."""

            calls.append(("after", delay))
            return "after-1"

    class FakeView:
        """Minimal desk view stand-in that records deadline updates."""

        def update_deadline(self, text, meter, running, status_text=None):
            """Record a live deadline presentation update."""

            calls.append(("view", text, meter, running, status_text))

    controller.root = FakeRoot()
    controller.view = FakeView()
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: 161.0)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [item])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {"D0000": profile})
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "write_state", lambda paths, state_arg: calls.append(("state", state_arg.week_day, dict(state_arg.daily_pressure))))
    monkeypatch.setattr(dashboard, "write_daily_pressure_overlays", lambda paths, districts, pressure: calls.append(("overlay", dict(pressure))))
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda paths, messages, **kwargs: calls.append(("rebuild", kwargs)))

    controller._deadline_tick()

    assert ("state", 2, {"D0000": 1}) in calls
    assert ("rebuild", {"layer_names": {dashboard.DISTRICTS}, "dirty_scope": dashboard.DIRTY_DISTRICTS}) in calls


def test_filed_report_local_changes_names_districts():
    """Verify the filed-report local-changes summary reads as district names."""
    result = SimpleNamespace(
        report="Approved Street Vendor Compact.",
        district_deltas={"D0000": {"activity": 3}},
        feature_updates={},
    )
    profile = rules.DistrictProfile("D0000", "Harbor Flats", 1000, 50, 20, 35, 25, 50, "mercantile")
    rules.normalize_profile(profile)

    text = dashboard._filed_report_text(result, {"D0000": profile})

    assert "Harbor Flats" in text
    assert "D0000" not in text


def _raise_scorecard(*_args, **_kwargs):
    """Stand-in scorecard that fails if the cached-grade path calls it."""

    raise AssertionError("rules.scorecard should not be called when audit_grade is provided")


def test_build_desk_model_uses_provided_audit_grade_without_scorecard(monkeypatch):
    """Verify a provided audit grade skips the scorecard recompute entirely."""

    item = rules.DocketItem("CASE-grade", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)
    districts = {"D0000": _profile("D0000")}
    monkeypatch.setattr(desk_model.rules, "scorecard", _raise_scorecard)

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id, audit_grade="CONDITIONAL")

    audit_row = next(row for row in model.ledger_rows if row.label == "Audit")
    assert audit_row.value == desk_model._short_audit_grade("CONDITIONAL")


def test_build_desk_model_without_audit_grade_computes_via_scorecard(monkeypatch):
    """Verify the default path still computes the grade through scorecard."""

    item = rules.DocketItem("CASE-grade", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)
    districts = {"D0000": _profile("D0000")}
    calls = []

    def fake_scorecard(state, districts_arg, features_arg, docket_arg):
        calls.append(True)
        return "PASS", "Audit PASS."

    monkeypatch.setattr(desk_model.rules, "scorecard", fake_scorecard)

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert calls == [True]
    audit_row = next(row for row in model.ledger_rows if row.label == "Audit")
    assert audit_row.value == desk_model._short_audit_grade("PASS")


def test_selection_only_reload_reuses_cached_audit_grade(monkeypatch):
    """Verify back-to-back selection reloads compute the grade at most once."""

    state = rules.CityState()
    districts = {"D0000": _profile("D0000")}
    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    calls = []

    class FakeView:
        def render(self, model):
            pass

    controller.view = FakeView()
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})

    def fake_scorecard(state_arg, districts_arg, features_arg, docket_arg):
        calls.append(True)
        return "PASS", "Audit PASS."

    monkeypatch.setattr(dashboard.rules, "scorecard", fake_scorecard)

    controller.reload()
    controller.reload()

    assert len(calls) == 1


def test_decision_marks_audit_grade_dirty_for_recompute(monkeypatch):
    """Verify a filed decision invalidates the cached grade so it recomputes."""

    item = rules.DocketItem("CASE-finish", "procession_route", "Procession Route", "LINE", 1)
    state = rules.CityState()
    districts = {"D0000": _profile("D0000")}
    controller = dashboard.DashboardController({"districts": "districts"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller._grade_dirty = False

    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_record_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_advance_triage_selection", lambda *args, **kwargs: None)

    result = rules.DecisionResult(True, "approve", item.item_id, "approved", affected_cell_ids=["D0000"])
    controller._finish_decision("CMD-1", item, state, districts, {}, result)

    assert controller._grade_dirty is True


def test_advance_turn_marks_audit_grade_dirty_for_recompute(monkeypatch):
    """Verify advancing the week invalidates the cached audit grade."""

    controller = dashboard.DashboardController({"state": "state"}, "district_layer", 2026, object())
    controller.status_text = ""
    controller.status_var = dashboard._StatusProxy(controller)
    controller.reload = lambda **kwargs: None
    controller._grade_dirty = False
    state = rules.CityState(turn=2)
    districts = {"D0000": _profile("D0000")}

    monkeypatch.setattr(dashboard, "command_insert", lambda paths, action, item_id, target_ids: "CMD-1")
    monkeypatch.setattr(dashboard, "command_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: districts)
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_projects", lambda paths: {})
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_district_updates", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_active_features", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "write_docket_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "generate_docket_rows", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "rebuild_output_layers", lambda *args, **kwargs: None)

    controller.advance_turn()

    assert controller._grade_dirty is True


def test_prepare_dashboard_session_fresh_start_skips_layer_add(monkeypatch):
    """Verify offering a fresh start leaves the saved board and adds no layers."""
    calls = []
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: calls.append("add"))

    dashboard.prepare_dashboard_session({}, 2026, object(), resume=False)

    assert calls == []


def test_prepare_dashboard_session_resume_adds_layers(monkeypatch):
    """Verify a normal resume re-adds the output layers."""
    calls = []
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: calls.append("add"))
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)

    dashboard.prepare_dashboard_session({"docket": "d"}, 2026, object(), resume=True)

    assert calls == ["add"]


def test_deferred_startup_session_runs_layer_work_after_initial_render(monkeypatch):
    """Verify dashboard can draw its first frame before map/session work."""

    controller = dashboard.DashboardController({"districts": "districts", "state": "state", "docket": "docket"}, "district_layer", 2026, object())
    state = rules.CityState(turn=5)
    order = []

    class FakeRoot:
        def after_idle(self, callback):
            order.append("idle")
            callback()
            return "idle-1"

        def after(self, delay, callback):
            order.append(("after", delay))
            callback()
            return "after-1"

    class FakeView:
        def render(self, model):
            order.append("render")

    controller.root = FakeRoot()
    controller.view = FakeView()
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "ensure_active_map", lambda messages: order.append("active_map"))
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: order.append("add"))
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)

    controller.reload()
    controller.schedule_startup_session_preparation()

    assert order.index("render") < order.index("active_map") < order.index("add")
    assert order[:3] == ["render", "idle", ("after", dashboard.STARTUP_SESSION_DELAY_MS)]


def test_deferred_startup_session_detects_fresh_start_after_first_frame(monkeypatch):
    """Verify deferred map probe still flips saved-game/no-layer state safely."""

    controller = dashboard.DashboardController({"districts": "districts", "state": "state", "docket": "docket"}, "district_layer", 2026, object())
    state = rules.CityState(turn=4)
    rendered_game_active = []

    class FakeRoot:
        def after_idle(self, callback):
            callback()
            return "idle-1"

        def after(self, delay, callback):
            assert delay == dashboard.STARTUP_SESSION_DELAY_MS
            callback()
            return "after-1"

    class FakeView:
        def render(self, model):
            rendered_game_active.append(model.game_active)

    controller.root = FakeRoot()
    controller.view = FakeView()
    monkeypatch.setattr(dashboard, "read_state", lambda paths: state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {"D0000": _profile("D0000")})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "ensure_active_map", lambda messages: None)
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: False)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: (_ for _ in ()).throw(AssertionError("fresh start must not add layers")))

    controller.reload()
    controller.schedule_startup_session_preparation()

    assert rendered_game_active == [True, False]
    assert controller._offer_fresh_start is True


def test_resolve_session_state_fresh_start_uses_defaults_without_reading_save(monkeypatch):
    """Verify fresh-start mode renders defaults and never reads the stale save."""
    controller = dashboard.DashboardController({}, "district_layer", 2026, object(), offer_fresh_start=True)
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)

    def _no_read(_paths):
        raise AssertionError("persisted rows must not be read in fresh-start mode")

    monkeypatch.setattr(dashboard, "read_state", _no_read)
    monkeypatch.setattr(dashboard, "read_districts", _no_read)

    state, districts, items, features, offering = controller._resolve_session_state(None, None, None, None)

    assert offering is True
    assert districts == {} and items == [] and features == []
    assert state.turn == rules.CityState().turn


def test_resolve_session_state_normal_reads_persisted_rows(monkeypatch):
    """Verify ordinary reloads read persisted rows (no fresh-start override)."""
    controller = dashboard.DashboardController({}, "district_layer", 2026, object(), offer_fresh_start=False)
    sentinel = rules.CityState(turn=4)
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: sentinel)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {"D0000": "x"})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: ["item"])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: ["feat"])

    state, districts, items, features, offering = controller._resolve_session_state(None, None, None, None)

    assert offering is False
    assert state is sentinel
    assert districts == {"D0000": "x"} and items == ["item"] and features == ["feat"]


def test_startup_experiment_parser_rejects_arcpy_worker_probe_after_live_freeze():
    """Verify ArcPy-bound probes are not selectable after proving unsafe in Pro."""

    assert dashboard.normalize_startup_experiment(None) == dashboard.STARTUP_EXPERIMENT_NONE
    assert dashboard.normalize_startup_experiment("None") == dashboard.STARTUP_EXPERIMENT_NONE
    assert dashboard.normalize_startup_experiment("threaded-cache") == "threaded-cache"
    assert dashboard.normalize_startup_experiment("threaded-arcpy-read-probe") == dashboard.STARTUP_EXPERIMENT_NONE
    assert dashboard.normalize_startup_experiment("surprise") == dashboard.STARTUP_EXPERIMENT_NONE


def test_threaded_arcpy_read_probe_string_is_ignored_without_worker(monkeypatch):
    """Verify stale ArcPy-threading selections fall back to safe startup."""

    controller = dashboard.DashboardController(
        {"state": "state", "districts": "districts", "docket": "docket"},
        "district_layer",
        2026,
        object(),
        startup_experiment="threaded-arcpy-read-probe",
    )
    calls = []

    class BlockingExecutor:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ArcPy probe must not start a worker")

    controller.view = SimpleNamespace(render=lambda model: calls.append("render"))
    monkeypatch.setattr(dashboard.concurrent_futures, "ThreadPoolExecutor", BlockingExecutor)
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: rules.CityState(turn=3))
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})
    monkeypatch.setattr(dashboard, "ensure_active_map", lambda messages: calls.append("active_map"))
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: calls.append("add"))
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)

    controller.reload()
    controller.prepare_startup_session()

    assert controller._startup_experiment == dashboard.STARTUP_EXPERIMENT_NONE
    assert calls == ["render", "active_map", "add"]


def test_threaded_cache_precomputes_current_rows_in_worker_after_main_read(monkeypatch):
    """Verify cache experiment reads rows on main and precomputes that model in worker."""

    controller = dashboard.DashboardController(
        {"state": "state", "districts": "districts", "docket": "docket"},
        "district_layer",
        2026,
        object(),
        startup_experiment="threaded-cache",
    )
    state = rules.CityState(turn=7)
    reads = []
    builds = []
    renders = []

    class FakeView:
        def render(self, model):
            assert not dashboard._startup_worker_active()
            renders.append("render")

    def fake_build(state_arg, districts_arg, items_arg, *args, **kwargs):
        builds.append(("worker" if dashboard._startup_worker_active() else "main", state_arg.turn, dict(districts_arg)))
        return SimpleNamespace(selected_item_id="", selected_report_id="", selected_desk_tab="")

    controller.view = FakeView()
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: reads.append(("state", "worker" if dashboard._startup_worker_active() else "main")) or state)
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: reads.append(("districts", "worker" if dashboard._startup_worker_active() else "main")) or {"D0000": _profile("D0000")})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: reads.append(("docket", "worker" if dashboard._startup_worker_active() else "main")) or [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: reads.append(("features", "worker" if dashboard._startup_worker_active() else "main")) or [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})
    monkeypatch.setattr(dashboard, "ensure_active_map", lambda messages: None)
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: None)
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)
    monkeypatch.setattr(dashboard, "build_desk_model", fake_build)

    controller.reload()
    assert builds == [("main", 7, {"D0000": _profile("D0000")})]
    assert renders == ["render"]

    controller.prepare_startup_session()

    assert all(thread_name == "main" for _kind, thread_name in reads)
    assert ("worker", 7, {"D0000": _profile("D0000")}) in builds
    assert ("main", 7, {"D0000": _profile("D0000")}) in builds


def test_threaded_cache_worker_exception_falls_back_without_blocking_render(monkeypatch):
    """Verify cache precompute errors are logged and normal render continues."""

    controller = dashboard.DashboardController(
        {"state": "state", "districts": "districts", "docket": "docket"},
        "district_layer",
        2026,
        object(),
        startup_experiment="threaded-cache",
    )
    warnings = []
    renders = []

    class FakeView:
        def render(self, model):
            assert not dashboard._startup_worker_active()
            renders.append("render")

    def fake_build(state_arg, districts_arg, items_arg, *args, **kwargs):
        if dashboard._startup_worker_active():
            raise RuntimeError("cache precompute failed")
        return SimpleNamespace(selected_item_id="", selected_report_id="", selected_desk_tab="")

    controller.view = FakeView()
    monkeypatch.setattr(dashboard, "_warn", lambda messages, tag, text: warnings.append((tag, text)))
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: rules.CityState(turn=2))
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})
    monkeypatch.setattr(dashboard, "ensure_active_map", lambda messages: None)
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: None)
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)
    monkeypatch.setattr(dashboard, "build_desk_model", fake_build)
    monkeypatch.setattr(dashboard, "write_state", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("worker must not write")))

    controller.reload()
    assert renders == ["render"]

    controller.prepare_startup_session()

    assert renders == ["render"]
    assert any("threaded-cache failed" in text for _tag, text in warnings)


def test_threaded_arcpy_probe_no_longer_progresses_to_worker_read():
    """Document the live finding: ArcPy reads stay on the main thread only."""

    controller = dashboard.DashboardController(
        {"state": "state", "districts": "districts", "docket": "docket"},
        "district_layer",
        2026,
        object(),
        startup_experiment="threaded-arcpy-read-probe",
    )

    assert controller._startup_experiment == dashboard.STARTUP_EXPERIMENT_NONE


def test_rejected_threaded_arcpy_probe_does_not_delay_main_map_work(monkeypatch):
    """Verify ignored ArcPy probe cannot block startup behind a worker."""

    controller = dashboard.DashboardController(
        {"state": "state", "districts": "districts", "docket": "docket"},
        "district_layer",
        2026,
        object(),
        startup_experiment="threaded-arcpy-read-probe",
    )
    events = []

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ignored ArcPy probe must not allocate a worker")

    controller.view = SimpleNamespace(render=lambda model: None)
    monkeypatch.setattr(dashboard.concurrent_futures, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: rules.CityState(turn=2))
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})

    def ensure_map(messages):
        events.append("active_map")

    monkeypatch.setattr(dashboard, "ensure_active_map", ensure_map)
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: events.append("add"))
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)

    controller.prepare_startup_session()

    assert events == ["active_map", "add"]


def test_threaded_cache_probe_timeout_falls_back_to_synchronous_reload(monkeypatch):
    """Verify startup probe timeout is captured without blocking normal reload."""

    controller = dashboard.DashboardController(
        {"state": "state", "districts": "districts", "docket": "docket"},
        "district_layer",
        2026,
        object(),
        startup_experiment="threaded-cache",
    )
    calls = []
    warnings = []

    class FakeFuture:
        def result(self, timeout=None):
            raise dashboard.concurrent_futures.TimeoutError()

        def cancel(self):
            calls.append("cancel")

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def submit(self, worker, *args):
            calls.append("submit")
            return FakeFuture()

        def shutdown(self, wait=False, cancel_futures=False):
            calls.append(("shutdown", wait, cancel_futures))

    class FakeView:
        def render(self, model):
            calls.append("render")

    controller.view = FakeView()
    monkeypatch.setattr(dashboard.concurrent_futures, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(dashboard, "_warn", lambda messages, tag, text: warnings.append((tag, text)))
    monkeypatch.setattr(dashboard, "has_saved_game", lambda paths: True)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: rules.CityState(turn=2))
    monkeypatch.setattr(dashboard, "read_districts", lambda paths: {})
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: [])
    monkeypatch.setattr(dashboard, "read_active_features", lambda paths: [])
    monkeypatch.setattr(dashboard, "proposal_visible_map", lambda paths: {})
    monkeypatch.setattr(dashboard, "ensure_active_map", lambda messages: None)
    monkeypatch.setattr(dashboard, "output_layers_present", lambda: True)
    monkeypatch.setattr(dashboard, "add_outputs_to_map", lambda paths, messages: None)
    monkeypatch.setattr(dashboard, "_row_count", lambda path: 5)

    controller.reload()
    assert calls == ["render"]

    controller.prepare_startup_session()

    assert "cancel" in calls
    assert ("shutdown", False, True) in calls
    assert "render" in calls
    assert any("threaded-cache timed out" in text for _tag, text in warnings)


def test_ticker_tick_scrolls_marquee_with_lightweight_view_update_without_row_reads(monkeypatch):
    """Verify ambient ticker advances as a marquee without a full ArcGIS reload."""

    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    updates = []
    reloads = []
    controller.status_text = ""
    controller._deadline_running = False
    controller.view = SimpleNamespace(
        model=SimpleNamespace(ticker_items=("one", "two", "three"), deadline_text="", deadline_meter=0, deadline_running=False),
        update_status_marquee=lambda offset: updates.append(offset),
    )
    controller.reload = lambda **kwargs: reloads.append(kwargs)
    monkeypatch.setattr(dashboard, "read_state", lambda paths: (_ for _ in ()).throw(AssertionError("ticker must not read state rows")))
    monkeypatch.setattr(dashboard, "read_docket", lambda paths: (_ for _ in ()).throw(AssertionError("ticker must not read docket rows")))

    controller._ticker_tick()
    controller._ticker_tick()
    controller._ticker_tick()

    assert updates == [dashboard.TICKER_STEP_PX, dashboard.TICKER_STEP_PX * 2, dashboard.TICKER_STEP_PX * 3]
    assert reloads == []


def test_ticker_tick_pauses_when_command_status_is_active():
    """Verify command-specific status text wins over ambient ticker motion."""

    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    updates = []
    controller.status_text = "Decision filed."
    controller._status_hold_until = dashboard.time.monotonic() + 10
    controller.view = SimpleNamespace(
        model=SimpleNamespace(ticker_items=("one", "two"), deadline_text="", deadline_meter=0, deadline_running=False),
        update_status_marquee=lambda offset: updates.append(offset),
    )

    controller._ticker_tick()

    assert updates == []
    assert controller._ticker_index == 0


def test_ticker_tick_resumes_after_transient_command_status_expires(monkeypatch):
    """Verify command status ages out so the ambient ticker visibly rolls again."""

    controller = dashboard.DashboardController({}, "district_layer", 2026, object())
    updates = []
    now = 1000.0
    controller.status_var = dashboard._StatusProxy(controller)
    controller.status_var.set("New game started with seed 99.")
    controller._status_hold_until = now - 0.1
    controller.view = SimpleNamespace(
        model=SimpleNamespace(ticker_items=("wire one", "wire two"), deadline_text="", deadline_meter=0, deadline_running=False),
        update_status_marquee=lambda offset: updates.append(offset),
    )
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: now)

    controller._ticker_tick()

    assert controller.status_text == ""
    assert updates == [dashboard.TICKER_STEP_PX]
