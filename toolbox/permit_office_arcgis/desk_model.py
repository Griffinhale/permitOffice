"""Presentation model for the Permit Office desk surface.

Pure gameplay-to-text formatting: no Tkinter, no drawing. The desk view imports
these dataclasses and builders to render one frame.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import re
from typing import Callable

from .rules_loader import rules
from .symbology_config import (
    DISPLAY_STATE_SYMBOLS,
    DISTRICT_TYPE_SYMBOLS,
    IDENTITY_STATE_SYMBOLS,
    PROSPERITY_BAND_SYMBOLS,
)


ACTIVE_STATUSES = frozenset(rules.OPEN_DOCKET_STATUSES)
HEADLINE_METRICS = (("Week", "WEEK"), ("AP", "AP"), ("Money", "$"))


@dataclass(frozen=True)
class DeskCallbacks:
    """UI actions exposed by the dashboard controller."""

    toggle_exhibit: Callable[[], None]
    update_from_map: Callable[[], None]
    inspect: Callable[[], None]
    approve: Callable[[], None]
    approve_mitigated: Callable[[], None]
    deny: Callable[[], None]
    advance_turn: Callable[[], None]
    new_game: Callable[[], None]
    scorecard: Callable[[], None]
    close: Callable[[], None]
    select_desk_tab: Callable[[str], None] = lambda _tab_id: None
    select_report: Callable[[str], None] = lambda _report_id: None
    show_help: Callable[[], None] = lambda: None
    end_game: Callable[[], None] = lambda: None
    cancel_queue_autoclose: Callable[[], None] = lambda: None
    pause_queue_autoclose: Callable[[], None] = lambda: None


@dataclass(frozen=True)
class DocketRow:
    """One visible in-tray case row."""

    item_id: str
    title: str
    geometry_type: str
    status: str
    selected: bool = False
    priority: int = 0
    due_turn: int = 0
    if_ignored: str = ""


@dataclass(frozen=True)
class CaseField:
    """A labeled line in the permit packet."""

    label: str
    value: str


@dataclass(frozen=True)
class ImpactBucket:
    """One compact permit-impact summary bucket."""

    label: str
    value: str
    tone: str = "neutral"


@dataclass(frozen=True)
class DistrictGridCell:
    """One cell in the selected-case district mini-grid."""

    cell_id: str
    label: str
    affected: bool = False
    swatch: str = ""


@dataclass(frozen=True)
class TrendCard:
    """One compact trend card for cultures, outcomes, or pulse rows."""

    label: str
    detail: str
    trend: str = "unknown"
    swatch: str = ""
    points: tuple[int, ...] = ()
    tone: str = "neutral"


@dataclass(frozen=True)
class CaseMapSymbol:
    """One live useful symbol row for the selected case map key."""

    shape: str
    label: str
    detail: str
    swatch: str
    state: str
    tone: str = "neutral"


@dataclass(frozen=True)
class CaseSummary:
    """Structured permit-packet content for the selected case."""

    title: str = "No Active Case"
    item_id: str = ""
    status: str = ""
    category: str = ""
    fields: tuple[CaseField, ...] = ()
    districts: str = "(seeded exhibit; use Retarget Map to revise)"
    preview: str = "Select a docket item from the in tray."
    inspection: str = "No inspection addendum filed."
    action_note: str = ""
    economy: str = ""
    risk_band: str = "unknown"
    impact_buckets: tuple[ImpactBucket, ...] = ()
    district_grid: tuple[DistrictGridCell, ...] = ()
    culture_cards: tuple[TrendCard, ...] = ()
    outcome_cards: tuple[TrendCard, ...] = ()


@dataclass(frozen=True)
class LedgerRow:
    """A compact audit ledger line."""

    label: str
    value: str
    tone: str = "neutral"
    meter: int | None = None
    trend: str = "unknown"
    points: tuple[int, ...] = ()


@dataclass(frozen=True)
class ReceiptModel:
    """The latest filed-report receipt drawn inline at the foot of the desk."""

    title: str = ""
    report: str = ""
    affected: tuple[str, ...] = ()
    metrics: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReportTab:
    """One selectable filed report or scorecard tab."""

    report_id: str
    title: str
    kind: str
    status: str
    selected: bool = False
    report: str = ""
    affected: tuple[str, ...] = ()
    metrics: tuple[tuple[str, str], ...] = ()
    # Presentation split of `report` (see report_sections); `report` stays the
    # joined text for anything that reads it. Held in controller memory only.
    sections: tuple[tuple[str, tuple[str, ...]], ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class ActionLane:
    """One action consequence lane for the selected application decision brief."""

    action_id: str
    label: str
    cost: str
    city_effect: str
    local_effect: str
    tone: str = "neutral"
    enabled: bool = True
    disabled_reason: str = ""
    hotkey: str = ""
    tooltip: str = ""


@dataclass(frozen=True)
class MapLegendRow:
    """One row in the public map key rail."""

    group: str
    label: str
    detail: str
    swatch: str
    tone: str = "neutral"
    shape: str = "zone"
    state: str = ""


@dataclass(frozen=True)
class DistrictGroupRow:
    """Aggregated district-division health for the city pulse rail."""

    label: str
    state: str
    detail: str
    tone: str = "neutral"
    meter: int = 0
    swatch: str = ""
    trend: str = "unknown"
    points: tuple[int, ...] = ()


@dataclass(frozen=True)
class DistrictTableRow:
    """One district state row for the live bottom attribute table."""

    district: str
    name: str
    district_type: str
    prosperity: str
    pressure: str
    community: str
    selected: bool = False
    changed: bool = False


@dataclass(frozen=True)
class DeskViewModel:
    """Everything the desk view needs to draw one frame."""

    docket_rows: tuple[DocketRow, ...] = ()
    selected_item_id: str = ""
    case: CaseSummary = field(default_factory=CaseSummary)
    ledger_rows: tuple[LedgerRow, ...] = ()
    status_text: str = ""
    exhibit_visible: bool = False
    receipt: ReceiptModel | None = None
    report_tabs: tuple[ReportTab, ...] = ()
    selected_report_id: str = ""
    ticker_items: tuple[str, ...] = ()
    show_start_help: bool = False
    selected_desk_tab: str = "applications"
    action_lanes: tuple[ActionLane, ...] = ()
    queue_cleared: bool = False
    auto_close_active: bool = False
    auto_close_seconds: int = 0
    game_active: bool = True
    map_legend_rows: tuple[MapLegendRow, ...] = ()
    district_group_rows: tuple[DistrictGroupRow, ...] = ()
    district_table_rows: tuple[DistrictTableRow, ...] = ()
    case_map_symbols: tuple[CaseMapSymbol, ...] = ()
    filed_rows: tuple[DocketRow, ...] = ()
    close_forecast: str = ""
    audit_grade: str = ""
    audit_score: int = 0
    audit_points_short: int = 0
    audit_criticals: int = 0
    money: int = 0
    money_net: int = 0
    week_label: str = ""
    ap_label: str = ""


# What week close does to a case nobody decided, by template expiration policy
# (toolbox/permit_office/expiration.py).
IF_IGNORED = {
    "mandatory_followup": "returns next week",
    "missed_window": "expires",
    "city_momentum": "expires, adds district pressure",
    "momentum_with_followup_risk": "expires, may bring a follow-up",
}


def build_desk_model(
    state,
    districts,
    items,
    selected_item_id="",
    status_text="",
    proposal_visible_by_item=None,
    active_features=None,
    receipt=None,
    report_tabs=None,
    selected_report_id="",
    show_start_help=False,
    selected_desk_tab="applications",
    auto_close_active=False,
    auto_close_seconds=0,
    audit_grade=None,
    game_active=True,
    audit=None,
    week_start=None,
) -> DeskViewModel:
    """Format gameplay state into a presentation-only desk model.

    `audit_grade`, when provided, is a controller-cached grade that lets the
    ledger skip the scorecard recompute on selection-only redraws; None keeps
    the original recompute-via-scorecard behavior. `audit` is the cached
    AuditResult for the header (recomputed from copies when None), and
    `week_start` the controller's week-start stat snapshot for real trends.
    """

    proposal_visible_by_item = proposal_visible_by_item or {}
    active_items = [item for item in items if item.status in ACTIVE_STATUSES]
    selected = _resolve_selected_item(active_items, selected_item_id)
    selected_id = selected.item_id if selected else ""
    docket_rows = tuple(
        DocketRow(
            item_id=item.item_id,
            title=item.title,
            geometry_type=item.geometry_type,
            status=item.status,
            selected=item.item_id == selected_id,
            priority=item.priority,
            due_turn=item.due_turn,
            if_ignored=_if_ignored(item),
        )
        for item in active_items
    )
    filed_rows = tuple(
        DocketRow(item_id=item.item_id, title=item.title, geometry_type=item.geometry_type, status=item.status)
        for item in items
        if item.status not in ACTIVE_STATUSES
    )
    if audit is None:
        audit = rules.generate_audit_result(state, deepcopy(districts), _feature_snapshots(active_features), deepcopy(list(items or ())))
    if audit_grade is None:
        audit_grade = audit.grade
    points_short, criticals = rules.pass_gap(audit)
    case = _case_summary(state, districts, selected)
    action_lanes = _action_lanes(state, districts, selected) if selected else ()
    ledger_rows = _ledger_rows(state, districts, active_features, active_items, audit_grade=audit_grade, week_start=week_start)
    status = status_text or "No report yet. Select a docket row; use Retarget Map when changing targets."
    report_tabs = tuple(report_tabs or _legacy_report_tabs(receipt))
    selected_report_id = _resolve_selected_report_id(report_tabs, selected_report_id)
    if report_tabs and selected_report_id:
        report_tabs = tuple(
            replace(tab, selected=tab.report_id == selected_report_id)
            for tab in report_tabs
        )
    exhibit_visible = bool(proposal_visible_by_item.get(selected_id))
    ticker_items = _ticker_items(state, districts, active_features, active_items, report_tabs)
    map_legend_rows = _map_legend_rows(districts, active_items, selected_id)
    case_map_symbols = _case_map_symbols(selected, active_features)
    district_group_rows = _district_group_rows(districts)
    district_table_rows = _district_table_rows(districts, selected, state)
    return DeskViewModel(
        docket_rows,
        selected_id,
        case,
        ledger_rows,
        status,
        exhibit_visible,
        receipt,
        report_tabs,
        selected_report_id,
        ticker_items,
        bool(show_start_help),
        _resolve_desk_tab(selected_desk_tab, report_tabs),
        action_lanes,
        not bool(active_items),
        bool(auto_close_active),
        int(auto_close_seconds or 0),
        bool(game_active),
        map_legend_rows,
        district_group_rows,
        district_table_rows,
        case_map_symbols,
        filed_rows=filed_rows,
        close_forecast=_close_forecast(active_items),
        audit_grade=audit.grade,
        audit_score=int(audit.score),
        audit_points_short=points_short,
        audit_criticals=criticals,
        money=int(state.money),
        money_net=int(state.last_net),
        week_label=f"{state.turn}/{state.max_turns}",
        ap_label=f"{state.ap}/{state.max_ap}",
    )


def _if_ignored(item) -> str:
    """Return what week close does to this case if nobody decides it."""

    template = rules.TEMPLATES.get(item.template_id)
    policy = (template.expiration_policy if template else "") or "city_momentum"
    return IF_IGNORED.get(policy, IF_IGNORED["city_momentum"])


def _close_forecast(active_items) -> str:
    """Summarize what End Week would do to the open cases, for the footer."""

    if not active_items:
        return "At close: no open cases."
    returns = sum(1 for item in active_items if _if_ignored(item) == IF_IGNORED["mandatory_followup"])
    expire = len(active_items) - returns
    parts = []
    if returns:
        parts.append(f"{returns} returns")
    if expire:
        parts.append(f"{expire} expire" if expire != 1 else "1 expires")
    return f"At close: {', '.join(parts)}."


def _resolve_selected_item(items, selected_item_id):
    """Return the requested active item or the first item as a fallback."""

    if selected_item_id:
        for item in items:
            if item.item_id == selected_item_id:
                return item
    return items[0] if items else None


def _resolve_desk_tab(selected_desk_tab, report_tabs):
    """Return the active primary lower-desk tab."""

    if selected_desk_tab == "reports":
        return "reports"
    if selected_desk_tab == "applications":
        return "applications"
    return "reports" if report_tabs else "applications"


def _legacy_report_tabs(receipt):
    """Represent the legacy single receipt as one selected report tab."""

    if not receipt:
        return ()
    summary, sections = report_sections(receipt.report)
    return (
        ReportTab(
            "latest",
            receipt.title,
            "report",
            _report_status(receipt.report),
            True,
            receipt.report,
            receipt.affected,
            receipt.metrics,
            sections,
            summary,
        ),
    )


def _resolve_selected_report_id(report_tabs, selected_report_id):
    """Return a valid selected report id for the current report tab set."""

    if selected_report_id:
        for tab in report_tabs:
            if tab.report_id == selected_report_id:
                return selected_report_id
    for tab in report_tabs:
        if tab.selected:
            return tab.report_id
    return report_tabs[-1].report_id if report_tabs else ""


def _trend_from_delta(delta: int | None) -> str:
    """Return the public trend token for a known or unknown delta."""

    if delta is None:
        return "unknown"
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def _spark_points(value: int, trend: str) -> tuple[int, ...]:
    """Return compact six-point sparkline values around a current value."""

    current = max(0, min(100, int(value or 0)))
    if trend == "up":
        return tuple(max(0, min(100, current + offset)) for offset in (-9, -6, -7, -3, -2, 0))
    if trend == "down":
        return tuple(max(0, min(100, current + offset)) for offset in (8, 5, 6, 3, 1, 0))
    if trend == "flat":
        return tuple(max(0, min(100, current + offset)) for offset in (0, 1, 0, -1, 0, 0))
    return ()


def _district_grid_cells(districts, item) -> tuple[DistrictGridCell, ...]:
    """Build a stable district mini-grid with selected targets marked."""

    profiles = sorted(
        list(districts.values() if isinstance(districts, dict) else (districts or ())),
        key=lambda profile: getattr(profile, "cell_id", ""),
    )
    targets = set(getattr(item, "target_cell_ids", ()) or ())
    cells = []
    for profile in profiles[:12]:
        cell_id = getattr(profile, "cell_id", "") or ""
        label = cell_id[1:] if cell_id.startswith("D") else cell_id
        symbol = DISTRICT_TYPE_SYMBOLS.get(getattr(profile, "district_type", "") or "")
        cells.append(DistrictGridCell(cell_id, label[-2:] or label, cell_id in targets, _symbol_hex(symbol)))
    if not cells and targets:
        for cell_id in sorted(targets)[:12]:
            label = cell_id[1:] if cell_id.startswith("D") else cell_id
            cells.append(DistrictGridCell(cell_id, label[-2:] or label, True, ""))
    return tuple(cells)


def _culture_cards(districts, item) -> tuple[TrendCard, ...]:
    """Build affected-culture cards capped to the useful selected-case set."""

    district_map = districts if isinstance(districts, dict) else {getattr(profile, "cell_id", ""): profile for profile in districts or ()}
    targets = [district_map[cid] for cid in getattr(item, "target_cell_ids", ()) or () if cid in district_map]
    scores: dict[str, int] = {}
    swatches: dict[str, str] = {}
    for profile in targets:
        symbol = DISTRICT_TYPE_SYMBOLS.get(getattr(profile, "district_type", "") or "")
        for group, weight in (getattr(profile, "population_mix", {}) or {}).items():
            scores[group] = scores.get(group, 0) + int(weight or 0)
            swatches.setdefault(group, _symbol_hex(symbol))
        for group, value in (getattr(profile, "dissatisfaction", {}) or {}).items():
            scores[group] = scores.get(group, 0) + int(value or 0)
            swatches.setdefault(group, _symbol_hex(symbol))
    cards = []
    for group, score in sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:4]:
        trend = "down" if score >= 4 else "unknown" if score == 0 else "flat"
        cards.append(
            TrendCard(
                _display(group),
                f"signal {score}",
                trend,
                swatches.get(group, ""),
                _spark_points(score * 12, trend),
                "watch" if trend == "down" else "neutral",
            )
        )
    return tuple(cards)


def _outcome_cards(item, template) -> tuple[TrendCard, ...]:
    """Build concise known/unknown outcome cards for the case evidence row."""

    inspected = bool(getattr(item, "inspected", False))
    cards = []
    if template.failure_mode:
        cards.append(
            TrendCard(
                _display(template.failure_mode),
                "known risk" if inspected else "inspection pending",
                "down" if inspected else "unknown",
                "",
                (),
                "bad" if inspected else "watch",
            )
        )
    if template.pressure_category:
        cards.append(TrendCard(_display(template.pressure_category), "pressure track", "unknown", "", (), "watch"))
    cards.append(
        TrendCard(
            "Follow-up order",
            "possible return" if not inspected else "filed evidence",
            "unknown" if not inspected else "flat",
            "",
            (),
            "watch",
        )
    )
    return tuple(cards[:3])


def _case_map_symbols(item, active_features=None) -> tuple[CaseMapSymbol, ...]:
    """Build selected-case map-symbol rows from current case and active features."""

    if not item:
        return ()
    geometry = str(getattr(item, "geometry_type", "") or "").upper()
    shape = "line" if geometry == "LINE" else "point" if geometry == "POINT" else "zone"
    rows = [CaseMapSymbol(shape, "Proposed feature", "selected case exhibit", "#9f7028", "ON", "watch")]
    target_count = len(getattr(item, "target_cell_ids", ()) or ())
    rows.append(
        CaseMapSymbol(
            "zone",
            "Selected target",
            f"{target_count} district(s)",
            "#2f6488",
            "ON" if target_count else "0",
            "good" if target_count else "watch",
        )
    )
    related = [feature for feature in active_features or () if getattr(feature, "item_id", "") == getattr(item, "item_id", "")]
    if related:
        rows.append(
            CaseMapSymbol(
                shape,
                "Filed feature",
                "active map feature",
                "#2f6b53",
                str(min(len(related), 4)) if len(related) < 5 else "4+",
                "neutral",
            )
        )
    return tuple(rows)


def _report_status(report):
    """Classify filed-report text for compact tab styling."""

    lower = str(report or "").lower()
    if "final audit" in lower or "audit " in lower:
        return "scorecard"
    if lower.startswith(("approved", "issued")):
        return "approved"
    if lower.startswith(("denied", "deny")):
        return "denied"
    if lower.startswith(("inspected", "inspection")):
        return "inspected"
    if lower.startswith(("advanced", "final week", "auto-close")):
        return "week"
    return "filed"


def _case_summary(state, districts, item) -> CaseSummary:
    """Convert a docket item into the structured case packet model."""

    if not item:
        return CaseSummary()

    # Template, archetype, stakeholder, and district context are merged here so
    # the Canvas layer only has presentation-ready text to draw.
    template = rules.TEMPLATES[item.template_id]
    archetype = rules.feature_archetype_for_template(template)
    stakeholder = item.stakeholder or template.stakeholder
    target_rule = item.target_rule or template.target_rule
    heat = state.stakeholder_heat.get(stakeholder, 0)
    target_profiles = [districts[cid] for cid in item.target_cell_ids if cid in districts]
    population_hint = rules.target_population_hint(item, target_profiles)
    preview = item.preview_text or template.preview
    if population_hint:
        preview = f"{preview} {population_hint}"
    cost = f"{template.ap_cost} AP / ${template.money_cost}; conditions +${template.mitigation_cost}"
    fields = (
        CaseField("Applicant", f"{_display(stakeholder)}; heat {heat}"),
        CaseField("Category", f"{_display(template.category)} / {item.geometry_type}"),
        CaseField("Feature", f"{archetype.label} ({_display(archetype.family)})"),
        CaseField("Contact", template.contact_name or "not assigned"),
        CaseField("Service", _display(archetype.service_type or "none")),
        CaseField("Cost", cost),
        CaseField("Target Rule", target_rule or "No targeting rule filed."),
        CaseField("Failure Mode", template.failure_mode or "none filed"),
    )
    action_note = _action_note(template)
    economy = _recurring_bucket_value(template)
    districts_text = ", ".join(item.target_cell_ids) if item.target_cell_ids else "(seeded exhibit; use Retarget Map to revise)"
    inspection = _inspection_summary(item)
    impact_buckets = _impact_buckets(state, districts, item, template)
    return CaseSummary(
        title=item.title,
        item_id=item.item_id,
        status=item.status,
        category=template.category,
        fields=fields,
        districts=districts_text,
        preview=preview,
        inspection=inspection,
        action_note=action_note,
        economy=economy,
        risk_band=item.risk_band or "unknown",
        impact_buckets=impact_buckets,
        district_grid=_district_grid_cells(districts, item),
        culture_cards=_culture_cards(districts, item),
        outcome_cards=_outcome_cards(item, template),
    )


def _impact_buckets(state, districts, item, template) -> tuple[ImpactBucket, ...]:
    """Build concise impact buckets for the selected case."""

    targets = [districts[cid] for cid in item.target_cell_ids if cid in districts]
    inspection = (item.case_json or {}).get("inspection") if isinstance(item.case_json, dict) else None
    total_money = template.money_cost + template.mitigation_cost
    cost_tone = "bad" if state.ap < template.ap_cost or state.money < template.money_cost else "watch" if state.money < total_money else "neutral"
    cost = _cost_bucket_value(template)
    city = _city_forecast_bucket(template, targets, mitigated=False)
    city_tone = _delta_tone(template.base_effects | template.spillover_effects)
    local = _local_bucket_value(item, template, targets)
    local_tone = _local_bucket_tone(item, template, targets)
    services = _services_bucket_value(template, targets)
    services_tone = _services_bucket_tone(targets)
    recurring = _recurring_bucket_value(template)
    recurring_tone = _recurring_tone(template)

    if inspection:
        people, people_tone = _inspected_people_bucket(template, targets)
        followup, followup_tone = _inspection_followup_bucket(inspection, item)
    else:
        people = _qualitative_people_bucket(template)
        people_tone = "watch" if template.concerned_groups else "neutral"
        followup = _qualitative_followup_bucket(template)
        followup_tone = "watch" if template.failure_mode else "neutral"

    return (
        ImpactBucket("Cost", cost, cost_tone),
        ImpactBucket("City", city, city_tone),
        ImpactBucket("Local", local, local_tone),
        ImpactBucket("People", people, people_tone),
        ImpactBucket("Services", services, services_tone),
        ImpactBucket("Budget", f"{recurring}; {followup}", _worst_tone(recurring_tone, followup_tone)),
    )


def _action_lanes(state, districts, item) -> tuple[ActionLane, ...]:
    """Build approve/conditions/deny consequence lanes for the selected case."""

    template = rules.TEMPLATES[item.template_id]
    targets = [districts[cid] for cid in item.target_cell_ids if cid in districts]
    issue_label, mitigate_label, deny_label = _action_lane_labels(template)
    issue_money = template.money_cost
    mitigated_money = template.money_cost + template.mitigation_cost
    # Ordinary denials are free; deferring an incident, enforcement, or maintenance order spends AP.
    deny_ap = template.ap_cost if (template.is_incident or template.is_enforcement) else 0
    issue_cost = f"{template.ap_cost} AP / ${issue_money}"
    mitigated_cost = f"{template.ap_cost} AP / ${mitigated_money}; conditions +${template.mitigation_cost}"
    deny_cost = f"{deny_ap} AP / $0; may return as follow-up" if deny_ap else "0 AP / $0; may return as follow-up"
    local = _local_bucket_value(item, template, targets)
    issue_city = _city_forecast_bucket(template, targets, mitigated=False)
    mitigated_city = _city_forecast_bucket(template, targets, mitigated=True)
    deny_city = _deny_forecast(template)
    threat = _template_primary_threat(template)
    issue_city = f"{threat}: {issue_city}"
    mitigated_city = f"{threat}: {mitigated_city}"
    local = f"Threat context: {local}"
    issue_enabled, issue_reason = _resource_available(state, template.ap_cost, issue_money)
    mitigated_enabled, mitigated_reason = _resource_available(state, template.ap_cost, mitigated_money)
    deny_enabled, deny_reason = _resource_available(state, deny_ap, 0)
    return (
        ActionLane(
            "approve",
            issue_label,
            issue_cost,
            issue_city,
            local,
            _delta_tone(template.base_effects),
            issue_enabled,
            issue_reason,
            "A",
            "Issue the permit and file the selected map change.",
        ),
        ActionLane(
            "approve_mitigated",
            mitigate_label,
            mitigated_cost,
            mitigated_city,
            local,
            "watch",
            mitigated_enabled,
            mitigated_reason,
            "M",
            "Issue the permit with mitigation conditions.",
        ),
        ActionLane(
            "deny",
            deny_label,
            deny_cost,
            deny_city,
            "unresolved pressure may persist",
            "watch",
            deny_enabled,
            deny_reason,
            "D",
            "Deny the filing; pressure may return as a follow-up.",
        ),
    )


def _resource_available(state, ap_cost, money_cost) -> tuple[bool, str]:
    """Return whether the current AP/money can pay an action."""

    if int(getattr(state, "ap", 0) or 0) < int(ap_cost or 0):
        return False, f"Needs {ap_cost} AP"
    if int(getattr(state, "money", 0) or 0) < int(money_cost or 0):
        return False, f"Needs ${money_cost}"
    return True, ""


def _action_lane_labels(template) -> tuple[str, str, str]:
    """Return action labels matched to maintenance/incident/enforcement/permit language."""

    # Maintenance reads as a repair order, not enforcement, even though it shares
    # the is_enforcement plumbing — so check pressure_category first.
    if getattr(template, "pressure_category", "") == "maintenance":
        return "Fund Repair", "Patch", "Defer"
    if template.is_incident:
        return "Respond", "Settlement", "Defer"
    if template.is_enforcement:
        return "Enforce", "Settle", "Defer"
    return "Issue Permit", "Add Conditions", "Deny"


def _template_primary_threat(template) -> str:
    """Return the public threat track most associated with a template."""

    category = getattr(template, "category", "")
    pressure = getattr(template, "pressure_category", "")
    if pressure in ("maintenance", "service"):
        return "Service Failure"
    if pressure in ("housing", "land", "construction"):
        return "Speculation Pressure"
    if pressure in ("health", "incident", "business", "event", "culture"):
        return "Public Anger"
    if pressure in (
        "inspection",
        "safety",
        "enforcement",
        "compliance",
        "utility",
        "department",
    ):
        return "Legal Exposure"
    if category in ("development", "residential", "land"):
        return "Speculation Pressure"
    if template.is_incident or category in ("event", "business", "culture"):
        return "Public Anger"
    if template.is_enforcement or category in ("compliance", "utility", "department"):
        return "Legal Exposure"
    if category in ("education",):
        return "Service Failure"
    return "Public Anger"


def _deny_forecast(template) -> str:
    """Return a compact deny/defer consequence forecast."""

    threat = _template_primary_threat(template)
    if getattr(template, "pressure_category", "") == "maintenance":
        return "Service Failure rises if maintenance is deferred"
    if template.is_incident:
        return "Public Anger rises while incident remains open"
    if template.is_enforcement:
        return "Legal Exposure rises while enforcement is deferred"
    if template.failure_mode:
        return f"{threat} may rise; risk of {template.failure_mode}"
    return f"{threat} may rise; applicant heat may return"


def _cost_bucket_value(template) -> str:
    """Format direct decision costs for all stamp choices."""

    return f"Issue {template.ap_cost}AP/${template.money_cost}; conditions +${template.mitigation_cost}; deny 0AP"


def _city_forecast_bucket(template, targets, mitigated: bool) -> str:
    """Forecast likely immediate metric effects before a decision is filed."""

    if not targets:
        base = rules._mitigate(template.base_effects) if mitigated else template.base_effects
        spill = rules._mitigate(template.spillover_effects) if mitigated else template.spillover_effects
        base_text = _format_effects(base)
        spill_text = _format_effects(spill)
        if spill_text != "none":
            return f"target {base_text}; spill {spill_text}"
        return f"target {base_text}"

    archetype = rules.feature_archetype_for_template(template)
    total = {metric: 0 for metric in rules.CORE_METRICS}
    for profile in targets:
        delta = rules._district_adjusted_effects(template.base_effects, profile.district_type, template.category)
        delta = rules._land_use_adjusted_effects(delta, profile, archetype)
        rules._merge_delta(delta, rules._service_coverage_effect(archetype, profile, "target"))
        if mitigated:
            delta = rules._mitigate(delta)
        for metric in rules.CORE_METRICS:
            total[metric] = total.get(metric, 0) + delta.get(metric, 0)
    averaged = {metric: round(value / max(1, len(targets))) for metric, value in total.items()}
    return _format_effects(averaged)


def _recurring_bucket_value(template) -> str:
    """Format recurring feature revenue and upkeep forecast."""

    archetype = rules.feature_archetype_for_template(template)
    operating = rules.operating_rule_for_feature(archetype.archetype_id)
    revenue = operating.revenue_per_turn
    upkeep = operating.upkeep_per_turn
    net = revenue - upkeep
    if revenue or upkeep:
        return f"rev ${revenue}/week, upkeep ${upkeep}/week, net {_signed_money(net)}"
    return "no recurring budget"


def _recurring_tone(template) -> str:
    """Return dashboard tone for recurring budget forecast."""

    archetype = rules.feature_archetype_for_template(template)
    operating = rules.operating_rule_for_feature(archetype.archetype_id)
    net = operating.revenue_per_turn - operating.upkeep_per_turn
    if net > 0:
        return "good"
    if net < 0:
        return "watch"
    return "neutral"


def _local_bucket_value(item, template, targets) -> str:
    """Format selected target context without long prose."""

    if not item.target_cell_ids:
        return "seeded target pending map update"
    archetype = rules.feature_archetype_for_template(template)
    types = sorted({profile.district_type for profile in targets if profile.district_type})
    type_text = "/".join(types[:2]) if types else "filed"
    fit = _land_use_fit_text(template, archetype, targets)
    local_effect = _city_forecast_bucket(template, targets, mitigated=False)
    causes = _pressure_cause_summary(targets) if targets else "cause pending"
    return f"{len(item.target_cell_ids)} district(s); {type_text}; {fit}; {causes}; {local_effect}"


def _local_bucket_tone(item, template, targets) -> str:
    """Return a tone for target fit and local adjusted effects."""

    if not item.target_cell_ids:
        return "watch"
    archetype = rules.feature_archetype_for_template(template)
    if any(
        profile.district_type in template.bad_fit_types
        or profile.district_type in archetype.conflict_district_types
        for profile in targets
    ):
        return "watch"
    return _delta_tone(template.base_effects)


def _land_use_fit_text(template, archetype, targets) -> str:
    """Summarize target fit/conflict against template and feature rules."""

    if not targets:
        return "fit unknown"
    good = sum(
        1
        for profile in targets
        if profile.district_type in template.good_fit_types
        or profile.district_type in archetype.allowed_district_types
    )
    conflict = sum(
        1
        for profile in targets
        if profile.district_type in template.bad_fit_types
        or profile.district_type in archetype.conflict_district_types
    )
    if conflict:
        return f"{conflict} conflict"
    if good:
        return f"{good} fit"
    return "neutral fit"


def _services_bucket_value(template, targets) -> str:
    """Format the most relevant service, network, hazard, or housing pressure."""

    archetype = rules.feature_archetype_for_template(template)
    service = archetype.service_type or archetype.network_type
    service_text = _service_gap_summary(targets, service)
    if service_text != "no service gap":
        return service_text
    hazard_text = _hazard_summary(targets)
    if hazard_text != "no active hazards":
        return hazard_text
    housing_text = _housing_pressure_summary(targets)
    if housing_text != "housing steady":
        return housing_text
    return "services steady"


def _services_bucket_tone(targets) -> str:
    """Return a tone for local service/hazard/housing pressure."""

    worst_gap = max((gap for profile in targets for gap in (profile.service_gap or {}).values()), default=0)
    worst_hazard = max((band for profile in targets for band in (profile.hazards or {}).values()), default=0)
    housing_pressure = any(_profile_housing_pressure(profile) for profile in targets)
    if worst_gap >= 40 or worst_hazard >= 3:
        return "bad"
    if worst_gap >= 25 or worst_hazard or housing_pressure:
        return "watch"
    return "neutral"


def _qualitative_people_bucket(template) -> str:
    """Format pre-inspection people impact qualitatively."""

    supporters = _join_labels(template.supporter_groups[:2])
    objectors = _join_labels(template.concerned_groups[:2])
    if supporters != "none" and objectors != "none":
        return f"{supporters} support; {objectors} object"
    if supporters != "none":
        return f"{supporters} likely support"
    if objectors != "none":
        return f"{objectors} may object"
    return "public comment pending"


def _qualitative_followup_bucket(template) -> str:
    """Format pre-inspection follow-up qualitatively."""

    if template.failure_mode:
        return f"inspect for {template.failure_mode}"
    return "routine filing path"


def _inspected_people_bucket(template, targets) -> tuple[str, str]:
    """Format inspected supporter, objector, and grievance context."""

    supporter = _strongest_group(targets, template.supporter_groups)
    objector = _strongest_group(targets, template.concerned_groups)
    grievance_group, grievance_band = _top_grievance(targets)
    parts = []
    if supporter:
        parts.append(f"{_group_label(supporter)} support")
    if objector:
        parts.append(f"{_group_label(objector)} object")
    if grievance_band:
        parts.append(f"{_group_label(grievance_group)} {rules.GRIEVANCE_BAND_LABELS[grievance_band]}")
    tone = "bad" if grievance_band >= rules.DISSATISFACTION_INCIDENT_THRESHOLD else "watch" if grievance_band >= rules.DISSATISFACTION_AGGRIEVED_THRESHOLD or objector else "neutral"
    return "; ".join(parts) if parts else "population evidence filed", tone


def _inspection_followup_bucket(inspection, item) -> tuple[str, str]:
    """Format inspected risk, evidence, and violations."""

    evidence = inspection.get("evidence") or []
    violations = inspection.get("violations") or []
    risk = str(inspection.get("risk_band") or item.risk_band or "unknown").lower()
    warnings = sum(1 for record in evidence if isinstance(record, dict) and record.get("severity") in ("warning", "critical"))
    value = f"{risk} risk; {warnings}/{len(evidence)} flagged evidence; {len(violations)} violation(s)"
    return value, _risk_tone(risk)


def _format_effects(effects) -> str:
    """Format metric deltas for a compact bucket."""

    labels = {
        "activity": "activity",
        "friction": "friction",
        "trust": "trust",
        "exposure": "exposure",
        "services": "service",
    }
    parts = [f"{labels.get(metric, metric[:4])} {amount:+d}" for metric, amount in sorted((effects or {}).items()) if amount]
    return ", ".join(parts[:3]) if parts else "none"


def _delta_tone(effects) -> str:
    """Return a tone for a metric delta map."""

    effects = effects or {}
    if effects.get("exposure", 0) > 0 or effects.get("friction", 0) > 1:
        return "bad"
    if effects.get("exposure", 0) < 0 or effects.get("activity", 0) > 0 or effects.get("trust", 0) > 0:
        return "good"
    if any(value for value in effects.values()):
        return "watch"
    return "neutral"


def _risk_tone(risk) -> str:
    """Return the dashboard tone for an inspection risk band."""

    if risk == "high":
        return "bad"
    if risk == "medium":
        return "watch"
    if risk == "low":
        return "good"
    return "neutral"


def _strongest_group(profiles, groups) -> str:
    """Return the strongest configured group across target profiles."""

    scores = []
    for group in groups:
        score = sum(profile.population_mix.get(group, 0) for profile in profiles)
        if score > 0:
            scores.append((score, group))
    return sorted(scores, key=lambda row: (-row[0], row[1]))[0][1] if scores else ""


def _top_grievance(profiles) -> tuple[str, int]:
    """Return the highest dissatisfaction band across target profiles."""

    totals = {group: 0 for group in rules.CITIZEN_GROUPS}
    for profile in profiles:
        for group, band in (profile.dissatisfaction or {}).items():
            if group in totals:
                totals[group] += int(band or 0)
    return sorted(totals.items(), key=lambda row: (-row[1], row[0]))[0]


def _join_labels(groups) -> str:
    """Join group labels for a compact bucket value."""

    labels = [_group_label(group) for group in groups if group]
    if not labels:
        return "none"
    return "/".join(labels[:2])


def _group_label(group) -> str:
    """Return a display label for a citizen or stakeholder group id."""

    return rules.GROUP_LABELS.get(group, str(group).replace("_", " "))


def _service_gap_summary(profiles, preferred_service: str = "") -> str:
    """Summarize the worst local service gap."""

    rows = []
    for profile in profiles:
        gaps = profile.service_gap or {}
        for service, gap in gaps.items():
            if gap:
                rows.append((int(gap or 0), service, profile.cell_id))
    if not rows:
        return "no service gap"
    gap, service, cell_id = sorted(rows, key=lambda row: (-row[0], row[1], row[2]))[0]
    return f"{service.replace('_', ' ')} gap {gap} in {cell_id}"


def _hazard_summary(profiles) -> str:
    """Summarize active hazard bands across district profiles."""

    counts: dict[str, int] = {}
    worst: dict[str, int] = {}
    for profile in profiles:
        for hazard, band in (profile.hazards or {}).items():
            band = int(band or 0)
            if band <= 0:
                continue
            counts[hazard] = counts.get(hazard, 0) + 1
            worst[hazard] = max(worst.get(hazard, 0), band)
    if not counts:
        return "no active hazards"
    hazard = sorted(counts, key=lambda key: (-worst[key], -counts[key], key))[0]
    return f"{hazard.replace('_', ' ')} band {worst[hazard]} x{counts[hazard]}"


def _housing_pressure_summary(profiles) -> str:
    """Summarize housing capacity, vacancy, affordability, or displacement pressure."""

    pressure = [_profile_housing_pressure(profile) for profile in profiles]
    pressure = [text for text in pressure if text]
    if not pressure:
        return "housing steady"
    return pressure[0]


def _profile_housing_pressure(profile) -> str:
    """Return one compact housing warning for a profile, if any."""

    if profile.displacement:
        group, band = sorted(profile.displacement.items(), key=lambda row: (-int(row[1] or 0), row[0]))[0]
        if int(band or 0) > 0:
            return f"{profile.cell_id} displacement: {_group_label(group)} {band}"
    if profile.housing_capacity and profile.population > profile.housing_capacity:
        return f"{profile.cell_id} over capacity by {profile.population - profile.housing_capacity}"
    if profile.affordability and profile.affordability < 35:
        return f"{profile.cell_id} affordability {profile.affordability}"
    if profile.vacancy_rate and profile.vacancy_rate < 3:
        return f"{profile.cell_id} vacancy {profile.vacancy_rate}%"
    return ""


def _maintenance_summary(features) -> str:
    """Summarize active feature maintenance backlog."""

    candidates = [
        feature
        for feature in features or ()
        if getattr(feature, "status", "") in ("maintenance_due", "degraded", "failed")
        or int(getattr(feature, "condition", 100) or 100) < 35
    ]
    if not candidates:
        return "none"
    lowest = min(int(getattr(feature, "condition", 100) or 100) for feature in candidates)
    return f"{len(candidates)} due; lowest condition {lowest}"


def _worst_tone(*tones) -> str:
    """Return the most urgent tone from a small set."""

    priority = {"bad": 3, "watch": 2, "good": 1, "neutral": 0}
    return max((tone or "neutral" for tone in tones), key=lambda tone: priority.get(tone, 0))


def _office_standing_index(state) -> int:
    """Fold the four core metrics into one 0-100 office legitimacy signal.

    Activity and Trust read positive; Friction and Exposure read negative. This
    preserves the prior City Health formula while changing the player-facing
    framing from generic wellness to the office's mandate to keep issuing permits.
    """

    return rules.office_standing_index(state)


def _office_standing_descriptor(value: int) -> tuple[str, str]:
    """Return a (word, tone) summary for Office Standing."""

    if value >= 65:
        return "Strong", "good"
    if value >= 45:
        return "Authorized", "watch"
    if value >= 30:
        return "Strained", "watch"
    return "Failing", "bad"


def _city_health_index(state) -> int:
    """Compatibility wrapper for older tests and callers."""

    return _office_standing_index(state)


def _city_health_descriptor(value: int) -> tuple[str, str]:
    """Compatibility wrapper for older tests and callers."""

    return _office_standing_descriptor(value)


THREAT_TRACKS = ("Public Anger", "Legal Exposure", "Service Failure", "Speculation Pressure")


def _threat_track_summaries(state, districts, active_features=None, docket=None) -> tuple[LedgerRow, ...]:
    """Collapse internal pressure fields into player-facing threat tracks."""

    derived = rules.derive_threat_tracks(state, districts, active_features, docket)
    rows: list[LedgerRow] = []
    for track in derived:
        if not track.reasons:
            continue
        value = "; ".join(track.reasons[:3])
        rows.append(LedgerRow(track.label, value, track.tone, track.score))

    if rows:
        order = {label: index for index, label in enumerate(THREAT_TRACKS)}
        return tuple(sorted(rows, key=lambda row: (-(row.meter or 0), order.get(row.label, len(order)))))
    return (LedgerRow("Threats", "quiet", "neutral"),)


def _stat_row(label, value, tone, week_start) -> LedgerRow:
    """Return a city stat row whose trend is the real change since week start, or unknown."""

    start = (week_start or {}).get(label.lower())
    trend = _trend_from_delta(None if start is None else int(value) - int(start))
    return LedgerRow(label, str(value), tone, value, trend)


def _ledger_rows(state, districts, active_features=None, docket=None, audit_grade=None, week_start=None) -> tuple[LedgerRow, ...]:
    """Build the city ledger rows shown in the dashboard sidebar.

    When `audit_grade` is provided the caller has a cached grade, so the
    scorecard recompute (and its two load-bearing deepcopies) is skipped.
    When it is None we recompute via `rules.scorecard`, copying districts and
    the docket so `generate_audit_result`'s in-place normalize cannot mutate the
    districts this function reads afterward.
    """

    standing = _office_standing_index(state)
    standing_word, standing_tone = _office_standing_descriptor(standing)
    heat = rules.heat_summary(state)
    # NOTE: the deepcopy on the None path is load-bearing. `scorecard` ->
    # `generate_audit_result` calls `normalize_profile` in place, which re-derives
    # service_gap / displacement / etc. On normalized inputs that is idempotent,
    # but the ledger summaries below read the same `districts`, so mutating them
    # here would change Services/Housing/Pressure for any caller passing
    # semi-normalized state. The controller passes a cached `audit_grade` to skip
    # this recompute on selection-only reloads; the None path keeps the safe copy.
    if audit_grade is None:
        audit_grade, _audit_report = rules.scorecard(state, deepcopy(districts), _feature_snapshots(active_features), deepcopy(docket or ()))
    population = rules.population_city_summary(districts)
    incidents = rules.incident_summary(districts)
    service_summary = _city_service_summary(districts)
    hazard_summary = _hazard_summary(districts.values() if isinstance(districts, dict) else districts)
    housing_summary = _housing_pressure_summary(districts.values() if isinstance(districts, dict) else districts)
    maintenance = _maintenance_summary(active_features) if active_features is not None else _maintenance_count_from_state(state)
    pressure = _pressure_cause_summary(districts)
    threat_rows = _threat_track_summaries(state, districts, active_features, docket or ())
    headline_threat = threat_rows[0]
    threat_value = headline_threat.value if headline_threat.label == "Threats" else f"{headline_threat.label}: {headline_threat.value}"
    threat_tone = headline_threat.tone
    threat_meter = headline_threat.meter
    week_value = f"{state.turn}/{state.max_turns} CLOSED" if state.status == "complete" or state.turn > state.max_turns else f"{state.turn}/{state.max_turns}"
    return (
        LedgerRow("Office Standing", f"{standing} {standing_word}", standing_tone, standing),
        LedgerRow("Week", week_value, "neutral", _meter(state.turn, state.max_turns)),
        LedgerRow("AP", f"{state.ap}/{state.max_ap}", "good" if state.ap else "watch", _meter(state.ap, state.max_ap)),
        LedgerRow("Money", f"${state.money}", "good" if state.money >= 20 else "watch"),
        LedgerRow("Audit", _short_audit_grade(audit_grade), "bad" if audit_grade == "FAIL" else "watch" if audit_grade == "CONDITIONAL" else "good"),
        LedgerRow("Threats", threat_value, threat_tone, threat_meter),
        LedgerRow("Heat", heat, "bad" if heat != "none" else "neutral"),
        LedgerRow("Pressure", pressure, "watch" if pressure != "stable" else "neutral"),
        _stat_row("Activity", state.activity, "good", week_start),
        _stat_row("Friction", state.friction, "bad" if state.friction >= 50 else "watch", week_start),
        _stat_row("Trust", state.trust, "good", week_start),
        _stat_row("Exposure", state.exposure, "bad" if state.exposure >= 50 else "watch", week_start),
        LedgerRow("Economy", f"rev ${state.last_revenue}; up ${state.last_upkeep}; net {state.last_net:+d}", "watch" if state.last_net < 0 else "good" if state.last_net > 0 else "neutral"),
        LedgerRow("Services", service_summary, "bad" if "critical" in service_summary else "watch" if service_summary != "none" else "neutral"),
        LedgerRow("Hazards", hazard_summary, "watch" if hazard_summary != "no active hazards" else "neutral"),
        LedgerRow("Housing", housing_summary, "watch" if housing_summary != "housing steady" else "neutral"),
        LedgerRow("Maintenance", maintenance, "watch" if maintenance != "none" else "neutral"),
        LedgerRow("Population", population, "neutral"),
        LedgerRow("Incidents", incidents, "bad" if incidents != "none" else "neutral"),
    )


def _city_service_summary(districts) -> str:
    """Summarize citywide service gaps by count and worst service."""

    profiles = list(districts.values() if isinstance(districts, dict) else districts)
    rows = [(int(gap or 0), service, profile.cell_id) for profile in profiles for service, gap in (profile.service_gap or {}).items() if int(gap or 0) > 0]
    if not rows:
        return "none"
    worst_gap, worst_service, _cell_id = sorted(rows, key=lambda row: (-row[0], row[1], row[2]))[0]
    critical = sum(1 for gap, _service, _cell_id in rows if gap >= 40)
    label = "critical" if critical else "warning"
    return f"{len(rows)} {label}; worst {worst_service.replace('_', ' ')} {worst_gap}"


def _pressure_cause_summary(districts) -> str:
    """Summarize top non-money map causes for city health."""

    counts: dict[str, int] = {}
    for profile in list(districts.values() if isinstance(districts, dict) else districts):
        cause = getattr(profile, "display_state", "stable") or "stable"
        if cause != "stable":
            counts[cause] = counts.get(cause, 0) + 1
    if not counts:
        return "stable"
    return ", ".join(f"{cause.replace('_', ' ')} x{count}" for cause, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))[:3])


def _short_audit_grade(grade: str) -> str:
    """Return a compact audit grade for the top banner."""

    return "COND" if grade == "CONDITIONAL" else grade or "NA"


def _feature_snapshots(features) -> list:
    """Return rule-native feature copies for audit display calculations."""

    snapshots = []
    for feature in features or ():
        snapshots.append(
            rules.FeatureInstance(
                feature_id=getattr(feature, "feature_id", ""),
                archetype_id=getattr(feature, "archetype_id", ""),
                family=getattr(feature, "family", ""),
                service_type=getattr(feature, "service_type", ""),
                network_type=getattr(feature, "network_type", ""),
                target_cell_ids=list(getattr(feature, "target_cell_ids", []) or []),
                capacity=int(getattr(feature, "capacity", 0) or 0),
                intensity=int(getattr(feature, "intensity", 1) or 1),
                status=getattr(feature, "status", "active"),
                turn_created=int(getattr(feature, "turn_created", 0) or 0),
                expires_turn=int(getattr(feature, "expires_turn", -1) if getattr(feature, "expires_turn", -1) not in (None, "") else -1),
                project_id=getattr(feature, "project_id", ""),
                metadata=dict(getattr(feature, "metadata", {}) or {}),
                item_id=getattr(feature, "item_id", ""),
                template_id=getattr(feature, "template_id", ""),
                owner_group=getattr(feature, "owner_group", ""),
                condition=int(getattr(feature, "condition", 100) if getattr(feature, "condition", 100) not in (None, "") else 100),
                maintenance_due_turn=int(getattr(feature, "maintenance_due_turn", -1) if getattr(feature, "maintenance_due_turn", -1) not in (None, "") else -1),
                last_maintained_turn=int(getattr(feature, "last_maintained_turn", 0) or 0),
                display_state=getattr(feature, "display_state", ""),
                chain_step_id=getattr(feature, "chain_step_id", ""),
                state_json=dict(getattr(feature, "state_json", {}) or {}),
            )
        )
    return snapshots


def _maintenance_count_from_state(state) -> str:
    """Format the persisted maintenance backlog count."""

    backlog = int(getattr(state, "maintenance_backlog", 0) or 0)
    return f"{backlog} active" if backlog else "none"


def _signed_money(value: int) -> str:
    """Format a signed dollar amount with the sign before the currency mark."""

    amount = int(value or 0)
    return f"+${amount}" if amount >= 0 else f"-${abs(amount)}"


def _ticker_items(state, districts, active_features=None, docket=None, report_tabs=None) -> tuple[str, ...]:
    """Build deterministic city ticker lines from live game state."""

    items = []
    threat_rows = _threat_track_summaries(state, districts, active_features, docket)
    lead_threat = threat_rows[0]
    if lead_threat.label != "Threats":
        items.append(f"WIRE: threat desk reports {lead_threat.label}: {lead_threat.value}.")
    heat = rules.heat_summary(state)
    if heat != "none":
        items.append(f"WIRE: heat desk flags {heat}; incident, enforcement, or follow-up filing may arrive.")
    pressure = _pressure_cause_summary(districts)
    if pressure != "stable":
        items.append(f"WIRE: street desk sees {pressure} showing on district files.")
    incidents = rules.incident_summary(districts)
    if incidents != "none":
        items.append(f"WIRE: civic desk logs {incidents}.")
    profiles = list(districts.values() if isinstance(districts, dict) else (districts or ()))
    contested = sorted((p for p in profiles if getattr(p, "identity_state", "stable") == "contested"), key=lambda p: p.cell_id)
    converted = sorted((p for p in profiles if getattr(p, "identity_state", "stable") == "converted"), key=lambda p: p.cell_id)
    if contested:
        lead = contested[0]
        items.append(f"WIRE: boundary desk says {lead.name} is fending off a {lead.contesting_type} buyout.")
    elif converted:
        lead = converted[0]
        items.append(f"WIRE: boundary desk notes {lead.name} flipped {lead.district_type} after a neighbor buyout cleared.")
    maintenance = _maintenance_summary(active_features) if active_features is not None else _maintenance_count_from_state(state)
    if maintenance != "none":
        items.append(f"WIRE: works desk lists maintenance {maintenance}.")
    open_count = len([item for item in docket or () if item.status in ACTIVE_STATUSES])
    if open_count:
        items.append(f"WIRE: clerk queue has {open_count} active application(s), {state.ap}/{state.max_ap} AP left.")
    if report_tabs:
        latest = report_tabs[-1]
        items.append(f"WIRE: filed report added, {latest.title}.")
    if not items:
        items.append("WIRE: city desk quiet, filings orderly and complaints contained.")
    return tuple(items[:4])


def _map_legend_rows(districts, docket=None, selected_item_id="") -> tuple[MapLegendRow, ...]:
    """Build public map-key rows for the Applications right rail."""

    rows: list[MapLegendRow] = []
    profiles = list(districts.values() if isinstance(districts, dict) else (districts or ()))
    district_type_keys = _ordered_values((getattr(profile, "district_type", "") or "district" for profile in profiles), DISTRICT_TYPE_SYMBOLS)
    for key in district_type_keys[:6] or ["residential"]:
        label = DISTRICT_TYPE_SYMBOLS.get(key, ([216, 225, 222, 100], _display(key)))[1]
        rows.append(MapLegendRow("PermitDistricts", label, "base district fill", _symbol_hex(DISTRICT_TYPE_SYMBOLS.get(key)), "neutral"))

    display_keys = _ordered_values((getattr(profile, "display_state", "") or "stable" for profile in profiles), DISPLAY_STATE_SYMBOLS)
    for key in display_keys[:5] or ["stable"]:
        label = DISPLAY_STATE_SYMBOLS.get(key, ([157, 175, 170, 100], _display(key)))[1]
        rows.append(MapLegendRow("District display", label, "district pressure overlay", _symbol_hex(DISPLAY_STATE_SYMBOLS.get(key)), "watch" if key != "stable" else "neutral"))

    prosperity_keys = _ordered_values((getattr(profile, "prosperity_band", "") or "stable" for profile in profiles), PROSPERITY_BAND_SYMBOLS)
    for key in prosperity_keys[:4] or ["stable"]:
        label = PROSPERITY_BAND_SYMBOLS.get(key, ([157, 175, 170, 100], _display(key)))[1]
        rows.append(MapLegendRow("Prosperity", label, "district prosperity outline", _symbol_hex(PROSPERITY_BAND_SYMBOLS.get(key)), "good" if key == "thriving" else "neutral"))

    identity_keys = _ordered_values((getattr(profile, "identity_state", "") or "stable" for profile in profiles), IDENTITY_STATE_SYMBOLS)
    for key in identity_keys[:4] or ["stable"]:
        label = IDENTITY_STATE_SYMBOLS.get(key, ([157, 175, 170, 100], _display(key)))[1]
        rows.append(MapLegendRow("Community", label, "identity overlay", _symbol_hex(IDENTITY_STATE_SYMBOLS.get(key)), "watch" if key != "stable" else "neutral"))

    rows.extend(_feature_legend_rows("PermitPoints", ("expired", "maintained", "maintenance_due", "proposed", "responded", "special_interest")))
    rows.extend(_feature_legend_rows("PermitLines", ("active", "road")))
    rows.extend(_feature_legend_rows("PermitZones", ("active", "campus", "civic", "commerce", "housing", "industry", "park")))

    selected = next((item for item in docket or () if getattr(item, "item_id", "") == selected_item_id), None)
    target_count = len(getattr(selected, "target_cell_ids", ()) or ()) if selected else 0
    rows.append(MapLegendRow("Selection", "Selected target", f"{target_count} district(s)", "#2f6488", "good" if target_count else "watch"))
    filed = _filed_state_label(getattr(selected, "status", "") if selected else "")
    rows.append(MapLegendRow("Selection", "Filed state", filed, "#2f6b53" if filed != "not filed" else "#9dafaa", "neutral"))
    return tuple(rows)


def _feature_legend_rows(group, keys):
    """Return Contents-style display_state rows for a feature layer."""

    rows = []
    for key in keys:
        symbol = DISPLAY_STATE_SYMBOLS.get(key)
        if not symbol:
            continue
        rows.append(MapLegendRow(group, symbol[1], "feature display state", _symbol_hex(symbol), "neutral"))
    return rows


def _ordered_values(values, symbols):
    """Return unique values in symbol order with observed values first."""

    seen = []
    for value in values:
        key = str(value or "").lower()
        if key and key not in seen:
            seen.append(key)
    ordered = [key for key in symbols if key in seen]
    ordered.extend(key for key in seen if key not in ordered)
    return ordered


def _symbol_hex(symbol):
    """Convert a symbology config RGBA tuple to a Tk color."""

    rgba = symbol[0] if symbol else [216, 225, 222, 100]
    return "#{:02x}{:02x}{:02x}".format(int(rgba[0]), int(rgba[1]), int(rgba[2]))


def _district_group_rows(districts) -> tuple[DistrictGroupRow, ...]:
    """Aggregate district health by district type for the city pulse rail."""

    profiles = list(districts.values() if isinstance(districts, dict) else (districts or ()))
    groups: dict[str, list] = {}
    for profile in profiles:
        key = getattr(profile, "district_type", "") or "district"
        groups.setdefault(key, []).append(profile)
    rows: list[DistrictGroupRow] = []
    for district_type, group_profiles in sorted(groups.items()):
        count = max(1, len(group_profiles))
        prosperity = sum(
            int(getattr(profile, "activity", 0) or 0)
            + int(getattr(profile, "trust", 0) or 0)
            + int(getattr(profile, "services", 0) or 0)
            - int(getattr(profile, "friction", 0) or 0)
            - int(getattr(profile, "exposure", 0) or 0)
            for profile in group_profiles
        ) / count
        heat = max((max((getattr(profile, "dissatisfaction", {}) or {}).values() or [0]) for profile in group_profiles), default=0)
        pressure = sum(1 for profile in group_profiles if (getattr(profile, "display_state", "") or "stable") != "stable")
        pressure += sum(1 for profile in group_profiles if getattr(profile, "identity_state", "stable") in ("vulnerable", "contested"))
        pressure += sum(1 for profile in group_profiles if int(getattr(profile, "buyout_pressure", 0) or 0) > 0)
        score = int(max(0, min(100, 50 + (prosperity / 4) - (heat * 8) - (pressure * 6))))
        if score < 30 or heat >= 4:
            state, tone = "Critical", "bad"
        elif score < 46 or heat >= 3 or pressure >= 2:
            state, tone = "Strained", "watch"
        elif score < 62 or pressure:
            state, tone = "Warming", "watch"
        else:
            state, tone = "Stable", "good"
        symbol = DISTRICT_TYPE_SYMBOLS.get(district_type)
        # No week-over-week district history is kept, so groups carry no trend.
        rows.append(
            DistrictGroupRow(
                _display(district_type),
                state,
                f"{count} district(s), heat {heat}, pressure {pressure}",
                tone,
                score,
                _symbol_hex(symbol),
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.meter, row.label)))


def _district_table_rows(districts, selected, state=None) -> tuple[DistrictTableRow, ...]:
    """Build the live-attribute table rows: case targets, then changed districts, then the rest.

    "Changed this week" uses what the model holds: this week's daily pressure,
    or a display, identity, or incident state other than the resting one.
    """

    profiles = list(districts.values() if isinstance(districts, dict) else (districts or ()))
    selected_targets = set(getattr(selected, "target_cell_ids", ()) or ())
    pressure = dict(getattr(state, "daily_pressure", None) or {})
    rows: list[DistrictTableRow] = []
    for profile in sorted(profiles, key=lambda p: getattr(p, "cell_id", "")):
        cell_id = getattr(profile, "cell_id", "") or ""
        display_state = getattr(profile, "display_state", "") or "stable"
        identity_state = getattr(profile, "identity_state", "") or "stable"
        changed = (
            int(pressure.get(cell_id, 0) or 0) > 0
            or display_state != "stable"
            or identity_state != "stable"
            or (getattr(profile, "incident_state", "") or "none") != "none"
        )
        rows.append(
            DistrictTableRow(
                cell_id,
                getattr(profile, "name", "") or cell_id,
                _display(getattr(profile, "district_type", "") or "district"),
                _display(getattr(profile, "prosperity_band", "") or "stable"),
                _display(display_state),
                _display(identity_state),
                cell_id in selected_targets,
                changed,
            )
        )
    return tuple(sorted(rows, key=lambda row: (not row.selected, not row.changed)))


def _permit_state_label(status) -> str:
    """Return public permit-state copy for a docket status."""

    value = str(status or "").lower()
    if value in ("open", "carried"):
        return "Open filing"
    if value == "inspected":
        return "Inspection filed"
    if value in ("active", "approved"):
        return "Approved"
    if value in ("denied", "deferred"):
        return "Denied"
    return _display(value or "filed")


def _filed_state_label(status) -> str:
    """Return compact selected-case filed-state copy."""

    value = str(status or "").lower()
    if value in ("active", "approved"):
        return "approved"
    if value in ("denied", "deferred"):
        return "denied"
    if value == "inspected":
        return "inspection filed"
    return "not filed"


def _permit_state_swatch(label) -> str:
    """Return a restrained swatch for a public permit-state label."""

    return {
        "Open filing": "#2f6488",
        "Inspection filed": "#9f7028",
        "Approved": "#2f6b53",
        "Denied": "#b5423f",
    }.get(label, "#5d6e69")


def _inspection_summary(item) -> str:
    """Format inspection evidence and violations for the case packet."""

    inspection = (item.case_json or {}).get("inspection") if isinstance(item.case_json, dict) else None
    if not inspection:
        if item.inspected:
            return f"Inspection filed. Risk band: {item.risk_band or 'unknown'}."
        return "No inspection addendum filed."
    evidence = inspection.get("evidence") or []
    violations = inspection.get("violations") or []
    severities = [record.get("severity", "watch") for record in evidence if isinstance(record, dict)]
    worst = "critical" if "critical" in severities else "warning" if "warning" in severities else "watch"
    notes = []
    for record in evidence[:2]:
        if isinstance(record, dict) and record.get("label"):
            notes.append(f"{record.get('label')}: {record.get('severity', 'watch')}")
    note_text = "; ".join(notes)
    if note_text:
        note_text = f" {note_text}."
    return f"Risk {inspection.get('risk_band', item.risk_band)}; {len(evidence)} evidence item(s), highest {worst}; {len(violations)} violation(s).{note_text}"


def _action_note(template) -> str:
    """Describe how the stamp actions map to this template type."""

    if template.is_incident:
        return "Issue=response; Mitigate=settlement; Deny=defer incident."
    if template.is_enforcement:
        return "Issue=enforce; Mitigate=settle; Deny=defer."
    return "Issue=approve permit; Mitigate=approve with conditions; Deny=reject."


def _meter(value, maximum):
    """Convert a value and maximum into a 0-100 meter percentage."""

    try:
        if maximum <= 0:
            return 0
        return int(100 * value / maximum)
    except Exception:
        return 0


def _display(value):
    """Convert an identifier into title-style display text."""

    text = str(value or "none").replace("_", " ")
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


# Report section headings, in display order.
REPORT_SECTION_ORDER = (
    "City effects",
    "Local changes",
    "Spillover",
    "Economy",
    "Side effects",
    "Docket",
    "Standing",
    "Details",
    "Audit",
)
# Labeled sentences: label prefix -> heading. Their bodies split on "; ".
_LABELED_SENTENCES = (
    ("Certain effects:", None),
    ("Exposure/side effects:", "Side effects"),
    ("Local changes:", "Local changes"),
    ("District tags:", "Local changes"),
    ("Economy:", "Economy"),
)
# Clauses inside "Certain effects:" start a new heading by their lead words;
# a clause with no known lead continues the previous heading.
_CLAUSE_HEADINGS = (
    (("affected ", "immediate city delta", "city delta", "project exposure"), "City effects"),
    (("primary pressure", "local deltas"), "Local changes"),
    (("spillover",), "Spillover"),
    (("recurring budget",), "Economy"),
)
# Whole sentences classified by lead words (week, scorecard, and audit reports).
_SENTENCE_HEADINGS = (
    (("Population file:", "Project "), "Side effects"),
    (("Carried ", "Stakeholder heat", "Local grievance files"), "Docket"),
    (("Population drift", "New civic incident", "Displacement pressure"), "City effects"),
    (("Office Standing", "Standing ", "Lead threat:"), "Standing"),
    (("Audit ",), "Audit"),
)
# Sentences with no known lead, classified by a word anywhere in them.
_SENTENCE_KEYWORDS = ((("buyout",), "Local changes"),)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_DISTRICT_ID = re.compile(r"\bD\d{4}\b")


def report_sections(report, district_names=None):
    """Split a filed report into (summary, ((heading, lines), ...)) for display.

    Pure presentation: every clause of the joined text lands in exactly one
    section, in its original words, except that raw district ids become names
    when ``district_names`` maps them. The first unlabeled sentence is the
    summary.
    """

    names = dict(district_names or {})
    text = " ".join(str(report or "").split())
    if not text:
        return "", ()
    buckets: dict[str, list[str]] = {}

    def add(heading, line):
        line = line.strip().rstrip(";").strip()
        if line:
            line = _DISTRICT_ID.sub(lambda match: names.get(match.group(0), match.group(0)), line)
            buckets.setdefault(heading, []).append(line[0].upper() + line[1:])

    summary = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        label = next((entry for entry in _LABELED_SENTENCES if sentence.startswith(entry[0])), None)
        if label is not None:
            prefix, heading = label
            body = sentence[len(prefix):].strip().rstrip(".")
            current = heading or "City effects"
            for clause in body.split("; "):
                if heading is None:
                    current = next((name for leads, name in _CLAUSE_HEADINGS if clause.startswith(leads)), current)
                add(current, clause)
            continue
        heading = next((name for leads, name in _SENTENCE_HEADINGS if sentence.startswith(leads)), None)
        if heading is None and summary:
            heading = next((name for words, name in _SENTENCE_KEYWORDS if any(word in sentence for word in words)), None)
        if heading == "Audit" and ": " in sentence:
            # The scored line: its lead, then each "; " clause, on its own line.
            lead, _sep, rest = sentence.partition(": ")
            add("Audit", lead)
            for clause in rest.rstrip(".").split("; "):
                add("Audit", clause)
            continue
        if heading == "Audit":  # audit flavor prose, not the scored line
            heading = "Details"
        if heading is None and not summary:
            summary = sentence
            continue
        add(heading or "Details", sentence)
    ordered = tuple((heading, tuple(buckets[heading])) for heading in REPORT_SECTION_ORDER if heading in buckets)
    return summary, ordered
