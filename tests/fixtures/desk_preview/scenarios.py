"""Fixed desk scenarios for tools/desk_preview.py, built from pure rules (no ArcPy).

Each scenario returns a DeskViewModel the real PermitDeskView can render. The
game is generated with a fixed seed so every preview run draws the same desk.
"""

from __future__ import annotations

from types import SimpleNamespace
import sys


# Same arcpy stand-in the controller tests use: the report text helpers live in
# dashboard.py, which imports arcpy at module load.
sys.modules.setdefault(
    "arcpy",
    SimpleNamespace(AddMessage=lambda text: None, AddWarning=lambda text: None, AddError=lambda text: None),
)

from toolbox import arcpy_permit_office_rules as rules  # noqa: E402
from toolbox.permit_office_arcgis import dashboard  # noqa: E402
from toolbox.permit_office_arcgis.desk_model import ReportTab, build_desk_model  # noqa: E402
from toolbox.permit_office_arcgis.desk_view import receipt_metrics  # noqa: E402


SEED = 2026


def _new_game():
    """Return a deterministic fresh city: state, districts by id, week-one docket."""

    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=SEED)}
    items = rules.generate_docket(state.turn, seed=SEED, state=state, districts=districts)
    for index, item in enumerate(items):
        if not item.target_cell_ids:
            item.target_cell_ids = [sorted(districts)[(index * 3) % len(districts)]]
    return state, districts, items


def _first_approvable(items):
    """Return the first docket item that is not a maintenance or enforcement follow-up."""

    for item in items:
        if item.template_id in rules.TEMPLATES and item.template_id != rules.MAINTENANCE_TEMPLATE_ID:
            return item
    return items[0]


def _report_tab(report_id, title, kind, status, report, state, districts, *, selected=False, affected=()):
    """Return a ReportTab with sections split the way the controller splits them."""

    summary, sections = dashboard._report_tab_sections(report, districts)
    return ReportTab(
        report_id, title, kind, status, selected, report, affected, receipt_metrics(state), sections=sections, summary=summary
    )


def applications_mid_week():
    """Applications tab mid-week with an inspected, selected case."""

    state, districts, items = _new_game()
    selected = items[min(1, len(items) - 1)]
    rules.resolve_decision(state, selected, districts, "inspect", selected.target_cell_ids, seed=SEED)
    return build_desk_model(
        state,
        districts,
        items,
        selected.item_id,
        "",
        {selected.item_id: True},
        selected_desk_tab="applications",
    )


def filed_reports_long():
    """Filed reports tab after a full week: one decision report and the week report."""

    state, districts, items = _new_game()
    item = _first_approvable(items)
    neighbours = [cid for cid in sorted(districts) if cid not in item.target_cell_ids][:4]
    result = rules.resolve_decision(state, item, districts, "approve", item.target_cell_ids, neighbours, seed=SEED)
    decision_text = dashboard._filed_report_text(result, districts)
    status = dashboard._report_status(decision_text)
    affected = tuple(result.affected_cell_ids)
    decision_tab = _report_tab("report-1", item.title, "report", status, decision_text, state, districts, affected=affected)
    week = rules.advance_turn_result(state, items, districts)
    week_tab = _report_tab("week-1", "Week Closed", "week", "week", week.report, state, districts)
    next_items = rules.generate_docket(state.turn, seed=SEED, state=state, districts=districts)
    tabs = (week_tab, decision_tab)
    return build_desk_model(
        state,
        districts,
        next_items,
        "",
        "",
        report_tabs=tabs,
        selected_report_id="report-1",
        selected_desk_tab="reports",
    )


def final_audit():
    """Final audit receipt after the season closes."""

    state, districts, items = _new_game()
    state.turn = state.max_turns + 1
    state.status = "complete"
    grade, card = rules.scorecard(state, districts, (), items)
    report = dashboard._final_audit_report(grade, card)
    tab = _report_tab("scorecard-1", f"Final Audit: {grade}", "scorecard", "scorecard", report, state, districts, selected=True)
    return build_desk_model(
        state,
        districts,
        [],
        "",
        "",
        report_tabs=(tab,),
        selected_report_id="scorecard-1",
        selected_desk_tab="reports",
        audit_grade=grade,
    )


# Scenarios drawn with the scrolling ticker (status line cleared) at this offset.
TICKER_OFFSETS = {
    "reports": 520,
}


SCENARIOS = {
    "applications": applications_mid_week,
    "reports": filed_reports_long,
    "final-audit": final_audit,
}
