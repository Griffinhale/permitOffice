# Permit Office

Permit Office is a turn-based city planning game that runs inside ArcGIS Pro.
You play as a municipal permit clerk trying to keep a strange city functional
through inspections, approvals, denials, mitigation conditions, and weekly audit
reports.

The premise is a joke about bureaucracy. The rules are not: every permit is tied
to map geometry, district state, stakeholder pressure and recurring costs, and
its consequences show up on the map.

> **Status: public beta.** v0.95.0 is the last tested release. The
> `docs/presentation` branch reworks the loop (seeded goals, an audit ladder,
> player initiatives, no real-time clock) and the dashboard (a narrow,
> resizable pane); its live ArcGIS Pro check is pending. Running the game needs
> ArcGIS Pro 3.3+ with ArcPy (tested on 3.6 and 3.7). The pure-Python rules and
> tests run without it.

![Permit Office running as a geoprocessing tool in ArcGIS Pro, with the labeled
district grid and two districts selected from the active docket](docs/images/01-arcgis-engine-selection.png)

![The district grid: each tile is a district, and approved permits add the point
and line features drawn on it](docs/images/02-district-board.png)

## How It Plays

A season is 12 office weeks. The clerk has 2 AP a week, a small budget, and a
council that audits the office at weeks 4, 8 and 12.

1. **Pick a goal.** New Game offers three goals drawn from the seed, such as
   growing one district type by three districts, quieting every grievance, or
   raising city trust to 55. Meet the one you file by week 12 to win the season.
   Any other goal the city happens to meet counts as an achievement.
2. **Work the docket.** Two new cases arrive each week, plus follow-ups. Select
   one to draw its proposed exhibit and select its target districts on the map.
   Inspect it (1 AP) to reveal risk, then issue it, issue it with conditions,
   or deny it. Each case says what happens if you ignore it: it returns, expires,
   or expires and leaves trouble behind.
3. **Start one initiative.** Once a week, earmark a district type so it wins
   neighbor buyouts and its kind of case comes up more often, or fund a civic
   action or a market push on the districts selected on the map.
4. **End the week.** Ignored cases build district pressure, heat and grievance.
   Features age, the economy settles, districts drift, and stronger neighbors
   bid for weak districts.
5. **Survive the audits.** A failed checkpoint audit costs council patience
   (warning, then sanctioned with one AP less, then dismissed). A pass wins it
   back.

Approvals can spawn points, lines, or polygons on the map: vendor markets,
utility trenches, fire coverage areas, public art grants, corridors, reserves,
incidents, inspection orders, and other civic paperwork with consequences.

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
- **Python 3.11+** for the pure-rules test suite (this runs without ArcGIS).
  With [uv](https://docs.astral.sh/uv/):

  ```bash
  uv venv
  uv pip install -r requirements-dev.txt
  uv run pytest -q
  ```

  Or with pip: `python3 -m pip install -r requirements-dev.txt` then
  `python3 -m pytest -q`. pip and uv only set up the offline tests; the game
  itself runs on ArcGIS Pro's bundled Python and arcpy.

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
5. If no saved game exists, click `New Game` in the dashboard and pick one of
   the three goals.
6. Keep the dashboard beside the map: select cases, retarget them from map
   selections, inspect, issue or deny, start the week's initiative, and end
   the week.

By default (no **Game Workspace** chosen) the tool creates or resumes
`permit_office.gdb` under the ArcGIS project's `data/` folder; set the optional
**Game Workspace** parameter to use a specific geodatabase or folder instead. The
geodatabase is the save file. The decision cache is rebuilt from it and never
saved. Don't commit the generated `.gdb`.

### Run Pure Python Tests

```bash
uv run pytest -q          # or: python3 -m pytest -q
```

The tests cover the ArcPy-free rules and lightweight ArcGIS adapter shims. They
do not replace a live ArcGIS Pro smoke test
([`docs/arcgis-pro-smoke-checklist.md`](docs/arcgis-pro-smoke-checklist.md)).

### Preview The Dashboard Without Pro

```bash
uv run --with pillow python tools/desk_preview.py --out /tmp/desk
```

This draws the real dashboard from fixed game states to PNGs at the default and
minimum pane sizes. It needs Tk and, without a display, Xvfb. Fonts are a
stand-in for Segoe UI, so spacing is close, not exact.

## Repository Map

- `toolbox/arcpy_permit_office.pyt` - ArcGIS Pro toolbox entrypoint.
- `toolbox/permit_office/` - ArcPy-free gameplay rules, catalogs, decisions,
  turn advancement, audits, season goals (`mandates.py`), initiatives
  (`initiatives.py`), and city systems.
- `toolbox/permit_office_arcgis/` - ArcPy/Tkinter adapter: schema, geodatabase
  store helpers, geometry operations, symbology, dashboard command flow,
  redraw planning, and district/support display rings.
- `tests/` - regression tests for the rules and ArcGIS adapter shims, plus the
  preview scenarios in `tests/fixtures/desk_preview/`.
- `tools/desk_preview.py` - renders the dashboard to PNG without ArcGIS Pro.
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
- `docs/live-testing-loop.md` - how sub-second map bugs get caught: a lead on
  Linux, an agent driving Pro inside a Windows VM, and a frame-stamped recorder.

## Current Status

**Playable offline, pending live check:** seeded goals, the week 4/8/12 audit
ladder, two cases a week, weekly initiatives, no real-time clock, and the
narrow dashboard pane. The rules and the pane are covered by the offline tests
and previews; the live ArcGIS Pro pass is the next step.

**Verified live (v0.95 and the ArcPy review):** generated districts and city
detail, weighted dockets, inspections, approvals and denials, incidents,
maintenance, recurring economy, projects, buyouts, the final audit, shipped
`.lyrx` layer styles, and a week-close redraw that drops each feature layer
once with no white flash. How those map bugs were caught is in
[`docs/live-testing-loop.md`](docs/live-testing-loop.md).

**Open:** a fair 12-week balance pass for the new goals and initiatives, and
the week-close redraw batching probe.

## License

Released under the MIT License — see [`LICENSE`](LICENSE). This covers the game
code in this repository; running it still requires your own licensed ArcGIS Pro
/ ArcPy installation, which is not included.
