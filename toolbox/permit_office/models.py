"""Pure Python rules for the Permit Office prototype.

This module intentionally does not import ArcPy. The Python toolbox persists
these rules into feature classes and tables, but the gameplay math is kept
testable outside ArcGIS Pro.
"""

from __future__ import annotations

from dataclasses import dataclass, field


CORE_METRICS = ("activity", "friction", "trust", "exposure")
DISTRICT_METRICS = CORE_METRICS + ("services",)

DISTRICT_TYPES = ("residential", "mercantile", "industrial", "civic", "academic", "natural")
FEATURE_FAMILIES = (
    "business",
    "public_resource",
    "natural_resource",
    "infrastructure",
    "event",
    "land_use",
    "incident",
    "compliance",
)
SERVICE_TYPES = (
    "child_services",
    "fire_response",
    "utilities",
    "mobility",
    "culture_access",
    "green_buffer",
)
NETWORK_TYPES = ("utilities", "mobility", "fire_response", "green_buffer")
HAZARD_TYPES = ("pollution", "flood", "fire", "noise", "heat", "ecology")

LAND_USE_BY_DISTRICT_TYPE = {
    "residential": "housing",
    "mercantile": "commerce",
    "industrial": "industry",
    "civic": "civic_core",
    "academic": "campus",
    "natural": "open_space",
}

CITIZEN_GROUPS = (
    "families",
    "elders",
    "students",
    "commuters",
    "workers",
    "artists",
    "vendors",
    "homeowners",
    "renters",
    "civil_servants",
    "developers",
    "conservationists",
)

GROUP_LABELS = {
    "families": "families",
    "elders": "elders",
    "students": "students",
    "commuters": "commuters",
    "workers": "workers",
    "artists": "artists",
    "vendors": "vendors",
    "homeowners": "homeowners",
    "renters": "renters",
    "civil_servants": "civil servants",
    "developers": "developers",
    "conservationists": "conservationists",
}

PRESSURE_BAND_LABELS = ("absent", "noted", "present", "prominent")
GRIEVANCE_BAND_LABELS = ("quiet", "watching", "annoyed", "aggrieved", "incident-ready")

STAKEHOLDER_HEAT_THRESHOLD = 3
ENFORCEMENT_TEMPLATE_ID = "unpermitted_followthrough"
CIVIC_INCIDENT_TEMPLATE_ID = "civic_incident_response"
MAINTENANCE_TEMPLATE_ID = "feature_maintenance_order"
DISSATISFACTION_AGGRIEVED_THRESHOLD = 3
DISSATISFACTION_INCIDENT_THRESHOLD = 4

DISPLAY_STATES = (
    "stable",
    "daily_pressure",
    "incident",
    "grievance",
    "service_gap",
    "hazard",
    "housing_pressure",
    "economic_growth",
    "high_activity",
    "high_friction",
    "high_trust",
    "high_exposure",
    "strained",
    "aggrieved",
)


@dataclass(frozen=True)
class FeatureArchetype:
    """Catalog definition for the map feature created by a docket template."""

    archetype_id: str
    label: str
    family: str
    geometry_type: str
    service_type: str = ""
    coverage_radius_m: int = 125
    capacity: int = 0
    land_use: str = ""
    base_effects: dict[str, int] = field(default_factory=dict)
    coverage_effects: dict[str, int] = field(default_factory=dict)
    network_type: str = ""
    network_strength: int = 0
    hazard_effects: dict[str, int] = field(default_factory=dict)
    mitigation_effects: dict[str, int] = field(default_factory=dict)
    housing_effects: dict[str, int] = field(default_factory=dict)
    starts_chain_id: str = ""
    allowed_district_types: tuple[str, ...] = ()
    conflict_district_types: tuple[str, ...] = ()
    incident_type: str = ""
    display_state: str = "active"


@dataclass
class FeatureInstance:
    """Runtime state for a persisted support feature on the map."""

    feature_id: str
    archetype_id: str
    family: str = ""
    service_type: str = ""
    network_type: str = ""
    target_cell_ids: list[str] = field(default_factory=list)
    capacity: int = 0
    intensity: int = 1
    status: str = "active"
    turn_created: int = 0
    expires_turn: int = -1
    project_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    item_id: str = ""
    template_id: str = ""
    owner_group: str = ""
    condition: int = 100
    maintenance_due_turn: int = -1
    last_maintained_turn: int = 0
    display_state: str = "active"
    chain_step_id: str = ""
    state_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HazardRule:
    """Rules for hazard pressure, decay, and mitigation."""

    hazard_type: str
    affected_groups: tuple[str, ...]
    exposure_threshold: int = 2
    friction_threshold: int = 3
    decay: int = 1
    source_effects: dict[str, int] = field(default_factory=dict)
    mitigation_service_types: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectStepTemplate:
    """One configured step in a multi-turn project chain."""

    step_id: str
    title: str
    template_id: str
    due_after: int = 1
    next_step_id: str = ""
    failure_step_id: str = ""
    status_on_approval: str = "advanced"
    status_on_failure: str = "failed"
    stakeholder: str = ""
    payload_effects: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectChainTemplate:
    """Catalog definition for a sequenced project workflow."""

    chain_id: str
    label: str
    initial_step_id: str
    steps: tuple[ProjectStepTemplate, ...]


@dataclass
class ProjectRecord:
    """Runtime state for a project chain opened by an approval."""

    project_id: str
    chain_template_id: str
    current_step_id: str
    status: str = "active"
    turn_started: int = 1
    due_turn: int = 1
    stakeholder: str = ""
    target_cell_ids: list[str] = field(default_factory=list)
    payload: dict[str, object] = field(default_factory=dict)
    last_report: str = ""


@dataclass(frozen=True)
class ScenarioRule:
    """Scenario-specific starting biases and scoring priorities."""

    scenario_id: str
    label: str
    starting_city_effects: dict[str, int] = field(default_factory=dict)
    starting_district_effects: dict[str, int] = field(default_factory=dict)
    docket_priority: tuple[str, ...] = ()
    score_weights: dict[str, int] = field(default_factory=dict)
    audit_priorities: tuple[str, ...] = ()
    district_biases: dict[str, int] = field(default_factory=dict)
    hazard_bias: dict[str, int] = field(default_factory=dict)
    housing_bias: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DocketTemplate:
    """Catalog definition for a permit, enforcement, or incident docket item."""

    template_id: str
    title: str
    category: str
    geometry_type: str
    base_effects: dict[str, int]
    spillover_effects: dict[str, int]
    ap_cost: int = 1
    money_cost: int = 10
    mitigation_cost: int = 8
    expires_after: int = 0
    expiration_policy: str = "city_momentum"
    pressure_category: str = "general"
    preview: str = ""
    inspect_hint: str = ""
    target_rule: str = ""
    stakeholder: str = "general_public"
    denial_heat: int = 1
    ignore_heat: int = 1
    failure_mode: str = ""
    failure_effects: dict[str, int] = field(default_factory=dict)
    failure_base_chance: float = 0.18
    housing_effects: dict[str, int] = field(default_factory=dict)
    hazard_effects: dict[str, int] = field(default_factory=dict)
    starts_chain_id: str = ""
    project_step_id: str = ""
    scenario_tags: tuple[str, ...] = ()
    good_fit_types: tuple[str, ...] = ()
    bad_fit_types: tuple[str, ...] = ()
    is_enforcement: bool = False
    is_incident: bool = False
    supporter_groups: tuple[str, ...] = ()
    concerned_groups: tuple[str, ...] = ()
    growth_groups: tuple[str, ...] = ()
    decline_groups: tuple[str, ...] = ()
    contact_name: str = ""
    spawn_archetype_id: str = ""


@dataclass
class DistrictProfile:
    """Mutable gameplay state for one generated district cell."""

    cell_id: str
    name: str
    population: int
    activity: int
    friction: int
    trust: int
    exposure: int
    services: int
    district_type: str
    prior_district_type: str = ""
    identity_state: str = "stable"
    contesting_cell_id: str = ""
    contesting_type: str = ""
    transition_due_turn: int = 0
    buyout_pressure: int = 0
    last_buyout_report: str = ""
    display_state: str = "stable"
    prosperity_band: str = "stable"
    population_mix: dict[str, int] = field(default_factory=dict)
    dissatisfaction: dict[str, int] = field(default_factory=dict)
    incident_state: str = "none"
    incident_group: str = ""
    public_profile: str = ""
    land_use: str = ""
    zoning_overlay: str = ""
    service_gap: dict[str, int] = field(default_factory=dict)
    adjacent_cell_ids: list[str] = field(default_factory=list)
    network_access: dict[str, int] = field(default_factory=dict)
    hazards: dict[str, int] = field(default_factory=dict)
    housing_capacity: int = 0
    affordability: int = 0
    vacancy_rate: int = 0
    displacement: dict[str, int] = field(default_factory=dict)


@dataclass
class CityState:
    """Mutable citywide turn, resource, metric, and heat state."""

    turn: int = 1
    max_turns: int = 12
    ap: int = 2
    max_ap: int = 2
    money: int = 60
    # Patience ladder (owner D5): index into AUDIT_RUNGS.
    audit_rung: int = 0
    status: str = "playing"
    last_report: str = ""
    activity: int = 50
    friction: int = 20
    trust: int = 35
    exposure: int = 25
    scenario_id: str = "default"
    stakeholder_heat: dict[str, int] = field(default_factory=dict)
    last_revenue: int = 0
    last_upkeep: int = 0
    last_net: int = 0
    maintenance_backlog: int = 0
    stakeholder_memory: dict[str, int] = field(default_factory=dict)
    type_ledger: dict[str, dict[str, int]] = field(default_factory=dict)
    pending_followups: dict[str, str] = field(default_factory=dict)
    week_day: int = 0
    daily_pressure: dict[str, int] = field(default_factory=dict)
    # Season goal (owner D9): {"offer": [3 keys], "chosen": key, "baseline": {...}}.
    mandate: dict[str, object] = field(default_factory=dict)
    # Season result once status is complete: "won", "lost" or "dismissed".
    outcome: str = ""
    # Player initiatives: {"week": turn of the last one, "earmarks": {type: until turn}}.
    initiatives: dict[str, object] = field(default_factory=dict)


# Docket statuses that still await a decision; every other status is filed.
OPEN_DOCKET_STATUSES = ("open", "inspected", "carried")


@dataclass
class DocketItem:
    """Runtime docket case shown to the player for action."""

    item_id: str
    template_id: str
    title: str
    geometry_type: str
    turn: int
    status: str = "open"
    inspected: bool = False
    target_cell_ids: list[str] = field(default_factory=list)
    preview_text: str = ""
    risk_band: str = "unknown"
    carryover: str = "expire_or_return"
    stakeholder: str = ""
    origin_item_id: str = ""
    target_rule: str = ""
    project_id: str = ""
    chain_step_id: str = ""
    priority: int = 0
    due_turn: int = 0
    subject_feature_id: str = ""
    case_json: dict[str, object] = field(default_factory=dict)


@dataclass
class DecisionResult:
    """Structured result returned by a resolved docket action."""

    ok: bool
    action: str
    item_id: str
    report: str
    city_delta: dict[str, int] = field(default_factory=dict)
    affected_cell_ids: list[str] = field(default_factory=list)
    district_deltas: dict[str, dict[str, int]] = field(default_factory=dict)
    item_status: str = ""
    command_status: str = "applied"
    stakeholder_delta: dict[str, int] = field(default_factory=dict)
    failure_triggered: bool = False
    feature_updates: dict[str, dict[str, object]] = field(default_factory=dict)


ActiveFeature = FeatureInstance


@dataclass(frozen=True)
class FeatureOperatingRule:
    """Lifecycle, upkeep, and maintenance economics for a feature archetype."""

    archetype_id: str
    revenue_per_turn: int = 0
    upkeep_per_turn: int = 0
    lifespan_turns: int = 0
    decay_per_turn: int = 0
    maintenance_interval: int = 0
    maintenance_cost: int = 0
    repair_amount: int = 40
    degrade_threshold: int = 35
    failure_threshold: int = 0
    failure_effects: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class InspectionRule:
    """Evidence and violation configuration for inspections."""

    rule_id: str
    evidence_codes: tuple[str, ...]
    violation_codes: tuple[str, ...] = ()
    deadline_turns: int = 2


@dataclass(frozen=True)
class EvidenceRecord:
    """One inspection evidence entry recorded on a docket case."""

    evidence_id: str
    label: str
    severity: str
    source: str
    note: str = ""


@dataclass(frozen=True)
class ViolationRecord:
    """One inspection violation and its compliance deadline."""

    violation_id: str
    code: str
    severity: str
    deadline_turn: int
    status: str = "open"
    compliance_outcome: str = "pending"
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StakeholderProfile:
    """Political behavior profile for stakeholder heat escalation."""

    stakeholder_id: str
    influence: int = 2
    patience: int = 2
    interests: tuple[str, ...] = ()
    allies: tuple[str, ...] = ()
    rivals: tuple[str, ...] = ()
    escalation_threshold: int = STAKEHOLDER_HEAT_THRESHOLD
    cooldown_turns: int = 1
    followup_template_id: str = ENFORCEMENT_TEMPLATE_ID


@dataclass(frozen=True)
class AuditFinding:
    """One audit finding and its contribution to the final score."""

    finding_id: str
    severity: str
    source: str
    message: str
    score_delta: int = 0


@dataclass(frozen=True)
class AuditResult:
    """Final audit grade, score, findings, and report text."""

    grade: str
    score: int
    findings: tuple[AuditFinding, ...]
    report: str


# Weeks whose close runs a checkpoint audit, and the patience ladder it moves
# (owner D5): FAIL climbs a rung, PASS descends one, CONDITIONAL holds.
AUDIT_WEEKS = (4, 8, 12)
AUDIT_RUNGS = ("attended", "warning", "sanctioned", "dismissed")
SANCTIONED_RUNG = 2
DISMISSED_RUNG = 3

# Audit grade floors: PASS also needs money >= 0 and no critical finding;
# CONDITIONAL allows at most one critical.
PASS_SCORE = 70
CONDITIONAL_SCORE = 45


@dataclass
class TurnAdvanceResult:
    """Structured result from advancing the game loop by one turn."""

    report: str
    city_delta: dict[str, int] = field(default_factory=dict)
    district_deltas: dict[str, dict[str, int]] = field(default_factory=dict)
    feature_updates: dict[str, dict[str, object]] = field(default_factory=dict)
    generated_followups: list[str] = field(default_factory=list)
    revenue: int = 0
    upkeep: int = 0
    net: int = 0
    audit: AuditResult | None = None
    # Set only on the week 4, 8 and 12 closes, which move the patience ladder.
    checkpoint_audit: AuditResult | None = None


@dataclass(frozen=True)
class ThreatTrack:
    """Public pressure track derived from hidden civic systems."""

    label: str
    score: int
    tone: str
    reasons: tuple[str, ...] = ()

