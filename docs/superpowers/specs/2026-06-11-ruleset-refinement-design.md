# Permit Office Ruleset Refinement Design

## Purpose

Refine Permit Office from a rich municipal simulation into a clearer player
game: a map-first civic sandbox where the player survives a 12-week audit season
by making legible, spatially meaningful permit decisions.

The approved design direction is:

- Spatial Route Engine
- Threat Track Collapse
- Ruthless Trim
- Soft Office Standing pressure

This design does not replace the existing pure-rule architecture. It reframes
which systems are player-facing, which systems are route-defining, and which
rules should be cut, hidden, or simplified before further balance work.

## Current Ruleset Summary

The current Python rules already support:

- A 12-week season with 2 AP per week and a money budget.
- Permit, incident, enforcement, maintenance, and project-step docket items.
- Inspect, issue, issue with conditions, deny, defer, and end-week actions.
- City vitals: Activity, Friction, Trust, and Exposure.
- A dashboard City Health index derived from those four vitals.
- District state for Services, population mix, dissatisfaction, civic incidents,
  hazards, housing, displacement, maintenance, stakeholder heat, and buyout
  identity pressure.
- Weighted docket generation from district type, citizen culture, city state,
  scenario priority, active features, heat, incidents, maintenance, and projects.
- Scenario routes: Default Audit, Housing Mandate, Port Boom, and Garden City.
- Active support features with recurring revenue/upkeep, service network effects,
  hazards, mitigation, maintenance decay, and map consequences.
- End-week unresolved-case pressure through heat, grievances, expiration,
  carryover, followups, project delay, and buyout pressure.

The simulation depth is already strong. The main refinement need is player
legibility: the player should see the board state, understand the primary threat,
and make a proud, risky choice instead of feeling managed by hidden penalties.

## Player Goal

Primary goal:

> Survive 12 weeks with the permit office's mandate intact, then pass or
> conditionally pass the final audit.

Secondary legacy goal:

> Leave behind a legible city identity on the map.

The player is still a municipal permit clerk, not a mayor. The clerk has limited
authority but meaningful leverage through attention, sequencing, conditions,
denials, inspections, enforcement priorities, and the timing of paperwork.

The final audit remains the climax. City identity is the remembered story and
route-specific legacy score, not a separate survival condition.

## Design Pillars

### 1. Map-First Civic Puzzle

Every important decision should leave a visible board-state memory. A player
should be able to glance at the map and infer:

- which districts are profitable but exposed
- which districts are underserved
- which districts are angry or incident-prone
- which districts are being transformed by development or buyouts
- which routes, buffers, clusters, or gaps are shaping future filings

If a template or feature cannot describe its spatial meaning, it should be cut,
hidden, or rewritten.

### 2. Few Public Threats, Rich Internal Causes

The current internals can remain rich, but the public-facing pressure language
should collapse into four named threat tracks:

- **Public Anger**: grievances, civic incidents, stakeholder anger, local
  backlash, protest risk.
- **Legal Exposure**: unsafe work, violations, audit-critical exposure, failed
  approvals, compliance risk.
- **Service Failure**: service gaps, maintenance failures, network strain,
  degraded public resources.
- **Speculation Pressure**: buyout pressure, displacement, identity conversion,
  land-use churn, growth capture.

These tracks should replace overlapping public concepts rather than add four new
meters on top of the old ones. The old fields can still feed calculations,
generation, and audit findings.

### 3. Route Identity Changes The Question

Routes should not only change docket weights. Each route should ask a recurring
spatial question:

- **Default Audit**: Can the office keep a balanced city stable while learning
  the core loop?
- **Housing Mandate**: Where can density go without breaking services, trust, or
  affordability?
- **Port Boom**: How much corridor revenue and activity justify exposure,
  hazard buffers, and labor friction?
- **Garden City**: Can green identity and public trust survive constrained
  money, service strain, and slower activity?

Each route should expose 3-4 audit criteria from week 1. The player should know
what the route will judge before irreversible map choices land.

### 4. Ruthless Template And Feature Trim

Every docket template and feature should pass this test:

- one upside
- one risk or upkeep burden
- one visible map mark
- one route or threat-track reason it exists

Templates that resolve through the same verbs and the same consequences should
merge. Features that mainly add passive entropy should become part of a broader
threat track or disappear from the player-facing loop.

### 5. Office Standing, Not A Death Bar

City Health should become an **Office Standing** signal:

- It warns when the office is losing legitimacy.
- It can trigger probation or Emergency Oversight.
- It should not casually end a run before the final audit.

Hard early firing is rejected as a default rule because it kills comeback stories.
The fun arc should allow the player to make a mess and claw back legitimacy.

## Proposed Player-Facing Model

### City Pulse

Keep the City Pulse rail, but make its hierarchy clearer:

1. **Office Standing**: derived from current City Health.
2. **Audit Outlook**: PASS / CONDITIONAL / FAIL preview, with route criteria.
3. **Threats**: top 1-2 active threat tracks.
4. **AP and Money**: the operational resources.
5. **Route Brief**: the current route's recurring dilemma and audit criteria.

Activity, Trust, Friction, and Exposure can remain available as the vital
breakdown, but they should not compete with the threat tracks as the main
decision language.

### Threat Track Rules

Threat tracks should summarize existing internal causes:

| Threat | Existing feeders | Player remedies |
| --- | --- | --- |
| Public Anger | dissatisfaction, stakeholder heat, incidents, high friction | respond, mitigate, deny carefully, preserve local fit |
| Legal Exposure | exposure, violations, failed approvals, unsafe work, hazards | inspect, condition, enforce, buffer hazards |
| Service Failure | service gaps, degraded features, maintenance, network access | fund repair, add service features, connect routes |
| Speculation Pressure | buyout pressure, displacement, low activity, land-use churn | stabilize districts, add services, route growth deliberately |

Threat text should point at causes and remedies, not just values.

Example:

> Public Anger: Harbor Flats renters are aggrieved; deferment likely opens an
> incident file.

Example:

> Speculation Pressure: Cinder Yard is vulnerable; office attention this week can
> still hold the line.

### End-Week Consequences

End Week should be predictable. Each unresolved item type should usually apply
one primary consequence:

- Ordinary permit: Public Anger or Speculation Pressure.
- Incident: Public Anger worsens or Service Failure spreads.
- Maintenance: Service Failure or Legal Exposure worsens.
- Enforcement/followup: Legal Exposure or Public Anger worsens.
- Project step: project delay, with one route-specific threat if overdue.

Do not routinely stack heat, grievance, expiration, followup, project delay,
incident risk, and buyout pressure from one unresolved item.

Rare escalations can still happen, but they must be clearly telegraphed by the
case preview or inspection result.

### Decision Previews

Each action lane should preview the primary consequence:

- **Issue**: main upside plus main risk.
- **Add Conditions**: reduced risk plus money cost.
- **Deny/Defer**: no or low AP cost plus named threat increase.
- **Inspect**: AP cost plus risk/violation clarity.

The player does not need exact formulas, but they do need to know the primary
kind of pain before committing.

## Route Designs

### Default Audit

Purpose:

Teach the core loop and remain the baseline 12-week route.

Audit criteria:

- Maintain Office Standing.
- Keep Activity and Trust viable.
- Limit Friction and Exposure.
- Keep money nonnegative.

Spatial dilemma:

Balanced growth: no single threat track should dominate the board.

### Housing Mandate

Purpose:

Turn the map into a density and displacement puzzle.

Audit criteria:

- Add housing capacity.
- Protect affordability.
- Avoid severe displacement.
- Keep new density connected to services.

Spatial dilemma:

Density is good near services and corridors, but dangerous when it overwhelms
low-service residential districts or accelerates speculation.

Primary threats:

- Service Failure
- Speculation Pressure
- Public Anger

### Port Boom

Purpose:

Make activity and revenue tempting while exposure and labor friction accumulate.

Audit criteria:

- Build productive corridors.
- Preserve buffer districts.
- Control pollution, fire, flood, and logistics exposure.
- Avoid labor/community blowback.

Spatial dilemma:

Connected industrial and mercantile corridors generate money and activity, but
unbuffered adjacency pushes Legal Exposure and Public Anger.

Primary threats:

- Legal Exposure
- Public Anger
- Service Failure

### Garden City

Purpose:

Make trust, green networks, and preservation valuable without turning them into
free good-stuff buttons.

Audit criteria:

- Build connected green buffers.
- Reduce heat, ecology, noise, and exposure.
- Preserve trust and identity.
- Avoid starving services or the budget.

Spatial dilemma:

Green networks and reserves improve trust/exposure, but can suppress activity,
consume upkeep, and intensify conflicts around development.

Primary threats:

- Service Failure
- Speculation Pressure
- Legal Exposure

## Office Standing And Emergency Oversight

Office Standing uses the existing City Health idea:

> Office Standing = average(Activity, Trust, 100 - Friction, 100 - Exposure)

Initial bands:

- 65-100: Strong
- 45-64: Authorized
- 30-44: Strained
- 0-29: Failing

Week-close behavior:

- First Failing close: warning memo.
- Second consecutive Failing close: Emergency Oversight begins.
- During Emergency Oversight, the player receives a recovery docket and clearer
  relief tools.
- Removal before week 12 should require continued failure after an Emergency
  Oversight opportunity, and should be rare in the default route.

Emergency Oversight should be playable, not merely punitive. The first
implementation should add:

- one mandatory oversight case in the next docket
- one relief filing that can improve the active route's top threat
- one route-specific audit penalty if the oversight case is ignored
- a clear recovery target: return Office Standing to Strained or Authorized

## Dashboard Changes

### Add Or Promote

- **Office Standing** as the renamed City Health headline.
- **Route Brief** with the current route's audit criteria and recurring dilemma.
- **Threat Track Summary** showing the top 1-2 active threats and their causes.
- **Action Consequence Previews** that name the primary threat affected by Issue,
  Add Conditions, Deny/Defer, and Inspect.
- **End-Week Forecast** summarizing what unresolved cases will mainly do.
- **Map Cause Labels** that connect visible district states to the four threat
  tracks.
- **Emergency Oversight Notice** only when Office Standing enters warning or
  probation states.

### Remove Or Downgrade

- Do not show dissatisfaction, stakeholder heat, grievance, incident risk, and
  friction as separate equal-level public concepts.
- Do not show maintenance as a constant separate dashboard concern unless an
  active degraded/failed feature creates Service Failure or Legal Exposure.
- Do not lead with raw route weight/scenario math.
- Do not expose all internal district fields in the main rail.
- Do not make City Health feel like the final score or the whole game.

## Rules To Add

1. Route audit criteria visible from week 1.
2. Threat-track summarization from existing internal fields.
3. One-primary-consequence policy for unresolved cases.
4. Template/feature validation against upside, risk/upkeep, and map mark.
5. Soft Office Standing warning and Emergency Oversight sequence.
6. Decision preview text that names threat-track changes.
7. Route-specific spatial scoring hooks for adjacency, buffers, networks, and
   service-connected density.

## Rules To Remove Or Hide

1. Stacked ordinary unresolved penalties.
2. Public-facing duplicate anger/pressure channels.
3. Low-impact passive maintenance decay as a constant dashboard concern.
4. Templates that differ only by flavor but create the same decision.
5. Feature effects that do not create a visible map memory or route dilemma.
6. Hard early firing after two bad City Health weeks as the default rule.

## Testing And Validation

Rules tests should cover:

- Office Standing band calculation and warning/probation transitions.
- Emergency Oversight trigger and recovery behavior.
- Threat-track summaries from representative district and city states.
- One-primary-consequence end-week behavior by item type.
- Route-specific audit criteria and scoring text.
- Template/feature catalog validation for upside, risk/upkeep, and map mark.
- Seeded route regressions for Default, Housing, Port, and Garden.

Live ArcGIS validation should check:

- Threat-track map states are readable on the board.
- Route-specific map patterns are visible by week 2.
- End-week forecast matches actual fallout.
- Emergency Oversight reads as a recovery chapter, not a surprise loss.

## Open Implementation Notes

- Existing internals do not need to be deleted immediately. The first pass can
  compute threat summaries as a presentation layer over current state.
- Avoid schema changes unless Office Standing strikes need persistence outside
  `CityState.stakeholder_memory`.
- Keep the final audit as the campaign climax.
- Prefer changing text, grouping, validation, and route scoring before adding new
  simulation fields.
