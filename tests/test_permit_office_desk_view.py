"""Pure desk-model tests for compact Permit Office impact buckets."""

from __future__ import annotations

from toolbox import arcpy_permit_office_rules as rules
from toolbox.permit_office_arcgis.desk_view import (
    HEADLINE_METRICS,
    Palette,
    PermitDeskView,
    ReceiptModel,
    ReportTab,
    _Stacker,
    _hazard_summary,
    _maintenance_summary,
    _service_gap_summary,
    build_desk_model,
)


def _vendor_case():
    """Return a vendor docket item with a local target district."""

    item = rules.DocketItem(
        "T01-vendor",
        "street_vendor_compact",
        rules.TEMPLATES["street_vendor_compact"].title,
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )
    profile = rules.DistrictProfile(
        "D0000",
        "Market Row",
        1200,
        48,
        24,
        38,
        27,
        44,
        "mercantile",
        population_mix={"vendors": 3, "homeowners": 2, "students": 1},
        dissatisfaction={"homeowners": 3},
    )
    rules.normalize_profile(profile)
    return item, {profile.cell_id: profile}


def test_uninspected_case_uses_qualitative_impact_buckets():
    """Verify uninspected cases show qualitative impact buckets."""

    item, districts = _vendor_case()

    model = build_desk_model(
        rules.CityState(ap=3, money=60),
        districts,
        [item],
        item.item_id,
        proposal_visible_by_item={item.item_id: True},
        deadline_text="MON INTAKE 1:00",
        deadline_meter=0,
        deadline_running=True,
    )
    buckets = {bucket.label: bucket for bucket in model.case.impact_buckets}

    assert list(buckets) == ["Cost", "City", "Local", "People", "Services", "Budget"]
    assert model.exhibit_visible is True
    assert model.deadline_text == "MON INTAKE 1:00"
    assert model.deadline_running is True
    assert "Issue 1AP/$12" in buckets["Cost"].value
    assert "conditions +$6" in buckets["Cost"].value
    assert "deny 0AP" in buckets["Cost"].value
    assert "activity" in buckets["City"].value
    assert "district(s)" in buckets["Local"].value
    assert "fit" in buckets["Local"].value
    assert "grievance" in buckets["Local"].value
    assert "vendors" in buckets["People"].value
    assert "homeowners" in buckets["People"].value
    assert "gap" in buckets["Services"].value
    assert "rev $4/week" in buckets["Budget"].value
    assert "upkeep $1/week" in buckets["Budget"].value
    assert "inspect for unlicensed spillover" in buckets["Budget"].value
    assert "evidence" not in buckets["Budget"].value


def test_empty_target_copy_uses_retarget_map_language():
    """Verify fallback targeting copy matches the current button label."""

    item = rules.DocketItem("T01-vendor", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1)

    model = build_desk_model(rules.CityState(), {}, [item], item.item_id)

    assert "Retarget Map" in model.case.districts
    assert "Retarget Map" in model.status_text


def test_inspected_case_buckets_surface_evidence_and_population_context():
    """Verify inspected cases surface evidence and population context."""

    item, districts = _vendor_case()
    item.inspected = True
    item.risk_band = "high"
    item.case_json = {
        "inspection": {
            "risk_band": "high",
            "evidence": [{"severity": "warning"}, {"severity": "critical"}],
            "violations": [{"code": "public_nuisance"}],
        }
    }

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    buckets = {bucket.label: bucket for bucket in model.case.impact_buckets}

    assert "high risk; 2/2 flagged evidence; 1 violation(s)" in buckets["Budget"].value
    assert "rev $4/week" in buckets["Budget"].value
    assert "upkeep $1/week" in buckets["Budget"].value
    assert "net +$3" in buckets["Budget"].value
    assert buckets["Budget"].tone == "bad"
    assert "homeowners aggrieved" in buckets["People"].value


def test_heat_ticker_explains_future_followup_pressure():
    """Verify stakeholder heat is framed as future docket pressure."""

    item, districts = _vendor_case()
    state = rules.CityState()
    state.stakeholder_heat["vendors"] = 3

    model = build_desk_model(state, districts, [item], item.item_id)

    heat_line = next(line for line in model.ticker_items if line.startswith("WIRE: heat desk"))
    assert heat_line.startswith("WIRE: heat desk flags vendors 3")
    for phrase in ("incident", "enforcement", "follow-up filing"):
        assert phrase in heat_line


def test_ticker_leads_with_threat_track_summary():
    """Verify the ambient ticker names threat tracks instead of generic pressure."""

    item, districts = _vendor_case()
    districts["D0000"].service_gap["utilities"] = 42

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert model.ticker_items[0].startswith("WIRE: threat desk reports Service Failure")
    assert "utilities gap 42" in model.ticker_items[0]


def test_ticker_surfaces_contested_buyout_for_legibility():
    """Verify a contested district shows up on the city news ticker."""

    item, districts = _vendor_case()
    target = districts["D0000"]
    target.name = "Cinder Yard"
    target.identity_state = "contested"
    target.contesting_type = "mercantile"

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert any(line.startswith("WIRE: boundary desk") and "Cinder Yard" in line for line in model.ticker_items)


def test_ledger_rows_surface_non_money_city_health():
    """Verify desk ledger rows expose non-money city systems."""

    item, districts = _vendor_case()
    profile = districts["D0000"]
    profile.service_gap["child_services"] = 21
    profile.hazards = {"noise": 2}
    profile.affordability = 28

    model = build_desk_model(
        rules.CityState(last_revenue=4, last_upkeep=7, last_net=-3, maintenance_backlog=2),
        districts,
        [item],
        item.item_id,
    )
    ledger = {row.label: row for row in model.ledger_rows}

    assert ledger["Audit"].value == "FAIL"
    assert ledger["Economy"].value == "rev $4; up $7; net -3"
    assert "grievance" in ledger["Pressure"].value
    assert "worst mobility" in ledger["Services"].value
    assert ledger["Hazards"].value == "noise band 2 x1"
    assert ledger["Housing"].value == "D0000 affordability 28"
    assert ledger["Maintenance"].value == "2 active"


def test_ledger_rows_use_renamed_city_health_vitals():
    """Verify the desk model surfaces renamed city-health vitals."""

    item, districts = _vendor_case()

    model = build_desk_model(
        rules.CityState(activity=63, friction=28, trust=47, exposure=19),
        districts,
        [item],
        item.item_id,
    )
    ledger = {row.label: row for row in model.ledger_rows}

    assert ledger["Activity"].value == "63"
    assert ledger["Friction"].value == "28"
    assert ledger["Trust"].value == "47"
    assert ledger["Exposure"].value == "19"
    for legacy in ("Prosperity", "Unrest", "Culture", "Risk"):
        assert legacy not in ledger


def test_player_facing_vitals_are_exactly_the_four_audit_goals():
    """Lock the 5A model (ADR-10): the rail vitals are the four the audit scores.

    Player-facing goals = Activity/Friction/Trust/Exposure, mirroring state and the
    audit report; the granular support systems are detail, not part of this set.
    """

    item, districts = _vendor_case()
    state = rules.CityState(activity=63, friction=28, trust=47, exposure=19)

    model = build_desk_model(state, districts, [item], item.item_id)
    ledger = {row.label: row for row in model.ledger_rows}

    assert ledger["Activity"].value == "63"
    assert ledger["Friction"].value == "28"
    assert ledger["Trust"].value == "47"
    assert ledger["Exposure"].value == "19"

    # The audit scores and reports those same four vitals by the same names.
    _grade, report = rules.scorecard(state, districts, None, [item])
    for vital in ("activity", "friction", "trust", "exposure"):
        assert vital in report

    # Heat is a distinct stakeholder-pressure signal, not one of the four vitals.
    assert "Heat" in ledger
    assert ledger["Heat"].label not in ("Activity", "Friction", "Trust", "Exposure")


def test_headline_metrics_hide_generic_city_builder_stats():
    """Verify headline banner focuses on desk triage signals."""

    labels = [label for label, _display in HEADLINE_METRICS]

    assert labels == ["Week", "AP", "Money"]
    assert "Activity" not in labels
    assert "Friction" not in labels
    assert "Trust" not in labels
    assert "Exposure" not in labels
    assert "Heat" not in labels
    assert "Pressure" not in labels
    assert "Office Standing" not in labels
    assert "Audit" not in labels
    assert "Threats" not in labels


def test_top_header_renders_only_title_core_metrics_deadline_and_global_menu():
    """Verify the top header owns the only menu and keeps headline copy compact."""

    item, districts = _vendor_case()
    model = build_desk_model(
        rules.CityState(ap=2, money=75),
        districts,
        [item],
        item.item_id,
        deadline_text="MON INTAKE 1:00",
        deadline_meter=40,
        deadline_running=True,
    )
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    texts = _text_values(canvas)
    assert "PERMIT OFFICE" in texts
    assert "WEEK" in texts
    assert "AP" in texts
    assert "$" in texts
    assert "DAY" in texts
    assert "TIME" in texts
    assert "MON" in texts
    assert "1:00" in texts
    assert "STAND" not in texts
    assert "AUDIT" not in texts
    assert "THREAT" not in texts
    assert "FILING DEADLINE" not in texts
    assert "INTAKE" not in texts

    menu_targets = [(kind, ident, bbox) for kind, ident, bbox, _callback in view._click_targets if (kind, ident) == ("session", "Menu")]
    assert len(menu_targets) == 1
    _kind, _ident, bbox = menu_targets[0]
    assert bbox[1] < 64


def test_ledger_rows_include_top_threat_track_summary():
    """Verify the ledger exposes threat tracks as the public pressure language."""

    item, districts = _vendor_case()
    profile = districts["D0000"]
    profile.service_gap["utilities"] = 42
    state = rules.CityState(exposure=68)

    model = build_desk_model(state, districts, [item], item.item_id)
    ledger = {row.label: row for row in model.ledger_rows}

    assert [row.label for row in model.ledger_rows[:6]] == ["Office Standing", "Week", "AP", "Money", "Audit", "Threats"]
    assert "Threats" in ledger
    assert ledger["Threats"].value.startswith("Legal Exposure:")
    assert "city exposure 68" in ledger["Threats"].value


def test_ledger_derives_maintenance_from_active_features_when_available():
    """Verify feature rows override the compatibility backlog count."""

    item, districts = _vendor_case()
    feature = rules.FeatureInstance("F-market", "vendor_market", status="degraded", condition=22)

    model = build_desk_model(
        rules.CityState(maintenance_backlog=4),
        districts,
        [item],
        item.item_id,
        active_features=[feature],
    )
    ledger = {row.label: row for row in model.ledger_rows}

    assert ledger["Maintenance"].value == "1 due; lowest condition 22"


def test_threat_track_summaries_group_existing_pressure_causes():
    """Verify internal pressure fields collapse into four public threat tracks."""

    from toolbox.permit_office_arcgis.desk_model import _threat_track_summaries

    item, districts = _vendor_case()
    profile = districts["D0000"]
    profile.hazards = {"fire": 3}
    profile.service_gap["utilities"] = 42
    profile.displacement = {"renters": 3}
    profile.buyout_pressure = 4
    state = rules.CityState(friction=62, exposure=71)
    state.stakeholder_heat["vendors"] = 3
    feature = rules.FeatureInstance("F-market", "vendor_market", status="degraded", condition=22, target_cell_ids=["D0000"])

    tracks = _threat_track_summaries(state, districts, [feature], [item])
    by_label = {track.label: track for track in tracks}

    assert "Public Anger" in by_label
    assert "homeowners grievance" in by_label["Public Anger"].value
    assert "vendors heat 3" in by_label["Public Anger"].value
    assert "Legal Exposure" in by_label
    assert "city exposure 71" in by_label["Legal Exposure"].value
    assert "fire band 3" in by_label["Legal Exposure"].value
    assert "Service Failure" in by_label
    assert "utilities gap 42" in by_label["Service Failure"].value
    assert "maintenance due" in by_label["Service Failure"].value
    assert "Speculation Pressure" in by_label
    assert "renters displacement 3" in by_label["Speculation Pressure"].value
    assert "buyout pressure 4" in by_label["Speculation Pressure"].value


def test_threat_track_summary_returns_quiet_when_no_pressure():
    """Verify calm boards do not invent threat-track noise."""

    from toolbox.permit_office_arcgis.desk_model import _threat_track_summaries

    _item, districts = _vendor_case()
    for profile in districts.values():
        profile.dissatisfaction = {"homeowners": 0}
        profile.hazards = {}
        profile.service_gap = {}
        profile.displacement = {}
        profile.buyout_pressure = 0
        profile.incident_state = "none"
        profile.incident_group = ""

    tracks = _threat_track_summaries(rules.CityState(), districts, [], [])

    assert [track.label for track in tracks] == ["Threats"]
    assert tracks[0].value == "quiet"
    assert tracks[0].tone == "neutral"


def test_build_desk_model_threads_receipt_into_view_model():
    """Verify the inline filed-report receipt is carried into the view model."""

    item, districts = _vendor_case()
    receipt = ReceiptModel(
        title="Street Vendor",
        report="approved",
        affected=("D0000",),
        metrics=(("$", "12"),),
    )

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id, receipt=receipt)

    assert model.receipt is receipt
    assert model.report_tabs[0].title == "Street Vendor"
    assert model.selected_report_id == "latest"


def test_build_desk_model_defaults_receipt_to_none():
    """Verify the receipt defaults to None until a decision is filed."""

    item, districts = _vendor_case()

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert model.receipt is None
    assert model.report_tabs == ()


def test_build_desk_model_selects_report_tabs_and_builds_ticker():
    """Verify report tabs and ticker text are deterministic view-model inputs."""

    item, districts = _vendor_case()
    districts["D0000"].display_state = "grievance"
    tabs = (
        ReportTab("r1", "Inspection", "report", "inspected", False, "Inspected file."),
        ReportTab("r2", "Scorecard", "scorecard", "scorecard", False, "Audit PASS."),
    )

    first = build_desk_model(rules.CityState(ap=1), districts, [item], item.item_id, report_tabs=tabs, selected_report_id="r1")
    second = build_desk_model(rules.CityState(ap=1), districts, [item], item.item_id, report_tabs=tabs, selected_report_id="r1")

    assert first.selected_report_id == "r1"
    assert first.selected_desk_tab == "applications"
    assert [tab.selected for tab in first.report_tabs] == [True, False]
    assert first.ticker_items == second.ticker_items
    assert all(text.startswith("WIRE:") for text in first.ticker_items)
    assert any("street desk" in text.lower() for text in first.ticker_items)


def test_build_desk_model_can_select_reports_primary_tab():
    """Verify the combined lower tabbox can switch to filed reports."""

    item, districts = _vendor_case()
    tabs = (ReportTab("r1", "Inspection", "report", "inspected", True, "Inspected file."),)

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id, report_tabs=tabs, selected_desk_tab="reports")

    assert model.selected_desk_tab == "reports"
    assert model.selected_report_id == "r1"


def test_build_desk_model_adds_decision_lanes_for_selected_case():
    """Verify selected applications expose consequence lanes for triage."""

    item, districts = _vendor_case()

    model = build_desk_model(rules.CityState(ap=2, money=60), districts, [item], item.item_id)

    lanes = {lane.action_id: lane for lane in model.action_lanes}
    assert list(lanes) == ["approve", "approve_mitigated", "deny"]
    assert lanes["approve"].label == "Issue Permit"
    assert "1 AP" in lanes["approve"].cost
    assert "$12" in lanes["approve"].cost
    assert "activity" in lanes["approve"].city_effect
    assert lanes["approve_mitigated"].label == "Add Conditions"
    assert "conditions" in lanes["approve_mitigated"].cost.lower()
    assert lanes["deny"].label == "Deny"
    assert "0 AP" in lanes["deny"].cost


def test_case_summary_exposes_evidence_grid_cultures_and_outcomes():
    """Verify selected cases expose presentation-ready evidence widgets."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert model.case.district_grid
    assert any(cell.cell_id == "D0000" and cell.affected for cell in model.case.district_grid)
    assert model.case.culture_cards
    assert all(card.trend in {"up", "down", "flat", "unknown"} for card in model.case.culture_cards)
    assert model.case.outcome_cards
    assert any(card.trend == "unknown" for card in model.case.outcome_cards)


def test_action_lanes_include_hotkeys_and_tooltip_copy():
    """Verify action cards carry command clarity without view inference."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(ap=0, money=60), districts, [item], item.item_id)
    lanes = {lane.action_id: lane for lane in model.action_lanes}

    assert lanes["approve"].hotkey == "A"
    assert lanes["approve_mitigated"].hotkey == "M"
    assert lanes["deny"].hotkey == "D"
    assert lanes["approve"].tooltip
    assert lanes["approve"].disabled_reason == "Needs 1 AP"


def test_visible_map_symbols_are_filtered_to_selected_case_context():
    """Verify case map symbols expose shape, swatch, and live state."""

    item, districts = _vendor_case()
    item.geometry_type = "POLYGON"
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    rows = model.case_map_symbols
    assert rows
    assert {row.shape for row in rows} <= {"point", "line", "zone"}
    assert any(row.state in {"ON", "0", "1", "2", "3", "4+"} for row in rows)


def test_action_lanes_name_primary_threat_for_deny_and_issue():
    """Verify action previews use threat-track language before commitment."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(ap=2, money=60), districts, [item], item.item_id)
    lanes = {lane.action_id: lane for lane in model.action_lanes}

    assert "Public Anger" in lanes["deny"].city_effect
    assert "Public Anger" in lanes["approve"].city_effect
    assert "Public Anger" in lanes["approve_mitigated"].city_effect
    assert "Threat" in lanes["approve_mitigated"].local_effect


def test_template_primary_threat_prioritizes_specific_pressure_categories():
    """Verify threat copy uses specific pressure tracks before broad catalog buckets."""

    from toolbox.permit_office_arcgis.desk_model import _template_primary_threat

    business_utility = type(
        "Template",
        (),
        {
            "category": "business",
            "pressure_category": "utility",
            "is_incident": False,
            "is_enforcement": False,
            "failure_mode": "",
        },
    )()
    compliance_housing = type(
        "Template",
        (),
        {
            "category": "compliance",
            "pressure_category": "housing",
            "is_incident": False,
            "is_enforcement": False,
            "failure_mode": "",
        },
    )()

    assert _template_primary_threat(business_utility) == "Legal Exposure"
    assert _template_primary_threat(compliance_housing) == "Speculation Pressure"
    assert _template_primary_threat(rules.TEMPLATES["occupancy_certificate"]) == "Speculation Pressure"
    assert _template_primary_threat(rules.TEMPLATES[rules.MAINTENANCE_TEMPLATE_ID]) == "Service Failure"
    assert _template_primary_threat(rules.TEMPLATES[rules.ENFORCEMENT_TEMPLATE_ID]) == "Legal Exposure"


def test_incident_decision_lanes_disable_ap_gated_actions_when_ap_empty():
    """Verify AP-gated incident actions are visibly unavailable at 0 AP."""

    item = rules.DocketItem("incident", rules.CIVIC_INCIDENT_TEMPLATE_ID, "Civic Incident", "POINT", 1, target_cell_ids=["D0000"])
    profile = rules.DistrictProfile("D0000", "D0000", 1000, 50, 20, 35, 25, 50, "mercantile")
    districts = {profile.cell_id: rules.normalize_profile(profile)}

    model = build_desk_model(rules.CityState(ap=0, money=60), districts, [item], item.item_id)

    lanes = {lane.action_id: lane for lane in model.action_lanes}
    assert lanes["approve"].enabled is False
    assert lanes["approve_mitigated"].enabled is False
    assert lanes["deny"].enabled is False
    assert lanes["deny"].cost.startswith("1 AP")
    assert lanes["deny"].disabled_reason == "Needs 1 AP"


def test_permit_deny_stays_enabled_when_ap_empty():
    """Verify ordinary permit denial remains available with 0 AP."""

    item, districts = _vendor_case()

    model = build_desk_model(rules.CityState(ap=0, money=60), districts, [item], item.item_id)

    lanes = {lane.action_id: lane for lane in model.action_lanes}
    assert lanes["approve"].enabled is False
    assert lanes["deny"].enabled is True
    assert lanes["deny"].cost.startswith("0 AP")


def test_maintenance_case_uses_repair_action_family():
    """Verify maintenance follow-ups read as repair actions and service threat previews."""

    item = rules.DocketItem(
        "maint",
        rules.MAINTENANCE_TEMPLATE_ID,
        "Feature Maintenance Order",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )
    profile = rules.DistrictProfile("D0000", "Market Row", 1000, 50, 20, 35, 25, 50, "mercantile")
    districts = {profile.cell_id: rules.normalize_profile(profile)}

    model = build_desk_model(
        rules.CityState(ap=3, money=60),
        districts,
        [item],
        item.item_id,
        proposal_visible_by_item={item.item_id: True},
    )
    lanes = {lane.action_id: lane for lane in model.action_lanes}

    assert lanes["approve"].label == "Fund Repair"
    assert "Service Failure" in lanes["approve"].city_effect
    assert lanes["approve_mitigated"].label == "Patch"
    assert "Service Failure" in lanes["approve_mitigated"].city_effect
    assert lanes["deny"].label == "Defer"
    assert "Service Failure rises if maintenance is deferred" in lanes["deny"].city_effect


def test_build_desk_model_marks_queue_cleared_when_no_active_items():
    """Verify empty active dockets expose the queue-cleared state."""

    model = build_desk_model(
        rules.CityState(),
        {},
        [rules.DocketItem("done", "street_vendor_compact", "Done", "POINT", 1, status="approved")],
    )

    assert model.queue_cleared is True
    assert model.action_lanes == ()


def test_build_desk_model_exposes_map_key_legend_rows():
    """Verify the view model carries Contents-style map-key concepts."""

    item, districts = _vendor_case()
    districts["D0000"].display_state = "grievance"

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    legend = {(row.group, row.label): row for row in model.map_legend_rows}
    assert ("PermitDistricts", "Mercantile") in legend
    assert ("District display", "Local Grievance") in legend
    assert ("PermitPoints", "Proposed") in legend
    assert ("PermitLines", "Road") in legend
    assert ("PermitZones", "Commerce") in legend
    assert ("Selection", "Selected target") in legend
    assert ("Selection", "Filed state") in legend


def test_build_desk_model_tracks_district_group_health_from_profiles():
    """Verify group tracker combines prosperity, pressure, and local heat."""

    item, districts = _vendor_case()
    residential = rules.DistrictProfile(
        "D0001",
        "Old Annex",
        900,
        activity=32,
        friction=70,
        trust=18,
        exposure=65,
        services=25,
        district_type="residential",
        population_mix={"renters": 3, "families": 2},
        dissatisfaction={"renters": 4, "families": 3},
        display_state="grievance",
    )
    districts[residential.cell_id] = rules.normalize_profile(residential)

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    rows = {row.label: row for row in model.district_group_rows}
    assert "Residential" in rows
    assert rows["Residential"].state in {"Critical", "Strained"}
    assert "heat" in rows["Residential"].detail
    assert "pressure" in rows["Residential"].detail


def test_build_desk_model_keeps_all_district_groups_visible_to_rail():
    """Verify the city pulse receives every district type represented on the map."""

    item, districts = _vendor_case()
    for idx, district_type in enumerate(("residential", "industrial", "civic", "academic", "natural", "housing")):
        profile = rules.DistrictProfile(
            f"D10{idx}",
            f"{district_type.title()} District",
            700 + idx,
            activity=42 - idx,
            friction=22 + idx,
            trust=38,
            exposure=24,
            services=44,
            district_type=district_type,
        )
        districts[profile.cell_id] = rules.normalize_profile(profile)

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    labels = {row.label for row in model.district_group_rows}
    assert labels >= {"Mercantile", "Residential", "Industrial", "Civic", "Academic", "Natural", "Housing"}


def test_build_desk_model_exposes_district_attribute_rows():
    """Verify the wireframe bottom table has district state to render."""

    item, districts = _vendor_case()
    profile = districts["D0000"]
    profile.prosperity_band = "thriving"
    profile.identity_state = "converted"
    profile.display_state = "service_gap"

    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    row = model.district_table_rows[0]
    assert row.district == "D0000"
    assert row.name
    assert row.district_type == "Mercantile"
    assert row.prosperity == "Thriving"
    assert row.community == "Converted"
    assert row.pressure == "Service Gap"
    assert row.selected is True


def test_hybrid_application_workspace_draws_folder_rail_with_map_key_below_inbox():
    """Verify Applications uses a folder rail with map key below the inbox."""

    item, districts = _vendor_case()
    rows = [
        item,
        rules.DocketItem("T02", "street_vendor_compact", "Business License Fee Sweep", "POINT", 1),
        rules.DocketItem("T03", "street_vendor_compact", "Public Art and Museum Grant", "POINT", 1),
    ]
    districts["D0000"].display_state = "grievance"
    model = build_desk_model(rules.CityState(), districts, rows, item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 900, 620))

    texts = _text_values(canvas)
    assert "INBOX" in texts
    assert "DECISION BRIEF" in texts
    assert "MAP KEY" in texts
    assert "CHEAT SHEET" in texts
    assert "FEATURES" in texts
    assert "DISTRICT FILLS" in texts
    assert "OVERLAYS" in texts
    assert "Proposed" in texts
    assert "Road" in texts
    assert "Mercantile" in texts
    assert "Service Gap" in texts
    assert "Selected target" in texts
    assert "display_state" not in texts
    assert "PermitDistricts" not in texts
    assert "District Type" not in texts
    assert "Prosperity" not in texts
    assert "Community" not in texts
    docket_targets = [(ident, bbox) for kind, ident, bbox, _callback in view._click_targets if kind == "docket"]
    assert {ident for ident, _bbox in docket_targets} == {"T01-vendor", "T02", "T03"}
    assert all(bbox[2] <= 250 for _ident, bbox in docket_targets)
    assert any(kind == "map-key" for kind, _args, _kwargs in canvas.created)
    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    inbox_targets = [bbox for _ident, bbox in docket_targets]
    assert map_key[0] < 250
    assert map_key[1] > max(bbox[3] for bbox in inbox_targets)
    assert 240 <= map_key[3] - map_key[1] <= 360
    assert ("desk-tab", "Applications") in [(kind, ident) for kind, ident, _bbox, _callback in view._click_targets]
    assert ("desk-tab", "Filed Reports") in [(kind, ident) for kind, ident, _bbox, _callback in view._click_targets]


def test_wireframe_workspace_draws_center_application_table_and_side_rails():
    """Verify Applications follows the wireframe regions."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    regions = {kind: args for kind, args, _kwargs in canvas.created if kind in {"app-info", "action-grid", "district-table", "map-key"}}
    assert {"app-info", "action-grid", "district-table", "map-key"} <= set(regions)
    assert regions["app-info"][1] < regions["action-grid"][1] < regions["district-table"][1]
    assert regions["map-key"][0] < regions["app-info"][0]
    assert regions["district-table"][3] >= 880


def test_wireframe_workspace_keeps_table_attached_to_decision_module():
    """Verify tall windows do not leave a large blank well above the table."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    regions = {kind: args for kind, args, _kwargs in canvas.created if kind in {"active-card", "action-grid", "district-table"}}
    assert {"active-card", "action-grid", "district-table"} <= set(regions)
    assert regions["active-card"][3] - regions["action-grid"][3] <= 44
    assert 40 <= regions["district-table"][1] - regions["active-card"][3] <= 140


def test_full_dashboard_table_spans_under_decision_and_city_pulse():
    """Verify the bottom attributes pane cuts under both upper work columns."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    active = next(args for kind, args, _kwargs in canvas.created if kind == "active-card")
    pulse = next(args for kind, args, _kwargs in canvas.created if kind == "city-pulse")
    table = next(args for kind, args, _kwargs in canvas.created if kind == "district-table")

    assert pulse[3] - pulse[1] < table[3] - pulse[1]
    assert table[1] > active[3]
    assert table[0] < pulse[0] < table[2]
    assert table[3] >= 880


def test_full_dashboard_left_folder_rail_runs_single_map_cheat_sheet():
    """Verify the left rail owns the map state cheat sheet."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    table = next(args for kind, args, _kwargs in canvas.created if kind == "district-table")
    assert map_key[0] < table[0]
    assert map_key[3] >= table[3] - 20
    texts = _text_values(canvas)
    assert "CHEAT SHEET" in texts
    assert "MAP STATE" not in texts


def test_map_key_draws_contents_style_point_line_and_area_glyphs():
    """Verify legend symbols resemble ArcGIS Contents layer glyphs."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_map_key_rail(canvas, (0, 0, 260, 520), groups=("PermitPoints", "PermitLines", "PermitZones", "Selection"))

    assert any(kind == "oval" for kind, _args, _kwargs in canvas.created)
    assert any(kind == "line" and kwargs.get("width", 1) >= 3 for kind, _args, kwargs in canvas.created)
    assert any(kind == "rect" and kwargs.get("width", 1) >= 2 for kind, _args, kwargs in canvas.created)


def test_filed_reports_workspace_uses_history_list_and_detail_panel():
    """Verify Filed Reports swaps the folder rail from inbox to report history."""

    item, districts = _vendor_case()
    tabs = (
        ReportTab("week-1", "Week Closed", "week", "week", False, "Week one report."),
        ReportTab("decision-1", "Street Vendor", "decision", "approved", False, "Decision filed."),
    )
    model = build_desk_model(
        rules.CityState(),
        districts,
        [item],
        item.item_id,
        report_tabs=tabs,
        selected_report_id="week-1",
        selected_desk_tab="reports",
    )
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 900, 620))

    texts = _text_values(canvas)
    assert "HISTORY" in texts
    assert "REPORT DETAIL" in texts
    assert "Week Closed" in texts
    assert "Week one report." in texts
    report_targets = [(ident, bbox) for kind, ident, bbox, _callback in view._click_targets if kind == "report"]
    assert [ident for ident, _bbox in report_targets] == ["week-1", "decision-1"]


def test_city_pulse_draws_standing_stat_grid_wire_and_district_groups():
    """Verify right rail uses standing, stats, and group graphs without ticker duplication."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(activity=55, trust=42, friction=35, exposure=28), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_ledger_rail(canvas, (0, 0, 290, 720))

    texts = _text_values(canvas)
    assert "OFFICE STANDING" in texts
    assert "DISTRICT GROUPS" in texts
    assert "MAP STATE" not in texts
    assert "PermitDistricts" not in texts
    assert "district_type" not in texts
    assert "District display" not in texts
    assert "display_state" not in texts
    assert "WIRE" not in texts
    assert "WIRE QUEUE" not in texts
    assert "Latest city signals driving current risk." not in texts
    assert {"ACTIVITY", "TRUST", "FRICTION", "EXPOSURE"} <= set(texts)


def test_selected_case_actions_are_nearby_cards_without_global_toolbar_targets():
    """Verify selected-case actions live in the central card region only."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 900, 620))

    case_actions = [(ident, bbox) for kind, ident, bbox, _callback in view._click_targets if kind == "case-action"]
    action_labels = {ident for ident, _bbox in case_actions}
    assert {"Issue Permit", "Add Conditions", "Deny", "Inspect File", "Retarget Map"} <= action_labels
    assert all(230 < bbox[0] < 700 and bbox[1] > 110 for _ident, bbox in case_actions)
    assert not [target for target in view._click_targets if target[0] == "action"]


def test_selected_case_action_cards_attach_to_brief_with_costs():
    """Verify all map tools and decisions render as one 3x2 action grid."""

    item = rules.DocketItem(
        "maint",
        rules.MAINTENANCE_TEMPLATE_ID,
        "Feature Maintenance Order",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )
    profile = rules.DistrictProfile("D0000", "Market Row", 1000, 50, 20, 35, 25, 50, "mercantile")
    districts = {profile.cell_id: rules.normalize_profile(profile)}
    model = build_desk_model(
        rules.CityState(ap=3, money=60),
        districts,
        [item],
        item.item_id,
        proposal_visible_by_item={item.item_id: True},
    )
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    case_actions = [(ident, bbox) for kind, ident, bbox, _callback in view._click_targets if kind == "case-action"]
    labels = {ident for ident, _bbox in case_actions}
    assert {
        "Hide Proposed Feature",
        "Retarget Map",
        "Inspect File",
        "Fund Repair",
        "Patch",
        "Defer",
    } <= labels
    assert len(case_actions) == 6
    assert len(labels) == 6
    heights = [bbox[3] - bbox[1] for _ident, bbox in case_actions]
    assert min(heights) >= 70
    assert max(heights) <= 180
    regions = {kind: args for kind, args, _kwargs in canvas.created if kind in {"app-info", "action-grid", "district-table"}}
    assert regions["app-info"][3] < regions["action-grid"][1] < regions["district-table"][1]
    assert regions["district-table"][3] >= 880
    row_tops = sorted({round(bbox[1] / 10) * 10 for _ident, bbox in case_actions})
    assert len(row_tops) == 2
    assert all(sum(1 for _ident, bbox in case_actions if abs((round(bbox[1] / 10) * 10) - row) <= 10) == 3 for row in row_tops)

    texts = _text_values(canvas)
    assert "0 AP" in texts
    assert "1 AP" in texts
    assert "1 AP / $6" in texts
    assert "1 AP / $10; conditions +$4" in texts
    assert "0 AP / $0; may return as follow-up" in texts
    assert "City" in texts
    assert "Local" in texts
    assert any("Service" in str(text) for text in texts)
    info_boxes = [args for kind, args, _kwargs in canvas.created if kind == "decision-info"]
    assert info_boxes
    info_h = info_boxes[0][3] - info_boxes[0][1]
    grid_h = max(bbox[3] for _ident, bbox in case_actions) - min(bbox[1] for _ident, bbox in case_actions)
    assert 1.0 <= info_h / grid_h <= 1.6


def test_decision_actions_are_not_duplicated_in_bottom_utility_row():
    """Verify decision and utility actions share one 3x2 grid instead of separate rows."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    action_boxes = {ident: bbox for kind, ident, bbox, _callback in view._click_targets if kind == "case-action"}
    assert len(action_boxes) == 6
    assert {"Issue Permit", "Add Conditions", "Deny", "Retarget Map", "Inspect File"} <= set(action_boxes)
    row_tops = sorted({round(bbox[1] / 10) * 10 for bbox in action_boxes.values()})
    assert len(row_tops) == 2
    assert min(bbox[3] - bbox[1] for bbox in action_boxes.values()) >= 70
    assert max(bbox[3] - bbox[1] for bbox in action_boxes.values()) <= 180


def test_inbox_keeps_multiple_rows_with_the_unified_map_key():
    """Verify the unified map key does not starve the inbox list."""

    rows = [
        rules.DocketItem(f"T{n:02d}", "street_vendor_compact", f"Case {n}", "POINT", 1)
        for n in range(1, 9)
    ]
    _item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, rows, "T01")
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    docket_boxes = [bbox for kind, _ident, bbox, _callback in view._click_targets if kind == "docket"]
    assert docket_boxes
    assert map_key[3] - map_key[1] >= 300
    assert len(docket_boxes) >= 4


def test_inbox_rows_are_large_enough_to_use_the_left_rail():
    """Verify application inbox rows are not compressed into tiny strips."""

    rows = [
        rules.DocketItem(f"T{n:02d}", "street_vendor_compact", f"Long Application Case {n}", "POINT", 1)
        for n in range(1, 7)
    ]
    _item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, rows, "T01")
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    docket_boxes = [bbox for kind, _ident, bbox, _callback in view._click_targets if kind == "docket"]
    assert docket_boxes
    assert min(bbox[3] - bbox[1] for bbox in docket_boxes) >= 52


def test_inbox_rows_end_close_to_the_map_key():
    """Verify the inbox list does not leave a large dead gap above the key."""

    rows = [
        rules.DocketItem(f"T{n:02d}", "street_vendor_compact", f"Application Case {n}", "POINT", 1)
        for n in range(1, 5)
    ]
    _item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, rows, "T01")
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    docket_boxes = [bbox for kind, _ident, bbox, _callback in view._click_targets if kind == "docket"]
    assert map_key[1] - max(bbox[3] for bbox in docket_boxes) <= 96


def test_status_strip_marquee_draws_wire_text_at_scrolled_position():
    """Verify ambient status text is rendered as a right-to-left marquee."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas
    view._status_strip_box = (0, 0, 420, 28)

    view.update_status_marquee(0)
    first_wire = [
        args[0]
        for kind, args, kwargs in canvas.created
        if kind == "text" and kwargs.get("tags") == ("status-strip", "status-marquee")
    ][0]

    canvas.created.clear()
    view.update_status_marquee(36)
    second_wire = [
        args[0]
        for kind, args, kwargs in canvas.created
        if kind == "text" and kwargs.get("tags") == ("status-strip", "status-marquee")
    ][0]

    assert second_wire < first_wire


def test_hybrid_layout_gives_unified_map_key_legible_block():
    """Verify the folder rail gives the unified map key a legible block."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    map_key_boxes = [args for kind, args, _kwargs in canvas.created if kind == "map-key"]
    assert map_key_boxes
    x0, y0, x1, y1 = map_key_boxes[0]
    assert x1 <= 260
    assert y0 > 240
    assert 300 <= y1 - y0 <= 420
    texts = _text_values(canvas)
    assert "CHEAT SHEET" in texts
    assert "DISTRICT FILLS" in texts
    assert "OVERLAYS" in texts


def test_tall_workspace_splits_left_rail_between_inbox_and_map_cheat_sheet():
    """Verify tall ArcGIS windows do not hide map-state meaning in the right rail."""

    rows = [
        rules.DocketItem(f"T{n:02d}", "street_vendor_compact", f"Application Case {n}", "POINT", 1)
        for n in range(1, 5)
    ]
    _item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, rows, "T01")
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 1500))

    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    docket_boxes = [bbox for kind, _ident, bbox, _callback in view._click_targets if kind == "docket"]
    assert docket_boxes
    assert map_key[3] - map_key[1] >= 500
    assert map_key[1] - max(bbox[3] for bbox in docket_boxes) <= 300


def test_application_workspace_uses_full_available_height_for_detail_and_rails():
    """Verify the main desk does not leave a large unused lower half at startup."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    district_table = next(args for kind, args, _kwargs in canvas.created if kind == "district-table")
    action_grid = next(args for kind, args, _kwargs in canvas.created if kind == "action-grid")
    case_action_boxes = [bbox for kind, _ident, bbox, _callback in view._click_targets if kind == "case-action"]
    assert map_key[3] >= 880
    assert district_table[3] >= 880
    assert action_grid[3] < district_table[1]
    assert case_action_boxes


def test_attribute_table_top_aligns_with_map_key_top():
    """Verify the attribute table rises to the map-key top line."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    district_table = next(args for kind, args, _kwargs in canvas.created if kind == "district-table")
    assert abs(district_table[1] - map_key[1]) <= 4


def test_application_work_stack_has_vertical_breathing_room():
    """Verify the decision stack is not pinned to the very top or bottom of the work area."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 1120, 900))

    active = next(args for kind, args, _kwargs in canvas.created if kind == "active-card")
    map_key = next(args for kind, args, _kwargs in canvas.created if kind == "map-key")
    assert active[1] >= 34
    assert map_key[1] - active[3] >= 44


def test_decision_lane_bounds_local_text_inside_narrow_card():
    """Verify narrow hybrid lanes do not push Local text outside the card."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    lane = model.action_lanes[0]
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    lane_x1 = 520

    view._draw_decision_lane(canvas, 240, 120, lane_x1, 200, lane)

    local_texts = [
        (args, kwargs)
        for kind, args, kwargs in canvas.created
        if kind == "text" and kwargs.get("text") == "Local"
    ]
    assert local_texts
    local_x = local_texts[0][0][0]
    assert 240 < local_x < lane_x1
    bounded_texts = [
        (args, kwargs)
        for kind, args, kwargs in canvas.created
        if kind == "text" and kwargs.get("width") is not None
    ]
    assert bounded_texts
    for args, kwargs in bounded_texts:
        width = kwargs["width"]
        assert width > 0
        assert args[0] + width <= lane_x1 - 12


def test_inbox_rail_pins_selected_case_when_beyond_visible_cap():
    """Verify the selected item remains visible even deep in a long docket."""

    item, districts = _vendor_case()
    rows = [item] + [
        rules.DocketItem(f"T{n:02d}", "street_vendor_compact", f"Case {n}", "POINT", 1)
        for n in range(2, 18)
    ]
    selected_id = "T16"
    model = build_desk_model(rules.CityState(), districts, rows, selected_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_inbox_rail(canvas, (0, 0, 190, 330), list(model.docket_rows), selected_id)

    target_ids = [ident for kind, ident, _bbox, _callback in view._click_targets if kind == "docket"]
    assert target_ids[0] == selected_id
    assert "ACTIVE" in _text_values(canvas)


class _FakeCanvas:
    """Headless canvas stand-in: records draw calls, never measures real text."""

    def __init__(self):
        """Start an empty record of created canvas items."""

        self.created = []

    def _record(self, kind, args, kwargs=None):
        """Record one create_* call and return a synthetic item id."""

        self.created.append((kind, args, kwargs or {}))
        return len(self.created)

    def create_rectangle(self, *args, **kwargs):
        """Record a rectangle and return its synthetic id."""

        return self._record("rect", args, kwargs)

    def create_text(self, *args, **kwargs):
        """Record a text item and return its synthetic id."""

        return self._record("text", args, kwargs)

    def create_line(self, *args, **kwargs):
        """Record a line and return its synthetic id."""

        return self._record("line", args, kwargs)

    def create_oval(self, *args, **kwargs):
        """Record an oval and return its synthetic id."""

        return self._record("oval", args, kwargs)

    def delete(self, *_args):
        """Record canvas clearing for full-draw tests."""

        self.created.clear()

    def bbox(self, _item):
        """Report no measurable box, exercising the conservative fallback."""

        return None


def test_stacker_blocks_never_overlap_or_exceed_bottom():
    """Verify stacked blocks advance past each other and never cross the hard bottom."""

    canvas = _FakeCanvas()
    stack = _Stacker(canvas, 0, 100, top=0, bottom=200, pad=10)
    tops = []
    bottoms = []

    def block(height):
        """Return a draw_fn that records its top and returns a clamped bottom."""

        def draw(_canvas, _x0, _x1, y, max_y):
            tops.append(y)
            return min(y + height, max_y)

        return draw

    for height in (30, 40, 500):  # the last block intentionally overflows
        bottom = stack.add(block(height))
        assert bottom is not None
        assert bottom <= stack.bottom
        bottoms.append(bottom)

    assert tops == sorted(tops)
    for idx in range(1, len(tops)):
        assert tops[idx] >= bottoms[idx - 1]  # next block starts at/after prior bottom

    # No room left, so a further block is skipped instead of overlapping.
    assert stack.add(block(20)) is None


class _Callbacks:
    """Callable bundle for headless desk view tests."""

    def __init__(self):
        """Record invoked callbacks by name."""

        self.calls = []

    def __getattr__(self, name):
        """Return a recorder function for any callback field."""

        def _callback(*args):
            self.calls.append((name, args))

        return _callback


def _view_for_drawing(model):
    """Return a PermitDeskView shell without creating a Tk widget."""

    callbacks = _Callbacks()
    view = object.__new__(PermitDeskView)
    view.callbacks = callbacks
    view.on_select_item = lambda item_id: callbacks.calls.append(("select_item", (item_id,)))
    view.model = model
    view._click_targets = []
    view._hover_key = ""
    view._font_cache = {}
    view._fit_cache = {}
    view._lookup_model = None
    view._ledger_by_label = {}
    view._lane_by_action = {}
    view._menu_open = False
    view.root = None
    view._font = lambda size, weight="normal": ("Segoe UI", size, weight)
    view._px_measurer = lambda _size, _weight: None
    return view, callbacks


def _text_values(canvas):
    """Return all text values written to the fake canvas."""

    return [kwargs.get("text") for kind, _args, kwargs in canvas.created if kind == "text"]


def test_application_workspace_lists_active_and_queued_cases_in_left_inbox():
    """Verify the active case expands centrally while all docket rows stay selectable in the rail."""

    item, districts = _vendor_case()
    rows = [
        item,
        rules.DocketItem("T02", "street_vendor_compact", "Business License Fee Sweep", "POINT", 1),
        rules.DocketItem("T03", "street_vendor_compact", "Public Art and Museum Grant", "POINT", 1),
        rules.DocketItem("T04", "street_vendor_compact", "Street Vendor Compact", "POINT", 1),
    ]
    model = build_desk_model(rules.CityState(), districts, rows, "T03")
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 760, 700))

    target_ids = [ident for kind, ident, _bbox, _callback in view._click_targets if kind == "docket"]
    assert target_ids == ["T01-vendor", "T02", "T03", "T04"]
    assert "INBOX" in _text_values(canvas)
    assert "ACTIVE" in _text_values(canvas)
    assert "DECISION BRIEF" in _text_values(canvas)
    assert not any(text.startswith("+") and "queued" in text for text in _text_values(canvas))


def test_application_workspace_caps_queued_stack_and_reports_overflow():
    """Verify a short workspace caps the collapsed queue and flags overflow."""

    item, districts = _vendor_case()
    rows = [item] + [
        rules.DocketItem(f"T{n:02d}", "street_vendor_compact", f"Case {n}", "POINT", 1)
        for n in range(2, 12)
    ]
    model = build_desk_model(rules.CityState(), districts, rows, item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    # A short box forces the collapsed stack to cap below the 10 queued cases.
    view._draw_application_tab_content(canvas, (0, 0, 760, 460))

    target_ids = [ident for kind, ident, _bbox, _callback in view._click_targets if kind == "docket"]
    assert 0 < len(target_ids) < 10
    assert any(text.startswith("+") and "queued" in text for text in _text_values(canvas))


def test_fit_px_memoizes_so_repeated_draws_skip_recompute():
    """Verify _fit_px caches per (text, size, weight, max_px) across redraws.

    The hover/redraw path re-fits every label string each frame; _fit_px is a pure
    function of its args (font metrics are fixed at runtime), so repeated calls
    must reuse the cached result instead of re-running the fit each time.
    """

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)

    calls = []
    real_compute = view._fit_px_compute
    view._fit_px_compute = lambda *args: (calls.append(args), real_compute(*args))[1]

    first = view._fit_px("A district label that may need clipping", 9, "bold", 70)
    second = view._fit_px("A district label that may need clipping", 9, "bold", 70)

    assert first == second
    assert len(calls) == 1  # second call served from the memo
    assert ("A district label that may need clipping", 9, "bold", 70) in view._fit_cache


def test_draw_lookups_rebuild_only_on_model_swap():
    """Verify ledger/lane lookup dicts are cached and rebuilt only when the model changes.

    The banner and ledger rail both index ledger rows by label, and case controls
    index action lanes by action_id, every redraw. Those maps are a pure function
    of the current model, so they are built once per model swap and reused across
    hover/deadline redraws instead of rebuilt each frame.
    """

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)

    view._ensure_lookups()
    first_ledger = view._ledger_by_label
    first_lanes = view._lane_by_action
    assert first_ledger["Audit"].label == "Audit"
    assert {lane.action_id for lane in model.action_lanes} == set(first_lanes)

    view._ensure_lookups()
    # Same model object -> the cached dicts are reused, not rebuilt.
    assert view._ledger_by_label is first_ledger
    assert view._lane_by_action is first_lanes

    # A new model object invalidates the cache and rebuilds the maps.
    model2 = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view.model = model2
    view._ensure_lookups()
    assert view._ledger_by_label is not first_ledger


def test_build_desk_model_game_active_defaults_true_and_can_be_false():
    """Verify the model carries a game_active flag (True by default)."""

    assert build_desk_model(rules.CityState(), {}, []).game_active is True
    assert build_desk_model(rules.CityState(), {}, [], game_active=False).game_active is False


def test_empty_docket_before_a_game_shows_start_prompt_not_queue_cleared():
    """Verify a not-started session prompts New Game, not 'all applications filed'."""

    model = build_desk_model(rules.CityState(), {}, [], game_active=False)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 760, 700))
    texts = _text_values(canvas)

    assert any("No game" in (text or "") for text in texts)
    assert any("New Game" in (text or "") for text in texts)
    assert not any("have been filed" in (text or "") for text in texts)
    assert not any((text or "") == "End Week" for text in texts)


def test_empty_docket_mid_game_still_shows_queue_cleared():
    """Verify an active game with an empty docket keeps the queue-cleared panel."""

    model = build_desk_model(rules.CityState(), {}, [], game_active=True)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 760, 700))
    texts = _text_values(canvas)

    assert any("Queue cleared" in (text or "") for text in texts)
    assert any("have been filed" in (text or "") for text in texts)


def test_utility_menu_button_is_compact_hamburger_without_text_label():
    """Verify utility actions are collapsed behind a small menu affordance."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_menu_button(canvas, 100, 10, 136, 34)

    assert "MENU" not in _text_values(canvas)
    assert any(kind == "line" for kind, _args, _kwargs in canvas.created)
    assert [target[:2] for target in view._click_targets] == [("session", "Menu")]


def test_session_menu_anchors_to_hamburger_button():
    """Verify the utility menu opens under the hamburger, not at window edge."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_menu_button(canvas, 720, 20, 760, 48)
    view._draw_session_menu(canvas, 1300)

    menu_rects = [
        args
        for kind, args, kwargs in canvas.created
        if kind == "rect" and kwargs.get("fill") == Palette.PAPER and len(args) == 4 and args[3] - args[1] > 100
    ]
    assert menu_rects
    x0, y0, _x1, _y1 = menu_rects[-1]
    assert x0 == 584
    assert y0 == 48


def test_full_draw_has_no_global_case_action_bar_targets():
    """Verify case actions are not registered in the old global toolbar zone."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    early_actions = [
        ident
        for kind, ident, bbox, _callback in view._click_targets
        if kind == "action" and bbox[1] < 230
    ]
    assert early_actions == []


def test_selected_application_draws_decision_brief_lanes():
    """Verify the active application renders modern decision-lane content."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 760, 620))

    texts = _text_values(canvas)
    assert "DECISION BRIEF" in texts
    assert "Issue Permit" in texts
    assert "Add Conditions" in texts
    assert "Deny" in texts


def test_selected_application_uses_explicit_proposed_feature_copy():
    """Verify exhibit controls name the proposed feature, not a generic exhibit."""

    item, districts = _vendor_case()
    hidden = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    visible = build_desk_model(
        rules.CityState(),
        districts,
        [item],
        item.item_id,
        proposal_visible_by_item={item.item_id: True},
    )

    for model, expected in ((hidden, "Show Proposed Feature"), (visible, "Hide Proposed Feature")):
        view, _callbacks = _view_for_drawing(model)
        canvas = _FakeCanvas()

        view._draw_active_card(canvas, (0, 0, 760, 620))

        assert expected in _text_values(canvas)


def test_decision_brief_uses_two_row_action_grid_for_breathing_room():
    """Verify actions use a roomy 3x2 grid instead of cramped columns."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 760, 620))

    action_boxes = [bbox for kind, _ident, bbox, _callback in view._click_targets if kind == "case-action"]
    assert len(action_boxes) == 6
    row_tops = sorted({round(bbox[1] / 10) * 10 for bbox in action_boxes})
    assert len(row_tops) == 2
    assert all(sum(1 for bbox in action_boxes if round(bbox[1] / 10) * 10 == row) == 3 for row in row_tops)


def test_active_card_draws_evidence_grid_culture_cards_and_outcomes():
    """Verify the decision brief draws the approved case evidence row."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    texts = _text_values(canvas)
    assert "AFFECTED DISTRICTS" in texts
    assert "CULTURE PRESSURE" in texts
    assert "POSSIBLE OUTCOMES" in texts
    assert any(kind == "district-grid" for kind, _args, _kwargs in canvas.created)
    assert any(kind == "culture-card" for kind, _args, _kwargs in canvas.created)
    assert any(kind == "outcome-card" for kind, _args, _kwargs in canvas.created)


def test_evidence_columns_have_wider_gutters():
    """Verify district, culture, and outcome columns do not run into each other."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    grid = next(args for kind, args, _kwargs in canvas.created if kind == "district-grid")
    culture = next(args for kind, args, _kwargs in canvas.created if kind == "culture-card")
    outcome = next(args for kind, args, _kwargs in canvas.created if kind == "outcome-card")
    assert culture[0] - grid[2] >= 18
    assert outcome[0] - culture[2] >= 18


def test_active_card_gives_evidence_row_plan_weight():
    """Verify evidence widgets get enough vertical room to read like the plan."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    district_grid = next(args for kind, args, _kwargs in canvas.created if kind == "district-grid")
    culture_cards = [args for kind, args, _kwargs in canvas.created if kind == "culture-card"]
    assert district_grid[3] - district_grid[1] >= 70
    assert min(card[3] - card[1] for card in culture_cards) >= 30


def test_active_card_gives_brief_info_larger_than_evidence_row():
    """Verify the decision narrative remains the dominant top section."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    info = next(args for kind, args, _kwargs in canvas.created if kind == "app-info")
    grid = next(args for kind, args, _kwargs in canvas.created if kind == "district-grid")
    assert info[3] - info[1] > grid[3] - grid[1]


def test_decision_brief_draws_framed_title_and_applicant_block():
    """Verify the active brief follows the skeleton title/applicant/description stack."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    texts = _text_values(canvas)
    info = next(args for kind, args, _kwargs in canvas.created if kind == "app-info")
    title_frames = [args for kind, args, kwargs in canvas.created if kind == "rect" and kwargs.get("tags") == ("application-title-frame",)]
    description_blocks = [args for kind, args, kwargs in canvas.created if kind == "rect" and kwargs.get("tags") == ("description-block",)]

    assert "APPLICANT" in texts
    assert "Vendor Compact Office" in texts
    assert title_frames
    assert description_blocks
    assert description_blocks[0][3] - description_blocks[0][1] >= 72
    assert info[3] - info[1] >= 210


def test_decision_brief_keeps_evidence_and_actions_separated():
    """Verify the evidence row breathes before the command tile row starts."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    evidence = next(args for kind, args, _kwargs in canvas.created if kind == "evidence-row")
    action_grid = next(args for kind, args, _kwargs in canvas.created if kind == "action-grid")

    assert action_grid[1] - evidence[3] >= 18


def test_action_cards_draw_hotkeys_and_disabled_reason():
    """Verify actions show hotkey hints and unavailable reasons."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(ap=0, money=60), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    texts = _text_values(canvas)
    assert "A" in texts
    assert "M" in texts
    assert "D" in texts
    assert any("Needs 1 AP" in str(text) for text in texts)
    assert texts.count("City") >= 3
    assert texts.count("Local") >= 3


def test_action_cards_draw_separate_impact_compartment():
    """Verify action cards read as title/cost on left and impact on right."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    dividers = [args for kind, args, kwargs in canvas.created if kind == "line" and kwargs.get("tags") == ("impact-divider",)]
    impact_boxes = [args for kind, args, kwargs in canvas.created if kind == "rect" and kwargs.get("tags") == ("impact-box",)]
    assert len(dividers) >= 6
    assert len(impact_boxes) >= 6


def test_action_cards_place_hotkey_before_cost_and_graphical_impact():
    """Verify action cards make command key, AP/cost, and impact separately scannable."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_active_card(canvas, (0, 0, 820, 680))

    hotkeys = [(args, kwargs) for kind, args, kwargs in canvas.created if kind == "text" and kwargs.get("tags") == ("hotkey-badge",)]
    costs = [(args, kwargs) for kind, args, kwargs in canvas.created if kind == "text" and kwargs.get("tags") == ("cost-line",)]
    markers = [args for kind, args, kwargs in canvas.created if kind in {"line", "rect"} and kwargs.get("tags") == ("impact-marker",)]
    impact_boxes = [args for kind, args, kwargs in canvas.created if kind == "rect" and kwargs.get("tags") == ("impact-box",)]

    assert hotkeys
    assert costs
    assert hotkeys[0][0][0] < costs[0][0][0]
    assert markers
    assert max(box[2] - box[0] for box in impact_boxes) >= 104


def test_office_standing_rail_keeps_threat_tracks_in_wire_ticker_not_pulse():
    """Verify City Pulse focuses on standing/stats while threats live in WIRE."""

    item, districts = _vendor_case()
    districts["D0000"].service_gap["utilities"] = 42
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_ledger_rail(canvas, (0, 0, 260, 620))

    texts = _text_values(canvas)
    assert "CITY PULSE" in texts
    assert "OFFICE STANDING" in texts
    assert "THREATS" not in texts
    assert not any("Service Failure" in str(text) for text in texts)
    assert any(text.startswith("WIRE:") and "Service Failure" in text and "utilities gap 42" in text for text in model.ticker_items)
    assert "ACTIVITY" in texts
    assert "TRUST" in texts
    assert "FRICTION" in texts
    assert "EXPOSURE" in texts
    assert "PRESSURE" not in texts
    assert "SERVICES" not in texts
    assert "HOUSING" not in texts
    assert "MAINTENANCE" not in texts


def test_ledger_rows_include_trend_points_for_core_pulse_stats():
    """Verify pulse stats carry static sparkline data for the view."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(activity=63, friction=28, trust=47, exposure=19), districts, [item], item.item_id)
    ledger = {row.label: row for row in model.ledger_rows}

    for label in ("Activity", "Trust", "Friction", "Exposure"):
        assert ledger[label].trend in {"up", "down", "flat", "unknown"}
        assert len(ledger[label].points) in {0, 6}


def test_city_pulse_draws_sparklines_and_group_swatches():
    """Verify the right rail draws trend graphics and culture swatches."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_ledger_rail(canvas, (0, 0, 300, 700))

    assert any(kind == "sparkline" for kind, _args, _kwargs in canvas.created)
    assert any(kind == "group-swatch" for kind, _args, _kwargs in canvas.created)


def test_city_pulse_focuses_on_graphs_not_map_state_key():
    """Verify the right pane leaves map symbology to the unified map key."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_ledger_rail(canvas, (0, 0, 300, 700))

    texts = _text_values(canvas)
    sparkline_boxes = [args for kind, args, _kwargs in canvas.created if kind == "sparkline"]
    assert "MAP STATE" not in texts
    assert sparkline_boxes
    assert max(box[2] - box[0] for box in sparkline_boxes) >= 58


def test_city_pulse_labels_right_rail_as_office_context():
    """Verify the right rail matches the plan framing."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_ledger_rail(canvas, (0, 0, 300, 700))

    texts = _text_values(canvas)
    assert "OFFICE CONTEXT" in texts


def test_filed_reports_keep_attribute_table_under_report_and_pulse():
    """Verify Filed Reports keeps the live district table attached under the work area."""

    item, districts = _vendor_case()
    tabs = (ReportTab("week-1", "Week Closed", "week", "week", False, "Week one report."),)
    model = build_desk_model(
        rules.CityState(),
        districts,
        [item],
        item.item_id,
        report_tabs=tabs,
        selected_report_id="week-1",
        selected_desk_tab="reports",
    )
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    table = next(args for kind, args, _kwargs in canvas.created if kind == "district-table")
    report = next(args for kind, args, _kwargs in canvas.created if kind == "report-detail")
    pulse = next(args for kind, args, _kwargs in canvas.created if kind == "city-pulse")
    assert table[1] > report[3]
    assert table[3] >= 880


def test_city_pulse_stops_above_external_attribute_table():
    """Verify the right rail and live district table share the same vertical boundary."""

    item, districts = _vendor_case()
    for idx, district_type in enumerate(("residential", "industrial", "civic", "academic", "natural")):
        profile = rules.DistrictProfile(
            f"D20{idx}",
            f"{district_type.title()} District",
            800,
            activity=44,
            friction=22 + idx,
            trust=40,
            exposure=25,
            services=43,
            district_type=district_type,
        )
        districts[profile.cell_id] = rules.normalize_profile(profile)
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas

    view._draw(1120, 900)

    table = next(args for kind, args, _kwargs in canvas.created if kind == "district-table")
    pulse = next(args for kind, args, _kwargs in canvas.created if kind == "city-pulse")
    assert pulse[3] <= table[1] - 8


def test_map_key_draws_shape_matched_case_symbols():
    """Verify selected-case map symbols use point/line/zone glyphs."""

    item, districts = _vendor_case()
    item.geometry_type = "LINE"
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_map_key_rail(canvas, (0, 0, 240, 260), groups=("Selection",))

    assert any(kind == "symbol-line" for kind, _args, _kwargs in canvas.created)


def test_build_desk_model_does_not_mutate_districts_argument():
    """Verify scoring the Audit row leaves the caller's district profiles intact.

    `_ledger_rows` scores the live audit for the Audit row. Its defensive copy
    path must leave the caller's districts unchanged, so a normalized profile
    must round-trip unchanged.
    """

    import copy

    item, districts = _vendor_case()
    before = copy.deepcopy(districts)

    model = build_desk_model(
        rules.CityState(activity=70, trust=55, friction=15, exposure=20),
        districts,
        [item],
        item.item_id,
    )

    assert districts == before
    assert "Audit" in {row.label for row in model.ledger_rows}


def test_city_health_index_folds_core_metrics():
    """Verify the compatibility wrapper preserves the City Health formula."""

    from toolbox.permit_office_arcgis.desk_model import _city_health_index

    # Activity/Trust positive, Friction/Exposure negative: (80 + 60 + (100-10) + (100-20)) / 4 = 77.5 -> 78
    healthy = rules.CityState(activity=80, trust=60, friction=10, exposure=20)
    assert _city_health_index(healthy) == 78
    # A struggling city reads lower.
    failing = rules.CityState(activity=20, trust=15, friction=70, exposure=65)
    assert _city_health_index(failing) < _city_health_index(healthy)
    assert 0 <= _city_health_index(failing) <= 100


def test_office_standing_index_reuses_city_vital_formula():
    """Verify Office Standing preserves the existing City Health formula."""

    from toolbox.permit_office_arcgis.desk_model import _office_standing_index

    strong = rules.CityState(activity=80, trust=60, friction=10, exposure=20)
    assert _office_standing_index(strong) == 78

    failing = rules.CityState(activity=20, trust=15, friction=70, exposure=65)
    assert _office_standing_index(failing) == 25
    assert 0 <= _office_standing_index(failing) <= 100


def test_ledger_surfaces_office_standing_headline():
    """Verify the ledger leads with Office Standing instead of City Health."""

    item, districts = _vendor_case()
    model = build_desk_model(
        rules.CityState(activity=70, trust=55, friction=15, exposure=20),
        districts,
        [item],
        item.item_id,
    )
    ledger = {row.label: row for row in model.ledger_rows}

    assert "Office Standing" in ledger
    assert ledger["Office Standing"].meter is not None
    assert ledger["Office Standing"].value == "72 Strong"
    assert "Health" not in ledger


def test_ledger_surfaces_office_standing_headline_and_heat():
    """Verify the ledger leads with Office Standing and keeps Heat visible."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(activity=70, trust=55, friction=15, exposure=20), districts, [item], item.item_id)
    ledger = {row.label: row for row in model.ledger_rows}

    assert "Office Standing" in ledger
    assert ledger["Office Standing"].meter is not None
    assert "Heat" in ledger


def test_city_health_rail_draws_office_standing_without_wire_headline():
    """Verify the pulse rail renders Office Standing without duplicating the ticker."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_ledger_rail(canvas, (0, 0, 260, 620))

    texts = _text_values(canvas)
    assert "OFFICE STANDING" in texts
    assert "THREATS" not in texts
    assert any(text.startswith("WIRE:") for text in model.ticker_items)
    assert "HEAT" not in texts
    assert "CITY HEALTH" not in texts


def test_selected_case_renders_economy_and_action_note():
    """Verify the decision brief carries a recurring budget line and action note."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert model.case.economy
    assert "Issue=" in model.case.action_note


def test_queue_cleared_state_draws_end_week_and_cancel_autoclose():
    """Verify the application workspace exposes the queue-cleared controls."""

    model = build_desk_model(rules.CityState(), {}, [], auto_close_active=True, auto_close_seconds=3)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_application_tab_content(canvas, (0, 0, 760, 620))

    texts = _text_values(canvas)
    assert "Queue cleared" in texts
    assert "End Week" in texts
    assert "Cancel Auto Close" in texts
    targets = [(kind, ident) for kind, ident, _bbox, _callback in view._click_targets]
    assert ("case-action", "End Week") in targets
    assert ("case-action", "Cancel Auto Close") in targets


def test_session_menu_offers_manual_end_week_when_out_of_ap():
    """Verify the utility menu always exposes a manual End Week control.

    When the player is out of AP with cases still queued (especially incidents,
    whose Deny lane also costs AP), no in-card lane is affordable and the
    queue-cleared End Week button never appears. The session menu must offer a
    manual way to close the week regardless of AP.
    """

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(ap=0, money=0), districts, [item], item.item_id)
    view, callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_menu_button(canvas, 720, 20, 760, 48)
    view._draw_session_menu(canvas, 1300)

    assert "END WEEK" in _text_values(canvas)
    end_week = [cb for kind, ident, _bbox, cb in view._click_targets if (kind, ident) == ("session", "End Week")]
    assert end_week
    end_week[0]()
    assert ("advance_turn", ()) in callbacks.calls


def test_session_menu_contains_help_and_session_commands_only():
    """Verify the hamburger owns session actions and Help."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_menu_button(canvas, 720, 20, 760, 48)
    view._draw_session_menu(canvas, 1300)

    texts = _text_values(canvas)
    assert "END WEEK" in texts
    assert "NEW GAME" in texts
    assert "SCORECARD" in texts
    assert "HELP" in texts
    assert "END GAME" in texts


def test_help_overlay_describes_symbology_hotkeys_stats_and_unknowns():
    """Verify Help is a compact dashboard reference, not a long manual."""

    model = build_desk_model(rules.CityState(), {}, [], show_start_help=True)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_start_help_overlay(canvas, 1120, 860)

    body = " ".join(str(text or "") for text in _text_values(canvas))
    for phrase in ("Symbology", "Hotkeys", "Stats", "Unknowns", "Controls"):
        assert phrase in body
    for key in ("V", "T", "I", "A", "M", "D", "W", "S"):
        assert key in body


def test_start_help_overlay_is_short_start_card_with_primary_actions():
    """Verify the start/help sheet is a compact dashboard reference."""

    model = build_desk_model(rules.CityState(), {}, [], show_start_help=True)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()

    view._draw_start_help_overlay(canvas, 1120, 860)

    texts = [str(text or "") for text in _text_values(canvas)]
    body = " ".join(texts)
    assert "PERMIT OFFICE" in texts
    assert "NEW GAME" in texts
    assert "HELP" in texts
    for phrase in ("Symbology", "Hotkeys", "Stats", "Unknowns", "Controls"):
        assert phrase in body
    assert "Score optimization" not in body
    assert "Show Proposed Feature" not in body
    assert "Unresolved cases forecast" not in body


def test_summary_helpers_report_service_hazard_and_maintenance_backlog():
    """Verify compact summary helpers report services, hazards, and upkeep."""

    _item, districts = _vendor_case()
    profile = districts["D0000"]
    profile.service_gap["child_services"] = 21
    profile.hazards = {"fire": 3, "noise": 1}
    feature = rules.FeatureInstance("F-market", "vendor_market", status="degraded", condition=22)

    assert _service_gap_summary([profile], "child_services") == "mobility gap 40 in D0000"
    assert _hazard_summary([profile]) == "fire band 3 x1"
    assert _maintenance_summary([feature]) == "1 due; lowest condition 22"
