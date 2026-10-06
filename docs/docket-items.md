# Docket Items — Worked Examples

How a docket case is shaped, where its data lives, and a fully worked template.
Pairs with `systems-overview.md` (the turn loop) and `writing-and-tone.md` (the
copy voice).

## Two objects: Template vs. Item

- **`DocketTemplate`** (`toolbox/permit_office/catalogs/templates.py`) — the
  static *design* of a case: effects, costs, risk, stakeholders, flavor. Authored
  by hand, deterministic.
- **`DocketItem`** (`toolbox/permit_office/models.py`) — a *runtime instance* of a
  template placed on the board for a given week, carrying mutable state (status,
  inspection results, targets). Generated each week, persisted in `PermitDocket`.

Generation (`profiles.py: generate_docket_rows`) weights templates by district
type mix, good/bad fit, and stat pressure, then samples without replacement,
reserving slots for any scenario-mandated priority templates. See the
weighting comments in `profiles.py` and the determinism ADR in `decisions.md`.

## Worked template: `connector_corridor`

```python
"connector_corridor": DocketTemplate(
    "connector_corridor",                 # template_id (stable key)
    "Connector Corridor Pilot",           # title (player-facing)
    "transit",                            # category (families below)
    "LINE",                               # geometry_type: POINT | LINE | POLYGON
    {"activity": 5, "exposure": -2, "friction": 2},   # approve effects (target)
    {"activity": 2, "exposure": -1, "friction": 1},   # spillover effects (neighbors)
    money_cost=24,
    mitigation_cost=10,                   # extra cost for "Add Conditions"
    expiration_policy="city_momentum",    # what an unattended item does at End Week
    pressure_category="transit",
    preview="Improves access between two districts. Adds a new line with some "
            "access and exposure tradeoffs.",          # fuzzy pre-inspection text
    inspect_hint="Ridership estimates support the route. Several alignment "
                 "objections are already on file.",     # revealed on Inspect
    target_rule="Select exactly two districts to connect.",
    stakeholder="transit_authority",
    failure_mode="construction delay",
    failure_effects={"friction": 3, "exposure": 2, "activity": -2},
    failure_base_chance=0.22,             # tuned by risk band, fit, exposure, mitigation
    good_fit_types=("mercantile", "civic"),   # bonus weight + lower failure
    bad_fit_types=("residential", "natural"),  # penalty weight + higher failure
    supporter_groups=("commuters", "workers", "developers"),
    concerned_groups=("homeowners", "elders", "conservationists"),
    growth_groups=("commuters", "workers"),    # population groups that grow on success
    contact_name="Transit Alignment Office",   # report flavor
    spawn_archetype_id="connector_corridor",   # FeatureInstance archetype on approval
),
```

### The runtime `DocketItem` it becomes

```python
DocketItem(
    item_id="CASE-...",            # unique runtime id
    template_id="connector_corridor",
    title="Connector Corridor Pilot",
    geometry_type="LINE",
    turn=1,
    status="open",                 # open | inspected | active | denied | deferred | carried | ...
    inspected=False,
    target_cell_ids=["D0101", "D0102"],   # set by selection / Retarget from map
    preview_text="...",            # copied from template, refined with a target census note
    risk_band="unknown",           # -> low/medium/high after Inspect
    carryover="expire_or_return",
    stakeholder="transit_authority",
    origin_item_id="",             # set for generated follow-ups (e.g. enforcement)
    project_id="", chain_step_id="",  # set for multi-turn project chains
    priority=0, due_turn=0,
    subject_feature_id="",         # set for maintenance items referencing an active feature
    case_json={},                  # inspection evidence packet, free-form
)
```

## Card shape (what the desk shows)

Each card is a small case file. Before targeting it shows the template preview;
after targeting it adds a **target census note** from the selected districts;
`Inspect File` reveals likely supporters, likely objectors, highest local
grievance, and service-capacity wording, without exposing exact formulas.

Besides Inspect File, the three decision buttons are reused across card kinds:

| Card kind | Approve | Approve + Mitigate | Deny |
| --- | --- | --- | --- |
| Ordinary permit | issue the permit | issue with conditions | reject (0 AP) |
| Enforcement follow-up | enforce the order | settle / retro-permit under conditions | defer enforcement |
| Civic incident | formal response | settlement / service response | defer (raises friction + group heat) |

## Permit families

Templates draw from: transit & roads · events · utilities · land & natural
resources · education & parks · business/nonprofit/vendors · residential &
building · public safety & departments · infrastructure upkeep · culture &
branding.

Each week the weighted draw adds two ordinary cases (`DRAWN_CASES_PER_WEEK` in
`profiles.py`) on top of follow-up work. No seed is locked as a demo route; the
live smoke run uses seed `2034`.

## Generated (non-authored) items

Some items are spawned by city state instead of drawn from the weighted pool:

- **Enforcement** (`unpermitted_followthrough`): appears when a stakeholder
  group's hidden heat crosses its threshold (from denied/ignored permits).
- **Maintenance orders**: reference an existing active `FeatureInstance`
  (`subject_feature_id`) whose condition has degraded; skip spillover.
- **Civic incidents**: spawned when a district's dissatisfaction hits band 4.
- **Project steps**: due steps of a multi-turn `ProjectRecord` chain.

These keep `origin_item_id` / `subject_feature_id` / `project_id` linkage so the
docket reads as a response to the living city, not a random draw.

## Consequence model

- Rejected/ignored permits add **stakeholder heat**, a pressure layer that can spawn
  enforcement cards. It is not an audit metric.
- Approved permits can still **fail**, deterministically from seed, scaled by
  inspection risk band, archetype fit, service capacity, district exposure, and
  whether mitigation was applied. Failure applies `failure_effects` and can leave
  failed-feature state + audit risk instead of the normal active feature.
- Unattended items at End Week resolve through their `expiration_policy`
  (`missed_window`, `city_momentum`, `momentum_with_followup_risk`, mandatory
  carryover), so they create future pressure instead of quietly disappearing.
