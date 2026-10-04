"""Decision resolution for docket actions and follow-up case types."""

from __future__ import annotations

import random
from typing import Iterable, Mapping

from .models import *
from .catalogs import *
from .helpers import *
from .incidents import incident_identity_from_item, write_incident_case_identity
from .profiles import inspect_item, inspection_case_for_item
from .buyouts import reduce_buyout_pressure
from .systems import (
    advance_project_from_item,
    apply_stat_cascade,
    apply_template_long_term_effects,
    feature_update_payload,
    operating_rule_for_feature,
    start_project_from_approval,
)
from .turns import _settle_item_violations

TURN_PERMIT_SPEND_KEY = "_turn_permit_spend"


def resolve_decision(
    state: CityState,
    item: DocketItem,
    districts: dict[str, DistrictProfile],
    action: str,
    target_cell_ids: Iterable[str],
    spillover_cell_ids: Iterable[str] = (),
    seed: int = 2026,
    mitigated: bool = False,
    active_features: Iterable[FeatureInstance] | None = None,
    projects: dict[str, ProjectRecord] | None = None,
) -> DecisionResult:
    """Apply a docket action to city state, district profiles, and case records."""

    action_key = action.lower().strip()
    if item.status not in OPEN_DOCKET_STATUSES:
        return _blocked(action, item.item_id, f"{item.title} is already filed ({item.status}).")
    # Keep spillovers distinct so target approval math is not double-counted.
    targets = _unique_known(target_cell_ids, districts)
    spillovers = [cid for cid in _unique_known(spillover_cell_ids, districts) if cid not in targets]
    template = TEMPLATES[item.template_id]
    archetype = feature_archetype_for_template(template)
    item.stakeholder = item.stakeholder or template.stakeholder
    item.target_rule = item.target_rule or template.target_rule

    # Generated follow-ups have different lifecycle rules than ordinary permits.
    if template.template_id == MAINTENANCE_TEMPLATE_ID:
        return _resolve_maintenance_decision(
            state,
            item,
            action,
            action_key,
            targets,
            template,
            mitigated,
            active_features,
        )

    # Inspection only spends AP and enriches the case packet; it intentionally
    # stops before permit effects, projects, or ArcGIS feature activation.
    if action_key == "inspect":
        blocked = _spend_ap(state, action, item.item_id, "Inspection")
        if blocked:
            return blocked
        inspect_item(item, seed, [districts[cid] for cid in targets])
        item.status = "inspected"
        return DecisionResult(
            True,
            action,
            item.item_id,
            f"Inspection completed for {item.title}: {item.preview_text}",
            affected_cell_ids=targets,
            item_status=item.status,
        )

    if template.is_enforcement:
        return _resolve_enforcement_decision(
            state,
            item,
            districts,
            action,
            action_key,
            targets,
            spillovers,
            template,
            mitigated,
            projects,
        )

    if template.is_incident:
        return _resolve_incident_decision(
            state,
            item,
            districts,
            action,
            action_key,
            targets,
            spillovers,
            template,
            mitigated,
            projects,
        )

    if action_key == "deny":
        # Denial records an office disposition but does not consume scarce
        # attention. Inspecting, issuing, and mitigating remain AP-gated.
        if state.ap < 0:
            return _blocked(action, item.item_id, "Invalid AP state.")
        # Denials avoid project effects but still create friction, stakeholder
        # heat, and local population reactions.
        item.status = "denied"
        delta = {"friction": 1, "activity": -1}
        _apply_city_delta(state, delta)
        district_deltas = _apply_population_reaction(template, [districts[cid] for cid in targets], "deny", mitigated)
        surfaced = _surface_new_incidents(state, [districts[cid] for cid in targets])
        if surfaced:
            delta["friction"] = delta.get("friction", 0) + surfaced
        heat_delta = _adjust_heat(state, item.stakeholder, template.denial_heat)
        project_note = ""
        if projects and item.project_id:
            project = advance_project_from_item(projects, item, state, approved=False)
            if project:
                project_note = f" Project {project.project_id} delayed."
        report = (
            f"Denied {item.title}. Certain effects: project exposure avoided; city delta {_format_delta(delta)}. "
            f"Exposure/side effects: {item.stakeholder.replace('_', ' ')} heat {heat_delta:+d}."
            f"{project_note} {_population_report_fragment([districts[cid] for cid in targets])}"
        )
        return DecisionResult(
            True,
            action,
            item.item_id,
            report,
            delta,
            targets,
            district_deltas,
            item_status=item.status,
            stakeholder_delta={item.stakeholder: heat_delta},
        )

    if action_key not in ("approve", "approve_mitigated"):
        return _blocked(action, item.item_id, f"Unknown decision action {action!r}.")
    if not targets:
        return _blocked(action, item.item_id, "Decision requires at least one selected target district.")
    total_money = template.money_cost + (template.mitigation_cost if mitigated else 0)
    blocked = _spend_resources(state, action, item.item_id, "Approval", template.ap_cost, total_money)
    if blocked:
        return blocked

    # Approval builds district-level deltas first, then averages those effects
    # into citywide state after side effects, failures, and population reactions.
    district_deltas: dict[str, dict[str, int]] = {}
    city_delta = {metric: 0 for metric in CORE_METRICS}
    # Include the selected targets in the seed so approval failures are repeatable per placement.
    rng = random.Random(f"{seed}:{item.item_id}:{','.join(targets)}:{mitigated}")

    item.target_cell_ids = targets

    # Target and spillover deltas share the city accumulator but still need
    # separate per-district records for ArcGIS field updates and reports.
    for cid in targets:
        delta = _district_adjusted_effects(template.base_effects, districts[cid].district_type, template.category)
        delta = _land_use_adjusted_effects(delta, districts[cid], archetype)
        _merge_delta(delta, _service_coverage_effect(archetype, districts[cid], "target"))
        if mitigated:
            delta = _mitigate(delta)
        _apply_profile_delta(districts[cid], delta)
        district_deltas[cid] = delta
        _merge_delta(city_delta, delta)

    for cid in spillovers:
        delta = dict(template.spillover_effects)
        delta = _land_use_adjusted_effects(delta, districts[cid], archetype)
        _merge_delta(delta, _service_coverage_effect(archetype, districts[cid], "spillover"))
        if mitigated:
            delta = _mitigate(delta)
        _apply_profile_delta(districts[cid], delta)
        district_deltas[cid] = delta
        _merge_delta(city_delta, delta)

    side = _context_side_effect(template, [districts[cid] for cid in targets], rng, mitigated)
    if side:
        # Context side effects represent local surprises that affect every
        # selected district and the city accumulator.
        for cid in targets:
            _apply_profile_delta(districts[cid], side)
            _merge_delta(district_deltas.setdefault(cid, {}), side)
        _merge_delta(city_delta, side)

    failure_delta = _approval_failure_effect(template, [districts[cid] for cid in targets], item.risk_band, rng, mitigated)
    failure_triggered = bool(failure_delta)
    if failure_triggered:
        item.status = "failed"
        for cid in targets:
            _apply_profile_delta(districts[cid], failure_delta)
            _merge_delta(district_deltas.setdefault(cid, {}), failure_delta)
        _merge_delta(city_delta, failure_delta)
    else:
        item.status = "active"
        for cid in targets:
            _apply_land_use_change(districts[cid], archetype)
            normalize_profile(districts[cid])

    dissatisfaction_before = {cid: sum((districts[cid].dissatisfaction or {}).values()) for cid in targets}
    approval_grievance_floors: dict[str, dict[str, int]] = {}
    if not failure_triggered and mitigated:
        for cid in targets:
            floors = _dissatisfaction_floor(districts[cid], template.supporter_groups, -1)
            _merge_dissatisfaction_floors(approval_grievance_floors, cid, floors)

    population_deltas = _apply_population_reaction(
        template,
        [districts[cid] for cid in targets],
        "failure" if failure_triggered else "approve",
        mitigated,
    )
    for cid, delta in population_deltas.items():
        _merge_delta(district_deltas.setdefault(cid, {}), delta)
    if not failure_triggered:
        # Long-term housing and hazard changes only land when the permit itself
        # succeeds; failed outcomes already applied their corrective delta.
        long_term_deltas = apply_template_long_term_effects(template, [districts[cid] for cid in targets], mitigated)
        for cid, delta in long_term_deltas.items():
            _merge_delta(district_deltas.setdefault(cid, {}), delta)
    if not failure_triggered:
        for cid in targets:
            reduction = 3 if mitigated else 2
            reduce_buyout_pressure(districts[cid], reduction)
    affected_ids = targets + spillovers
    for cid in affected_ids:
        cascade_delta = apply_stat_cascade(districts[cid])
        if cascade_delta:
            _merge_delta(district_deltas.setdefault(cid, {}), cascade_delta)
            _merge_delta(city_delta, {metric: value for metric, value in cascade_delta.items() if metric in CORE_METRICS})
    for cid, floors in approval_grievance_floors.items():
        _apply_dissatisfaction_floors(districts[cid], floors)
    for cid, before in dissatisfaction_before.items():
        delta = sum((districts[cid].dissatisfaction or {}).values()) - before
        if delta:
            district_deltas.setdefault(cid, {})["dissatisfaction"] = delta
        elif cid in district_deltas:
            district_deltas[cid].pop("dissatisfaction", None)
    surfaced = _surface_new_incidents(state, [districts[cid] for cid in targets])

    averaged = _average_city_delta(city_delta, targets, spillovers)
    _apply_city_delta(state, averaged)
    if surfaced:
        averaged["friction"] = averaged.get("friction", 0) + surfaced
    affected = affected_ids
    _settle_item_violations(item, mitigated)
    mitigation_text = " with mitigation" if mitigated else ""
    failure_text = _approval_risk_report(template, failure_triggered, failure_delta, side, item.risk_band, mitigated, [districts[cid] for cid in targets])
    spillover_text = _spillover_report_fragment(spillovers, template, mitigated, districts)
    recurring_text = "no new active feature because the decision failed" if failure_triggered else _recurring_budget_report(archetype)
    project_text = ""
    if projects is not None and not failure_triggered:
        # Project records are either opened from a new approval or advanced when
        # this docket item is a due project step.
        if template.starts_chain_id:
            project = start_project_from_approval(state, item, template, targets)
            if project:
                projects[project.project_id] = project
                project_text = f" Project {project.project_id} opened; next step {project.current_step_id} due turn {project.due_turn}."
        elif item.project_id:
            project = advance_project_from_item(projects, item, state, approved=True, failed=False)
            if project:
                project_text = f" Project {project.project_id} status {project.status}; current step {project.current_step_id}."
    elif projects is not None and failure_triggered and item.project_id:
        project = advance_project_from_item(projects, item, state, approved=True, failed=True)
        if project:
            project_text = f" Project {project.project_id} status {project.status}."
    report = (
        f"Approved {item.title}{mitigation_text}. Certain effects: affected {len(affected)} district(s): "
        f"{_district_list_fragment(affected, districts)}; immediate city delta {_format_delta(averaged)}; "
        f"{_local_cause_fragment([districts[cid] for cid in affected], district_deltas)}; {spillover_text}; recurring budget {recurring_text}. "
        f"Exposure/side effects: {failure_text} "
        f"{_population_report_fragment([districts[cid] for cid in targets])}{project_text}"
    )
    return DecisionResult(
        True,
        action,
        item.item_id,
        report,
        averaged,
        affected,
        district_deltas,
        item.status,
        failure_triggered=failure_triggered,
    )


def _resolve_maintenance_decision(
    state: CityState,
    item: DocketItem,
    action: str,
    action_key: str,
    targets: list[str],
    template: DocketTemplate,
    mitigated: bool,
    active_features: Iterable[FeatureInstance] | None,
) -> DecisionResult:
    """Resolve repair, deferral, and inspection actions for active features."""

    features = list(active_features or ())
    feature = next((candidate for candidate in features if candidate.feature_id == item.subject_feature_id), None)
    # Maintenance inspections attach condition evidence to the docket item but
    # do not require the referenced feature to be mutable.
    if action_key == "inspect":
        blocked = _spend_ap(state, action, item.item_id, "Maintenance inspection")
        if blocked:
            return blocked
        item.inspected = True
        item.status = "inspected"
        condition = feature.condition if feature else 0
        item.risk_band = "high" if condition <= 10 else "medium" if condition < 35 else "low"
        item.case_json = dict(item.case_json or {})
        item.case_json["inspection"] = inspection_case_for_item(item, (), fallback_risk_band=item.risk_band)
        item.preview_text = f"{template.preview} Inspection: referenced feature condition {condition}."
        return DecisionResult(True, action, item.item_id, f"Inspection completed for {item.title}: {item.preview_text}", affected_cell_ids=targets, item_status=item.status)

    if feature is None:
        return _blocked(action, item.item_id, "Maintenance order requires a referenced active feature.")
    rule = operating_rule_for_feature(feature)
    if action_key == "deny":
        blocked = _spend_ap(state, action, item.item_id, "Deferring maintenance")
        if blocked:
            return blocked
        # Deferral leaves the feature in the maintenance queue and raises city
        # exposure so the backlog is visible outside the docket.
        item.status = "deferred"
        feature.status = "maintenance_due"
        feature.display_state = "maintenance_due"
        heat_delta = _adjust_heat(state, feature.owner_group or "maintenance_office", template.denial_heat)
        delta = {"exposure": 1}
        _apply_city_delta(state, delta)
        return DecisionResult(
            True,
            action,
            item.item_id,
            f"Deferred {item.title}. Maintenance remains open; heat {heat_delta:+d}.",
            delta,
            targets,
            item_status=item.status,
            stakeholder_delta={feature.owner_group or "maintenance_office": heat_delta},
            feature_updates={feature.feature_id: feature_update_payload(feature)},
        )

    if action_key not in ("approve", "approve_mitigated"):
        return _blocked(action, item.item_id, f"Unknown decision action {action!r}.")
    total_money = max(template.money_cost, rule.maintenance_cost) + (template.mitigation_cost if mitigated else 0)
    blocked = _spend_resources(state, action, item.item_id, "Maintenance", template.ap_cost, total_money)
    if blocked:
        return blocked

    repair = rule.repair_amount + (20 if mitigated else 0)
    # Successful maintenance repairs lifecycle fields and clears one-shot
    # failure markers so future failures can be applied once again if needed.
    feature.condition = max(0, min(100, feature.condition + repair))
    feature.last_maintained_turn = state.turn
    if rule.maintenance_interval:
        feature.maintenance_due_turn = state.turn + rule.maintenance_interval + (1 if mitigated else 0)
    feature.status = "active" if feature.condition > rule.degrade_threshold else "degraded"
    feature.display_state = feature.status
    feature.state_json = dict(feature.state_json or {})
    feature.state_json.pop("failure_applied", None)
    item.status = "settled" if mitigated else "maintained"
    _settle_item_violations(item, mitigated)
    delta = _mitigate(template.base_effects) if mitigated else dict(template.base_effects)
    _apply_city_delta(state, delta)
    return DecisionResult(
        True,
        action,
        item.item_id,
        f"Maintained {item.title}. Feature condition is now {feature.condition}; next due turn {feature.maintenance_due_turn}.",
        delta,
        targets,
        item_status=item.status,
        feature_updates={feature.feature_id: feature_update_payload(feature)},
    )


def _resolve_enforcement_decision(
    state: CityState,
    item: DocketItem,
    districts: dict[str, DistrictProfile],
    action: str,
    action_key: str,
    targets: list[str],
    spillovers: list[str],
    template: DocketTemplate,
    mitigated: bool,
    projects: dict[str, ProjectRecord] | None = None,
) -> DecisionResult:
    """Resolve compliance follow-ups produced by stakeholder heat."""

    if action_key == "deny":
        blocked = _spend_ap(state, action, item.item_id, "Deferring enforcement")
        if blocked:
            return blocked
        # Deferred enforcement keeps the condition unresolved and converts
        # stakeholder pressure into broader friction and exposure.
        item.status = "deferred"
        delta = {"friction": 2, "exposure": 1}
        _apply_city_delta(state, delta)
        heat_delta = _adjust_heat(state, item.stakeholder, template.denial_heat)
        project_note = ""
        if projects and item.project_id:
            project = advance_project_from_item(projects, item, state, approved=False)
            if project:
                project_note = f" Project {project.project_id} delayed."
        report = (
            f"Deferred enforcement for {item.title}. The unpermitted condition remains unresolved; "
            f"{item.stakeholder.replace('_', ' ')} heat {heat_delta:+d}.{project_note}"
        )
        return DecisionResult(
            True,
            action,
            item.item_id,
            report,
            delta,
            targets,
            item_status=item.status,
            stakeholder_delta={item.stakeholder: heat_delta},
        )

    if action_key not in ("approve", "approve_mitigated"):
        return _blocked(action, item.item_id, f"Unknown decision action {action!r}.")
    if not targets:
        return _blocked(action, item.item_id, "Enforcement requires one selected district.")
    total_money = template.money_cost + (template.mitigation_cost if mitigated else 0)
    blocked = _spend_resources(state, action, item.item_id, "Enforcement", template.ap_cost, total_money)
    if blocked:
        return blocked

    item.target_cell_ids = targets
    item.status = "settled" if mitigated else "enforced"

    district_deltas: dict[str, dict[str, int]] = {}
    city_delta = {metric: 0 for metric in CORE_METRICS}
    base = _mitigate(template.base_effects) if mitigated else dict(template.base_effects)
    spill = _mitigate(template.spillover_effects) if mitigated else dict(template.spillover_effects)

    # Enforcement acts like a targeted correction: selected districts get the
    # full base effect, while spillover districts get only catalog spillovers.
    for cid in targets:
        _apply_profile_delta(districts[cid], base)
        district_deltas[cid] = dict(base)
        _merge_delta(city_delta, base)

    for cid in spillovers:
        delta = dict(spill)
        _apply_profile_delta(districts[cid], delta)
        district_deltas[cid] = delta
        _merge_delta(city_delta, delta)

    heat_relief = -2 if mitigated else -STAKEHOLDER_HEAT_THRESHOLD
    heat_delta = _adjust_heat(state, item.stakeholder, heat_relief)
    averaged = _average_city_delta(city_delta, targets, spillovers)
    _apply_city_delta(state, averaged)
    affected = targets + spillovers
    _settle_item_violations(item, mitigated)
    project_note = ""
    if projects and item.project_id:
        project = advance_project_from_item(projects, item, state, approved=True, failed=False)
        if project:
            project_note = f" Project {project.project_id} status {project.status}."
    if mitigated:
        report = (
            f"Settled {item.title} through a retroactive permit and compliance schedule. "
            f"Certain effects: city delta {_format_delta(averaged)}. "
            f"Exposure/side effects: {item.stakeholder.replace('_', ' ')} heat {heat_delta:+d}.{project_note}"
        )
    else:
        report = (
            f"Enforced {item.title}. Certain effects: city delta {_format_delta(averaged)}. "
            f"Exposure/side effects: compliance record is clearer, but local objections may increase; "
            f"{item.stakeholder.replace('_', ' ')} heat {heat_delta:+d}.{project_note}"
        )
    return DecisionResult(
        True,
        action,
        item.item_id,
        report,
        averaged,
        affected,
        district_deltas,
        item.status,
        stakeholder_delta={item.stakeholder: heat_delta},
    )


def _resolve_incident_decision(
    state: CityState,
    item: DocketItem,
    districts: dict[str, DistrictProfile],
    action: str,
    action_key: str,
    targets: list[str],
    spillovers: list[str],
    template: DocketTemplate,
    mitigated: bool,
    projects: dict[str, ProjectRecord] | None = None,
) -> DecisionResult:
    """Resolve civic incident responses produced by local dissatisfaction."""

    identity, identity_cell_id, identity_group = incident_identity_from_item(item)
    if identity:
        if identity_cell_id not in districts:
            return _blocked(action, item.item_id, f"Incident district {identity_cell_id!r} is not available.")
        targets = [identity_cell_id]
        spillovers = []
        write_incident_case_identity(item, identity_cell_id, identity_group)
    if not targets:
        return _blocked(action, item.item_id, "Incident response requires one selected district.")
    if action_key == "deny":
        blocked = _spend_ap(state, action, item.item_id, "Deferring a civic incident")
        if blocked:
            return blocked
        # Deferring an incident raises the attached group's dissatisfaction
        # before the city-level friction/exposure penalty is applied.
        item.status = "deferred"
        group = identity_group if identity_group in CITIZEN_GROUPS else item.stakeholder if item.stakeholder in CITIZEN_GROUPS else _top_dissatisfaction(districts[targets[0]])[0]
        for cid in targets:
            _adjust_dissatisfaction(districts[cid], (group,), 1)
            normalize_profile(districts[cid])
        if identity:
            write_incident_case_identity(item, targets[0], group, districts[targets[0]].incident_state)
        delta = {"friction": 2, "exposure": 1}
        _apply_city_delta(state, delta)
        heat_delta = _adjust_heat(state, group, template.denial_heat)
        project_note = ""
        if projects and item.project_id:
            project = advance_project_from_item(projects, item, state, approved=False)
            if project:
                project_note = f" Project {project.project_id} delayed."
        report = (
            f"Deferred {item.title}. The incident remains unresolved and may affect citywide friction. "
            f"City delta: {_format_delta(delta)}. {_group_label(group).title()} heat {heat_delta:+d}.{project_note}"
        )
        return DecisionResult(
            True,
            action,
            item.item_id,
            report,
            delta,
            targets,
            {cid: {"dissatisfaction": 1} for cid in targets},
            item.status,
            stakeholder_delta={group: heat_delta},
        )

    if action_key not in ("approve", "approve_mitigated"):
        return _blocked(action, item.item_id, f"Unknown decision action {action!r}.")
    total_money = template.money_cost + (template.mitigation_cost if mitigated else 0)
    blocked = _spend_resources(state, action, item.item_id, "Incident response", template.ap_cost, total_money)
    if blocked:
        return blocked

    item.target_cell_ids = targets
    item.status = "settled" if mitigated else "responded"
    group = identity_group if identity_group in CITIZEN_GROUPS else item.stakeholder if item.stakeholder in CITIZEN_GROUPS else _top_dissatisfaction(districts[targets[0]])[0]
    relief = -3 if mitigated else -2
    base = _mitigate(template.base_effects) if mitigated else dict(template.base_effects)
    spill = _mitigate(template.spillover_effects) if mitigated else dict(template.spillover_effects)
    district_deltas: dict[str, dict[str, int]] = {}
    city_delta = {metric: 0 for metric in CORE_METRICS}

    # Incident response combines template effects with explicit grievance relief
    # so normalized profiles can clear visible incident state.
    for cid in targets:
        _apply_profile_delta(districts[cid], base)
        _adjust_dissatisfaction(districts[cid], (group,), relief)
        normalize_profile(districts[cid])
        if identity and cid == identity_cell_id:
            write_incident_case_identity(item, cid, group, districts[cid].incident_state)
        district_deltas[cid] = dict(base)
        district_deltas[cid]["dissatisfaction"] = relief
        _merge_delta(city_delta, base)

    for cid in spillovers:
        delta = dict(spill)
        _apply_profile_delta(districts[cid], delta)
        _adjust_dissatisfaction(districts[cid], (group,), -1)
        normalize_profile(districts[cid])
        district_deltas[cid] = delta
        district_deltas[cid]["dissatisfaction"] = -1
        _merge_delta(city_delta, delta)

    averaged = _average_city_delta(city_delta, targets, spillovers)
    _apply_city_delta(state, averaged)
    heat_delta = _adjust_heat(state, group, -1 if mitigated else 0)
    _settle_item_violations(item, mitigated)
    mode_text = "settled with conditions" if mitigated else "accepted for response"
    project_note = ""
    if projects and item.project_id:
        project = advance_project_from_item(projects, item, state, approved=True, failed=False)
        if project:
            project_note = f" Project {project.project_id} status {project.status}."
    report = (
        f"{item.title} {mode_text}. Certain effects: Target group: {_group_label(group)}; "
        f"city delta {_format_delta(averaged)}. Exposure/side effects: local grievance may continue. "
        f"{_population_report_fragment([districts[cid] for cid in targets])}{project_note}"
    )
    return DecisionResult(
        True,
        action,
        item.item_id,
        report,
        averaged,
        targets + spillovers,
        district_deltas,
        item.status,
        stakeholder_delta={group: heat_delta} if heat_delta else {},
    )


def _spend_ap(state: CityState, action: str, item_id: str, label: str, cost: int = 1) -> DecisionResult | None:
    """Spend AP for a decision branch, or return the existing error shape."""

    if state.ap < cost:
        return _blocked(action, item_id, f"{label} requires {cost} AP.")
    state.ap -= cost
    return None


def _spend_resources(
    state: CityState,
    action: str,
    item_id: str,
    label: str,
    ap_cost: int,
    money_cost: int,
) -> DecisionResult | None:
    """Validate and spend the AP/money pair used by approve-style actions."""

    if state.ap < ap_cost:
        return _blocked(action, item_id, f"{label} requires {ap_cost} AP.")
    if state.money < money_cost:
        return _blocked(action, item_id, f"{label} requires ${money_cost}.")
    state.ap -= ap_cost
    state.money -= money_cost
    _record_permit_spend(state, money_cost)
    return None


def _record_permit_spend(state: CityState, amount: int) -> None:
    """Accumulate same-turn approval spending for the next economy report."""

    if amount <= 0:
        return
    current = int(state.stakeholder_memory.get(TURN_PERMIT_SPEND_KEY, 0) or 0)
    state.stakeholder_memory[TURN_PERMIT_SPEND_KEY] = current + int(amount)


def _average_city_delta(city_delta: dict[str, int], targets: list[str], spillovers: list[str]) -> dict[str, int]:
    """Average district-level effects into a single citywide metric delta."""

    divisor = max(1, len(targets) + len(spillovers))
    return {metric: round(value / divisor) for metric, value in city_delta.items()}


def _approval_risk_report(
    template: DocketTemplate,
    failure_triggered: bool,
    failure_delta: dict[str, int],
    side_delta: dict[str, int],
    risk_band: str,
    mitigated: bool,
    targets: list[DistrictProfile],
) -> str:
    """Explain approval uncertainty separately from certain effects."""

    if failure_triggered:
        return f"Outcome failed: {template.failure_mode}; corrective delta {_format_delta(failure_delta)}."
    chance = round(_failure_chance(template, targets, risk_band or "unknown", mitigated) * 100)
    side_text = f" Side-effect delta {_format_delta(side_delta)}." if side_delta else ""
    if template.failure_mode:
        return f"{template.failure_mode} did not trigger; estimated failure chance was {chance}%.{side_text}"
    return f"no failure mode triggered; estimated side-effect chance was {chance}%.{side_text}"


def _recurring_budget_report(archetype: FeatureArchetype) -> str:
    """Format expected recurring feature economy after approval."""

    operating = operating_rule_for_feature(archetype.archetype_id)
    intensity = max(1, int(archetype.capacity or 1))
    revenue = operating.revenue_per_turn * intensity
    upkeep = operating.upkeep_per_turn * intensity
    net = revenue - upkeep
    if not revenue and not upkeep:
        return "no recurring revenue or upkeep"
    if net > 0:
        return f"helps the budget later: revenue ${revenue}/week, upkeep ${upkeep}/week, net ${net:+d}"
    if net < 0:
        return f"creates maintenance burden: revenue ${revenue}/week, upkeep ${upkeep}/week, net ${net:+d}"
    return f"budget neutral: revenue ${revenue}/week, upkeep ${upkeep}/week, net ${net:+d}"


def _spillover_report_fragment(
    spillovers: list[str],
    template: DocketTemplate,
    mitigated: bool,
    districts: Mapping[str, DistrictProfile] | None = None,
) -> str:
    """Format spillover effects separately from target and city effects."""

    if not spillovers:
        return "spillover none"
    delta = _mitigate(template.spillover_effects) if mitigated else dict(template.spillover_effects)
    return f"spillover {_district_list_fragment(spillovers, districts)} gets {_format_delta(delta)}"


def _district_list_fragment(
    cell_ids: list[str],
    districts: Mapping[str, DistrictProfile] | None = None,
) -> str:
    """Keep report prose readable when many districts are affected.

    Resolves cell_ids to human-readable district names when a districts map is
    supplied, falling back to the cell_id key for any unknown district.
    """

    def label(cid: str) -> str:
        profile = districts.get(cid) if districts else None
        return district_label(profile) if profile is not None else cid

    labels = [label(cid) for cid in cell_ids]
    if len(labels) <= 4:
        return ", ".join(labels)
    shown = ", ".join(labels[:3])
    return f"{shown}, +{len(labels) - 3} more"


def _local_cause_fragment(profiles: list[DistrictProfile], district_deltas: dict[str, dict[str, int]]) -> str:
    """Format primary pressure causes and local stat deltas for reports."""

    counts: dict[str, int] = {}
    deltas = []
    for profile in profiles:
        counts[profile.display_state] = counts.get(profile.display_state, 0) + 1
        delta_text = _format_delta(district_deltas.get(profile.cell_id, {}))
        if delta_text != "no net citywide metric change":
            deltas.append(f"{district_label(profile)} {delta_text}")
    causes = ", ".join(f"{cause.replace('_', ' ')} x{count}" for cause, count in sorted(counts.items()))
    if deltas:
        return f"primary pressure {causes}; local deltas {'; '.join(deltas[:3])}"
    return f"primary pressure {causes}; local deltas none"


__all__ = [name for name in globals() if not name.startswith("__")]
