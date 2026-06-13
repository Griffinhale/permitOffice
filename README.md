# Permit Office

Permit Office is a turn-based city planning game that runs inside ArcGIS Pro.
You play as a municipal permit clerk trying to keep a strange city functional
through inspections, approvals, denials, mitigation conditions, and weekly audit
reports.

The joke is bureaucratic, but the game loop is real: every permit is tied to
map geometry, district state, stakeholder pressure, recurring costs, and visible
city consequences.

> **Status: public beta - v0.95.0.** Playable end to end; the current work is
> focused on ArcGIS Pro redraw polish, packaging, and balance. Requires ArcGIS
> Pro 3.3+ with ArcPy to run (tested on 3.6); the pure-Python rules run and test
> without it.

## How It Plays

Each office week gives you a small docket of permit applications, incidents, or
follow-up orders.

1. Select a docket item in the dashboard.
2. Review its proposed map exhibit and selected district targets.
3. Optionally inspect the file to reveal risk, evidence, violations, and local
   population context.
4. Issue the permit, issue it with mitigation conditions, deny it, or leave it
   unresolved until the week closes.
5. ArcPy applies the result to districts, support features, stakeholder heat,
   population grievances, recurring revenue/upkeep, maintenance, and audit risk.
6. At the end of the run, the city receives an audit scorecard.

Approvals can spawn points, lines, or polygons on the map: vendor markets,
utility trenches, fire coverage areas, public art grants, corridors, reserves,
incidents, inspection orders, and other civic paperwork with consequences.
The current rules target a 12-week civic season with scarce AP, more docket
items than the player can fully process, and city momentum from unresolved
cases.

## Why I Built It

Permit Office is an experiment in using ArcGIS Pro as a game engine rather than
only a mapping tool. Feature classes are the save file, map selections are the
input device, and ArcPy geometry operations become part of the rules system.

The project is also a small design study in "paperwork as play": the player is
not an all-powerful mayor, but an audit-facing office that shapes the city by
filing, approving, delaying, and explaining official decisions.

## What Is Interesting

- **ArcGIS-native game state:** districts, docket rows, projects, commands,
  logs, and permit features live in a file geodatabase.
- **Spatial consequences:** selected districts, adjacency, buffers, feature
  geometry, and support layers drive gameplay effects.
- **Pure Python rules:** the main simulation is testable without ArcGIS Pro,
  while ArcPy handles persistence and map operations.
- **Procedural civic texture:** district names, populations, services,
  grievances, hazards, housing pressure, stakeholder heat, and docket items are
  generated from a seed.
- **District identity pressure:** district type *and* citizen-culture mix
  influence which proposals appear, ignored proposals can create hidden momentum,
  and low-activity districts can enter contested buyout transitions where multiple
  stronger neighbors bid — a successful buyout shifts the district's type,
  culture, and name.
- **Dry municipal absurdism:** the interface is built like a cluttered permit
  desk, with filed reports and audit language instead of fantasy UI tropes.

## Quick Start

### Requirements

- **ArcGIS Pro with ArcPy** to run the game. Developed and tested on **ArcGIS
  Pro 3.6**; **3.3+** is the practical floor. The only version-gated arcpy call is
  `arcpy.RefreshLayer` (added at Pro 3.3, and already wrapped in a guard, so older
  builds degrade gracefully rather than crash); everything else is Pro 2.x-era.
- **Python 3** for the pure-rules test suite (this runs without ArcGIS). Install
  the test dependency (`pytest`) with **either** pip or
  [uv](https://docs.astral.sh/uv/):

  ```bash
  # pip
  python3 -m pip install -r requirements-dev.txt

  # uv (creates an isolated .venv, then installs)
  uv venv
  uv pip install -r requirements-dev.txt
  ```

  pip/uv only set up the **offline test** environment — running the game itself
  uses ArcGIS Pro's bundled Python and arcpy, not a pip/uv install.

### Run In ArcGIS Pro

For a downloaded release, unzip the folder first. In ArcGIS Pro, add the
unzipped folder to the project by dragging it into the **Contents** pane or by
adding it as a folder connection from the Catalog pane. Then open the Python
toolbox inside that folder.

1. Open an ArcGIS Pro project.
2. Add or open `toolbox/arcpy_permit_office.pyt` from the unzipped/repo folder.
3. Run `Permit Office Prototype`.
4. Leave **Game Workspace** empty to create/resume the project-default save, or
   choose a folder/`.gdb` for a separate save.
5. If no saved game exists, click `New Game` in the dashboard.
6. Use the dashboard and map together: select docket rows, update targets from
   map selections, inspect files, issue or deny permits, and end the week.

By default (no **Game Workspace** chosen) the tool creates or resumes
`permit_office.gdb` under the ArcGIS project's `data/` folder; set the optional
**Game Workspace** parameter to use a specific geodatabase or folder instead. The
geodatabase *is* the save file and remains authoritative. Speculative decision
futures are cache data only; they are rebuilt from the GDB and never replace it
as persistence. The generated `.gdb` is local state and should not be committed.

### Run Pure Python Tests

```bash
python3 -m pytest -q     # or: uv run pytest -q
```

The tests cover the ArcPy-free rules and lightweight ArcGIS adapter shims. They
do not replace a live ArcGIS Pro smoke test.

## Repository Map

- `toolbox/arcpy_permit_office.pyt` - ArcGIS Pro toolbox entrypoint.
- `toolbox/permit_office/` - ArcPy-free gameplay rules, catalogs, decisions,
  turn advancement, audits, city systems, cache keys, dirty scopes, speculative
  future nodes.
- `toolbox/permit_office_arcgis/` - ArcPy/Tkinter adapter: schema, geodatabase
  store helpers, geometry operations, symbology, dashboard command flow,
  hydrated redraw planning, and district/support display rings.
- `tests/` - regression tests for the rules and ArcGIS adapter shims.
- `docs/` - the core reference set (see below).

## Documentation

- `docs/systems-overview.md` - architecture, persisted state, stat model, and the
  turn loop. Start here.
- `docs/docket-items.md` - docket template/item shape with worked examples.
- `docs/arcpy-usage.md` - which stock ArcPy APIs we use and how (cursors, schema,
  geometry, map refresh/redraw, selection).
- `docs/writing-and-tone.md` - the municipal voice and real copy examples.
- `docs/decisions.md` - concise ADRs: choices made and alternatives rejected.

## Current Status

Permit Office is a playable prototype. It has generated districts, seeded city
detail, weighted docket templates (driven by district type and citizen culture),
inspections, approvals, no-AP ordinary denials, mitigation, incidents,
maintenance follow-ups, recurring economy, projects, audits, map symbology,
human-readable district identity with multi-bidder buyout pressure, start/help
flow, selected-case exhibit controls, an inline final audit receipt, and a
Tkinter dashboard.

The current ArcGIS redraw path uses a small **district display ring** plus
support-feature rings for points/lines/zones. Real decisions still resolve
against current GDB-backed state and write the GDB once; redraw planning then
uses dirty scopes and cache hints to refresh only the relevant display layers.
If the display ring fails, the toolbox falls back to the older remove/add/refresh
path for live ArcGIS safety.

The next public-readiness work is focused on evidence and balance: running the
live ArcGIS Pro smoke test (`docs/arcgis-pro-smoke-checklist.md`: workspace
routing, feature-ring repaint, cold-start resume, legacy `.gdb` migration,
symbology), tuning a fair 12-week route, and deepening map symbology only where
the live map proves it is still hard to read.

See `docs/systems-overview.md` for the implementation map, current status, and
the live ArcGIS validation walkthrough.

## License

Released under the MIT License — see [`LICENSE`](LICENSE). This covers the game
code in this repository; running it still requires your own licensed ArcGIS Pro
/ ArcPy installation, which is not included.
