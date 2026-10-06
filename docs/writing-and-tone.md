# Writing & Tone

The voice of Permit Office and a bank of real in-code examples to match when
adding previews, reports, inspection hints, or status copy.

## Voice

**Dry municipal absurdism.** Write like a competent, slightly weary permit
office: bureaucratic register, procedural nouns, understatement. The *situations*
are strange; the *prose* stays deadpan and official. The player is an
audit-facing clerk, never an all-powerful mayor.

Principles:

1. **Procedural, not heroic.** "Audit closes with conditions," not "You saved the
   city!" Outcomes are filed, noted, flagged — not celebrated.
2. **Legible in hindsight.** Reports name what changed and why in plain civic
   terms. Surprise comes from the city, not from obscured copy.
3. **Partial information by design.** Previews are fuzzy; inspection hints narrow
   uncertainty without exposing formulas or exact numbers.
4. **Absurdity in the margins.** Keep the desk readable as a civic sim; let the
   strangeness live in *what* is being permitted (processions, rewilding,
   shrines, incidents), not in jokey UI chrome.
5. **Quantities as bands/relatives.** Prefer "elevated friction," "service gap,"
   "mitigated nuisance risk" over raw stat deltas in player-facing prose.

## Examples (verbatim from code)

**Template preview** — fuzzy, pre-inspection (`catalogs/templates.py`):
> "Improves access between two districts. Adds a new line with some access and
> exposure tradeoffs."
> "Adds a temporary event route. Trust may improve, but crowd control needs review."
> "Reduces infrastructure exposure and improves utility service coverage."

**Inspection hint** — narrows uncertainty, names tensions, no numbers:
> "Ridership estimates support the route. Several alignment objections are already
> on file."
> "Route review found strong interest, likely bottlenecks, and signage conflicts."

**Final audit flavor** (`dashboard.py: FINAL_AUDIT_FLAVOR`):
> PASS — "Audit accepts the closing file. The city can keep issuing permits under
> the current desk model."
> CONDITIONAL — "Audit closes with conditions. Core services continue, but flagged
> pressure areas need a follow-up docket."

**Deadline ticker** (`dashboard.py: WORK_WEEK_DAYS`):
> "FRI CLOSE — Filing close approaching. Open cases advance unresolved."

**Status line** (transient, terse, factual):
> "New game started with seed 99."
> "No saved game found. Click New Game to create Permit Office layers and start play."
> "Decision failed: proposal locked"

**Report wording register** (per the stat model in `systems-overview.md`): activity → "activity
gain, local activity, economic growth"; friction → "civic friction, visible
incident, pressure"; trust → "trust, cohesion, public value"; exposure → "safety
exposure, operational exposure, hazard pressure"; services → "service capacity,
coverage gap, local service relief." Dissatisfaction bands read as: quiet,
watching, annoyed, aggrieved, incident-ready.

## Do / Don't

| Do | Don't |
| --- | --- |
| "Mitigated nuisance risk; activity and trust improve." | "Nice! +5 activity, +8 trust!!" |
| "Contractor heat rises." | "The contractors are now your enemies." |
| "Generated follow-up appears in the docket." | "A new quest unlocked!" |
| "Active feature maintenance review." (a finding) | "WARNING: BUILDING BROKEN" |
| Name the stakeholder office ("Transit Alignment Office"). | Use a person's first name / casual address. |
| Let the *thing* be weird (a rewilding permit). | Make the *narrator* be quirky. |

## Practical rules

- **Length:** previews 1–2 sentences; inspect hints 1 sentence; reports a compact
  paragraph; status lines a clause. Report fields are truncated by the GDB (e.g.
  1024 chars), so keep copy well under the limit.
- **Tense/voice:** present tense, mostly passive/impersonal ("the route is
  approved," "objections are on file") to keep the office register.
- **Stakeholders are offices/groups**, not individuals: `transit_authority`,
  `celebrants`, `utility_board`, `conservationists`.
- New flavor for a template goes in its `DocketTemplate` fields (`preview`,
  `inspect_hint`, `failure_mode`, `contact_name`), not hardcoded in the adapter.
