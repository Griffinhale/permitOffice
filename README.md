# Permit Office

Permit Office is a turn-based city planning game that runs inside ArcGIS Pro.
You play as a municipal permit clerk trying to keep a strange city functional
through inspections, approvals, denials, mitigation conditions, and weekly audit
reports.

The premise is a joke about bureaucracy. The rules are not: every permit is tied
to map geometry, district state, stakeholder pressure and recurring costs, and
its consequences show up on the map.

> **Status: public beta, v0.95.0.** Playable end to end. `main` has a dashboard
> redesign that hasn't been through a live ArcGIS Pro test yet; use the v0.95.0
> release for the tested build. Running the game needs ArcGIS Pro 3.3+ with ArcPy
> (tested on 3.6). The pure-Python rules and tests run without it.

![Permit Office running as a geoprocessing tool in ArcGIS Pro, with the labeled
district grid and two districts selected from the active docket](docs/images/01-arcgis-engine-selection.png)

![The district grid: each tile is a district, and approved permits add the point
and line features drawn on it](docs/images/02-district-board.png)

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
A season runs 12 weeks. Each week brings more cases than your action points can
cover, and the cases you leave unresolved keep pushing the city.

## Why I Built It

Permit Office uses ArcGIS Pro as a game engine. Feature classes are the save
file, map selections are the input, and ArcPy geometry operations are part of
the rules.

The longer aim is live-updating city simulations built from the standard ArcGIS
tools analysts already use, for work like disaster readiness, service
disruption and emergency rerouting. A game is a demanding first test of that
idea, because it has to stay correct and responsive while the map changes every
turn.

It is also a small study in paperwork as play. You are a permit office, not a
mayor, and you shape the city by filing, approving, delaying and explaining
official decisions.

## Design Notes

- **The geodatabase is the game state.** Districts, docket rows, projects,
  commands, logs and permit features all live in one file geodatabase.
- **Space drives the rules.** Selected districts, adjacency, buffers and feature
  geometry decide who a decision affects.
- **The rules are plain Python.** The simulation runs and tests without ArcGIS
  Pro; ArcPy handles storage, spatial analysis and the map.
- **Cities come from a seed.** District names, populations, services,
  grievances, hazards, housing pressure, stakeholder heat and the docket are
  all generated, and the same seed gives the same city.
- **Districts change hands.** District type and citizen culture shape which
  proposals appear, and ignored proposals build hidden momentum. A quiet district
  can be bid for by stronger neighbors, and a successful buyout changes its type,
  culture and name.
- **The tone is dry.** The interface looks like a cluttered permit desk, with
  filed reports and audit language.
- **It works within ArcGIS Pro's limits.** ArcPy and the map have to stay on one
  thread, and before Pro 3.7 `RefreshLayer` didn't reload changed attributes. So only
  ArcPy-free work runs in the background, from copied rows. A decision cache
  predicts which districts and layers each choice will change, and a three-slot
  display ring rebuilds a hidden layer and shows it only once the rebuild
  succeeds. That replaced a 4-5 second full redraw. The measured alternatives and
  why each was dropped are in
  [`docs/failed-experiments.md`](docs/failed-experiments.md).

## Quick Start

### Requirements

- **ArcGIS Pro with ArcPy** to run the game. Developed and tested on **ArcGIS
  Pro 3.6 and 3.7**; **3.3+** is the practical floor. The only version-gated arcpy
  call is `arcpy.RefreshLayer` (added at Pro 3.3, and already wrapped in a guard, so
  older builds degrade gracefully rather than crash). The faster district redraw
  (a definition-query flip) turns on only at Pro 3.7+, where it was tested; older
  builds use the display ring. Everything else is Pro 2.x-era.
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
geodatabase is the save file. The decision cache is rebuilt from it and never
saved. Don't commit the generated `.gdb`.

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
- `docs/decisions.md` - concise ADRs: choices made and alternatives rejected.
- `docs/failed-experiments.md` - probes that were tried live and retired, and why.
- `docs/arcpy-usage.md` - which stock ArcPy APIs we use and how (cursors, schema,
  geometry, map refresh/redraw, selection).
- `docs/docket-items.md` - docket template/item shape with worked examples.
- `docs/writing-and-tone.md` - the municipal voice and real copy examples.
- `docs/arcgis-pro-smoke-checklist.md` - the manual live test in ArcGIS Pro.

## Current Status

**Playable:** generated districts and seeded city detail, weighted docket
templates driven by district type and citizen culture, inspections, approvals,
mitigation, denials, incidents, maintenance follow-ups, recurring economy,
multi-week projects, district identity and multi-bidder buyouts, a final audit,
and a Tkinter dashboard.

**Redraw:** a district display ring plus point/line/zone rings. Decisions resolve
against the geodatabase and write it once; redraw planning refreshes only the
layers that decision changed. If a ring fails, the toolbox falls back to the
older remove/add/refresh path.

**Not yet verified live:** the ArcGIS Pro smoke checklist (workspace routing,
feature-ring repaint, cold-start resume, legacy `.gdb` migration, symbology), a
fair 12-week balance route, and the dashboard changes listed under Unreleased in
[`CHANGELOG.md`](CHANGELOG.md).

## License

Released under the MIT License — see [`LICENSE`](LICENSE). This covers the game
code in this repository; running it still requires your own licensed ArcGIS Pro
/ ArcPy installation, which is not included.
