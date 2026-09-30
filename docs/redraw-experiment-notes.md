# Live ArcGIS Redraw Experiment Notes

Canonical notes for redraw experiments that changed or nearly changed the map
refresh strategy. Superseded spike designs live in `docs/archive/`; this
file records only live findings that should survive.

## 2026-06-08 rehydrate/swap benchmark

**Setup:** ArcGIS Pro live run, `Log Refresh Timings` enabled, saved
`permit_office.gdb`, `Redraw Benchmark Runs = 1`.

### Baselines

- Old district-family remove/add was correct but commonly spent about 4-5s in
  rebuild work.
- `predrawn-rehydrate` preserved district correctness and reduced hot redraws,
  but point decisions still paid for `PermitPoints` remove/add.
- Pure `predrawn-swap` remained the fastest path (`~0.4-0.7s` benchmark rebuilds)
  but can hold stale district symbology because it does not re-read district
  attributes from the GDB.
- `volatile-overlay` was rejected as a default because it can leave the board
  incomplete/filtered.

### Variant results

- `predrawn-rehydrate-smart-features` was the strongest correctness-safe
  candidate. It rehydrates districts but strips `PermitPoints` out of the live
  remove/add scope, relying on `RefreshLayer('PermitPoints')` for point redraws.
  Live benchmark: `districts+points` rebuilt in `1.789s`, versus roughly
  `2.85-3.83s` for normal rehydrate in the same session.
- `predrawn-swap-refresh` was very fast (`~1.0-1.3s` rebuilds during benchmark
  and normal play). During play it did not obviously corrupt the board, but after
  closing/completion the map showed red/gray district display-state styling over
  the whole board. Treat this as a correctness bug, not a default candidate.
- `hybrid-rehydrate-districts-swap-points` did not win. Initial point snapshot
  seeding and all-layer cases were slower than smart-features (`~3.3-3.9s` in
  benchmark), so keep it only as a diagnostic experiment.
- `predrawn-rehydrate-template-style` and hidden-first rehydrate remained viable
  probes but did not beat `predrawn-rehydrate-smart-features` for point decisions.
- `style-cache` did not deliver reliable savings because removing/re-adding the
  layer loses the Python-side style marker.

### Decision

Do not promote `predrawn-swap-refresh` yet, despite good timings. Promote only a
path that preserves visual correctness after decisions, week advance, and
dashboard close/completion. At the time, the next promotion candidate was
`predrawn-rehydrate-smart-features`; it was superseded by `district-ring` on
2026-06-10.

Keep the fast swap probes in the GP dropdown for diagnosis, but prune them from
default behavior until the close-state red/gray rendering bug is understood.

## 2026-06-10 district/support ring promotion

**Setup:** ArcGIS Pro live runs during the precomputed-decision-cache spike, with
`Log Refresh Timings` enabled against project-local `permit_office.gdb` saves.

### Findings

- `district-ring` is now the promoted default. It keeps three numeric district
  slots (`Permit Office Predrawn 0/1/2`), prepares one hidden slot from the GDB,
  applies district-type symbology, then swaps visibility only after successful
  preparation. The previous visible slot remains available on failure.
- Support layers now use matching point/line/zone rings. Existing visible support
  slots first try cheap `RefreshLayer`; failures fall back to ring rehydrate and
  then to legacy remove/add.
- The older `predrawn-rehydrate` path is now retired from runtime code. Its notes
  remain useful historical evidence, but live fallback is the narrow legacy
  remove/add/refresh path after a ring failure.
- Future-cache data is speculative only. Real decisions still resolve against
  current state, write the GDB once, then drive map work from the actual
  `DecisionResult` plus cache hints.

### Perf labels to watch

- `ring_redraw` - total ring redraw path.
- `RefreshLayer` - district slot refresh after visibility swap.
- `feature_PermitPoints_ring_refresh` - cheap support-ring refresh path.
- `feature_PermitPoints_ring_rehydrate` - fallback support-ring rehydrate path.

### Decision

Keep `district-ring` as the default and continue optimizing inside that strategy.
Do not accept stale district symbology, blank boards, or one layer stack per
speculative branch as performance wins.
