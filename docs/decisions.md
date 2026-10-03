# Architecture Decision Records

Concise record of the load-bearing choices and the alternatives tried and
rejected — so they aren't re-litigated from scratch. Each entry: the decision,
why, and what we turned down. Newest spikes fold in their original dated notes.

---

### ADR-1 — Two layers: pure rules + ArcGIS adapter
**Decision:** `toolbox/permit_office/` is ArcPy-free and holds all game logic;
`toolbox/permit_office_arcgis/` holds persistence, geometry, and the Tk UI.
**Why:** the rules are testable in plain Python (fast regression, no ArcGIS
license needed); ArcPy stays a swappable I/O edge.
**Rejected:** logic interleaved with cursors — untestable offline and couples
balance tuning to a live ArcGIS install.

### ADR-2 — The geodatabase *is* the save file
**Decision:** game state lives entirely in feature classes/tables; the dashboard
holds only UI/session bookkeeping that `reload()` re-derives from rows.
**Why:** one source of truth, free crash-resume, and the map and game can't drift
apart. **Rejected:** a separate serialized save alongside the GDB — two states to
keep consistent.

### ADR-3 — Synchronous command flow (for now)
**Decision:** every action runs inline on the Tk thread: command row → reads →
resolve → writes → map refresh → reload. No threads, no async.
**Why:** ArcPy cursor/`mp` thread-safety is fraught; the single-thread model is
simple and correct, and the timer can't interleave with a command.
**Rejected (deferred):** backgrounding I/O with `root.after()` marshalling — real
responsiveness win but real concurrency risk; revisit as a spike only if live
runs show UI-freeze pain.

### ADR-4 — Predrawn rehydrate for district redraws
**Decision:** `rebuild_output_layers()` defaults district-dirty redraws to a
**predrawn rehydrate** path: one hidden pre-drawn district snapshot layer is
re-added from the GDB, symbolized, refreshed, and swapped visible. Feature layers
stay refresh-only except when explicitly in the dirty scope; `force_readd=True`
keeps the full remove→add of every in-scope layer (New Game / schema / symbology
change). The previous district-family remove+add path remains the fallback if
rehydrate fails.
**Why:** `arcpy.RefreshLayer` only redraws the cached renderer — it does **not**
reload GDB attribute writes. The district layers render on attribute values
(`district_type`, `prosperity_band`, `identity_state`) that change every decision
and turn, so refresh-only left them frozen on the new-game snapshot (districts
"not rendering"). Live June 2026 redraw experiments showed district-family
remove+add was correct but expensive (`rebuild` commonly ~4-5s), volatile overlay
was cheaper but visually incomplete (districts could disappear), pure predrawn
visibility swap was extremely fast (~0.004-0.008s) but could keep stale district
symbology, and predrawn rehydrate preserved visual correctness with lower live
rebuild cost (~1.1-2.3s in the recorded runs). **Rejected:** unconditional
remove→add of *all* layers every turn (slow, flicker-prone); pure refresh-only
(correctness bug above); volatile overlay as the default (filters away baseline
districts); pure predrawn visibility swap as the default (stale district
symbology).
**June 8 follow-up:** `predrawn-rehydrate-smart-features` is the next promotion
candidate because it kept the district rehydrate model while cutting
point-decision redraws to ~1.8s in live benchmark. `predrawn-swap-refresh` stayed
very fast (~1.0-1.3s) but produced a red/gray close-state map corruption, so it
remains a diagnostic experiment. Retired probes and why are in
`docs/failed-experiments.md`.
**September 30 follow-up (Pro 3.7):** a live probe found that on Pro 3.7,
`RefreshLayer` alone does show new attribute values, and so does flipping the
layer's `definitionQuery` between `1=1` and `2=2`. A later recorded probe showed
the flip briefly drops the city shapes (labels stay, no white flash). On Pro 3.7+ (`QUERY_FLIP_MIN_PRO` in `map_layers.py`), a district redraw now
flips the visible ring slot's query instead of re-adding a layer. In live play
the district step took 0.33-0.52 s, down from 1.1-2.3 s. Older Pro and any slot
that reads another save still use the ring. The whole-map white flash seen on
each redraw in both builds came from the points `RefreshLayer`, which repaints
every visible layer. Feature-only decisions now toggle that layer's query
instead.
**October 1 follow-up (Pro 3.7, offline; not yet recorded):** week close and
other district redraws also redraw the feature layers they touch. A feature
layer with no ring slot yet (often lines or zones at the first week close) used
to get one seeded, and seeding calls `RefreshLayer`, the whole-map white flash.
On 3.7+ the district path now toggles that layer's own query in place instead.
The district flip itself still drops the city shapes briefly; the recorded
probes fit a query change redrawing the changed layer and every layer drawn
above it.
**October 2 follow-up (Pro 3.7, recorded single-call probe):** eight clips, one
map call each, on the six base layers with no ring slots. A definition-query
flip on the points layer drops only the points, for about 0.3 s. A GP
`NEW_SELECTION` on any layer draws its highlight with no captured drop. A
`setSelectionSet([], "NEW")` clear repaints only the cleared layer, for about
one frame, and only the district clear shows white, because the base district
fill is the bottom opaque layer. No single call dropped another layer, so the
"every layer drawn above it" reading above is withdrawn for the base-layer
case. What a player sees on a points-only decision is three one-frame repaints
stacked by the pre-redraw selection clear, one per base layer that held the
previous case's selection, plus the points flip. The fix is in the selection
path, not the draw order. A later clip the same day (run 3) showed the ring-slot
district flip and the overlay flips also drop only the flipped layer, white only
where nothing opaque lies under it, so the week-close blink is a stack of per-layer
drops too. Wrapping the same calls in `arcpy.PauseDrawing` (run 4) did not help:
the pause neither holds the old frame nor repaints once; a paused district flip
still drops the fills for about 0.2 s at block exit, and a paused decision shape
hid the points for the whole block. PauseDrawing is not used.
*(Supersedes the 2026-05-27 refresh spike, which mis-measured refresh-only as
reliability-safe; the RefreshLayer-does-not-reload-data behavior was confirmed
later. Supersedes the earlier district-family remove+add default with a measured
rehydrate default.)*

### ADR-5 — Cursor efficiency: hint + guard, memoize, read-once
**Decision:** single-row cursors carry a `where_clause` **and** keep the in-Python
row check; immutable district geometry is memoized per session; `reload()` makes
a fixed number of cursor opens (batch `proposal_visible_map`, pre-read reuse via
`reload_kwargs`). **Why:** fewer/narrower cursor opens without trusting backend
SQL semantics; correctness is identical whether or not the predicate is honored.
**Rejected:** where-clause with the guard removed (broke when a backend ignored
the predicate); per-item visibility scans (`4 + 3N` opens per reload).

### ADR-6 — No ArcGIS Pro SDK add-in for display
**Decision:** stay on stock ArcPy; don't ship a C# SDK display-cache button.
**Why:** the SDK redraw-cache spike timed *slower* than main and added
build/install/deploy burden outside the dashboard flow.
**Rejected:** SDK display-cache control — retired as a capability proof.

### ADR-7 — No pane automation from Python
**Decision:** startup repairs the GDB and layers and opens the dashboard at a
fixed size; ArcGIS Pro keeps Contents/Geoprocessing pane layout under user
control. **Why:** ArcPy exposes no stable public API to arrange panes; UI/SDK
automation is fragile. **Rejected:** scripted pane layout — solve with setup
instructions if a live demo ever needs it.

### ADR-8 — Unique-value symbology; defer richer renderers
**Decision:** districts render by `district_type`, support layers by
`display_state`, via a `UniqueValueRenderer` (with list/string/CIM field-set
fallbacks for cross-build robustness). **Why:** these channels keep workflow and
identity readable now. **Rejected (deferred):** bivariate activity/exposure
renderer and per-family support layers — overload the same visual channels before
live ArcGIS evidence proves the need.

### ADR-9 — District identity & buyouts: legible first-pass
**Decision:** deterministic refuse → contested → stabilize/convert; conversion
relabels the district and shifts fill; legibility via name/color/news-ticker/
report, not a raw ledger. **Why:** closeable and understandable without exposing
internals. **Rejected (deferred):** multi-bid/round-robin negotiation and ledger-
snapshot RNG keys — larger design, follow-up scope (multi-bid now picked up in
ADR-14).

### ADR-10 — Small public stat model, hidden internals
**Decision:** the player-facing audit goals are exactly four vitals — **Activity /
Friction / Trust / Exposure** — shown on the City Pulse rail under a folded
**City Health** headline, with **Heat** (stakeholder pressure) and a single
**Pressure** causes-rollup as the at-a-glance risk signals. Everything else —
**services**, dissatisfaction, hazards, housing/affordability, maintenance,
population mix, identity, heat bands, economy detail — is internal/derived and
surfaces only in the **scorecard report and inspected cases**, not the always-on
rail (it still influences the four vitals and the audit score). **Why:** a few
legible goals + rich docket/scorecard detail beat many shallow exposed systems;
the same names are used in the rail, the audit report, and the help overlay.
**Rejected:** keeping Services (or any granular support system) on the always-on
rail; surfacing every internal system as a player-facing stat.

### ADR-11 — Deterministic seeded generation
**Decision:** boards and dockets are generated from an RNG keyed on
`seed:turn:district-mix:stats`; docket sampling is weighted-without-replacement
with reserved scenario-priority slots. **Why:** reproducible demos and locked
regression routes (seed `2026`) while staying responsive to city state.
**Rejected:** unseeded randomness — untestable, unrepeatable demos.

### ADR-12 — File-size budget is a soft target
**Decision:** ~1000 lines is the soft review target; the guardrail test only
trips at a 1500 hard ceiling. **Why:** keep files reviewable without forcing
premature splits or comment-stripping when logic genuinely warrants the length.
**Rejected:** a hard 1050 cap — pushed against documentation and cohesive modules.

### ADR-13 — Seeded proposals, not Feature Set drawing
**Decision:** proposed geometry is generated from filed targets and replaced via
`Retarget Map` from the current district selection; no interactive Feature Set
drawing. **Why:** keeps the map the *input* device and the dashboard the
controller, with deterministic, reproducible exhibits. **Rejected:** freehand
Feature Set drawing — non-deterministic and off the command flow.

### ADR-14 — District identity & buyouts: 6B deepening
**Decision:** pick up the multi-bid/round-robin work ADR-9 deferred, plus
names-first text and type/culture-driven proposals. Four parts:

1. **Names everywhere player-facing.** Audit findings, the dashboard incident
   summary, civic-incident previews, and approval/spillover/local-delta report
   prose read as district *names* via `helpers.district_label(profile)` (=
   `name or cell_id`). `cell_id` stays the stable key — `finding_id`,
   `case_json` incident identity, and `affected_cell_ids` are unchanged.

2. **Type + culture shape the docket.** Alongside `TYPE_CATEGORY_WEIGHTS`,
   `GROUP_CATEGORY_WEIGHTS` lets a district's dominant citizen groups (any
   `population_mix` band ≥ `CULTURE_DOMINANT_BAND` = 2) pull proposals toward the
   categories that serve them. The docket RNG key (`_district_mix_key`) now
   encodes both the type histogram and the dominant-culture histogram, so two
   boards with identical types but different mixes diverge. Balanced/neutral
   boards are unaffected.

3. **Multi-bidder negotiation.** `resolve_buyout_round` no longer just takes the
   top-scored eligible neighbor. Each eligible bidder rolls a deterministic
   **willingness** check — `chance = clamp(0.05..1.0, 0.25 + 0.01·advantage +
   0.01·capital + 0.05·appetite − 0.05·fatigue − 0.08·overextension)` where
   `advantage = max(0, bidder.activity − target.activity)` — on a **side RNG
   stream** keyed `willing:{seed}:{turn}:{bidder}:{target}` so it never disturbs
   the shared shuffle/refusal draw order (ADR-11 determinism, existing seeds
   preserved). A flush/eager type clamps to 1.0 and always bids; a marginal one
   often abstains, so a field of eligible neighbors can resolve to one, several,
   or none. The strongest *willing* bidder leads. Refusal is unchanged —
   `chance = clamp(0..0.65, 0.025·(activity−30) − 0.06·pressure)`, i.e. target
   prosperity/leverage — and is reasonable; finer tuning is a live-playtest item.

4. **Conversion shifts type + culture + resources.** On convert, the census
   blends toward the new type's `ARCHETYPE_BASE_MIX` (the new type's two leading
   groups rise into the dominant band, the prior strongest decays) so the
   district reads and *generates proposals* as its new identity. Winning a
   contested field of N costs the winner extra capital (`−3·(N−1)`) and
   overextension (`+(N−1)`) — the upside-with-risk lever.

**Data/schema:** no new persisted fields. All buyout fields
(`identity_state`, `contesting_cell_id`, `buyout_pressure`,
`last_buyout_report`, `prior_district_type`, …) and `population_mix_json` were
already in `schema.py`/`store.py`; existing saved `.gdb`s load unchanged.
**Why:** delivers ADR-9's deferred depth while keeping determinism, save-compat,
and text legibility. **Rejected/deferred:** retuning the refusal curve and
round-robin counter-bidding (want live feedback first); persisting a
ledger-snapshot RNG key (still unnecessary — the side stream is keyed by the
stable pairing).

### ADR-15 - ArcPy stays main-thread; pure workers are allowed
**Decision:** ArcPy cursor work, GDB writes, map/layer operations, and Tk widget
mutation stay on the main Tk/ArcPy thread. Worker threads may run only
ArcPy-free pure-Python work after the main thread has already captured row data
into dataclasses/dicts/lists. Any worker path must have timeout/error fallback
and must never touch `arcpy`, Tk widgets, or live layer objects.
**Why:** live Pro testing showed ArcPy-bound worker probes can freeze or behave
unpredictably, while pure dashboard/cache computation is safe when fed copied
row snapshots.
**Rejected:** threaded ArcPy reads/writes/map refresh; making worker results
authoritative persistence; GP-selectable threading diagnostics in the public
tool.
**Tk teardown:** Tk must also be freed on the thread that made it. ArcPy runs a
full GC pass at the start of every GP tool call, on the calling thread, so a Tk
root left in a reference cycle can be freed on another thread and abort Pro
with `Tcl_AsyncDelete`. `DashboardController.open()` breaks its controller/view
cycle in a `finally` so refcounting frees Tk before `open()` returns. Do not
use `gc.collect()` for this.

### ADR-16 - Display-ring redraw is production, not an experiment
**Decision:** the promoted district/support display-ring redraw path is normal
production behavior. The public toolbox exposes no redraw experiment dropdown or
benchmark runner; `district-ring` remains only as an internal perf label and
layer-ring concept. Failures fall back narrowly to the legacy remove/add/refresh
path for live ArcGIS safety.
**Why:** the ring path is the measured correctness-safe strategy: it prepares a
hidden slot from current GDB truth, applies symbology, swaps visibility only
after success, and avoids accepting stale symbology or blank boards as
performance wins.
**Rejected:** keeping `predrawn-rehydrate` selectable; retaining GP-facing
redraw experiment controls; pure visibility swap, volatile overlay, and SDK
display-cache probes as runtime options. Details live in
`docs/failed-experiments.md`.
**September 30 follow-up:** the ring is still the path below Pro 3.7 and the
fallback above it. On 3.7+ the first step is a query flip on the visible slot
(see the ADR-4 follow-up). From October 1, a feature layer redrawn as part of a
district redraw is requeried in place when it has no slot yet, instead of
seeding a slot.

### ADR-17 - The desk matches the ArcGIS Pro light theme
**Decision:** the Tk desk uses Pro's light theme. `Palette` in `desk_view.py`
holds named tokens sampled from a Pro 3.7 screenshot: frame `#eff0f2`, panes
`#f7f9f8`/`#f9f9f9`, content `#ffffff`, dividers `#e3e4e6`, accent `#005daa`.
Tone colors (good, watch, bad) pass 4.5:1 contrast on every surface. Text uses
Segoe UI at four sizes (`Type`: 8, 9, 11, 14) and spacing comes from one scale
(`Space`: 4/8/12/16/24). Panes are flat with 1 px borders and no drop shadows.
The header is a light title row.
**Why:** the desk sits beside Pro. The old dark-green theme looked like a
different program, and its header values (green on dark green) were unreadable.
**Rules:** no hex colors outside `Palette`. Legend swatches come from
`symbology_config`, so the key always matches the map. Text width is measured
with the Tk font, never estimated from character counts. Tests check header
contrast, stray hex colors, and the number of font sizes.
**Rejected:** ttk or another toolkit (the canvas stays); a dark theme.
