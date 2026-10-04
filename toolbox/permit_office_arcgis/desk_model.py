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
    choose_mandate: Callable[[str], None] = lambda _key: None
    start_initiative: Callable[..., None] = lambda _kind, _target=None: None


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
class CaseSummary:
    """Structured permit-packet content for the selected case."""

    title: str = "No Active Case"
    item_id: str = ""
    status: str = ""
    category: str = ""
    fields: tuple[CaseField, ...] = ()
    districts: str = "(seeded exhibit; use Retarget from map to revise)"
    preview: str = "Select a docket item from the in tray."
    inspection: str = "No inspection addendum filed."
    action_note: str = ""
    economy: str = ""
    risk_band: str = "unknown"


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
class DistrictTypeRow:
    """One district type on the City tab: how many districts it holds, and any earmark."""

    label: str
    count: int
    swatch: str = ""
    earmarked_until: int = 0


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
    auto_close_active: bool = False
    auto_close_seconds: int = 0
    game_active: bool = True
    district_type_rows: tuple[DistrictTypeRow, ...] = ()
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
    goal_title: str = ""
    goal_progress: str = ""
    goal_met: bool = False
    goal_offer: tuple[tuple[str, str, str, str], ...] = ()
    ladder_rung: str = ""
    next_checkpoint: int = 0
    outcome: str = ""
    initiative_open: bool = False
    initiative_note: str = ""
    earmark_types: tuple[str, ...] = ()


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
    game_active=True,
    audit=None,
    week_start=None,
) -> DeskViewModel:
    """Format gameplay state into a presentation-only desk model.

    `audit` is the cached
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
    points_short, criticals = rules.pass_gap(audit)
    case = _case_summary(state, districts, selected)
    action_lanes = _action_lanes(state, districts, selected) if selected else ()
    ledger_rows = _ledger_rows(state, districts, active_features, active_items, week_start=week_start)
    status = status_text or "No report yet. Select a case; use Retarget from map to change its targets."
    report_tabs = tuple(report_tabs or _legacy_report_tabs(receipt))
    selected_report_id = _resolve_selected_report_id(report_tabs, selected_report_id)
    if report_tabs and selected_report_id:
        report_tabs = tuple(
            replace(tab, selected=tab.report_id == selected_report_id)
            for tab in report_tabs
        )
    exhibit_visible = bool(proposal_visible_by_item.get(selected_id))
    ticker_items = _ticker_items(state, districts, active_features, active_items, report_tabs)
    district_type_rows = _district_type_rows(districts, state)
    goal = _goal_facts(state, districts, active_features)
    return DeskViewModel(
        docket_rows=docket_rows,
        selected_item_id=selected_id,
        case=case,
        ledger_rows=ledger_rows,
        status_text=status,
        exhibit_visible=exhibit_visible,
        receipt=receipt,
        report_tabs=report_tabs,
        selected_report_id=selected_report_id,
        ticker_items=ticker_items,
        show_start_help=bool(show_start_help),
        selected_desk_tab=_resolve_desk_tab(selected_desk_tab, report_tabs),
        action_lanes=action_lanes,
        auto_close_active=bool(auto_close_active),
        auto_close_seconds=int(auto_close_seconds or 0),
        game_active=bool(game_active),
        district_type_rows=district_type_rows,
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
        **goal,
        ladder_rung=rules.AUDIT_RUNGS[max(0, min(len(rules.AUDIT_RUNGS) - 1, int(state.audit_rung or 0)))],
        next_checkpoint=next((week for week in rules.AUDIT_WEEKS if week >= state.turn), 0) if state.status != "complete" else 0,
        outcome=str(getattr(state, "outcome", "") or ""),
        **_initiative_facts(state, districts),
    )


def _initiative_facts(state, districts) -> dict:
    """Return whether this week's initiative can start, why not, and the types to earmark."""

    profiles = list(districts.values() if isinstance(districts, dict) else (districts or ()))
    types = tuple(sorted({getattr(profile, "district_type", "") for profile in profiles} - {""}))
    cheapest = min(rules.EARMARK_COST, rules.CIVIC_ACTION_COST, rules.MARKET_PUSH_COST)
    if rules.initiative_used_this_week(state):
        note = "Filed this week. One initiative a week."
    elif int(state.ap or 0) < rules.INITIATIVE_AP:
        note = f"Needs {rules.INITIATIVE_AP} AP."
    elif int(state.money or 0) < cheapest:
        note = f"Needs ${cheapest}."
    else:
        note = ""
    return {"initiative_open": not note and state.status != "complete", "initiative_note": note, "earmark_types": types}


def _goal_facts(state, districts, active_features) -> dict:
    """Return the season goal fields: the filed mandate's progress, or the open offer."""

    mandate = getattr(state, "mandate", {}) or {}
    chosen = str(mandate.get("chosen") or "")
    district_map = districts if isinstance(districts, dict) else {profile.cell_id: profile for profile in districts or ()}
    features = _feature_snapshots(active_features)
    offer = tuple(
        (key, rules.mandate_title(key), rules.mandate_brief(key), rules.mandate_status(key, state, district_map, features)[1])
        for key in mandate.get("offer") or ()
    )
    if not chosen:
        return {"goal_offer": offer}
    met, progress = rules.mandate_status(chosen, state, district_map, features)
    return {"goal_title": rules.mandate_title(chosen), "goal_progress": progress, "goal_met": met, "goal_offer": offer}


def _district_type_rows(districts, state) -> tuple[DistrictTypeRow, ...]:
    """Count districts per type, largest first, marking earmarked types."""

    profiles = list(districts.values() if isinstance(districts, dict) else (districts or ()))
    counts: dict[str, int] = {}
    for profile in profiles:
        dtype = getattr(profile, "district_type", "") or "district"
        counts[dtype] = counts.get(dtype, 0) + 1
    earmarks = rules.active_earmarks(state)
    return tuple(
        DistrictTypeRow(_display(dtype), count, _symbol_hex(DISTRICT_TYPE_SYMBOLS.get(dtype)), earmarks.get(dtype, 0))
        for dtype, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))
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
    if selected_desk_tab in ("applications", "city"):
        return selected_desk_tab
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
    target_names = [getattr(districts.get(cid), "name", "") or cid for cid in item.target_cell_ids]
    districts_text = ", ".join(target_names) if target_names else "(seeded exhibit; use Retarget from map to revise)"
    inspection = _inspection_summary(item)
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


def _ledger_rows(state, districts, active_features=None, docket=None, week_start=None) -> tuple[LedgerRow, ...]:
    """Build the meter rows the desk shows (owner D10): resources, heat, the four stats, and city services."""

    heat = rules.heat_summary(state)
    incidents = rules.incident_summary(districts)
    service_summary = _city_service_summary(districts)
    week_value = f"{state.turn}/{state.max_turns} CLOSED" if state.status == "complete" or state.turn > state.max_turns else f"{state.turn}/{state.max_turns}"
    return (
        LedgerRow("Week", week_value, "neutral", _meter(state.turn, state.max_turns)),
        LedgerRow("AP", f"{state.ap}/{state.max_ap}", "good" if state.ap else "watch", _meter(state.ap, state.max_ap)),
        LedgerRow("Money", f"${state.money}", "good" if state.money >= 20 else "watch"),
        LedgerRow("Heat", heat, "bad" if heat != "none" else "neutral"),
        _stat_row("Activity", state.activity, "good", week_start),
        _stat_row("Friction", state.friction, "bad" if state.friction >= 50 else "watch", week_start),
        _stat_row("Trust", state.trust, "good", week_start),
        _stat_row("Exposure", state.exposure, "bad" if state.exposure >= 50 else "watch", week_start),
        LedgerRow("Economy", f"rev ${state.last_revenue}; up ${state.last_upkeep}; net {state.last_net:+d}", "watch" if state.last_net < 0 else "good" if state.last_net > 0 else "neutral"),
        LedgerRow("Services", service_summary, "bad" if "critical" in service_summary else "watch" if service_summary != "none" else "neutral"),
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


def _symbol_hex(symbol):
    """Convert a symbology config RGBA tuple to a Tk color."""

    rgba = symbol[0] if symbol else [216, 225, 222, 100]
    return "#{:02x}{:02x}{:02x}".format(int(rgba[0]), int(rgba[1]), int(rgba[2]))


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
