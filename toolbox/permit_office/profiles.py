"""Profile, docket, and inspection generation for the rules package."""

from __future__ import annotations

import copy
import random
from typing import Iterable

from .models import *
from .catalogs import *
from .helpers import *
from .initiatives import EARMARK_DRAW_MULTIPLIER, active_earmarks
from .incidents import incident_identity, incident_identity_from_item, write_incident_case_identity
from .systems import normalize_feature_instance, project_step_template


def generate_district_profiles(rows: int = 5, cols: int = 5, seed: int = 2026) -> list[DistrictProfile]:
    """Generate deterministic district profiles for a rectangular board."""

    rng = random.Random(seed)
    prefixes = ["North", "Old", "Canal", "Bright", "Lower", "Cinder", "Glass", "Civic"]
    suffixes = ["Ward", "Market", "Steps", "Yard", "Row", "Crossing", "Annex", "Green"]
    profiles: list[DistrictProfile] = []
    for row in range(rows):
        for col in range(cols):
            cell_id = f"D{row:02d}{col:02d}"
            dtype = DISTRICT_TYPES[(row + col + rng.randrange(len(DISTRICT_TYPES))) % len(DISTRICT_TYPES)]
            base = 30 + rng.randrange(36)
            activity = max(10, min(90, base + rng.randrange(-12, 13)))
            friction = max(5, min(80, 28 + rng.randrange(-14, 15)))
            trust = max(10, min(90, 34 + rng.randrange(-14, 22)))
            exposure = max(5, min(80, 26 + rng.randrange(-12, 18)))
            services = max(5, min(90, 35 + rng.randrange(-15, 16)))
            population_mix = _initial_population_mix(dtype, rng)
            dissatisfaction = _initial_dissatisfaction(population_mix, activity, friction, exposure, services)
            profile = DistrictProfile(
                cell_id=cell_id,
                name=f"{rng.choice(prefixes)} {rng.choice(suffixes)}",
                population=650 + rng.randrange(2200),
                activity=activity,
                friction=friction,
                trust=trust,
                exposure=exposure,
                services=services,
                district_type=dtype,
                population_mix=population_mix,
                dissatisfaction=dissatisfaction,
            )
            normalize_profile(profile)
            profiles.append(profile)
    assign_grid_adjacency(profiles, rows, cols)
    for profile in profiles:
        normalize_profile(profile)
    return profiles


# New ordinary cases drawn each week (owner D7); follow-ups come on top, up to the docket cap.
DRAWN_CASES_PER_WEEK = 2


def generate_docket(
    turn: int,
    seed: int = 2026,
    count: int = 4,
    state: CityState | None = None,
    districts: dict[str, DistrictProfile] | None = None,
    projects: Iterable[ProjectRecord] | dict[str, ProjectRecord] | None = None,
    active_features: Iterable[FeatureInstance] | None = None,
    carried_items: Iterable[DocketItem] | None = None,
    drawn: int = DRAWN_CASES_PER_WEEK,
) -> list[DocketItem]:
    """Generate the current turn docket: follow-up work first, then ``drawn`` new cases.

    ``count`` caps the whole docket; the weighted draw adds at most ``drawn``
    ordinary cases on top of due project steps, carried cases and follow-ups.
    """

    scenario = SCENARIO_RULES.get(state.scenario_id if state else "default", SCENARIO_RULES["default"])
    chosen = _weighted_template_pool(turn, seed, drawn, state, districts, scenario)

    items: list[DocketItem] = []
    for due in _project_due_items(turn, projects):
        if len(items) >= count:
            break
        items.append(due)

    for carried in _carried_docket_items(turn, carried_items, count - len(items), start_idx=len(items) + 1):
        if len(items) >= count:
            break
        items.append(carried)

    for followup in _pending_momentum_followup_items(turn, state, count - len(items), start_idx=len(items) + 1):
        if len(items) >= count:
            break
        items.append(followup)

    if active_features:
        followup = _maintenance_followup_item(turn, active_features, idx=len(items) + 1)
        if followup and len(items) < count:
            items.append(followup)

    if districts:
        followup = _incident_followup_item(
            turn,
            districts,
            idx=len(items) + 1,
            skip_identities=_incident_identities_for_items(items),
        )
        if followup and len(items) < count:
            items.append(followup)

    if state:
        followup = _heat_followup_item(turn, state, idx=len(items) + 1)
        if followup and len(items) < count:
            items.append(followup)

    # Fill remaining docket slots with the weighted template draw after
    # mandatory follow-up work has been given priority.
    for template_id in chosen:
        if len(items) >= count:
            break
        items.append(_make_docket_item(turn, len(items) + 1, template_id))
    return items


TYPE_CATEGORY_WEIGHTS = {
    "mercantile": {"business": 4, "development": 3, "compliance": 2},
    "industrial": {"utility": 4, "department": 2, "compliance": 2, "transit": 2},
    "civic": {"department": 3, "education": 2, "culture": 2, "transit": 2},
    "academic": {"culture": 3, "education": 3, "event": 2, "land": 1},
    "natural": {"land": 5, "culture": 1},
    "residential": {"residential": 4, "education": 2, "development": 2, "business": 1},
}

# Culture pull: a district's dominant citizen groups demand the kind of work that
# serves them, so the population mix (not just the district type) shapes the
# docket. Parallels TYPE_CATEGORY_WEIGHTS but keys on CITIZEN_GROUPS. Only groups
# at or above CULTURE_DOMINANT_BAND contribute, so a balanced mix stays neutral.
GROUP_CATEGORY_WEIGHTS = {
    "families": {"education": 3, "residential": 2},
    "elders": {"department": 3, "residential": 2},
    "students": {"education": 3, "culture": 2},
    "commuters": {"transit": 3, "utility": 2},
    "workers": {"business": 2, "utility": 3},
    "artists": {"culture": 3, "event": 3},
    "vendors": {"business": 3, "development": 2},
    "homeowners": {"residential": 3, "compliance": 2},
    "renters": {"residential": 3, "development": 2},
    "civil_servants": {"department": 3, "compliance": 2},
    "developers": {"development": 3, "business": 2},
    "conservationists": {"land": 3, "culture": 2},
}

CULTURE_DOMINANT_BAND = 2


def _weighted_template_pool(
    turn: int,
    seed: int,
    count: int,
    state: CityState | None,
    districts: dict[str, DistrictProfile] | None,
    scenario: ScenarioRule,
) -> list[str]:
    """Return deterministic proposal templates weighted by district mix."""

    rng = random.Random(f"docket:{seed}:{turn}:{_district_mix_key(districts)}:{state.activity if state else 0}:{state.friction if state else 0}:{state.exposure if state else 0}")
    weights = {template_id: 1 for template_id in DEMO_TEMPLATE_IDS}
    if districts:
        populations = [max(0, int(getattr(profile, "population", 0) or 0)) for profile in districts.values()]
        avg_population = (sum(populations) / len(populations)) if populations else 0
        # The weightable template set is district-independent, so resolve it once
        # instead of re-scanning all TEMPLATES (skipping non-demo ids) per district.
        relevant_templates = [
            (template_id, template)
            for template_id, template in TEMPLATES.items()
            if template_id in weights
        ]
        for profile in districts.values():
            # Above-average-population districts pull the docket harder toward
            # their type, so dominant cultures see more related proposals and can
            # snowball. This is a purely additive bonus that only rewards density
            # past the city average: a balanced board leaves pop_bonus == 0 and
            # weighting is identical to the prior type-only behavior.
            pop_bonus = 0.0
            if avg_population > 0:
                pop_bonus = max(0.0, min(1.0, int(profile.population or 0) / avg_population - 1.0))
            # The category weight map depends only on the district type; hoist it
            # out of the per-template loop.
            type_weights = TYPE_CATEGORY_WEIGHTS.get(profile.district_type, {})
            # Fold this district's dominant cultures into a single category->weight
            # map so the per-template loop stays a flat dict lookup. A district can
            # have several strong groups; their category pulls accumulate.
            culture_weights: dict[str, int] = {}
            for group, band in (profile.population_mix or {}).items():
                if int(band or 0) < CULTURE_DOMINANT_BAND:
                    continue
                for category, weight in GROUP_CATEGORY_WEIGHTS.get(group, {}).items():
                    culture_weights[category] = culture_weights.get(category, 0) + weight
            for template_id, template in relevant_templates:
                type_weight = type_weights.get(template.category, 0)
                if type_weight:
                    weights[template_id] += type_weight + round(type_weight * pop_bonus)
                culture_weight = culture_weights.get(template.category, 0)
                if culture_weight:
                    weights[template_id] += culture_weight + round(culture_weight * pop_bonus)
                if profile.district_type in template.good_fit_types:
                    weights[template_id] += 2 + round(2 * pop_bonus)
                if profile.district_type in template.bad_fit_types:
                    weights[template_id] = max(1, weights[template_id] - 1)
                # Stat-driven nudges: a district in trouble on one axis pulls the
                # docket toward the templates that address it, so the generated
                # work reads as a response to city condition. 45 is the mid-band
                # threshold on the 0-100 stat scale (below = struggling activity,
                # above = elevated friction/exposure).
                if profile.activity < 45 and template.category in {"development", "business", "residential"}:
                    weights[template_id] += 2
                if profile.friction > 45 and template.is_incident:
                    weights[template_id] += 3
                if profile.exposure > 45 and template.category in {"utility", "department", "compliance"}:
                    weights[template_id] += 2
    # An earmarked district type pulls its kind of case into the draw (owner D6).
    earmarked = set(active_earmarks(state)) if state else set()
    for template_id in weights:
        if earmarked & set(TEMPLATES[template_id].good_fit_types):
            weights[template_id] *= EARMARK_DRAW_MULTIPLIER
    chosen: list[str] = []
    available = dict(weights)
    # Scenario docket priority is the strongest designer signal (e.g. a housing
    # mandate forcing housing filings). Guarantee its in-pool templates a slot so
    # forced scenarios reliably surface their mandated work instead of depending
    # on weighted-sampling luck. Leave at least one organic slot for variety, and
    # prefer the highest-weighted (best-fit) priority template first. The default
    # scenario has no priority, so ordinary play is untouched.
    priority_present = sorted(
        (template_id for template_id in scenario.docket_priority if template_id in available),
        key=lambda template_id: (-available[template_id], template_id),
    )
    reserved = max(0, count - 1) if count > 1 else count
    for template_id in priority_present[:reserved]:
        chosen.append(template_id)
        available.pop(template_id, None)
    while available and len(chosen) < count:
        # Weighted sampling without replacement: pick a point in [0, total) and
        # walk the cumulative weight sum until we pass it (sorted for determinism
        # given the seeded rng). Higher-weighted templates own a wider slice.
        total = sum(available.values())
        pick = rng.randrange(total)
        running = 0
        selected = next(iter(available))
        for template_id, weight in sorted(available.items()):
            running += weight
            if pick < running:
                selected = template_id
                break
        chosen.append(selected)
        available.pop(selected)
    return chosen


def _district_mix_key(districts: dict[str, DistrictProfile] | None) -> str:
    """Return a stable type+culture distribution signature.

    Encodes both the district-type histogram (e.g. "civic:3,natural:2") and the
    dominant-culture histogram (e.g. "artists:4") so two boards with the same
    types but different population mixes generate different dockets, while a given
    board stays deterministic.
    """

    if not districts:
        return "none"
    type_counts: dict[str, int] = {}
    culture_counts: dict[str, int] = {}
    for profile in districts.values():
        type_counts[profile.district_type] = type_counts.get(profile.district_type, 0) + 1
        for group, band in (profile.population_mix or {}).items():
            if int(band or 0) >= CULTURE_DOMINANT_BAND:
                culture_counts[group] = culture_counts.get(group, 0) + 1
    type_key = ",".join(f"{key}:{type_counts[key]}" for key in sorted(type_counts))
    culture_key = ",".join(f"{key}:{culture_counts[key]}" for key in sorted(culture_counts))
    return f"{type_key}|{culture_key}"


def _project_due_items(turn: int, projects: Iterable[ProjectRecord] | dict[str, ProjectRecord] | None) -> list[DocketItem]:
    """Convert due project records into docket items for the current turn."""

    if not projects:
        return []
    records = projects.values() if isinstance(projects, dict) else projects
    due: list[tuple[int, str, ProjectRecord, ProjectStepTemplate]] = []
    for project in records:
        if project.status not in ("active", "delayed", "overdue"):
            continue
        if project.due_turn > turn:
            continue
        step = project_step_template(project.chain_template_id, project.current_step_id)
        if step:
            due.append((project.due_turn, project.project_id, project, step))
    out: list[DocketItem] = []
    # Each due project step reuses its template, but carries project ids and
    # targets forward so the decision resolver can advance the chain.
    for idx, (_due_turn, _project_id, project, step) in enumerate(sorted(due, key=lambda row: (row[0], row[1])), start=1):
        item = _make_docket_item(turn, idx, step.template_id, stakeholder=step.stakeholder or project.stakeholder, origin_item_id=f"project:{project.project_id}")
        item.project_id = project.project_id
        item.chain_step_id = step.step_id
        item.target_cell_ids = list(project.target_cell_ids)
        if project.due_turn < turn:
            item.preview_text = f"{item.preview_text} Project step is overdue from turn {project.due_turn}."
        else:
            item.preview_text = f"{item.preview_text} Project step due this turn."
        out.append(item)
    return out


def _carried_docket_items(
    turn: int,
    carried_items: Iterable[DocketItem] | None,
    limit: int,
    start_idx: int = 1,
) -> list[DocketItem]:
    """Clone carried mandatory docket work into the current week's docket."""

    if not carried_items or limit <= 0:
        return []
    out: list[DocketItem] = []
    local_incident_identities: set[str] = set()
    for carried in carried_items:
        if len(out) >= limit:
            break
        if carried.status != "carried" or carried.template_id not in TEMPLATES:
            continue
        incident_identity, incident_cell_id, incident_group = incident_identity_from_item(carried)
        if carried.template_id == CIVIC_INCIDENT_TEMPLATE_ID and incident_identity:
            if incident_identity in local_incident_identities:
                continue
            local_incident_identities.add(incident_identity)
        item = _make_docket_item(
            turn,
            start_idx + len(out),
            carried.template_id,
            stakeholder=carried.stakeholder,
            origin_item_id=carried.origin_item_id,
        )
        item.title = carried.title
        item.geometry_type = carried.geometry_type
        item.status = "open"
        item.target_cell_ids = list(carried.target_cell_ids)
        item.stakeholder = carried.stakeholder
        item.origin_item_id = carried.origin_item_id
        item.target_rule = carried.target_rule
        item.project_id = carried.project_id
        item.chain_step_id = carried.chain_step_id
        item.priority = carried.priority
        item.due_turn = carried.due_turn
        item.subject_feature_id = carried.subject_feature_id
        item.case_json = copy.deepcopy(carried.case_json)
        if carried.template_id == CIVIC_INCIDENT_TEMPLATE_ID and incident_identity:
            write_incident_case_identity(item, incident_cell_id, incident_group)
        carry_text = "Carried forward from prior week."
        item.preview_text = f"{carried.preview_text} {carry_text}".strip() if carried.preview_text else carry_text
        out.append(item)
    return out


def _pending_momentum_followup_items(
    turn: int,
    state: CityState | None,
    limit: int,
    start_idx: int = 1,
) -> list[DocketItem]:
    """Convert pending momentum follow-ups into current docket items."""

    if not state or not state.pending_followups or limit <= 0:
        return []
    out: list[DocketItem] = []
    consumed: list[str] = []
    for idx, origin_item_id in enumerate(sorted(state.pending_followups), start=start_idx):
        if len(out) >= limit:
            break
        template_id = state.pending_followups[origin_item_id]
        if template_id not in TEMPLATES:
            consumed.append(origin_item_id)
            continue
        item = _make_docket_item(turn, idx, template_id, origin_item_id=f"momentum:{origin_item_id}")
        item.preview_text = f"{item.preview_text} Follow-up from unattended city momentum."
        item.priority = max(item.priority, 2)
        out.append(item)
        consumed.append(origin_item_id)
    for origin_item_id in consumed:
        state.pending_followups.pop(origin_item_id, None)
    return out


def _make_docket_item(turn: int, idx: int, template_id: str, stakeholder: str = "", origin_item_id: str = "") -> DocketItem:
    """Instantiate a docket item and specialize follow-up case labels."""

    template = TEMPLATES[template_id]
    item_stakeholder = stakeholder or template.stakeholder
    item_id = f"T{turn:02d}-{idx:02d}-{template_id}"
    title = template.title
    preview = template.preview
    if template.is_enforcement and item_stakeholder != template.stakeholder:
        label = item_stakeholder.replace("_", " ").title()
        title = f"Compliance Follow-Up: {label}"
        item_id = f"T{turn:02d}-{idx:02d}-{template_id}-{item_stakeholder}"
        preview = f"{template.preview} Stakeholder heat is now focused on {label}."
    elif template.is_incident and item_stakeholder != template.stakeholder:
        label = _group_label(item_stakeholder).title()
        title = f"Civic Incident Response: {label}"
        item_id = f"T{turn:02d}-{idx:02d}-{template_id}-{item_stakeholder}"
        preview = f"{template.preview} Incident file is currently attached to {label}."
    return DocketItem(
        item_id=item_id,
        template_id=template_id,
        title=title,
        geometry_type=template.geometry_type,
        turn=turn,
        preview_text=preview,
        stakeholder=item_stakeholder,
        origin_item_id=origin_item_id,
        target_rule=template.target_rule,
        chain_step_id=template.project_step_id,
    )


def _heat_followup_item(turn: int, state: CityState, idx: int = 1) -> DocketItem | None:
    """Return the highest-priority stakeholder heat follow-up due this turn."""

    hot = []
    for stakeholder, heat in state.stakeholder_heat.items():
        profile = _stakeholder_profile(stakeholder)
        last_issued = state.stakeholder_memory.get(heat_followup_memory_key(stakeholder))
        cooled = last_issued is None or turn > int(last_issued) + profile.cooldown_turns
        if heat >= profile.escalation_threshold and cooled:
            hot.append((stakeholder, heat, _stakeholder_escalation_score(stakeholder, heat)))
    if not hot:
        return None
    stakeholder, _heat, _score = sorted(hot, key=lambda row: (-row[2], row[0]))[0]
    template_id = _stakeholder_profile(stakeholder).followup_template_id
    if template_id not in TEMPLATES:
        template_id = ENFORCEMENT_TEMPLATE_ID
    return _make_docket_item(turn, idx, template_id, stakeholder=stakeholder, origin_item_id="stakeholder_heat")


def _maintenance_followup_item(turn: int, active_features: Iterable[FeatureInstance], idx: int = 1) -> DocketItem | None:
    """Return the most urgent feature maintenance item, if one is due."""

    candidates = []
    for feature in active_features:
        normalize_feature_instance(feature, turn)
        if feature.status in ("maintenance_due", "degraded", "failed"):
            candidates.append((feature.condition, feature.feature_id, feature))
    if not candidates:
        return None
    _condition, _feature_id, feature = sorted(candidates, key=lambda row: (row[0], row[1]))[0]
    item = _make_docket_item(turn, idx, MAINTENANCE_TEMPLATE_ID, stakeholder=feature.owner_group or "maintenance_office", origin_item_id=f"feature:{feature.feature_id}")
    item.title = f"Maintenance Order: {feature.archetype_id.replace('_', ' ').title()}"
    item.subject_feature_id = feature.feature_id
    item.target_cell_ids = list(feature.target_cell_ids)
    item.priority = 3 if feature.status == "failed" else 2 if feature.status == "degraded" else 1
    item.due_turn = turn + 1
    item.case_json = {
        "maintenance": {
            "feature_id": feature.feature_id,
            "archetype_id": feature.archetype_id,
            "condition": feature.condition,
            "status": feature.status,
        }
    }
    item.preview_text = f"{item.preview_text} Feature {feature.feature_id} is {feature.status} at condition {feature.condition}."
    return item


def _incident_identities_for_items(items: Iterable[DocketItem]) -> set[str]:
    """Return local civic incident identities already represented on the docket."""

    identities: set[str] = set()
    for item in items:
        if item.template_id != CIVIC_INCIDENT_TEMPLATE_ID:
            continue
        identity, _cell_id, _group = incident_identity_from_item(item)
        if identity:
            identities.add(identity)
    return identities


def _incident_followup_item(
    turn: int,
    districts: dict[str, DistrictProfile],
    idx: int = 1,
    skip_identities: Iterable[str] = (),
) -> DocketItem | None:
    """Return the earliest visible local grievance that needs civic response."""

    visible = []
    skip = set(skip_identities or ())
    for profile in districts.values():
        normalize_profile(profile)
        if profile.incident_state != "none" and profile.incident_group:
            identity = incident_identity(profile.cell_id, profile.incident_group)
            if identity not in skip:
                visible.append((profile.incident_state, profile.incident_group, profile.cell_id))
    if not visible:
        return None
    incident_state, group, cell_id = sorted(visible, key=lambda row: (row[2], row[1], row[0]))[0]
    item = _make_docket_item(turn, idx, CIVIC_INCIDENT_TEMPLATE_ID, stakeholder=group, origin_item_id=f"dissatisfaction:{cell_id}:{group}")
    item.target_cell_ids = [cell_id]
    write_incident_case_identity(item, cell_id, group, incident_state)
    label = district_label(districts[cell_id]) if cell_id in districts else cell_id
    item.preview_text = f"{item.preview_text} Visible condition: {incident_state} in {label}."
    return item


def inspect_item(item: DocketItem, seed: int = 2026, target_profiles: Iterable[DistrictProfile] | None = None) -> DocketItem:
    """Attach deterministic inspection evidence and risk to a docket item."""

    template = TEMPLATES[item.template_id]
    rng = random.Random(f"{seed}:{item.item_id}:inspect")
    profiles = list(target_profiles or ())
    item.inspected = True
    item.stakeholder = item.stakeholder or template.stakeholder
    item.target_rule = item.target_rule or template.target_rule
    item.risk_band = rng.choice(["low", "medium", "medium", "high"])
    inspection_case = inspection_case_for_item(item, profiles, seed=seed, fallback_risk_band=item.risk_band)
    item.risk_band = str(inspection_case["risk_band"])
    item.case_json = dict(item.case_json or {})
    item.case_json["inspection"] = inspection_case
    failure_text = f" Failure mode on file: {template.failure_mode}." if template.failure_mode else ""
    population_text = inspection_population_note(template, profiles)
    evidence_text = _inspection_summary_text(inspection_case)
    consequence_text = _inspection_consequence_text(template, inspection_case)
    item.preview_text = (
        f"Inspection: filed {item.risk_band.upper()} side-effect risk. Certain effects if approved: "
        f"{_format_delta(template.base_effects)}. Risk/side effects: {consequence_text}{failure_text} "
        f"{template.inspect_hint} {population_text} {evidence_text}"
    ).strip()
    return item


def inspection_case_for_item(
    item: DocketItem,
    target_profiles: Iterable[DistrictProfile],
    seed: int = 2026,
    fallback_risk_band: str = "medium",
) -> dict[str, object]:
    """Build structured inspection evidence and violations for a docket item."""

    template = TEMPLATES[item.template_id]
    rule = INSPECTION_RULES.get(template.template_id, INSPECTION_RULES["default"])
    profiles = list(target_profiles)
    rng = random.Random(f"{seed}:{item.item_id}:evidence")
    avg_exposure = sum(profile.exposure for profile in profiles) / max(1, len(profiles))
    avg_services = sum(profile.services for profile in profiles) / max(1, len(profiles))
    max_grievance = max((_top_dissatisfaction(profile)[1] for profile in profiles), default=0)

    # Evidence codes are catalog data; this branch translates each code into
    # context-sensitive severity without making templates carry game math.
    evidence: list[EvidenceRecord] = []
    for code in rule.evidence_codes:
        severity = "watch"
        note = "Routine file check."
        if code == "service_gap":
            if avg_services < 25:
                severity = "critical"
                note = "Service capacity is already below the operating floor."
            elif avg_services < 40:
                severity = "warning"
                note = "Service capacity is thin enough to affect compliance."
            else:
                note = "Service capacity can absorb the request."
        elif code == "unsafe_work":
            if avg_exposure >= 65:
                severity = "critical"
                note = "Site exposure is high enough to require follow-through."
            elif avg_exposure >= 45:
                severity = "warning"
                note = "Site exposure is visible in the inspection worksheet."
            else:
                note = "No acute site safety concern is visible."
        elif code == "public_nuisance":
            if max_grievance >= DISSATISFACTION_INCIDENT_THRESHOLD:
                severity = "critical"
                note = "Local grievance is already incident-ready."
            elif max_grievance >= DISSATISFACTION_AGGRIEVED_THRESHOLD:
                severity = "warning"
                note = "Local grievance is aggrieved."
            elif rng.random() < 0.35:
                severity = "watch"
                note = "Public comment is noisy but not decisive."
        elif code == "maintenance_overdue":
            severity = "warning"
            note = "Referenced support feature has an open maintenance condition."
        elif rng.random() < 0.2:
            severity = "warning"
            note = "The file is mostly consistent but needs one assumption checked."
        label = VIOLATION_CODES.get(code, {}).get("label", code.replace("_", " ").title())
        evidence.append(EvidenceRecord(f"{item.item_id}:{code}", label, severity, code, note))

    # Risk bands intentionally derive from evidence, then violations inherit
    # the band so inspections and later compliance audits agree.
    severity_weights = {"watch": 1, "warning": 2, "critical": 4}
    severity_score = 0
    has_critical = False
    has_warning = False
    for record in evidence:
        severity_score += severity_weights.get(record.severity, 1)
        if record.severity == "critical":
            has_critical = True
        elif record.severity == "warning":
            has_warning = True
    if has_critical or severity_score >= 6:
        risk_band = "high"
    elif has_warning or severity_score >= 3:
        risk_band = "medium"
    else:
        risk_band = fallback_risk_band if fallback_risk_band in {"low", "medium", "high"} else "low"

    violations: list[ViolationRecord] = []
    if risk_band in {"medium", "high"}:
        severity = "critical" if risk_band == "high" else "warning"
        deadline = item.turn + (1 if severity == "critical" else rule.deadline_turns)
        for code in rule.violation_codes:
            evidence_ids = tuple(record.evidence_id for record in evidence if record.source == code)
            violations.append(
                ViolationRecord(
                    f"{item.item_id}:{code}",
                    code,
                    severity,
                    deadline,
                    evidence_ids=evidence_ids,
                )
            )

    return {
        "rule_id": rule.rule_id,
        "risk_band": risk_band,
        "evidence": [record.__dict__ for record in evidence],
        "violations": [record.__dict__ | {"evidence_ids": list(record.evidence_ids)} for record in violations],
    }


def _inspection_summary_text(inspection_case: dict[str, object]) -> str:
    """Format structured inspection evidence into short packet text."""

    evidence = inspection_case.get("evidence", [])
    violations = inspection_case.get("violations", [])
    if not evidence:
        return ""
    worst = "watch"
    for record in evidence:
        if isinstance(record, dict) and record.get("severity") == "critical":
            worst = "critical"
            break
        if isinstance(record, dict) and record.get("severity") == "warning":
            worst = "warning"
    violation_text = f" Violations opened: {len(violations)}." if violations else ""
    return f"Evidence filed: {len(evidence)} item(s), highest severity {worst}.{violation_text}"


def _inspection_consequence_text(template: DocketTemplate, inspection_case: dict[str, object]) -> str:
    """Format sharper post-inspection consequence hints for previews."""

    exposure = str(inspection_case.get("risk_band") or "unknown").lower()
    violations = inspection_case.get("violations", [])
    if exposure == "high":
        base = f"{template.failure_mode or 'approval failure'} is a live exposure"
    elif exposure == "medium":
        base = f"{template.failure_mode or 'side effects'} should be watched"
    elif exposure == "low":
        base = "no acute side-effect flag"
    else:
        base = "side-effect review is incomplete"
    if violations:
        return f"{base}; {len(violations)} compliance deadline(s) may follow."
    return f"{base}; no compliance deadline opened."


__all__ = [name for name in globals() if not name.startswith("__")]
