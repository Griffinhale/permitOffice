"""Shared rule helpers for district state, population pressure, and effects."""

from __future__ import annotations

import random
from typing import Iterable

from .models import *
from .catalogs import *

def display_state_for_profile(profile: DistrictProfile) -> str:
    """Return the map-facing primary pressure cause for a district profile.

    Rules-facing stat reference: activity is economic activity, friction is
    visible civic friction, trust is trust/cohesion, exposure is exposure, and
    services are local capacity. Display state shows the leading cause, while
    district attributes retain severity and type.
    """
    if profile.incident_state != "none":
        return "incident"
    if _top_dissatisfaction(profile)[1] >= DISSATISFACTION_AGGRIEVED_THRESHOLD:
        return "grievance"
    if max((int(gap or 0) for gap in (profile.service_gap or {}).values()), default=0) >= 30:
        return "service_gap"
    if max((int(band or 0) for band in (profile.hazards or {}).values()), default=0) >= 2:
        return "hazard"
    if max((int(band or 0) for band in (profile.displacement or {}).values()), default=0) >= 2:
        return "housing_pressure"
    if profile.activity >= 65:
        return "economic_growth"
    return "stable"


def district_prosperity_band(profile: DistrictProfile) -> str:
    """Graduated per-district prosperity band from the four core metrics.

    Activity + trust read positive, friction + exposure negative (the dashboard
    City Health index, per-district). Derived display band only; the four core
    metrics stay the source of truth.
    """
    score = (int(profile.activity or 0) + int(profile.trust or 0)
             + (100 - int(profile.friction or 0)) + (100 - int(profile.exposure or 0))) / 4
    if score >= 65:
        return "thriving"
    if score >= 45:
        return "stable"
    if score >= 30:
        return "strained"
    return "failing"




def heat_followup_memory_key(stakeholder: str) -> str:
    """Return the stakeholder_memory key holding the turn a heat follow-up was last issued."""

    return f"followup:{stakeholder}"


def record_heat_followups(state: CityState, items: Iterable[DocketItem]) -> None:
    """Remember the turn of each closing week's heat follow-up so the cooldown can hold.

    Recorded at week close rather than when the docket is generated, so a
    docket regenerated within the same week still offers the same follow-up.
    """

    for item in items:
        if item.origin_item_id != "stakeholder_heat" or not item.stakeholder:
            continue
        key = heat_followup_memory_key(item.stakeholder)
        state.stakeholder_memory[key] = max(int(state.stakeholder_memory.get(key, 0) or 0), int(item.turn))


def _stakeholder_profile(stakeholder: str) -> StakeholderProfile:
    """Return a configured or synthetic profile for stakeholder heat math."""
    if stakeholder in STAKEHOLDERS:
        return STAKEHOLDERS[stakeholder]
    if stakeholder in CITIZEN_GROUPS:
        return StakeholderProfile(stakeholder, influence=2, patience=1, interests=(stakeholder,), followup_template_id=CIVIC_INCIDENT_TEMPLATE_ID)
    return StakeholderProfile(stakeholder or "general_public")


def _stakeholder_escalation_score(stakeholder: str, heat: int) -> int:
    """Score stakeholder heat after accounting for influence and patience."""
    profile = _stakeholder_profile(stakeholder)
    return heat * profile.influence - profile.patience


def adjust_stakeholder_pressure(state: CityState, stakeholder: str, amount: int, reason: str = "") -> int:
    """Apply stakeholder heat, including patience and coalition side effects."""
    if not stakeholder or amount == 0:
        return 0
    profile = _stakeholder_profile(stakeholder)
    adjusted = amount
    if amount == 1 and profile.patience >= 4:
        adjusted = 0
    before = state.stakeholder_heat.get(stakeholder, 0)
    after = max(0, before + adjusted)
    if after:
        state.stakeholder_heat[stakeholder] = after
    else:
        state.stakeholder_heat.pop(stakeholder, None)
    if adjusted > 0 and adjusted >= 2:
        # Large heat changes ripple to allies and rivals so follow-ups feel political.
        for ally in profile.allies:
            if ally != stakeholder:
                state.stakeholder_heat[ally] = max(0, state.stakeholder_heat.get(ally, 0) + 1)
        for rival in profile.rivals:
            if rival != stakeholder:
                current = state.stakeholder_heat.get(rival, 0)
                if current > 0:
                    state.stakeholder_heat[rival] = max(0, current - 1)
    return after - before


def heat_summary(state: CityState) -> str:
    """Format the hottest stakeholder records for compact dashboard display."""
    hot = [(stakeholder, heat) for stakeholder, heat in state.stakeholder_heat.items() if heat > 0]
    if not hot:
        return "none"
    return ", ".join(f"{stakeholder.replace('_', ' ')} {heat}" for stakeholder, heat in sorted(hot, key=lambda pair: (-pair[1], pair[0]))[:3])




def feature_archetype_for_template(template_or_id: DocketTemplate | str) -> FeatureArchetype:
    """Look up the feature archetype spawned by a docket template."""
    template = TEMPLATES[template_or_id] if isinstance(template_or_id, str) else template_or_id
    archetype_id = template.spawn_archetype_id or template.template_id
    try:
        return FEATURE_ARCHETYPES[archetype_id]
    except KeyError as exc:
        raise KeyError(f"Template {template.template_id!r} references unknown feature archetype {archetype_id!r}.") from exc




def feature_metadata_for_template(template_or_id: DocketTemplate | str) -> dict[str, object]:
    """Return ArcGIS-safe metadata for the feature spawned by a template."""
    archetype = feature_archetype_for_template(template_or_id)
    return {
        "archetype_id": archetype.archetype_id,
        "family": archetype.family,
        "service_type": archetype.service_type,
        "network_type": archetype.network_type,
        "network_strength": archetype.network_strength,
        "coverage_radius_m": archetype.coverage_radius_m,
        "capacity": archetype.capacity,
        "land_use": archetype.land_use,
        "hazard_effects": dict(archetype.hazard_effects),
        "mitigation_effects": dict(archetype.mitigation_effects),
        "housing_effects": dict(archetype.housing_effects),
        "starts_chain_id": archetype.starts_chain_id,
        "allowed_district_types": list(archetype.allowed_district_types),
        "conflict_district_types": list(archetype.conflict_district_types),
        "incident_type": archetype.incident_type,
    }


def validate_feature_catalog() -> list[str]:
    """Return catalog consistency errors without mutating gameplay state."""
    errors: list[str] = []
    # Feature archetypes are the spatial contract that templates and lifecycle
    # rules depend on, so validate their identifiers and typed references first.
    for archetype_id, archetype in FEATURE_ARCHETYPES.items():
        if archetype.archetype_id != archetype_id:
            errors.append(f"{archetype_id}: archetype_id mismatch")
        if archetype.family not in FEATURE_FAMILIES:
            errors.append(f"{archetype_id}: invalid family {archetype.family!r}")
        if archetype.geometry_type not in {"POINT", "LINE", "POLYGON"}:
            errors.append(f"{archetype_id}: invalid geometry {archetype.geometry_type!r}")
        if archetype.service_type and archetype.service_type not in SERVICE_TYPES:
            errors.append(f"{archetype_id}: invalid service type {archetype.service_type!r}")
        if archetype.network_type and archetype.network_type not in SERVICE_TYPES:
            errors.append(f"{archetype_id}: invalid network type {archetype.network_type!r}")
        for hazard in archetype.hazard_effects:
            if hazard not in HAZARD_TYPES:
                errors.append(f"{archetype_id}: invalid hazard effect {hazard!r}")
        for hazard in archetype.mitigation_effects:
            if hazard not in HAZARD_TYPES:
                errors.append(f"{archetype_id}: invalid mitigation effect {hazard!r}")
        if archetype.starts_chain_id and archetype.starts_chain_id not in PROJECT_CHAINS:
            errors.append(f"{archetype_id}: unknown starts_chain_id {archetype.starts_chain_id!r}")
        if archetype_id not in FEATURE_OPERATING_RULES:
            errors.append(f"{archetype_id}: missing operating rule")
    for archetype_id, rule in FEATURE_OPERATING_RULES.items():
        if rule.archetype_id != archetype_id:
            errors.append(f"{archetype_id}: operating rule id mismatch")
        if archetype_id not in FEATURE_ARCHETYPES:
            errors.append(f"{archetype_id}: operating rule has no archetype")
    # Docket templates must resolve to real feature, stakeholder, project, and
    # scenario records before the ArcGIS toolbox can safely materialize them.
    for template_id, template in TEMPLATES.items():
        if not template.spawn_archetype_id:
            errors.append(f"{template_id}: missing spawn_archetype_id")
        elif template.spawn_archetype_id not in FEATURE_ARCHETYPES:
            errors.append(f"{template_id}: unknown spawn archetype {template.spawn_archetype_id!r}")
        else:
            archetype = FEATURE_ARCHETYPES[template.spawn_archetype_id]
            if archetype.geometry_type != template.geometry_type:
                errors.append(f"{template_id}: archetype geometry {archetype.geometry_type!r} != template geometry {template.geometry_type!r}")
        if template.stakeholder not in STAKEHOLDERS and template.stakeholder not in CITIZEN_GROUPS:
            errors.append(f"{template_id}: stakeholder {template.stakeholder!r} missing profile")
        for hazard in template.hazard_effects:
            if hazard not in HAZARD_TYPES:
                errors.append(f"{template_id}: invalid hazard effect {hazard!r}")
        if template.starts_chain_id and template.starts_chain_id not in PROJECT_CHAINS:
            errors.append(f"{template_id}: unknown starts_chain_id {template.starts_chain_id!r}")
        if template.project_step_id:
            chain_ids = [chain_id for chain_id, chain in PROJECT_CHAINS.items() if any(step.step_id == template.project_step_id for step in chain.steps)]
            if template.starts_chain_id:
                chain_ids = [chain_id for chain_id in chain_ids if chain_id == template.starts_chain_id]
            if not chain_ids:
                errors.append(f"{template_id}: unknown project_step_id {template.project_step_id!r}")
        for tag in template.scenario_tags:
            if tag not in SCENARIO_RULES:
                errors.append(f"{template_id}: unknown scenario tag {tag!r}")
    for rule_id, rule in INSPECTION_RULES.items():
        if rule.rule_id != rule_id:
            errors.append(f"{rule_id}: inspection rule id mismatch")
        for code in rule.evidence_codes + rule.violation_codes:
            if code not in VIOLATION_CODES:
                errors.append(f"{rule_id}: unknown inspection code {code!r}")
    # Long-running systems share hazards, project chains, and scenario ids; a
    # missing reference here would surface later as a turn-resolution failure.
    for hazard_type, rule in HAZARD_RULES.items():
        if rule.hazard_type != hazard_type:
            errors.append(f"{hazard_type}: hazard_type mismatch")
        for group in rule.affected_groups:
            if group not in CITIZEN_GROUPS:
                errors.append(f"{hazard_type}: invalid affected group {group!r}")
        for service in rule.mitigation_service_types:
            if service not in SERVICE_TYPES:
                errors.append(f"{hazard_type}: invalid mitigation service {service!r}")
    for chain_id, chain in PROJECT_CHAINS.items():
        step_ids = {step.step_id for step in chain.steps}
        if chain.initial_step_id not in step_ids:
            errors.append(f"{chain_id}: initial step missing")
        for step in chain.steps:
            if step.template_id not in TEMPLATES:
                errors.append(f"{chain_id}:{step.step_id}: unknown template {step.template_id!r}")
            if step.next_step_id and step.next_step_id not in step_ids:
                errors.append(f"{chain_id}:{step.step_id}: unknown next step {step.next_step_id!r}")
            if step.failure_step_id and step.failure_step_id not in step_ids:
                errors.append(f"{chain_id}:{step.step_id}: unknown failure step {step.failure_step_id!r}")
    for scenario_id, scenario in SCENARIO_RULES.items():
        if scenario.scenario_id != scenario_id:
            errors.append(f"{scenario_id}: scenario_id mismatch")
        for template_id in scenario.docket_priority:
            if template_id not in TEMPLATES:
                errors.append(f"{scenario_id}: unknown docket priority {template_id!r}")
        for hazard in scenario.hazard_bias:
            if hazard not in HAZARD_TYPES:
                errors.append(f"{scenario_id}: invalid hazard bias {hazard!r}")
    return errors


def total_population(districts: Iterable[DistrictProfile] | dict[str, DistrictProfile]) -> int:
    """Return the non-negative total population across districts."""
    profiles = districts.values() if isinstance(districts, dict) else districts
    return sum(max(0, int(profile.population)) for profile in profiles)


def population_city_summary(districts: Iterable[DistrictProfile] | dict[str, DistrictProfile]) -> str:
    """Format a compact population, incident, and grievance summary."""
    profiles = list(districts.values() if isinstance(districts, dict) else districts)
    if not profiles:
        return "population unavailable"
    incident_count = sum(1 for profile in profiles if profile.incident_state != "none")
    aggrieved_count = sum(1 for profile in profiles if profile.incident_state == "none" and _top_dissatisfaction(profile)[1] >= DISSATISFACTION_AGGRIEVED_THRESHOLD)
    suffix = []
    if incident_count:
        suffix.append(f"{incident_count} incident")
    if aggrieved_count:
        suffix.append(f"{aggrieved_count} aggrieved")
    note = f"; {', '.join(suffix)}" if suffix else ""
    return f"Pop {total_population(profiles)}{note}"


def district_label(profile: DistrictProfile) -> str:
    """Return the player-facing district label, keeping cell_id as the fallback key."""
    return profile.name or profile.cell_id


def incident_summary(districts: Iterable[DistrictProfile] | dict[str, DistrictProfile]) -> str:
    """Format visible civic incident files for dashboard metrics."""
    profiles = list(districts.values() if isinstance(districts, dict) else districts)
    incidents = [
        f"{district_label(profile)} {_group_label(profile.incident_group)} {profile.incident_state}"
        for profile in profiles
        if profile.incident_state != "none" and profile.incident_group
    ]
    if not incidents:
        return "none"
    return ", ".join(incidents[:3])


def target_population_hint(item: DocketItem, target_profiles: Iterable[DistrictProfile]) -> str:
    """Summarize affected groups and grievances for an item preview."""
    profiles = list(target_profiles)
    if not profiles:
        return ""
    template = TEMPLATES[item.template_id]
    visible_groups = _top_presence_groups(profiles, limit=3)
    supporters = _present_template_groups(profiles, template.supporter_groups)
    concerned = _present_template_groups(profiles, template.concerned_groups)
    grievance_group, grievance_band = _top_dissatisfaction_for_profiles(profiles)
    parts = [f"Target census note: strongest public mix is {_join_group_labels(visible_groups)}."]
    if supporters:
        parts.append(f"Likely affirmative comments from {_join_group_labels(supporters)}.")
    if concerned:
        parts.append(f"Likely formal objections from {_join_group_labels(concerned)}.")
    if grievance_band >= DISSATISFACTION_AGGRIEVED_THRESHOLD:
        parts.append(f"Existing grievance file: {_group_label(grievance_group)} are {GRIEVANCE_BAND_LABELS[grievance_band]}.")
    return " ".join(parts)


def inspection_population_note(template: DocketTemplate, target_profiles: Iterable[DistrictProfile]) -> str:
    """Build the population context sentence appended to inspection text."""
    profiles = list(target_profiles)
    archetype = feature_archetype_for_template(template)
    if not profiles:
        interested = template.supporter_groups[:2] + template.concerned_groups[:2]
        if not interested:
            return "Census review awaits a selected district."
        return f"Census review: likely interested parties include {_join_group_labels(interested)}."
    supporter = _strongest_template_group(profiles, template.supporter_groups)
    objector = _strongest_template_group(profiles, template.concerned_groups)
    grievance_group, grievance_band = _top_dissatisfaction_for_profiles(profiles)
    avg_services = sum(profile.services for profile in profiles) / max(1, len(profiles))
    service_text = "service capacity appears adequate" if avg_services >= 45 else "service capacity is limited"
    parts = [f"Census review: {service_text}."]
    if archetype.service_type:
        avg_gap = sum(profile.service_gap.get(archetype.service_type, 0) for profile in profiles) / max(1, len(profiles))
        if avg_gap:
            parts.append(f"Coverage note: {archetype.service_type.replace('_', ' ')} gap remains on the worksheet.")
        else:
            parts.append(f"Coverage note: {archetype.service_type.replace('_', ' ')} fit is acceptable.")
    fit_note = _land_use_note(archetype, profiles)
    if fit_note:
        parts.append(fit_note)
    if supporter:
        parts.append(f"Strongest likely supporter: {_group_label(supporter)}.")
    if objector:
        parts.append(f"Strongest likely objector: {_group_label(objector)}.")
    if grievance_band:
        parts.append(f"Highest local grievance: {_group_label(grievance_group)} are {GRIEVANCE_BAND_LABELS[grievance_band]}.")
    return " ".join(parts)


def normalize_profile(profile: DistrictProfile) -> DistrictProfile:
    """Clamp derived district fields and refresh public-facing profile text."""
    if not profile.land_use:
        profile.land_use = LAND_USE_BY_DISTRICT_TYPE.get(profile.district_type, "mixed_use")
    profile.zoning_overlay = profile.zoning_overlay or ""
    profile.adjacent_cell_ids = sorted({str(cid) for cid in (profile.adjacent_cell_ids or ()) if cid and str(cid) != profile.cell_id})
    profile.network_access = _normalize_service_map(profile.network_access, SERVICE_TYPES, maximum=12, include_zeros=True)
    profile.hazards = _normalize_service_map(profile.hazards, HAZARD_TYPES, maximum=4, include_zeros=False)
    profile.population_mix = _normalize_bands(profile.population_mix, 3)
    if not any(profile.population_mix.values()):
        profile.population_mix = _normalize_bands(ARCHETYPE_BASE_MIX.get(profile.district_type, {}), 3)
    profile.dissatisfaction = _normalize_bands(profile.dissatisfaction, 4)
    for group, band in profile.population_mix.items():
        if band > 0 and group not in profile.dissatisfaction:
            profile.dissatisfaction[group] = 0
    _normalize_housing(profile)
    profile.service_gap = _service_gap_for_profile(profile)
    _refresh_incident(profile)
    profile.public_profile = public_profile_for(profile)
    profile.display_state = display_state_for_profile(profile)
    profile.prosperity_band = district_prosperity_band(profile)
    return profile


def public_profile_for(profile: DistrictProfile) -> str:
    """Build the public-facing census summary for a district."""
    groups = _top_presence_groups([profile], limit=3)
    if not groups:
        return "Public profile: no census emphasis filed."
    return f"Public profile: {_join_group_labels(groups)} are the main census groups."


def compact_group_bands(bands: dict[str, int]) -> dict[str, int]:
    """Return normalized non-zero group bands for persistence."""
    return {group: int(value) for group, value in _normalize_bands(bands, 4).items() if value > 0}


def _unique_known(values: Iterable[str], districts: dict[str, DistrictProfile]) -> list[str]:
    """Keep known district ids once while preserving first-seen order."""
    seen = set()
    out = []
    for value in values:
        try:
            known = value in districts
            already_seen = value in seen
        except TypeError:
            continue
        if known and not already_seen:
            seen.add(value)
            out.append(value)
    return out


def _initial_population_mix(district_type: str, rng: random.Random) -> dict[str, int]:
    """Create a deterministic census mix seeded from the district archetype."""
    mix = dict(ARCHETYPE_BASE_MIX.get(district_type, {}))
    spice_pool = [group for group in CITIZEN_GROUPS if group not in mix]
    for group in rng.sample(spice_pool, k=2):
        mix[group] = max(mix.get(group, 0), 1)
    if rng.random() < 0.35:
        group = rng.choice(tuple(mix))
        mix[group] = min(3, mix[group] + 1)
    return _normalize_bands(mix, 3)


def _initial_dissatisfaction(population_mix: dict[str, int], activity: int, friction: int, exposure: int, services: int) -> dict[str, int]:
    """Seed grievance bands from initial city metrics and local population mix."""
    dissatisfaction = {group: 0 for group, band in population_mix.items() if band > 0}
    if friction >= 40:
        _adjust_dissatisfaction_map(dissatisfaction, ("renters", "workers", "students"), 1)
    if exposure >= 45:
        _adjust_dissatisfaction_map(dissatisfaction, ("families", "elders", "workers"), 1)
    if services < 25:
        _adjust_dissatisfaction_map(dissatisfaction, ("families", "commuters", "civil_servants"), 1)
    if activity < 30:
        _adjust_dissatisfaction_map(dissatisfaction, ("renters", "vendors", "workers"), 1)
    return _normalize_bands(dissatisfaction, 4)


def _normalize_bands(bands: dict[str, int] | None, maximum: int) -> dict[str, int]:
    """Clamp all citizen-group bands and include missing groups at zero."""
    out = {group: 0 for group in CITIZEN_GROUPS}
    for group, value in (bands or {}).items():
        if group not in out:
            continue
        out[group] = max(0, min(maximum, int(value or 0)))
    return out


def _normalize_service_map(
    values: dict[str, int] | None,
    allowed: Iterable[str],
    maximum: int,
    include_zeros: bool = False,
) -> dict[str, int]:
    """Clamp a typed service or hazard map to allowed keys and bands."""
    allowed_tuple = tuple(allowed)
    allowed_keys = frozenset(allowed_tuple)
    out = {key: 0 for key in allowed_tuple} if include_zeros else {}
    for key, value in (values or {}).items():
        if key not in allowed_keys:
            continue
        band = max(0, min(maximum, int(value or 0)))
        if band or include_zeros:
            out[key] = band
    return out


def _normalize_housing(profile: DistrictProfile) -> None:
    """Fill derived housing fields and displacement pressure on a profile."""
    # Missing capacity and affordability values are inferred from district type
    # so old saves and generated profiles enter the same housing model.
    if profile.housing_capacity <= 0:
        vacancy_seed = {
            "residential": 14,
            "mercantile": 9,
            "industrial": 5,
            "civic": 7,
            "academic": 11,
            "natural": 4,
        }.get(profile.district_type, 8)
        profile.housing_capacity = max(profile.population + 50, round(profile.population * (100 + vacancy_seed) / 100))
    profile.housing_capacity = max(0, int(profile.housing_capacity))
    if profile.affordability <= 0:
        profile.affordability = {
            "residential": 58,
            "mercantile": 48,
            "industrial": 42,
            "civic": 52,
            "academic": 50,
            "natural": 44,
        }.get(profile.district_type, 50)
    profile.affordability = _clamp(profile.affordability)
    # Vacancy and displacement are derived together so dashboards and follow-up
    # logic read one consistent housing pressure snapshot.
    if profile.housing_capacity:
        profile.vacancy_rate = max(0, min(100, round((profile.housing_capacity - profile.population) * 100 / profile.housing_capacity)))
    else:
        profile.vacancy_rate = 0
    pressure = _displacement_pressure(profile)
    previous = {group: max(0, min(4, int(value or 0))) for group, value in (profile.displacement or {}).items() if group in CITIZEN_GROUPS}
    displacement: dict[str, int] = {}
    for group in ("renters", "elders", "artists", "families"):
        band = max(previous.get(group, 0), pressure if profile.population_mix.get(group, 0) else max(0, pressure - 1))
        if band:
            displacement[group] = min(4, band)
    profile.displacement = displacement


def _displacement_pressure(profile: DistrictProfile) -> int:
    """Convert housing, activity, exposure, and service context into pressure."""
    pressure = 0
    if profile.vacancy_rate <= 3:
        pressure += 2
    elif profile.vacancy_rate <= 7:
        pressure += 1
    if profile.affordability < 35:
        pressure += 2
    elif profile.affordability < 50:
        pressure += 1
    if profile.activity >= 65 or profile.trust >= 65:
        pressure += 1
    if profile.friction >= 55 or profile.exposure >= 55:
        pressure += 1
    if profile.network_access.get("utilities", 0) >= 2:
        pressure -= 1
    if profile.network_access.get("mobility", 0) >= 2:
        pressure -= 1
    if profile.service_gap.get("child_services", 0) == 0 and profile.population_mix.get("families", 0):
        pressure -= 1
    return max(0, min(4, pressure))


def build_grid_adjacency(rows: int, cols: int) -> dict[str, list[str]]:
    """Build four-way adjacency for deterministic generated district grids."""
    adjacency: dict[str, list[str]] = {}
    for row in range(rows):
        for col in range(cols):
            cell_id = f"D{row:02d}{col:02d}"
            neighbors = []
            for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if 0 <= nr < rows and 0 <= nc < cols:
                    neighbors.append(f"D{nr:02d}{nc:02d}")
            adjacency[cell_id] = neighbors
    return adjacency


def assign_grid_adjacency(profiles: Iterable[DistrictProfile], rows: int | None = None, cols: int | None = None) -> dict[str, list[str]]:
    """Assign grid neighbors to profiles and return the resulting adjacency map."""
    profiles_by_id = {profile.cell_id: profile for profile in profiles}
    if rows is None or cols is None:
        parsed = [_parse_grid_cell_id(cell_id) for cell_id in profiles_by_id]
        parsed = [value for value in parsed if value is not None]
        rows = max((row for row, _col in parsed), default=-1) + 1
        cols = max((col for _row, col in parsed), default=-1) + 1
    adjacency = build_grid_adjacency(rows or 0, cols or 0)
    for cell_id, profile in profiles_by_id.items():
        profile.adjacent_cell_ids = [cid for cid in adjacency.get(cell_id, []) if cid in profiles_by_id]
    return {cell_id: list(profile.adjacent_cell_ids) for cell_id, profile in profiles_by_id.items()}


def _parse_grid_cell_id(cell_id: str) -> tuple[int, int] | None:
    """Parse generated district ids into row and column coordinates."""
    if len(cell_id or "") != 5 or not cell_id.startswith("D"):
        return None
    try:
        return int(cell_id[1:3]), int(cell_id[3:5])
    except ValueError:
        return None


def _apply_population_reaction(
    template: DocketTemplate,
    target_profiles: list[DistrictProfile],
    outcome: str,
    mitigated: bool,
) -> dict[str, dict[str, int]]:
    """Apply approval outcome effects to population mix and grievances."""
    deltas: dict[str, dict[str, int]] = {}
    for profile in target_profiles:
        before_population = profile.population
        # Each outcome moves supporters, concerned groups, and population in a
        # different direction before the profile is re-normalized for display.
        if outcome == "approve":
            _adjust_dissatisfaction(profile, template.supporter_groups, -1)
            _adjust_dissatisfaction(profile, template.concerned_groups, 1 if not mitigated else 0)
            _shift_mix(profile, template.growth_groups, 1)
            _shift_mix(profile, template.decline_groups, -1)
            profile.population = max(100, profile.population + _population_decision_delta(profile, template, mitigated))
        elif outcome == "failure":
            _adjust_dissatisfaction(profile, template.concerned_groups or template.supporter_groups, 2 if not mitigated else 1)
            _adjust_dissatisfaction(profile, template.supporter_groups, 1)
            _shift_mix(profile, template.growth_groups, -1)
            profile.population = max(100, profile.population - max(10, profile.population // 90))
        elif outcome == "deny":
            _adjust_dissatisfaction(profile, template.supporter_groups, 1)
            _adjust_dissatisfaction(profile, template.concerned_groups, -1)
        elif outcome == "ignore":
            _adjust_dissatisfaction(profile, template.supporter_groups or (template.stakeholder,), 1)
        normalize_profile(profile)
        population_delta = profile.population - before_population
        if population_delta:
            deltas[profile.cell_id] = {"population": population_delta}
        else:
            deltas[profile.cell_id] = {}
    return deltas


def _dissatisfaction_floor(profile: DistrictProfile, groups: Iterable[str], amount: int) -> dict[str, int]:
    """Return minimum grievance bands after a known reaction amount."""

    profile.dissatisfaction = _normalize_bands(profile.dissatisfaction, 4)
    floors: dict[str, int] = {}
    for group in groups:
        if group not in CITIZEN_GROUPS:
            continue
        floors[group] = max(0, min(4, profile.dissatisfaction.get(group, 0) + amount))
    return floors


def _merge_dissatisfaction_floors(
    floors_by_cell: dict[str, dict[str, int]],
    cell_id: str,
    floors: dict[str, int],
) -> None:
    """Merge per-group grievance floors for later post-system normalization."""

    if not floors:
        return
    current = floors_by_cell.setdefault(cell_id, {})
    for group, floor in floors.items():
        current[group] = max(current.get(group, 0), floor)


def _apply_dissatisfaction_floors(profile: DistrictProfile, floors: dict[str, int]) -> int:
    """Restore minimum grievance bands and return the total dissatisfaction delta."""

    if not floors:
        return 0
    before = sum(_normalize_bands(profile.dissatisfaction, 4).values())
    profile.dissatisfaction = _normalize_bands(profile.dissatisfaction, 4)
    for group, floor in floors.items():
        if group not in CITIZEN_GROUPS:
            continue
        profile.dissatisfaction[group] = max(profile.dissatisfaction.get(group, 0), max(0, min(4, int(floor or 0))))
    normalize_profile(profile)
    return sum(profile.dissatisfaction.values()) - before


def _population_decision_delta(profile: DistrictProfile, template: DocketTemplate, mitigated: bool) -> int:
    """Estimate population growth from a successful growth-oriented template."""
    if not template.growth_groups:
        return 0
    base = max(8, profile.population // 100)
    if profile.services < 25 or profile.exposure > 60:
        base = max(4, base // 2)
    if mitigated:
        base = max(4, round(base * 0.75))
    return base


def _advance_population_pressure(profile: DistrictProfile) -> int:
    """Apply one turn of population drift from activity, exposure, and services."""
    before = profile.population
    delta = 0
    if profile.activity >= 60 and profile.exposure <= 45 and profile.friction <= 45:
        delta += max(6, profile.population // 120)
        _shift_mix(profile, PRESSURE_DRIFT_GROUPS.get(profile.district_type, ()), 1)
    if profile.exposure >= 60 or profile.friction >= 60 or profile.incident_state != "none":
        delta -= max(6, profile.population // 100)
        group, _band = _top_presence_group(profile)
        _shift_mix(profile, (group,), -1)
    if profile.services < 25 and profile.population > 1200:
        _adjust_dissatisfaction(profile, ("families", "commuters", "renters"), 1)
    profile.population = max(100, profile.population + delta)
    normalize_profile(profile)
    return profile.population - before


def _surface_new_incidents(state: CityState, profiles: Iterable[DistrictProfile]) -> int:
    """Refresh profiles and add friction when grievances become visible incidents."""
    surfaced = 0
    for profile in profiles:
        normalize_profile(profile)
        key = f"incident:{profile.cell_id}:{profile.incident_group}:{profile.incident_state}"
        if profile.incident_state != "none" and not state.stakeholder_memory.get(key):
            surfaced += 1
            state.stakeholder_memory[key] = state.turn
    if surfaced:
        _apply_city_delta(state, {"friction": surfaced})
    return surfaced


def _refresh_incident(profile: DistrictProfile) -> None:
    """Set or clear the visible incident state from top dissatisfaction."""
    group, band = _top_dissatisfaction(profile)
    if band >= DISSATISFACTION_INCIDENT_THRESHOLD:
        profile.incident_group = group
        profile.incident_state = INCIDENT_STATE_BY_GROUP.get(group, "protest")
    else:
        profile.incident_group = ""
        profile.incident_state = "none"


def _adjust_dissatisfaction(profile: DistrictProfile, groups: Iterable[str], amount: int) -> None:
    """Shift dissatisfaction for present citizen groups on one profile."""
    profile.dissatisfaction = _normalize_bands(profile.dissatisfaction, 4)
    for group in groups:
        if group not in CITIZEN_GROUPS:
            continue
        current = profile.dissatisfaction.get(group, 0)
        if profile.population_mix.get(group, 0) == 0 and amount < 0:
            continue
        profile.dissatisfaction[group] = max(0, min(4, current + amount))


def _adjust_dissatisfaction_map(dissatisfaction: dict[str, int], groups: Iterable[str], amount: int) -> None:
    """Shift an already-normalized dissatisfaction mapping in place."""
    for group in groups:
        if group in dissatisfaction:
            dissatisfaction[group] = max(0, min(4, dissatisfaction.get(group, 0) + amount))


def _shift_mix(profile: DistrictProfile, groups: Iterable[str], amount: int) -> None:
    """Move citizen-group presence bands and initialize grievances if needed."""
    profile.population_mix = _normalize_bands(profile.population_mix, 3)
    for group in groups:
        if group not in CITIZEN_GROUPS:
            continue
        profile.population_mix[group] = max(0, min(3, profile.population_mix.get(group, 0) + amount))
        if profile.population_mix[group] > 0:
            profile.dissatisfaction.setdefault(group, 0)


def _top_presence_group(profile: DistrictProfile) -> tuple[str, int]:
    """Return the most present citizen group on one profile."""
    profile.population_mix = _normalize_bands(profile.population_mix, 3)
    return min(profile.population_mix.items(), key=lambda pair: (-pair[1], pair[0]))


def _top_presence_groups(profiles: list[DistrictProfile], limit: int = 3) -> tuple[str, ...]:
    """Return the strongest citizen groups across profiles."""
    totals = {group: 0 for group in CITIZEN_GROUPS}
    for profile in profiles:
        for group, band in _normalize_bands(profile.population_mix, 3).items():
            totals[group] += band
    groups = [group for group, value in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0])) if value > 0]
    return tuple(groups[:limit])


def _top_dissatisfaction(profile: DistrictProfile) -> tuple[str, int]:
    """Return the highest dissatisfaction band on one profile."""
    profile.dissatisfaction = _normalize_bands(profile.dissatisfaction, 4)
    return min(profile.dissatisfaction.items(), key=lambda pair: (-pair[1], pair[0]))


def _top_dissatisfaction_for_profiles(profiles: list[DistrictProfile]) -> tuple[str, int]:
    """Return the worst single dissatisfaction band across profiles.

    Bands aggregate as a max, not a sum, so the result stays on the per-profile
    0-4 band scale: it remains a valid GRIEVANCE_BAND_LABELS index, and the
    threshold checks read as "at least one target district is this aggrieved"
    instead of inflating with the number of targets (summing two band-3 districts
    used to yield 6 and crash the resume path).
    """
    maxima = {group: 0 for group in CITIZEN_GROUPS}
    for profile in profiles:
        for group, band in _normalize_bands(profile.dissatisfaction, 4).items():
            if band > maxima[group]:
                maxima[group] = band
    return min(maxima.items(), key=lambda pair: (-pair[1], pair[0]))


def _present_template_groups(profiles: list[DistrictProfile], groups: Iterable[str]) -> tuple[str, ...]:
    """Filter template groups to those present in selected profiles."""
    present = []
    for group in groups:
        if group in CITIZEN_GROUPS and any(profile.population_mix.get(group, 0) > 0 for profile in profiles):
            present.append(group)
    return tuple(present[:3])


def _strongest_template_group(profiles: list[DistrictProfile], groups: Iterable[str]) -> str:
    """Return the configured group with the strongest selected presence."""
    scores = []
    for group in groups:
        if group not in CITIZEN_GROUPS:
            continue
        scores.append((sum(profile.population_mix.get(group, 0) for profile in profiles), group))
    scores = [score for score in scores if score[0] > 0]
    if not scores:
        return ""
    return min(scores, key=lambda pair: (-pair[0], pair[1]))[1]


def _group_label(group: str) -> str:
    """Return a display label for a citizen or stakeholder group id."""
    return GROUP_LABELS.get(group, group.replace("_", " "))


def _join_group_labels(groups: Iterable[str]) -> str:
    """Join group labels into compact dashboard prose."""
    labels = [_group_label(group) for group in groups if group]
    if not labels:
        return "no filed group"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _population_report_fragment(target_profiles: list[DistrictProfile]) -> str:
    """Summarize selected district population context for a filed report."""
    if not target_profiles:
        return "No local population file was attached."
    group, band = _top_dissatisfaction_for_profiles(target_profiles)
    if band >= DISSATISFACTION_INCIDENT_THRESHOLD:
        return f"Population file: {_group_label(group)} grievance is now incident-ready."
    if band >= DISSATISFACTION_AGGRIEVED_THRESHOLD:
        return f"Population file: {_group_label(group)} grievance is elevated but not yet an incident."
    groups = _top_presence_groups(target_profiles, limit=2)
    return f"Population file: {_join_group_labels(groups)} are the main affected groups."


def _service_gap_for_profile(profile: DistrictProfile) -> dict[str, int]:
    """Derive unmet service demand bands for one district profile."""
    services = max(0, min(100, int(profile.services)))
    gaps: dict[str, int] = {}
    if profile.population_mix.get("families", 0) >= 2:
        gaps["child_services"] = max(0, 45 - services)
    if profile.exposure >= 45 or profile.district_type in ("industrial", "civic"):
        gaps["fire_response"] = max(0, 50 - services)
    if profile.exposure >= 35 or profile.district_type in ("industrial", "mercantile"):
        gaps["utilities"] = max(0, 45 - services)
    if profile.population_mix.get("commuters", 0) >= 2:
        gaps["mobility"] = max(0, 40 - services)
    if profile.population_mix.get("artists", 0) >= 2 or profile.population_mix.get("students", 0) >= 2:
        gaps["culture_access"] = max(0, 35 - services)
    if profile.district_type == "natural" or profile.exposure >= 55:
        gaps["green_buffer"] = max(0, 40 - services)
    for service, demand in _network_demand_for_profile(profile).items():
        access = profile.network_access.get(service, 0)
        if demand > access:
            gaps[service] = max(gaps.get(service, 0), (demand - access) * 10)
    return {service: gap for service, gap in sorted(gaps.items()) if gap > 0}


def _network_demand_for_profile(profile: DistrictProfile) -> dict[str, int]:
    """Estimate per-service network demand from population and land context."""
    population_scale = max(1, profile.population // 1400)
    housing_scale = max(0, profile.housing_capacity // 2500)
    demand = {
        "utilities": 1 + population_scale + housing_scale,
        "mobility": 1 + population_scale,
        "fire_response": 1 if profile.exposure >= 35 or profile.district_type in ("industrial", "civic") else 0,
        "green_buffer": 1 if profile.exposure >= 45 or profile.hazards.get("heat", 0) or profile.hazards.get("ecology", 0) else 0,
        "child_services": 1 if profile.population_mix.get("families", 0) >= 2 else 0,
        "culture_access": 1 if profile.population_mix.get("artists", 0) >= 2 or profile.population_mix.get("students", 0) >= 2 else 0,
    }
    if profile.district_type in ("industrial", "mercantile"):
        demand["utilities"] += 1
        demand["mobility"] += 1
    if profile.land_use in ("mixed_use", "commerce", "transport_corridor", "bus_priority"):
        demand["mobility"] += 1
    if profile.zoning_overlay in ("mixed_use", "affordable_infill"):
        demand["utilities"] += 1
        demand["mobility"] += 1
    return {service: min(6, max(0, value)) for service, value in demand.items() if value > 0}


def _service_coverage_effect(archetype: FeatureArchetype, profile: DistrictProfile, role: str) -> dict[str, int]:
    """Translate a feature's service coverage into district metric deltas."""
    if not archetype.coverage_effects:
        return {}
    delta: dict[str, int] = {}
    for metric, value in archetype.coverage_effects.items():
        if role == "spillover":
            if value > 0:
                value = max(0, round(value / 2))
            elif value < 0:
                value = min(0, round(value / 2))
        if value:
            delta[metric] = value
    if archetype.service_type:
        gap = profile.service_gap.get(archetype.service_type, 0)
        if gap > 0 and archetype.capacity > 0 and role == "target":
            delta["services"] = delta.get("services", 0) + min(archetype.capacity, max(1, gap // 10))
    return delta


def _land_use_adjusted_effects(delta: dict[str, int], profile: DistrictProfile, archetype: FeatureArchetype) -> dict[str, int]:
    """Adjust district deltas for land-use fit or conflict."""
    adjusted = dict(delta)
    if profile.district_type in archetype.allowed_district_types:
        if archetype.service_type:
            adjusted["services"] = adjusted.get("services", 0) + 1
        if adjusted.get("friction", 0) > 0:
            adjusted["friction"] = max(0, adjusted["friction"] - 1)
        if adjusted.get("exposure", 0) > 0:
            adjusted["exposure"] = max(0, adjusted["exposure"] - 1)
    if profile.district_type in archetype.conflict_district_types:
        adjusted["friction"] = adjusted.get("friction", 0) + 2
        adjusted["exposure"] = adjusted.get("exposure", 0) + 1
        if adjusted.get("activity", 0) > 1:
            adjusted["activity"] -= 1
    if profile.zoning_overlay == "protected_reserve" and archetype.family in ("business", "land_use", "infrastructure"):
        adjusted["friction"] = adjusted.get("friction", 0) + 1
        adjusted["exposure"] = adjusted.get("exposure", 0) + 1
    return adjusted


def _land_use_note(archetype: FeatureArchetype, profiles: list[DistrictProfile]) -> str:
    """Explain land-use fit for selected profiles in filed-report prose."""
    district_types = {profile.district_type for profile in profiles}
    if district_types & set(archetype.conflict_district_types):
        return f"Land-use note: {archetype.label} conflicts with at least one selected district archetype."
    if district_types & set(archetype.allowed_district_types):
        return f"Land-use note: {archetype.label} fits the selected district pattern."
    if archetype.land_use:
        return f"Land-use note: proposed overlay is {archetype.land_use.replace('_', ' ')}."
    return ""


def _apply_land_use_change(profile: DistrictProfile, archetype: FeatureArchetype) -> None:
    """Persist zoning overlay changes caused by land-use features."""
    if archetype.family == "land_use":
        profile.zoning_overlay = archetype.land_use
    elif archetype.family in ("natural_resource", "infrastructure") and archetype.land_use:
        profile.zoning_overlay = archetype.land_use


def _district_adjusted_effects(base: dict[str, int], district_type: str, category: str) -> dict[str, int]:
    """Tune template effects for the receiving district archetype."""
    delta = dict(base)
    if district_type == "residential" and category in ("education", "residential"):
        delta["trust"] = delta.get("trust", 0) + 1
        delta["exposure"] = delta.get("exposure", 0) - 1
    elif district_type == "residential" and category in ("transit", "utility", "development"):
        delta["friction"] = delta.get("friction", 0) + 1
    elif district_type == "mercantile" and category in ("business", "transit", "culture"):
        delta["activity"] = delta.get("activity", 0) + 3
        delta["friction"] = delta.get("friction", 0) - 1
    elif district_type == "industrial" and category in ("utility", "department", "development"):
        delta["activity"] = delta.get("activity", 0) + 2
        delta["exposure"] = delta.get("exposure", 0) + 1
    elif district_type == "civic" and category in ("department", "education", "culture", "event"):
        delta["trust"] = delta.get("trust", 0) + 2
        delta["exposure"] = delta.get("exposure", 0) - 1
    elif district_type == "academic" and category in ("education", "culture", "event", "land"):
        delta["trust"] = delta.get("trust", 0) + 3
        delta["exposure"] = delta.get("exposure", 0) - 1
    elif district_type == "natural" and category == "land":
        delta["trust"] = delta.get("trust", 0) + 3
        delta["exposure"] = delta.get("exposure", 0) - 2
    elif district_type == "natural" and category in ("development", "utility", "transit", "business"):
        delta["friction"] = delta.get("friction", 0) + 2
        delta["exposure"] = delta.get("exposure", 0) + 1
    return delta


def _mitigate(delta: dict[str, int]) -> dict[str, int]:
    """Reduce harmful or oversized deltas for mitigated approvals."""
    out = {}
    for metric, value in delta.items():
        if metric in ("friction", "exposure") and value > 0:
            out[metric] = max(0, value - 2)
        elif metric in ("activity", "trust") and value > 0:
            out[metric] = max(0, value - 1)
        else:
            out[metric] = value
    return out


def _context_side_effect(template: DocketTemplate, target_profiles: list[DistrictProfile], rng: random.Random, mitigated: bool) -> dict[str, int]:
    """Roll a small contextual side effect from local exposure and friction."""
    if mitigated:
        threshold = 0.12
    else:
        threshold = 0.28
    avg_friction = sum(p.friction for p in target_profiles) / max(1, len(target_profiles))
    avg_exposure = sum(p.exposure for p in target_profiles) / max(1, len(target_profiles))
    if template.category in ("event", "development", "residential", "transit", "utility", "business") and (avg_friction > 50 or rng.random() < threshold):
        return {"friction": 2, "exposure": 1}
    if template.category in ("education", "department", "land") and avg_exposure > 45:
        return {"exposure": -2, "friction": -1}
    return {}


def _approval_failure_effect(
    template: DocketTemplate,
    target_profiles: list[DistrictProfile],
    risk_band: str,
    rng: random.Random,
    mitigated: bool,
) -> dict[str, int]:
    """Roll and return failure deltas for an approved docket item."""
    if not template.failure_effects:
        return {}
    chance = _failure_chance(template, target_profiles, risk_band, mitigated)
    if rng.random() >= chance:
        return {}
    delta = dict(template.failure_effects)
    if mitigated:
        delta = _mitigate(delta)
    return delta


def _failure_chance(template: DocketTemplate, target_profiles: list[DistrictProfile], risk_band: str, mitigated: bool) -> float:
    """Calculate approval failure probability from inspection and local fit."""
    # Failure chance combines the inspection result with local fit; the clamp
    # keeps routine approvals from becoming impossible or perfectly safe.
    avg_services = sum(p.services for p in target_profiles) / max(1, len(target_profiles))
    avg_exposure = sum(p.exposure for p in target_profiles) / max(1, len(target_profiles))
    avg_friction = sum(p.friction for p in target_profiles) / max(1, len(target_profiles))
    chance = template.failure_base_chance
    chance += {"low": -0.12, "medium": 0.02, "high": 0.25, "unknown": 0.05}.get(risk_band, 0.05)
    if avg_services < 25:
        chance += 0.16
    elif avg_services > 55:
        chance -= 0.08
    if avg_exposure > 55:
        chance += 0.10
    if avg_friction > 55:
        chance += 0.08
    if avg_exposure >= 70 or avg_friction >= 70:
        chance += 0.10
    district_types = {profile.district_type for profile in target_profiles}
    archetype = feature_archetype_for_template(template)
    if district_types & set(template.good_fit_types):
        chance -= 0.10
    if district_types & set(template.bad_fit_types):
        chance += 0.14
    if district_types & set(archetype.allowed_district_types):
        chance -= 0.05
    if district_types & set(archetype.conflict_district_types):
        chance += 0.08
    if archetype.service_type:
        avg_gap = sum(profile.service_gap.get(archetype.service_type, 0) for profile in target_profiles) / max(1, len(target_profiles))
        if avg_gap >= 20:
            chance += 0.08
        elif avg_gap == 0 and avg_services >= 45:
            chance -= 0.04
    if any(profile.zoning_overlay == "protected_reserve" for profile in target_profiles) and archetype.family in ("business", "land_use", "infrastructure"):
        chance += 0.10
    if mitigated:
        chance -= 0.20
    if risk_band == "high" and avg_services < 20 and avg_exposure > 75 and district_types & set(template.bad_fit_types):
        chance = 1.0
    return max(0.03, min(1.0, chance))


def _apply_profile_delta(profile: DistrictProfile, delta: dict[str, int]) -> None:
    """Apply clamped district metric deltas and refresh derived fields."""
    for metric, value in delta.items():
        if metric in DISTRICT_METRICS:
            setattr(profile, metric, _clamp(getattr(profile, metric) + value))
    normalize_profile(profile)


def _apply_city_delta(state: CityState, delta: dict[str, int]) -> None:
    """Apply clamped citywide metric deltas to the mutable city state."""
    for metric, value in delta.items():
        if metric in CORE_METRICS:
            setattr(state, metric, _clamp(getattr(state, metric) + value))


def _merge_delta(target: dict[str, int], delta: dict[str, int]) -> None:
    """Accumulate metric deltas into an existing dictionary."""
    for metric, value in delta.items():
        target[metric] = target.get(metric, 0) + value


def _adjust_heat(state: CityState, stakeholder: str, amount: int) -> int:
    """Proxy stakeholder heat changes through the public pressure helper."""
    return adjust_stakeholder_pressure(state, stakeholder, amount)


def _blocked(action: str, item_id: str, report: str) -> DecisionResult:
    """Build a standardized blocked decision result."""
    return DecisionResult(False, action, item_id, report, command_status="error")


def _format_delta(delta: dict[str, int]) -> str:
    """Format district metric deltas for human-readable reports."""
    parts = []
    for metric in DISTRICT_METRICS:
        value = delta.get(metric, 0)
        if value:
            parts.append(f"{metric} {value:+d}")
    return ", ".join(parts) if parts else "no net citywide metric change"


def _clamp(value: int) -> int:
    """Clamp gameplay metric values to the 0-100 band."""
    return max(0, min(100, int(value)))


__all__ = [name for name in globals() if not name.startswith("__")]
