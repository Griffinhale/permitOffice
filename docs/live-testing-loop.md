# Live testing loop: ArcGIS Pro inside a VM

The map bugs that matter here are visual and short. A white flash lasts under a
second. A layer blink lasts about a tenth of one. ArcPy logs cannot see either,
and ArcGIS Pro only runs on Windows while development happens on Linux. The
loop that catches them has three parts: a lead session on the Linux
host, an agent session inside a Windows VM that drives Pro by desktop control,
and an external screen recorder that timestamps every frame it captures.

It has been the main source of evidence for ADR-4, ADR-16, the
`Tcl_AsyncDelete` fix, and the shipped `.lyrx` repoint. The planning files and
the probe scripts are small, single-purpose, and kept in an untracked dev
folder. Their shapes are described below so they can be rebuilt from this page.

## The two machines and the three roles

**Linux lead.** Owns the repo, the dependency graph of work, the offline tests,
the ADRs, and the canonical status of every node. Writes each probe and its
handoff, reads the evidence that comes back, and picks the fix. Never touches
Pro.

**Windows VM.** A Windows 11 guest on the host's private network with ArcGIS
Pro 3.7, a clone of the repo checked out at the same commit as the host, and
SSH so files move with `scp` in both directions. Live runs use a throwaway
project and copied geodatabases, never a real save.

**VM agent session.** A Claude Code session on the guest with desktop control.
It clicks Pro, types into the Python window, runs the game's geoprocessing
tool, takes screenshots, and reads the Contents pane. It writes everything it
produces under an `artifacts/<node>-<sha>/` folder in the guest clone. It makes
no source edits, commits, or planning edits.

## The planning shape

Work is a dependency graph of small nodes kept in a JSON sidecar beside a
generated Markdown view. A checker script validates the sidecar and prints one
node on request. Each node carries:

- `id`, `title`, `kind` (task, spike, or decision), `size`, `lane`, `status`
- `depends_on` and `collides_with`, so lanes do not touch the same files
- `goal`, `problem`, and `read_first`, written for a session with no memory
- `steps`, a `scope_fence`, an `acceptance` sentence, and a `verify` command
- `evidence`, appended as results land, and `notes`, the running lab book

A spike node ends in a recorded result, never a code change. A task node lands
only when the lead has read evidence against its acceptance. Owner decisions
get their own nodes, so a ruling is a dated line and not a chat memory.

A handoff is one pasted block for a fresh session: where you are, what is
done, what to read first, what to do, what not to do, what done means, what to
do on a mismatch, and what to do when finished. It points at the sidecar and
the evidence folders instead of copying them.

## The script shapes

**Recorder.** One file, no dependencies beyond Pillow, run from a separate
Python process outside Pro. It grabs the desktop or a `--region` at a requested
`--fps` for `--seconds`, writes an MJPEG AVI, and writes a journal with one row
per fresh capture holding `utc_before`, `utc_after`, and the AVI slot. It is
bounded in seconds and bytes and finalizes the container on error. A
`--verify` mode decodes the container, every JPEG, and the index. It must run
outside the agent's sandbox: a sandboxed grab fails with `screen grab failed`.

```powershell
& '<Pro python.exe>' -B .\<recorder>.py --seconds 20 --fps 30 --label step4
& '<Pro python.exe>' -B .\<recorder>.py --verify '<path>\screen.avi'
```

**Probe.** One module per question, run from Pro's Python window with the
dashboard closed. One function per single map operation, named `step1`,
`step2`, or by what it does. Each resolves its layer the way production does
(visible ring slot first, then the base layer), performs exactly one call,
and prints the layer name and a millisecond timestamp. None writes to the
geodatabase. The docstring is the run recipe.

**Snapshot and inventory.** One module imported in Pro's Python window. A
`snapshot(label)` exports the render fields of every output layer through a
search cursor to JSON. An `inventory(label)` records every layer's name,
source, definition query, selection, and visibility. A `diff(before, after)`
lists changed object IDs per layer. A `runtime(label)` hashes the loaded
modules to prove the code under test is the code that is running.

**Clip review.** One script that takes an evidence folder and the printed
click timestamp. It rereads the AVI, checks the index and length, selects the
fresh frames inside a window around the click, drops held frames, and emits a
contact sheet plus a `review.json` with the largest capture gap in the window.

**Results.** A plain text file per run. One block per step with the printed
timestamp, which layers changed, for how long, whether white showed, and the
largest capture gap. A capture-limits block. A list of what did not run. A
hash manifest of the package.

**Native logs.** The game's geoprocessing messages carry `PERF` and `REDRAW`
lines when `PERMIT_OFFICE_PERF=1` is set. The game has no real-time clock, so
nothing closes a week inside a trial unless the operator does.

## One cycle

1. The lead writes the probe and a handoff: the exact map, commit, steps,
   and a prediction per step. Predictions are written before any clip exists.
2. The VM session syncs to the commit, proves the loaded code with a runtime
   export, saves the project, sets the environment, and takes a before snapshot.
3. It starts the recorder in its own process, waits two seconds for an idle
   baseline, performs one action, and waits two seconds for settling.
4. It takes the after snapshot, the inventory, the geoprocessing log, and a
   Contents screenshot.
5. It writes the results file and the hash manifest.
6. The folder goes to the host inbox by `scp`. The lead reads the clips and
   the diffs, records evidence in the node, updates the ADR, and writes the
   next handoff.

## Rules that keep the evidence honest

- **One action per clip.** A clip with two operations cannot say which one
  blinked. A combined clear in the AR18 probe was rerun as single clears
  for this reason.
- **Fresh frames only.** The AVI holds the last image across missed captures.
  The journal is the record. The map region captures about 15 fresh frames a
  second, with gaps up to 170 ms under load. A blink seen is positive
  evidence. A blink not seen is bounded by the gap next to the action, so every
  result reports that gap.
- **Predict first.** Each handoff states what the lead expects per step. A
  clip that contradicts the prediction is the useful kind.
- **Not verified is not failed.** A step that did not run, a span crossed by a
  timer tick, or a clip that ended before the click counts toward nothing and
  is reported as such.
- **Throwaway data only.** Copied geodatabases. No row restores or alias
  removal without the owner's say.
- **Crash rule.** Keep the Windows event 1000 entry and the dump, restart Pro,
  and stop after two crashes of the same action.
- **Lead owns status.** A VM report is evidence. The node moves only when the
  lead has read the clips and the diffs.

## What the loop has settled

- `arcpy.RefreshLayer` on a visible layer repaints the whole map, about 0.9
  seconds of white (AR15). Feature-only decisions now toggle the layer's
  definition query instead (AR16).
- The district definition-query flip drops every feature layer's geometry for
  about 0.2 seconds, with labels and basemap kept (AR15).
- A geoprocessing `CLEAR_SELECTION` on an empty, hidden layer changes nothing.
  Clearing a non-empty selection with `setSelectionSet` drops the cleared
  layers for about 0.1 seconds (AR15, AR18).
- Moving points above lines in Contents did not stop the lines blink, so the
  draw-order theory was retired (AR18, owner ruling D8).
- Closing the dashboard freed Tk on the wrong thread and crashed Pro with
  `Tcl_AsyncDelete`; the fix was witnessed by a `GetCount` after close (AR17,
  AR19).
- Shipped `.lyrx` files carried a stale geodatabase path; repointing through
  the layer's CIM connection was witnessed on a cold New Game (AR20).

A lighter version of the same loop checks the dashboard's look: screenshots
of both tabs at the default and minimum window sizes on Pro's Tk 8.6 and
Segoe UI, since Linux previews use substitute fonts and Tk 9.

## Limits

- About 15 captures a second is the useful temporal resolution. The recorder
  bounds visible change between captures. It cannot see Pro's render-complete
  event, and API timing is not rendered duration.
- JPEG compression, tooltips, the cursor, and dashboard overlays all move
  pixels. Reviews look for layer loss and white, not raw pixel difference.
- Recording costs the guest CPU. Timing claims need an idle baseline and a
  matched unrecorded trial.
- Desktop control can time out on a busy guest. When it does, the session
  records the failure and asks the owner to restart Pro rather than retrying
  input blind.
- Launch the dashboard through the game's geoprocessing tool, never from
  Pro's Python window. A Tk dashboard hosted in the Python window keeps the
  map view from repainting until it closes, and environment variables set
  there never reach the tool. Set `PERMIT_OFFICE_PERF` (and
  `PERMIT_OFFICE_LOG_FILE` when needed) in the shell that starts Pro.
- One operator, one Pro instance. The harness has no queue runner, no
  auto-restart, and no replay of an incomplete action, on purpose.
