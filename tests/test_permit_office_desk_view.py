"""Pure desk-model tests for compact Permit Office impact buckets."""

from __future__ import annotations

from dataclasses import replace as replace_model

from toolbox import arcpy_permit_office_rules as rules
from toolbox.permit_office_arcgis.desk_view import (
    HEADLINE_METRICS,
    KEY_MODIFIER_MASK,
    Palette,
    PermitDeskView,
    ReceiptModel,
    ReportTab,
    _Stacker,
    _hazard_summary,
    _maintenance_summary,
    _service_gap_summary,
    build_desk_model,
    report_sections,
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
    )
    buckets = {bucket.label: bucket for bucket in model.case.impact_buckets}

    assert list(buckets) == ["Cost", "City", "Local", "People", "Services", "Budget"]
    assert model.exhibit_visible is True
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


def _stub_measurer(size, _weight):
    """Return a deterministic width function standing in for Tk font.measure."""

    return lambda text: int(len(text) * abs(size) * 0.62)


def _status_strip_view():
    """Return (view, canvas, strip box) for drawing only the status strip with stub font metrics."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    view._px_measurer = _stub_measurer
    canvas = _FakeCanvas()
    view.canvas = canvas
    strip = (20, 78, 620, 106)
    view._status_strip_box = strip
    return view, canvas, strip


def test_ticker_redraw_stays_under_the_open_menu():
    """Verify a marquee tick redraws the status strip below the open menu, not over it."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    canvas = _FakeCanvas()
    view.canvas = canvas
    view._menu_open = True
    view._draw(1180, 860)
    menu_item = next(entry for entry in canvas.created if entry[0] == "text" and entry[2].get("text") == "NEW GAME")

    view.update_status_marquee(240)

    strip = [index for index, entry in enumerate(canvas.created) if "status-strip" in _tags(entry)]
    assert strip and max(strip) < canvas.created.index(menu_item)


def test_status_text_is_fitted_to_the_strip_by_measured_width():
    """Verify a long one-off status message never runs past the strip's right edge."""

    view, canvas, strip = _status_strip_view()

    view.update_status_strip("Decision filed. " * 30)

    _kind, args, kwargs = next(entry for entry in canvas.created if entry[0] == "text" and str(entry[2].get("text", "")).startswith("Decision"))
    assert args[0] + _stub_measurer(kwargs["font"][1], kwargs["font"][2])(kwargs["text"]) <= strip[2]


def test_no_hex_color_literals_outside_palette():
    """Verify desk colors are named Palette tokens, not stray hex literals."""

    import re
    from pathlib import Path

    from toolbox.permit_office_arcgis import desk_view

    source = Path(desk_view.__file__).read_text()
    palette_start = source.index("class Palette")
    palette_end = source.index("\nclass ", palette_start + 1)
    outside = source[:palette_start] + source[palette_end:]
    assert re.findall(r"#[0-9a-fA-F]{6}\b", outside) == []


def test_desk_uses_at_most_four_font_sizes():
    """Verify the full desk draws from one small type scale."""

    item, districts = _vendor_case()
    tabs = (ReportTab("week-1", "Week Closed", "week", "week", False, "Week one report."),)
    models = (
        build_desk_model(rules.CityState(), districts, [item], item.item_id, show_start_help=True),
        build_desk_model(rules.CityState(), districts, [item], item.item_id, report_tabs=tabs, selected_desk_tab="reports"),
        build_desk_model(rules.CityState(), districts, [], ""),
    )
    sizes = set()
    for model in models:
        view, _callbacks = _view_for_drawing(model)
        view._font = PermitDeskView._font.__get__(view)
        canvas = _FakeCanvas()
        view.canvas = canvas
        view._menu_open = True
        view._draw(1280, 1000)
        sizes |= {kwargs["font"][1] for kind, _args, kwargs in canvas.created if kind == "text"}
    assert len(sizes) <= 4, sorted(sizes)


APPROVED_REPORT = (
    "Approved Street Vendor Compact. Certain effects: affected 5 district(s): Civic Green, Cinder Yard, Old Row, +2 more; "
    "immediate city delta activity +3, friction +1, trust +2, services +1; primary pressure service gap x3, stable x2; "
    "local deltas Civic Green activity +7, friction +1, trust +5, services +2; Cinder Yard activity +2, trust +1, services +1; "
    "Old Row activity +2, friction +1, trust +1; spillover Cinder Yard, Old Row, Glass Market, Glass Row gets activity +2, "
    "friction +1, trust +1; recurring budget helps the budget later: revenue $8/week, upkeep $2/week, net $+6. "
    "Exposure/side effects: unlicensed spillover did not trigger; estimated failure chance was 12%. "
    "Population file: families, and homeowners are the main affected groups. Local changes: Civic Green act +7, dissat +3, "
    "fric +1, serv +2; Cinder Yard act +2, dissat +3, serv +1, trust +1; Old Row act +2, fric +1, trust +1; +2 district(s)."
)
WEEK_REPORT = (
    "Advanced week. Carried 0 item(s), expired 3 item(s). Stakeholder heat added to 3 unresolved case(s). "
    "Economy: start $60, permit spend $12, revenue $24, upkeep $0, net +12, end $72. Population drift +104. "
    "Civic Market entered contested buyout from North Row; natural bid cleared local leverage after weak activity and pressure. "
    "Office Standing 57 Authorized. Lead threat: Public Anger (Cinder Yard complaints; commuters grievance 4). "
    "District tags: Cinder Yard: Service Desert, Anger Cluster; D0003: Contested Edge."
)


def test_report_sections_split_a_decision_report_into_headed_sections():
    """Verify a filed decision report splits into a summary and the five headed sections."""

    summary, sections = report_sections(APPROVED_REPORT)

    assert summary == "Approved Street Vendor Compact."
    headings = [heading for heading, _lines in sections]
    assert headings == ["City effects", "Local changes", "Spillover", "Economy", "Side effects"]
    joined = " ".join(line for _heading, lines in sections for line in lines)
    for fragment in ("activity +3", "Old Row activity +2", "Glass Row", "net $+6", "12%", "homeowners", "+2 district(s)"):
        assert fragment in joined


def test_report_sections_use_district_names_not_ids():
    """Verify raw district ids in a week report become district names."""

    summary, sections = report_sections(WEEK_REPORT, {"D0003": "Glass Market"})

    lines = [line for _heading, section_lines in sections for line in section_lines]
    assert summary == "Advanced week."
    assert any("Glass Market: Contested Edge" in line for line in lines)
    assert not any("D0003" in line for line in lines)
    assert "Economy" in [heading for heading, _lines in sections]


def test_district_table_orders_targets_then_changed_districts():
    """Verify targets come first, then districts that changed this week, then the rest."""

    state = rules.CityState()
    state.daily_pressure = {"D0401": 2}
    districts_seed = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}
    for profile in districts_seed.values():
        profile.display_state = "stable"
        profile.identity_state = "stable"
        profile.incident_state = "none"
    districts_seed["D0302"].display_state = "service_gap"
    item = rules.DocketItem("T01", "street_vendor_compact", "Street Vendor Compact", "POINT", 1, target_cell_ids=["D0203"])

    model = build_desk_model(state, districts_seed, [item], item.item_id)

    order = [row.district for row in model.district_table_rows]
    assert order[0] == "D0203"
    assert set(order[1:3]) == {"D0302", "D0401"}
    assert order[3:] == sorted(order[3:])


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

    def delete(self, tag="all"):
        """Remove every item, or only the items carrying ``tag``."""

        if tag == "all":
            self.created.clear()
        else:
            self.created = [entry for entry in self.created if tag not in _tags(entry)]

    def tag_lower(self, tag, below):
        """Move the items carrying ``tag`` to just under the first item carrying ``below``, if any."""

        moved = [entry for entry in self.created if tag in _tags(entry)]
        rest = [entry for entry in self.created if tag not in _tags(entry)]
        at = next((index for index, entry in enumerate(rest) if below in _tags(entry)), None)
        if at is not None:
            self.created = rest[:at] + moved + rest[at:]

    def bbox(self, _item):
        """Report no measurable box, exercising the conservative fallback."""

        return None

    def winfo_width(self):
        """Report the width the test drew at (a pane default otherwise)."""

        return getattr(self, "size", (480, 820))[0]

    def winfo_height(self):
        """Report the height the test drew at (a pane default otherwise)."""

        return getattr(self, "size", (480, 820))[1]

    def configure(self, **_kwargs):
        """Accept cursor changes from hover handling."""


def _tags(entry):
    """Return the canvas tags recorded with one fake-canvas item."""

    tags = entry[2].get("tags") or ()
    return (tags,) if isinstance(tags, str) else tuple(tags)


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
    view._hotkey_targets = {}
    view._body_box = None
    view._body_scroll = 0
    view._body_tab = ""
    view._earmark_choice = ""
    view._menu_anchor = None
    view._ticker_offset_px = 0
    view.root = None
    view._font = lambda size, weight="normal": ("Segoe UI", size, weight)
    view._px_measurer = lambda _size, _weight: None
    return view, callbacks


def _text_values(canvas):
    """Return all text values written to the fake canvas."""

    return [kwargs.get("text") for kind, _args, kwargs in canvas.created if kind == "text"]


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
    hover redraws instead of rebuilt each frame.
    """

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)

    view._ensure_lookups()
    first_ledger = view._ledger_by_label
    first_lanes = view._lane_by_action
    assert first_ledger["Heat"].label == "Heat"
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
        if kind == "rect" and kwargs.get("fill") == Palette.CONTENT and len(args) == 4 and args[3] - args[1] > 100
    ]
    assert menu_rects
    x0, y0, _x1, _y1 = menu_rects[-1]
    assert x0 == 584
    assert y0 == 48


def test_session_menu_rows_and_buttons_tint_on_hover():
    """Verify the hovered menu row and help-card button draw the selection tint, others stay plain."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    view._hover_key = "session:Scorecard"
    canvas = _FakeCanvas()

    view._draw_session_menu(canvas, 1300)
    view._draw_session_button(canvas, 0, 0, 150, 36, "Scorecard", Palette.WATCH, lambda: None)
    view._draw_session_button(canvas, 0, 40, 150, 76, "Help", Palette.MUTED, lambda: None)

    tinted = [args for kind, args, kwargs in canvas.created if kind == "rect" and kwargs.get("fill") == Palette.SELECT]
    assert len(tinted) == 2
    assert tinted[1] == (0, 0, 150, 36)


def test_wrapped_panel_text_fits_its_measured_width():
    """Verify inbox titles wrap by measured width: no line wider than the row, a cut ends in '...'."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)
    view, _callbacks = _view_for_drawing(model)
    view._px_measurer = _stub_measurer
    measure = _stub_measurer(8, "bold")

    lines = view._fit_lines_px("Contractor Renovation Waiver for the Old Market Row Arcade Extension", 8, "bold", 120, 2)

    assert len(lines) == 2
    assert all(measure(line) <= 120 for line in lines)
    assert lines[-1].endswith("...")


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


def test_ledger_rows_include_trend_points_for_core_pulse_stats():
    """Verify pulse stats carry static sparkline data for the view."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(activity=63, friction=28, trust=47, exposure=19), districts, [item], item.item_id)
    ledger = {row.label: row for row in model.ledger_rows}

    for label in ("Activity", "Trust", "Friction", "Exposure"):
        assert ledger[label].trend in {"up", "down", "flat", "unknown"}
        assert len(ledger[label].points) in {0, 6}


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
    assert "Services" in {row.label for row in model.ledger_rows}


def test_selected_case_renders_economy_and_action_note():
    """Verify the decision brief carries a recurring budget line and action note."""

    item, districts = _vendor_case()
    model = build_desk_model(rules.CityState(), districts, [item], item.item_id)

    assert model.case.economy
    assert "Issue=" in model.case.action_note


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


def _key(view, char, state=0, widget=None):
    """Send one key press to the view's hotkey handler."""

    from types import SimpleNamespace

    event = SimpleNamespace(char=char, keysym=char, state=state, widget=view.canvas if widget is None else widget)
    return view._on_key(event)


def _drawn_case_view(state):
    """Return a fully drawn view with one selected vendor case."""

    item, districts = _vendor_case()
    model = build_desk_model(state, districts, [item], item.item_id)
    view, callbacks = _view_for_drawing(model)
    view.canvas = _FakeCanvas()
    view._draw(1120, 900)
    return view, callbacks


def test_shown_hotkeys_run_their_enabled_actions():
    """Verify each badge letter and the help card's W, S, ? keys run their actions."""

    view, callbacks = _drawn_case_view(rules.CityState(ap=2, money=75))

    for char in ("v", "T", "i", "a", "m", "d", "w", "s", "?"):
        assert _key(view, char) == "break"

    assert [name for name, _args in callbacks.calls] == [
        "toggle_exhibit",
        "update_from_map",
        "inspect",
        "approve",
        "approve_mitigated",
        "deny",
        "advance_turn",
        "scorecard",
        "show_help",
    ]


def test_hotkeys_skip_disabled_actions_modifiers_and_text_entry():
    """Verify a key cannot do what a click cannot, and typing a seed never decides a case."""

    view, callbacks = _drawn_case_view(rules.CityState(ap=0, money=75))

    _key(view, "a")
    _key(view, "m")
    _key(view, "d", state=0x4)
    _key(view, "d", state=KEY_MODIFIER_MASK & ~0x4)
    _key(view, "d", widget=object())
    _key(view, "x")

    assert callbacks.calls == []
    _key(view, "d")
    assert [name for name, _args in callbacks.calls] == ["deny"]


def test_start_help_card_only_answers_the_help_key():
    """Verify hotkeys stay off while the start/help card covers the desk."""

    model = build_desk_model(rules.CityState(), {}, [], show_start_help=True)
    view, callbacks = _view_for_drawing(model)
    view.canvas = _FakeCanvas()
    view._draw(1120, 900)

    _key(view, "w")
    _key(view, "s")
    _key(view, "?")

    assert [name for name, _args in callbacks.calls] == ["show_help"]


def test_approved_cases_leave_the_inbox_and_clear_the_queue():
    """Verify an approved (active) case is filed: off the inbox, and the last decision clears the queue."""

    item, districts = _vendor_case()
    item.status = "active"
    other = rules.DocketItem("T02", "street_vendor_compact", "Business License Fee Sweep", "POINT", 1)

    model = build_desk_model(rules.CityState(), districts, [item, other], item.item_id)
    assert [row.item_id for row in model.docket_rows] == ["T02"]
    assert model.selected_item_id == "T02"

    other.status = "denied"
    assert build_desk_model(rules.CityState(), districts, [item, other]).queue_cleared is True


def test_defer_lane_shows_the_ap_the_rules_charge():
    """Verify enforcement, maintenance, and incident defers show 1 AP and disable at 0 AP, like the rules."""

    _item, districts = _vendor_case()
    for template_id, geometry in (
        ("unpermitted_followthrough", "POINT"),
        ("feature_maintenance_order", "POINT"),
        ("civic_incident_response", "POLYGON"),
    ):
        case = rules.DocketItem("T09", template_id, rules.TEMPLATES[template_id].title, geometry, 1, target_cell_ids=["D0000"])
        deny = {lane.action_id: lane for lane in build_desk_model(rules.CityState(ap=0), districts, [case], "T09").action_lanes}["deny"]
        assert deny.cost.startswith("1 AP"), template_id
        assert deny.enabled is False, template_id

    plain = rules.DocketItem("T10", "street_vendor_compact", "Street Vendor Compact", "POINT", 1, target_cell_ids=["D0000"])
    deny = {lane.action_id: lane for lane in build_desk_model(rules.CityState(ap=0), districts, [plain], "T10").action_lanes}["deny"]
    assert deny.cost.startswith("0 AP") and deny.enabled


def _case(item_id, template_id, status="open"):
    """Return a docket item for one template with a known district target."""

    template = rules.TEMPLATES[template_id]
    return rules.DocketItem(item_id, template_id, template.title, template.geometry_type, 1, status=status, target_cell_ids=["D0000"])


def test_model_header_carries_audit_gap_money_net_and_clock():
    """Verify the header facts the narrow pane shows come from the model, not the view."""

    _item, districts = _vendor_case()
    audit = rules.AuditResult("CONDITIONAL", 58, (rules.AuditFinding("f", "critical", "money", "low"),), "Audit CONDITIONAL")
    state = rules.CityState(turn=3, money=58, last_net=3)

    model = build_desk_model(state, districts, [], audit=audit)

    assert (model.audit_grade, model.audit_score, model.audit_points_short, model.audit_criticals) == ("CONDITIONAL", 58, 12, 1)
    assert (model.money, model.money_net, model.week_label, model.ap_label) == (58, 3, "3/12", "2/2")


def test_model_rows_say_what_ignoring_each_case_does_and_forecast_the_close():
    """Verify each open case names its expiration outcome and the footer forecast counts them."""

    _item, districts = _vendor_case()
    items = [
        _case("A", "fire_budget_escalation"),
        _case("B", "procession_route"),
        _case("C", "connector_corridor"),
        _case("D", "street_vendor_compact"),
    ]

    model = build_desk_model(rules.CityState(), districts, items)

    assert {row.item_id: row.if_ignored for row in model.docket_rows} == {
        "A": "returns next week",
        "B": "expires",
        "C": "expires, adds district pressure",
        "D": "expires, may bring a follow-up",
    }
    assert model.close_forecast == "At close: 1 returns, 3 expire."


def test_model_lists_filed_cases_apart_from_open_ones():
    """Verify cases decided this week show as filed rows, never as open work."""

    _item, districts = _vendor_case()
    items = [_case("A", "street_vendor_compact", "active"), _case("B", "procession_route", "denied"), _case("C", "connector_corridor")]

    model = build_desk_model(rules.CityState(), districts, items)

    assert [row.item_id for row in model.docket_rows] == ["C"]
    assert [(row.item_id, row.status) for row in model.filed_rows] == [("A", "active"), ("B", "denied")]


def test_city_trends_are_deltas_since_week_start_or_unknown():
    """Verify stat trends come from a real week-start snapshot and are never invented."""

    _item, districts = _vendor_case()
    state = rules.CityState(activity=53, friction=20, trust=35, exposure=27)
    start = {"activity": 50, "friction": 22, "trust": 35, "exposure": 25, "money": 60}

    with_start = {row.label: row for row in build_desk_model(state, districts, [], week_start=start).ledger_rows}
    without = {row.label: row for row in build_desk_model(state, districts, []).ledger_rows}

    assert [with_start[name].trend for name in ("Activity", "Friction", "Trust", "Exposure")] == ["up", "down", "flat", "up"]
    assert all(without[name].trend == "unknown" for name in ("Activity", "Friction", "Trust", "Exposure"))
    assert all(row.points == () for row in (*with_start.values(), *without.values()))


def test_model_carries_the_goal_and_the_ladder_line():
    """Verify the header facts for the new loop: filed goal progress, offer, ladder rung, next checkpoint (D5, D9, D10)."""

    _item, districts = _vendor_case()
    goal = {"offer": ["public_confidence", "quiet_streets", "even_handed"], "chosen": "", "baseline": {"type_counts": {"mercantile": 1}, "critical_gaps": 0}}
    state = rules.CityState(turn=5, trust=40, audit_rung=1, mandate=dict(goal))

    unpicked = build_desk_model(state, districts, [])
    assert unpicked.goal_title == ""
    assert [card[1] for card in unpicked.goal_offer] == ["Public confidence", "Quiet streets", "Even-handed city"]

    state.mandate = {**goal, "chosen": "public_confidence"}
    model = build_desk_model(state, districts, [])
    assert (model.goal_title, model.goal_met) == ("Public confidence", False)
    assert "40 of 55" in model.goal_progress
    assert (model.ladder_rung, model.next_checkpoint) == ("warning", 8)


def test_ledger_keeps_only_the_approved_meters():
    """Verify Office Standing and the folded meters are gone from the desk (D10)."""

    _item, districts = _vendor_case()
    labels = {row.label for row in build_desk_model(rules.CityState(), districts, []).ledger_rows}

    assert labels == {"Week", "AP", "Money", "Heat", "Activity", "Friction", "Trust", "Exposure", "Economy", "Services", "Incidents"}


def test_district_type_rows_count_districts_and_mark_earmarks():
    """Verify the City tab's district types show counts and which type is earmarked."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=5, cols=5, seed=2034)}
    state = rules.CityState(turn=2, initiatives={"week": 2, "earmarks": {"civic": 5}})

    rows = {row.label: row for row in build_desk_model(state, districts, []).district_type_rows}

    assert rows["Mercantile"].count == 9 and rows["Civic"].earmarked_until == 5
    assert rows["Mercantile"].earmarked_until == 0


def test_goal_offer_cards_carry_brief_and_progress_and_the_city_tab_resolves():
    """Verify the goal picker has what its cards show, and 'city' is a desk tab."""

    _item, districts = _vendor_case()
    state = rules.CityState(trust=41, mandate={"offer": ["public_confidence"], "chosen": "", "baseline": {"type_counts": {}, "critical_gaps": 0}})

    model = build_desk_model(state, districts, [], selected_desk_tab="city")

    key, title, brief, progress = model.goal_offer[0]
    assert (key, title) == ("public_confidence", "Public confidence")
    assert "55" in brief and "41 of 55" in progress
    assert model.selected_desk_tab == "city"


def test_model_says_whether_this_weeks_initiative_is_open():
    """Verify the initiative card knows the slot, AP and money state, and the district types to earmark."""

    _item, districts = _vendor_case()
    open_model = build_desk_model(rules.CityState(turn=2), districts, [])
    used = build_desk_model(rules.CityState(turn=2, initiatives={"week": 2}), districts, [])
    broke = build_desk_model(rules.CityState(turn=2, ap=0), districts, [])

    assert (open_model.initiative_open, open_model.initiative_note) == (True, "")
    assert used.initiative_open is False and "this week" in used.initiative_note
    assert broke.initiative_open is False and "AP" in broke.initiative_note
    assert open_model.earmark_types == ("mercantile",)


# ----- narrow pane (D13) ------------------------------------------------------


def _pane(model, width=480, height=820):
    """Return a headless view drawn at a pane size, its callbacks, and its canvas."""

    view, callbacks = _view_for_drawing(model)
    view.canvas = _FakeCanvas()
    view.canvas.size = (width, height)
    view._draw(width, height)
    return view, callbacks, view.canvas


def _targets(view, kind):
    return {ident: bbox for target_kind, ident, bbox, _callback in view._click_targets if target_kind == kind}


def _click(view, kind, ident):
    for target_kind, target_ident, _bbox, callback in reversed(view._click_targets):
        if (target_kind, target_ident) == (kind, ident):
            callback()
            return True
    return False


def _goal_state(chosen="public_confidence", **kwargs):
    goal = {"offer": ["public_confidence", "quiet_streets", "even_handed"], "chosen": chosen, "baseline": {"type_counts": {"mercantile": 1}, "critical_gaps": 0}}
    return rules.CityState(mandate=goal, **kwargs)


def test_pane_header_shows_resources_goal_audit_and_patience():
    """Verify the header carries the new loop's facts: goal progress, next checkpoint, ladder."""

    item, districts = _vendor_case()
    model = build_desk_model(_goal_state(turn=3, trust=41, money=58, last_net=3, audit_rung=1), districts, [item], item.item_id)
    _view, _callbacks, canvas = _pane(model)

    texts = [str(text) for text in _text_values(canvas)]
    assert "3/12" in texts and "2/2" in texts and "58 (+3)" in texts
    assert any(text.startswith("trust 41 of 55 (open). Public confidence") for text in texts)
    assert any(text.startswith("week 4 checkpoint:") and "to PASS" in text for text in texts)
    assert [text for text in texts if text in rules.AUDIT_RUNGS] == list(rules.AUDIT_RUNGS)


def test_goal_picker_files_a_goal_by_click_or_number_key():
    """Verify an unfiled season shows the three seeded goals and picks one by click or 1-3."""

    item, districts = _vendor_case()
    model = build_desk_model(_goal_state(chosen=""), districts, [item], item.item_id)
    view, callbacks, canvas = _pane(model)

    assert set(_targets(view, "goal")) == {"public_confidence", "quiet_streets", "even_handed"}
    assert "OPEN (1)" not in _text_values(canvas)
    _click(view, "goal", "quiet_streets")
    _key(view, "3")
    assert callbacks.calls == [("choose_mandate", ("quiet_streets",)), ("choose_mandate", ("even_handed",))]


def test_desk_tab_lists_open_then_filed_cases_and_only_open_ones_select():
    """Verify filed cases show under open ones and cannot be picked for a decision."""

    item, districts = _vendor_case()
    filed = rules.DocketItem("T09", "procession_route", "Procession Route", "LINE", 1, status="active")
    model = build_desk_model(_goal_state(), districts, [item, filed], item.item_id)
    view, callbacks, canvas = _pane(model)

    texts = _text_values(canvas)
    assert "OPEN (1)" in texts and "FILED (1)" in texts and "ISSUED" in texts
    assert set(_targets(view, "docket")) == {item.item_id}
    _click(view, "docket", item.item_id)
    assert callbacks.calls == [("select_item", (item.item_id,))]


def test_case_brief_buttons_follow_lane_state_and_bind_their_keys():
    """Verify the four actions register clicks and keys only when enabled, with exhibit links on V and T."""

    item, districts = _vendor_case()
    view, callbacks, _canvas = _pane(build_desk_model(_goal_state(ap=0), districts, [item], item.item_id))

    assert set(_targets(view, "case-action")) == {"Inspect File", "Deny"}
    assert {"i", "d", "v", "t"} <= set(view._hotkey_targets) and "a" not in view._hotkey_targets
    _key(view, "v")
    _key(view, "t")
    assert [name for name, _args in callbacks.calls] == ["toggle_exhibit", "update_from_map"]


def test_initiative_card_earmarks_the_chosen_type_and_uses_the_map_for_the_others():
    """Verify the type chips choose what Earmark backs, and the other two act on the map selection."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=5, cols=5, seed=2034)}
    model = build_desk_model(_goal_state(turn=2), districts, [])
    view, callbacks, _canvas = _pane(model, height=1400)

    assert set(_targets(view, "earmark-type")) == set(model.earmark_types)
    _click(view, "earmark-type", "industrial")
    _click(view, "case-action", "Earmark industrial")
    _click(view, "case-action", "Civic action")
    _click(view, "case-action", "Market push")
    assert callbacks.calls == [
        ("start_initiative", ("earmark", "industrial")),
        ("start_initiative", ("civic_action", None)),
        ("start_initiative", ("market_push", None)),
    ]

    spent = build_desk_model(_goal_state(turn=2, initiatives={"week": 2}), districts, [])
    view, _callbacks, canvas = _pane(spent, height=1400)
    assert not any(ident.startswith(("Earmark", "Civic", "Market")) for ident in _targets(view, "case-action"))
    assert "Filed this week. One initiative a week." in _text_values(canvas)


def test_footer_end_week_is_always_there_until_the_season_ends():
    """Verify End Week (and W) is drawn at zero AP and gives way to New Game once the season is decided."""

    item, districts = _vendor_case()
    view, callbacks, canvas = _pane(build_desk_model(_goal_state(ap=0), districts, [item], item.item_id))
    assert "End Week" in _targets(view, "footer")
    assert any(str(text).startswith("At close:") for text in _text_values(canvas))
    _key(view, "w")
    assert callbacks.calls == [("advance_turn", ())]

    done = build_desk_model(_goal_state(status="complete", outcome="won"), districts, [])
    view, _callbacks, canvas = _pane(done)
    assert set(_targets(view, "footer")) == {"New Game"}
    assert "w" not in view._hotkey_targets
    assert "Season won" in _text_values(canvas)


def test_tabs_switch_between_desk_reports_and_city():
    """Verify the three tabs call select_desk_tab with their ids."""

    item, districts = _vendor_case()
    view, callbacks, _canvas = _pane(build_desk_model(_goal_state(), districts, [item], item.item_id))

    for tab_id in ("reports", "city", "applications"):
        _click(view, "desk-tab", tab_id)
    assert callbacks.calls == [("select_desk_tab", ("reports",)), ("select_desk_tab", ("city",)), ("select_desk_tab", ("applications",))]


def test_city_tab_draws_stats_trends_services_and_district_types():
    """Verify the City tab shows the four stats, only real trends, and earmarked district types."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=5, cols=5, seed=2034)}
    state = _goal_state(turn=2, initiatives={"week": 2, "earmarks": {"civic": 5}})
    start = {"activity": 47, "friction": 20, "trust": 35, "exposure": 25, "money": 60}

    _view, _callbacks, canvas = _pane(build_desk_model(state, districts, [], selected_desk_tab="city", week_start=start), height=1200)
    texts = [str(text) for text in _text_values(canvas)]
    for label in ("ACTIVITY", "TRUST", "FRICTION", "EXPOSURE", "ECONOMY", "HEAT", "SERVICES", "INCIDENTS", "DISTRICT TYPES"):
        assert label in texts
    assert "up" in texts and "?" not in texts
    assert "2   earmarked to week 5" in texts

    _view, _callbacks, canvas = _pane(build_desk_model(state, districts, [], selected_desk_tab="city"), height=1200)
    assert not {"up", "down", "flat"} & set(_text_values(canvas))


def test_reports_tab_lists_reports_and_draws_every_section_line():
    """Verify the Reports tab selects a report by click and draws its whole body (the tab scrolls, nothing is cut)."""

    item, districts = _vendor_case()
    long_report = " ".join(f"Line {index} of a long filed report with several clauses." for index in range(30))
    tabs = (
        ReportTab("r1", "Week Closed", "week", "week", False, "Advanced week.", (), (("WEEK", "2/12"),)),
        ReportTab("r2", "Street Vendor Compact", "report", "approved", True, long_report, (), (("WEEK", "2/12"),)),
    )
    model = build_desk_model(_goal_state(), districts, [item], item.item_id, report_tabs=tabs, selected_report_id="r2", selected_desk_tab="reports")
    view, callbacks, canvas = _pane(model, height=560)

    assert set(_targets(view, "report")) <= {"r1", "r2"}
    _click(view, "report", "r1")
    assert callbacks.calls == [("select_report", ("r1",))]
    body = " ".join(str(text) for text in _text_values(canvas))
    assert "Line 29" in body and "..." not in body


def test_long_body_scrolls_and_click_targets_stay_inside_the_viewport():
    """Verify the tab body scrolls under the fixed bands and off-screen controls take no clicks."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=5, cols=5, seed=2034)}
    items = rules.generate_docket(turn=1, seed=2034, state=rules.CityState(), districts=districts)
    model = build_desk_model(_goal_state(), districts, items, items[0].item_id)
    view, _callbacks, canvas = _pane(model, width=400, height=560)

    body = view._body_box
    assert any(kwargs.get("tags") == ("body-scrollbar",) for kind, _args, kwargs in canvas.created if kind == "rect")
    for kind, _ident, (x0, y0, x1, y1), _callback in view._click_targets:
        if kind in ("docket", "case-action", "case-link", "earmark-type", "goal"):
            assert body[1] <= y0 and y1 <= body[3]
        assert 0 <= x0 and x1 <= 400

    from types import SimpleNamespace

    view._on_mouse_wheel(SimpleNamespace(num=5, delta=0, x=(body[0] + body[2]) // 2, y=(body[1] + body[3]) // 2))
    assert view._body_scroll > 0


def test_empty_desk_before_a_game_and_after_the_queue_clears():
    """Verify a fresh workspace prompts New Game and a cleared queue points at End Week."""

    _view, _callbacks, canvas = _pane(build_desk_model(rules.CityState(), {}, [], game_active=False))
    assert "No game yet" in _text_values(canvas)

    _item, districts = _vendor_case()
    _view, _callbacks, canvas = _pane(build_desk_model(_goal_state(), districts, []))
    assert "Queue cleared. End Week to process follow-ups." in _text_values(canvas)


def test_ticker_is_masked_at_both_ends_of_the_status_strip():
    """Verify the scrolling wire never draws past the strip edges."""

    item, districts = _vendor_case()
    model = replace_model(build_desk_model(_goal_state(), districts, [item], item.item_id), status_text="")
    view, _callbacks, canvas = _pane(model)
    canvas.created.clear()
    view.update_status_marquee(300)
    x0, _y0, x1, _y1 = view._status_strip_box
    masks = [args for kind, args, kwargs in canvas.created if kind == "rect" and kwargs.get("fill") == Palette.FRAME and "status-strip" in kwargs.get("tags", ())]
    assert any(args[2] == x0 and args[0] < x0 for args in masks)
    assert any(args[0] == x1 and args[2] > x1 for args in masks)


def test_help_card_explains_the_season_and_the_keys():
    """Verify the help card names the goal, audits, cases, initiative and keys."""

    _view, _callbacks, canvas = _pane(build_desk_model(rules.CityState(), {}, [], show_start_help=True))
    body = " ".join(str(text) for text in _text_values(canvas))
    for phrase in ("Goal", "Audits", "Cases", "Initiative", "Keys", "week 12", "W end week"):
        assert phrase in body
