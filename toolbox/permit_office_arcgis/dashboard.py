"""Tkinter dashboard controller for ArcGIS-hosted Permit Office sessions."""

from __future__ import annotations

import time
import traceback
import threading
from concurrent import futures as concurrent_futures
from copy import deepcopy
from dataclasses import replace

import arcpy

from ._perf import perf_block, perf_session
from .city_features import seed_city_features
from .map_layers import add_outputs_to_map, ensure_active_map, output_layers_present, refresh_all, remove_outputs_from_map
from .proposals import (
    activate_proposal,
    case_proposal_visible,
    ensure_case_proposal,
    hide_case_proposal,
    insert_or_replace_proposal,
    mark_proposals,
    proposal_spillover,
    proposal_visible_map,
    select_case_context,
    selected_cell_ids,
)
from .messages import _log, _warn
from .rules_loader import rules
from .schema import DISTRICTS, LINES, POINTS, ZONES, clear_game_rows
from .store import (
    action_log,
    command_finish,
    command_insert,
    create_district_board,
    generate_docket_rows,
    read_active_features,
    read_districts,
    read_docket,
    read_projects,
    read_state,
    write_active_features,
    write_district_updates,
    write_docket_item,
    write_projects,
    write_state,
)
from . import desk_model
from .desk_model import report_sections
from .desk_view import DEFAULT_DESK_SIZE, MIN_DESK_H, MIN_DESK_W, DeskCallbacks, Palette, PermitDeskView, ReceiptModel, ReportTab, Type, build_desk_model, desk_font, receipt_metrics
from .map_redraw import rebuild_output_layers
from .redraw_plan import (
    DIRTY_DESK_ONLY,
    DIRTY_DISTRICTS,
    _GEOM_TYPE_TO_LAYER,
    _decision_layer_names,
    _feature_layer_key_for_item,
    _week_close_redraw_layers,
    hydrate_decision_redraw_plan,
    layer_names_for_plan,
    selection_layers_for_item,
)


TICKER_TICK_MS = 90
TICKER_STEP_PX = 3
STATUS_TEXT_HOLD_SECONDS = 6.0
QUEUE_AUTOCLOSE_SECONDS = 2
PURE_WORKER_TIMEOUT_SECONDS = 1.0
STARTUP_SESSION_DELAY_MS = 50
# _finish_decision passes this when the next case could not be read before the redraw.
_NEXT_UNREAD = object()
_pure_worker_state = threading.local()


def _pure_worker_active():
    """Return True while an ArcPy-free worker is active."""

    return bool(getattr(_pure_worker_state, "active", False))


def _run_pure_worker(fn):
    """Run one ArcPy-free worker while marking thread-local worker state."""

    _pure_worker_state.active = True
    try:
        return fn()
    finally:
        _pure_worker_state.active = False


def _pure_startup_precompute(state, districts, items, active_features, saved_game):
    """Precompute the pure dashboard model from already-read row snapshots."""

    build_desk_model(
        state,
        districts,
        items,
        "",
        "",
        {},
        active_features=active_features,
        game_active=saved_game,
    )
    return "dashboard-model"


def prepare_dashboard_session(paths, seed, messages, resume=True):
    """Resume or stage a session before the dashboard opens.

    Treats the geodatabase as the canonical save: when ``resume`` and the save
    rows exist we re-add the output layers and regenerate the docket only if it
    is empty. ``resume`` is False when the caller detected an empty map (no
    Permit Office layers) -- then the saved board is left untouched in the .gdb
    and the dashboard opens offering a fresh start instead of silently resuming.
    Returns the seed unchanged so the caller can thread it into the controller.
    """

    if resume and has_saved_game(paths):
        add_outputs_to_map(paths, messages)
        if _row_count(paths["docket"]) == 0:
            generate_docket_rows(paths, seed, messages)
            refresh_all(paths, messages)
        _log(messages, "DASH", "resuming saved Permit Office game")
    elif has_saved_game(paths):
        _log(messages, "DASH", "saved game present but no Permit Office layers on map; offering fresh start")
    else:
        _log(messages, "DASH", "no saved Permit Office game found; open dashboard start screen")
    return seed


def has_saved_game(paths):
    """Return True when both the district board and city state have rows.

    Those two tables are written together by New Game, so their joint presence
    is the cheapest reliable "a game exists here" signal.
    """

    return _row_count(paths.get("districts", "")) > 0 and _row_count(paths.get("state", "")) > 0


def _row_count(path):
    """Return the feature/row count for a table, or 0 if it can't be counted.

    Swallows arcpy errors (missing/locked table) so callers can treat an
    unreadable table as simply empty rather than crashing dashboard startup.
    """

    try:
        return int(arcpy.management.GetCount(path)[0])
    except Exception:
        return 0


def _configure_dashboard_window(root):
    """Open the desk as a narrow pane at the screen's top left, resizable down to the floor."""

    root.minsize(MIN_DESK_W, MIN_DESK_H)
    root.geometry(_startup_geometry(root))
    try:
        root.resizable(True, True)
    except Exception:
        pass


def _startup_geometry(root):
    """Return the default pane geometry, shortened to fit a small screen."""

    try:
        screen_h = int(root.winfo_screenheight())
    except Exception:
        screen_h = 1080
    width, height = DEFAULT_DESK_SIZE
    height = max(MIN_DESK_H, min(height, screen_h - 80))
    return f"{width}x{height}+0+0"


class _StatusProxy:
    """Tk-StringVar-shaped adapter that reads/writes controller.status_text.

    Lets view code use the familiar `.set()`/`.get()` status-variable API while
    the single source of truth stays the controller attribute (so a reload can
    render the current status without a separate Tk variable to keep in sync).
    """

    def __init__(self, controller):
        """Bind the proxy to the controller whose status_text it mirrors."""

        self.controller = controller

    def set(self, value):
        """Store a coerced, non-None status string on the controller."""

        self.controller._set_status_text(value)

    def get(self):
        """Return the controller's current status string."""

        return self.controller.status_text




class DashboardController:
    """Owns the Tkinter desk UI and the per-action command flow.

    This is the seam between the pure rules (toolbox/permit_office/) and ArcGIS:
    every player action reads game rows via store.py, resolves effects through
    `rules`, writes the results back, rebuilds/refreshes the affected map layers,
    and reloads the desk view. All work runs on the single Tk thread; there is
    no background I/O (see docs/decisions.md). The geodatabase, not this object,
    is the source of truth — instance state here is just UI selection/session
    bookkeeping that `reload()` re-derives from persisted rows.
    """

    def __init__(
        self,
        paths,
        district_layer,
        seed,
        messages,
        offer_fresh_start=False,
    ):
        """Wire paths, the district layer name, the run seed, and GP messages.

        Initializes UI/session state (selection, report tabs, queue auto-close,
        command-busy guard); the geodatabase rows are read later in reload().
        offer_fresh_start is True when the launcher detected a save in this
        workspace but no Permit Office layers on the map: the desk then opens on a
        clean start posture (New Game) instead of surfacing the old board.
        """

        self.paths = paths
        self.district_layer = district_layer
        self.seed = seed
        self.messages = messages
        self._offer_fresh_start = offer_fresh_start
        self._startup_session_prepared = False
        self._startup_session_scheduled = False
        self._pure_precompute_ran = False
        self.status_text = ""
        self._status_hold_until = 0.0
        self.selected_item_id = ""
        self.last_receipt = None
        self.report_tabs = []
        self.selected_report_id = ""
        self.selected_desk_tab = "applications"
        self._report_counter = 0
        self._report_week = 0
        self._show_help = False
        self._queue_autoclose_after_id = None
        self._queue_autoclose_active = False
        self._queue_autoclose_seconds = 0
        self._newgame_overlay = None
        self._ticker_after_id = None
        self._ticker_index = 0
        self._ticker_offset_px = 0
        self._command_busy = False
        # Audit grade is expensive (scorecard + two deepcopies of 25 districts).
        # Cache it and recompute only when a write path marks it dirty, so
        # selection-only reloads reuse the last computed grade.
        self._audit_grade = None
        self._grade_dirty = True
        # City stats and money at the start of the shown week, kept in memory so
        # the desk's trends are real deltas (no saved history exists).
        self._week_start = {}
        self._week_start_turn = 0

    def _run_pure_precompute_once(self, state, districts, items, active_features, saved_game):
        """Run ArcPy-free startup precompute from main-thread row snapshots."""

        if self._pure_precompute_ran:
            return
        self._pure_precompute_ran = True
        started = time.perf_counter()
        executor = concurrent_futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="permit-startup")
        active_future = None
        state_snapshot = deepcopy(state)
        districts_snapshot = deepcopy(districts)
        items_snapshot = deepcopy(items)
        active_features_snapshot = deepcopy(active_features)
        try:
            active_future = executor.submit(
                _run_pure_worker,
                lambda: _pure_startup_precompute(
                    state_snapshot,
                    districts_snapshot,
                    items_snapshot,
                    active_features_snapshot,
                    saved_game,
                ),
            )
            result = active_future.result(timeout=PURE_WORKER_TIMEOUT_SECONDS)
            elapsed = time.perf_counter() - started
            _log(self.messages, "STARTUP", f"pure worker precompute={result} elapsed={elapsed:.3f}")
        except concurrent_futures.TimeoutError:
            if active_future is not None:
                active_future.cancel()
            _warn(self.messages, "STARTUP", "pure worker precompute timed out; continuing synchronously")
        except Exception as exc:
            _warn(self.messages, "STARTUP", f"pure worker precompute failed: {exc}; continuing synchronously")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def schedule_startup_session_preparation(self):
        """Schedule map/session preparation after the first dashboard frame."""

        if self._startup_session_scheduled:
            return
        self._startup_session_scheduled = True

        def _after_paint_delay():
            try:
                self.root.after(STARTUP_SESSION_DELAY_MS, self.prepare_startup_session)
            except Exception:
                self.prepare_startup_session()

        try:
            self.root.after_idle(_after_paint_delay)
        except Exception:
            _after_paint_delay()

    def prepare_startup_session(self):
        """Run deferred ArcGIS map/session work after initial Tk rendering."""

        if self._startup_session_prepared:
            return
        self._startup_session_prepared = True
        with perf_session("startup_session", self.messages):
            with perf_block("pure_precompute"):
                state, districts, items, active_features, offering_fresh = self._resolve_session_state(
                    None, None, None, None
                )
                saved_game = has_saved_game(self.paths) and not offering_fresh
                self._run_pure_precompute_once(state, districts, items, active_features, saved_game)
            with perf_block("active_map"):
                ensure_active_map(self.messages)
            with perf_block("saved_game_probe"):
                offer_fresh_start = has_saved_game(self.paths) and not output_layers_present()
            if offer_fresh_start:
                self._offer_fresh_start = True
                _log(self.messages, "DASH", "saved game present but no Permit Office layers on map; offering fresh start")
                self.reload()
                return
            with perf_block("dashboard_session"):
                self.seed = prepare_dashboard_session(self.paths, self.seed, self.messages, resume=True)

    def open(self):
        try:
            import tkinter as tk
        except Exception as exc:
            raise RuntimeError(f"tkinter is not available: {exc}")

        self.root = tk.Tk()
        try:
            self.root.title("Permit Office")
            _configure_dashboard_window(self.root)
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                pass

            self.selected_item_id = ""
            self.status_text = ""
            self.status_var = _StatusProxy(self)
            # The view owns drawing and hit targets; the controller owns actions
            # that mutate ArcGIS-backed game state.
            self.view = PermitDeskView(self.root, self._desk_callbacks(), self.select_item)

            self.reload()
            self.schedule_startup_session_preparation()
            self._schedule_ticker_tick()
            self.root.mainloop()
        finally:
            self._release_tk()

    def _desk_callbacks(self):
        """Return the view's action callbacks, all bound to this controller."""

        return DeskCallbacks(
            toggle_exhibit=self.toggle_exhibit,
            update_from_map=self.update_from_map,
            inspect=self.inspect,
            approve=lambda: self.apply_decision("approve", False),
            approve_mitigated=lambda: self.apply_decision("approve_mitigated", True),
            deny=self.deny,
            advance_turn=self.advance_turn,
            new_game=self.new_game,
            scorecard=self.show_scorecard,
            select_desk_tab=self.select_desk_tab,
            select_report=self.select_report,
            show_help=self.show_help,
            end_game=self.end_game,
            cancel_queue_autoclose=self.cancel_queue_autoclose,
            pause_queue_autoclose=self._pause_queue_autoclose,
            choose_mandate=self.choose_mandate,
            start_initiative=self.start_initiative,
            close=self.root.destroy,
        )

    def _release_tk(self):
        """Break the controller/view cycle so refcounting frees Tk on this thread.

        The callbacks and click targets capture the controller, and the
        controller holds the view and root, so without this the Tk root lives
        until some later GC pass. ArcPy runs PyGC_Collect at the start of every
        GP tool call, often on another thread, and freeing tkapp there makes
        Tcl_AsyncDelete abort Pro (AR17, ADR-15). Never call gc.collect here.
        """

        root = getattr(self, "root", None)
        view = getattr(self, "view", None)
        if view is not None:
            view._font_cache.clear()
            view._click_targets = []
            view.callbacks = None
            view.on_select_item = None
            view.canvas = None
            view.root = None
        self.view = None
        self.root = None
        self.status_var = None
        self._newgame_overlay = None
        if root is not None:
            # Normal close already destroyed the window; this covers a setup
            # failure, so Tk widgets go away here and not with the traceback.
            try:
                root.destroy()
            except Exception:
                pass

    def select_item(self, item_id):
        """Select a docket item and redraw the dashboard model."""

        self.selected_item_id = item_id
        self.selected_desk_tab = "applications"
        item = self.active_item()
        if item:
            try:
                select_case_context(self.paths, self.district_layer, item, self.seed, self.messages)
                self._set_status_text(f"Selected {item.title}; map context updated.")
            except Exception as exc:
                self._set_status_text(f"Map selection failed: {exc}")
                _warn(self.messages, "DASH", traceback.format_exc().strip().splitlines()[-1])
        self.reload()

    def _current_audit(self, state, districts, active_features, items):
        """Return the cached AuditResult, recomputing it only when dirty.

        Copies districts and the docket before handing them to the audit so its
        in-place profile normalization never mutates the controller's live
        district objects.
        """

        if self._grade_dirty or self._audit_grade is None:
            self._audit_grade = rules.generate_audit_result(
                state,
                deepcopy(districts),
                desk_model._feature_snapshots(active_features),
                deepcopy(list(items or ())),
            )
            self._grade_dirty = False
        return self._audit_grade

    def _sync_week_start(self, state):
        """Snapshot the city stats and money the first time a week is shown."""

        week = int(getattr(state, "turn", 0) or 0)
        if week == self._week_start_turn and self._week_start:
            return
        self._week_start_turn = week
        self._week_start = {
            name: int(getattr(state, name, 0) or 0)
            for name in ("activity", "friction", "trust", "exposure", "money")
        }

    def _resolve_session_state(self, state, districts, items, active_features):
        """Return the rows to render plus whether a fresh start is being offered.

        When the launcher flagged offer_fresh_start (a save exists in this
        workspace but no Permit Office layers are on the map), render a clean
        start posture from defaults instead of reading the stale saved board, so
        the old game is not silently surfaced. Otherwise read persisted rows
        (honoring any caller-supplied, fully-persisted overrides).
        """

        if self._offer_fresh_start and has_saved_game(self.paths):
            return rules.CityState(), {}, [], [], True
        state = read_state(self.paths) if state is None else state
        districts = read_districts(self.paths) if districts is None else districts
        items = read_docket(self.paths) if items is None else items
        active_features = read_active_features(self.paths) if active_features is None else active_features
        return state, districts, items, active_features, False

    def reload(self, *, state=None, districts=None, items=None, active_features=None):
        """Read persisted game rows and render a fresh desk view model.

        A caller that has just written a fully-persisted copy of any of these
        objects may pass it to skip the re-read; anything left as None is read
        from the GDB. Pass ONLY values that match what was persisted — never an
        object mutated by side-channel GDB writes (e.g. activate_proposal) or by
        a rolled-back command, or the view will show stale/uncommitted data.
        """

        state, districts, items, active_features, offering_fresh = self._resolve_session_state(
            state, districts, items, active_features
        )
        self._sync_report_week(state)
        if (state.status == "complete" or state.turn > state.max_turns) and not self._has_final_audit_tab():
            # File the audit once; re-filing it on every reload pulled the Desk
            # and other report picks back to it (AR21 v8 items 9.1, 13.6).
            self._record_final_audit_receipt(state, districts, active_features, items)
        saved_game = has_saved_game(self.paths) and not offering_fresh
        if saved_game:
            self._sync_week_start(state)
        if offering_fresh and not self.status_text:
            self.status_text = "Saved board found, but no Permit Office layers are on the map. Click New Game to start fresh."
        elif not saved_game and not self.status_text:
            self.status_text = "No saved game found. Click New Game to create Permit Office layers and start play."
        try:
            visible = proposal_visible_map(self.paths)
        except Exception:
            visible = {}
        proposal_visible_by_item = {item.item_id: bool(visible.get(item.item_id)) for item in items}
        audit = self._current_audit(state, districts, active_features, items)
        model = build_desk_model(
            state,
            districts,
            items,
            self.selected_item_id,
            self._display_status_text(),
            proposal_visible_by_item,
            active_features=active_features,
            receipt=self.last_receipt,
            report_tabs=tuple(self.report_tabs),
            selected_report_id=self.selected_report_id,
            show_start_help=(not saved_game) or self._show_help,
            selected_desk_tab=self.selected_desk_tab,
            auto_close_active=self._queue_autoclose_active,
            auto_close_seconds=self._queue_autoclose_seconds,
            game_active=saved_game,
            audit=audit,
            week_start=self._week_start if saved_game else None,
        )
        self.selected_item_id = model.selected_item_id
        self.selected_report_id = model.selected_report_id
        self.selected_desk_tab = model.selected_desk_tab
        self._ticker_index = 0
        self.view.render(model)

    def _record_receipt(self, title, report, affected, state, districts=None):
        """Store the latest filed report for the inline receipt panel (no popup)."""

        receipt = ReceiptModel(
            title=title,
            report=report,
            affected=tuple(affected or ()),
            metrics=receipt_metrics(state),
        )
        self.last_receipt = receipt
        report_id = self._next_report_id("report", state)
        summary, sections = _report_tab_sections(report, districts)
        tab = ReportTab(
            report_id=report_id,
            title=title,
            kind="report",
            status=_report_status(report),
            selected=True,
            report=report,
            affected=receipt.affected,
            metrics=receipt.metrics,
            sections=sections,
            summary=summary,
        )
        self.report_tabs = [self._unselect_report(tab) for tab in self.report_tabs] + [tab]
        self.selected_report_id = report_id
        self.selected_desk_tab = "applications"

    def _record_final_audit_receipt(self, state, districts, active_features, items):
        """Store the current final audit scorecard as the inline receipt."""

        grade, scorecard = rules.scorecard(state, districts, active_features, items)
        title = f"Final Audit: {grade}"
        report = _final_audit_report(grade, scorecard)
        self._record_scorecard_tab(title, report, state)
        return grade, self.last_receipt.report

    def _has_final_audit_tab(self):
        return any(tab.kind == "scorecard" and tab.title.startswith("Final Audit:") for tab in self.report_tabs)

    def _record_scorecard_tab(self, title, report, state):
        """Store or select a scorecard report tab."""

        receipt = ReceiptModel(title=title, report=report, affected=(), metrics=receipt_metrics(state))
        self.last_receipt = receipt
        existing_id = ""
        for tab in self.report_tabs:
            if tab.kind == "scorecard" and tab.title == title:
                existing_id = tab.report_id
                break
        report_id = existing_id or self._next_report_id("scorecard", state)
        summary, sections = _report_tab_sections(report)
        scorecard_tab = ReportTab(report_id, title, "scorecard", "scorecard", True, report, (), receipt.metrics, sections, summary)
        tabs = [self._unselect_report(tab) for tab in self.report_tabs if tab.report_id != report_id]
        self.report_tabs = tabs + [scorecard_tab]
        self.selected_report_id = report_id
        self.selected_desk_tab = "reports"

    def _next_report_id(self, kind, state):
        """Return a stable in-session id for a newly filed report tab."""

        self._report_counter += 1
        week = int(getattr(state, "turn", 0) or 0)
        return f"{kind}-w{week}-{self._report_counter}"

    def _unselect_report(self, tab):
        return replace(tab, selected=False)

    def _sync_report_week(self, state):
        week = int(getattr(state, "turn", 0) or 0)
        if self._report_week == 0:
            self._report_week = week
            return
        if week != self._report_week and getattr(state, "status", "") != "complete":
            keep = [tab for tab in self.report_tabs if tab.status == "week"][-1:]
            self.report_tabs = keep
            self.selected_report_id = keep[-1].report_id if keep else ""
            self.last_receipt = _receipt_from_tab(keep[-1]) if keep else None
            self.selected_desk_tab = "reports" if keep else "applications"
            self._report_week = week

    def select_desk_tab(self, tab_id):
        self.selected_desk_tab = tab_id if tab_id in ("reports", "city") else "applications"
        if self.selected_desk_tab == "reports":
            self._pause_queue_autoclose()
        self.reload()

    def select_report(self, report_id):
        self._pause_queue_autoclose()
        self.selected_report_id = report_id
        self.selected_desk_tab = "reports"
        self.report_tabs = [
            replace(tab, selected=tab.report_id == report_id)
            for tab in self.report_tabs
        ]
        for tab in self.report_tabs:
            if tab.report_id == report_id:
                self.last_receipt = _receipt_from_tab(tab)
                break
        self.reload()

    def show_help(self):
        self._pause_queue_autoclose()
        self._show_help = not self._show_help
        self.reload()

    def _schedule_queue_autoclose(self, seconds=QUEUE_AUTOCLOSE_SECONDS):
        self._cancel_queue_autoclose_timer()
        self._queue_autoclose_active = True
        self._queue_autoclose_seconds = int(seconds)
        self.status_var.set(f"Queue cleared. Week closes automatically in {seconds} seconds.")
        root = getattr(self, "root", None)
        if root is not None:
            try:
                self._queue_autoclose_after_id = root.after(int(seconds * 1000), self._queue_autoclose_tick)
            except Exception:
                self._queue_autoclose_after_id = None

    def cancel_queue_autoclose(self):
        self._cancel_queue_autoclose_timer()
        self._queue_autoclose_active = False
        self._queue_autoclose_seconds = 0
        if getattr(self, "status_var", None):
            self.status_var.set("Queue cleared. End Week when ready.")
        self.reload()

    def _pause_queue_autoclose(self):
        if not self._queue_autoclose_active and not self._queue_autoclose_after_id:
            return
        self._cancel_queue_autoclose_timer()
        self._queue_autoclose_active = False
        self._queue_autoclose_seconds = 0

    def _cancel_queue_autoclose_timer(self):
        after_id = self._queue_autoclose_after_id
        self._queue_autoclose_after_id = None
        if after_id and getattr(self, "root", None):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass

    def _queue_autoclose_tick(self):
        self._queue_autoclose_after_id = None
        if not self._queue_autoclose_active:
            return
        self._queue_autoclose_active = False
        self._queue_autoclose_seconds = 0
        self.advance_turn(auto=True)

    def end_game(self):
        if getattr(self, "root", None):
            self.root.destroy()

    def _display_status_text(self):
        """Return the current action status; the ambient ticker fills the strip otherwise."""

        return self.status_text

    def _set_status_text(self, value):
        """Set transient command status before ambient ticker resumes."""

        self.status_text = str(value or "")
        self._status_hold_until = time.monotonic() + STATUS_TEXT_HOLD_SECONDS if self.status_text else 0.0

    def _schedule_ticker_tick(self):
        """Schedule the ambient status ticker without touching ArcGIS rows."""

        if not getattr(self, "root", None):
            return
        try:
            self._ticker_after_id = self.root.after(TICKER_TICK_MS, self._ticker_tick)
        except Exception:
            self._ticker_after_id = None

    def _ticker_tick(self):
        """Advance the ambient ticker through the lightweight status-strip path."""

        try:
            if self._command_busy:
                return
            if self.status_text:
                if time.monotonic() < getattr(self, "_status_hold_until", 0.0):
                    return
                self.status_text = ""
                self._status_hold_until = 0.0
            view = getattr(self, "view", None)
            model = getattr(view, "model", None)
            ticker_items = tuple(getattr(model, "ticker_items", ()) or ())
            if not ticker_items or not hasattr(view, "update_status_marquee"):
                return
            self._ticker_offset_px += TICKER_STEP_PX
            self._ticker_index = self._ticker_offset_px
            view.update_status_marquee(self._ticker_offset_px)
        finally:
            self._schedule_ticker_tick()

    def new_game(self):
        """Open an inline seed-entry overlay (no native dialog, single screen)."""

        try:
            import tkinter as tk
        except Exception as exc:
            self.status_var.set(f"New game failed: tkinter unavailable: {exc}")
            self.reload()
            return
        if getattr(self, "_newgame_overlay", None) is not None:
            return

        pal = Palette
        frame = tk.Frame(self.root, bg=pal.CONTENT, highlightbackground=pal.BORDER, highlightthickness=1)
        self._newgame_overlay = frame
        tk.Label(frame, text="START NEW GAME", bg=pal.CONTENT, fg=pal.INK, font=desk_font(Type.TITLE, "bold")).pack(padx=24, pady=(16, 8), anchor="w")
        message = (
            "Replace the current Permit Office game rows and map layers?"
            if has_saved_game(self.paths)
            else "Create Permit Office layers and start a new game."
        )
        tk.Label(frame, text=message, bg=pal.CONTENT, fg=pal.INK, font=desk_font(Type.BODY), wraplength=320, justify="left").pack(padx=24, pady=(0, 12), anchor="w")
        row = tk.Frame(frame, bg=pal.CONTENT)
        row.pack(padx=24, anchor="w")
        tk.Label(row, text="Random seed", bg=pal.CONTENT, fg=pal.MUTED, font=desk_font(Type.BODY, "bold")).pack(side="left")
        seed_var = tk.StringVar(value=str(self.seed))
        entry = tk.Entry(row, textvariable=seed_var, width=12, relief="solid", bd=1, font=desk_font(Type.BODY), highlightcolor=pal.ACCENT)
        entry.pack(side="left", padx=(8, 0))
        buttons = tk.Frame(frame, bg=pal.CONTENT)
        buttons.pack(padx=24, pady=(16, 16), anchor="e")

        def _start(_event=None):
            try:
                seed = abs(int(seed_var.get().strip()))
            except (TypeError, ValueError):
                seed = self.seed
            self._close_newgame_overlay()
            self.start_new_game(int(seed))

        def _cancel(_event=None):
            self._close_newgame_overlay()

        tk.Button(buttons, text="Start", command=_start, bg=pal.ACCENT, fg=pal.CONTENT, activebackground=pal.INK, activeforeground=pal.CONTENT, relief="flat", padx=16, pady=4, font=desk_font(Type.BODY, "bold")).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Cancel", command=_cancel, bg=pal.CONTENT, fg=pal.INK, activebackground=pal.SUBTLE, activeforeground=pal.INK, relief="solid", bd=1, padx=16, pady=4, font=desk_font(Type.BODY)).pack(side="left")
        frame.place(relx=0.5, rely=0.5, anchor="center")
        frame.lift()
        entry.focus_set()
        entry.bind("<Return>", _start)
        entry.bind("<Escape>", _cancel)
        frame.bind("<Escape>", _cancel)

    def _close_newgame_overlay(self):
        overlay = getattr(self, "_newgame_overlay", None)
        if overlay is not None:
            overlay.destroy()
            self._newgame_overlay = None

    def start_new_game(self, seed):
        with perf_session("new_game", self.messages):
            try:
                self._command_busy = True
                clear_game_rows(self.paths)
                create_district_board(self.paths, seed, self.messages)
                seed_city_features(self.paths, seed, self.messages)
                state = rules.CityState()
                state.mandate = rules.offer_mandates(seed, read_districts(self.paths))
                write_state(self.paths, state)
                generate_docket_rows(self.paths, seed, self.messages)
                remove_outputs_from_map(self.messages)
                add_outputs_to_map(self.paths, self.messages)
                refresh_all(self.paths, self.messages)
                self.seed = seed
                self.district_layer = DISTRICTS
                self._offer_fresh_start = False
                self._audit_grade = None
                self._grade_dirty = True
                self.selected_item_id = ""
                self.last_receipt = None
                self.report_tabs = []
                self.selected_report_id = ""
                self.selected_desk_tab = "applications"
                self._pause_queue_autoclose()
                self._report_counter = 0
                self._report_week = 0
                self._show_help = False
                self.status_var.set(f"New game started with seed {seed}.")
            except Exception as exc:
                self.status_var.set(f"New game failed: {exc}")
                _warn(self.messages, "NEW", traceback.format_exc().strip().splitlines()[-1])
            finally:
                self._command_busy = False
                self.reload()

    def start_initiative(self, kind, target=None):
        """File this week's player initiative and persist the result.

        Earmark takes a district type; Civic action and Market push use the
        given districts, or the current map selection when none are given.
        """

        if kind != "earmark" and not target:
            target = selected_cell_ids(self.district_layer)
        command_targets = [target] if isinstance(target, str) else list(target or [])
        command_id = command_insert(self.paths, kind, "", command_targets)
        try:
            self._command_busy = True
            state = read_state(self.paths)
            districts = read_districts(self.paths)
            result = rules.start_initiative(state, districts, kind, target, seed=self.seed)
            if result.ok:
                write_state(self.paths, state)
                self._grade_dirty = True
                if result.affected_cell_ids:
                    write_district_updates(self.paths, districts, result.report, result.affected_cell_ids)
                    rebuild_output_layers(self.paths, self.messages, layer_names={DISTRICTS}, dirty_scope=DIRTY_DISTRICTS)
            command_finish(self.paths, command_id, result.command_status, result.report)
            self.status_var.set(result.report)
        except Exception as exc:
            command_finish(self.paths, command_id, "error", error=str(exc))
            self.status_var.set(f"Initiative failed: {exc}")
        finally:
            self._command_busy = False
        self.reload()

    def choose_mandate(self, key):
        """Record the player's season goal from the New Game offer and persist it."""

        try:
            state = read_state(self.paths)
            if rules.choose_mandate(state, key):
                write_state(self.paths, state)
                self.status_var.set(f"Season goal filed: {rules.mandate_title(key)}.")
            else:
                self.status_var.set("That goal is not on offer, or one is already filed.")
        except Exception as exc:
            self.status_var.set(f"Goal filing failed: {exc}")
        self.reload()

    def show_scorecard(self):
        if not has_saved_game(self.paths):
            self.status_var.set("No saved Permit Office game found.")
            self.reload()
            return
        try:
            state = read_state(self.paths)
            districts = read_districts(self.paths)
            active_features = read_active_features(self.paths)
            items = read_docket(self.paths)
            grade, report = rules.scorecard(state, districts, active_features, items)
            summary = f"{report}\n\n{rules.population_city_summary(districts)}; incidents={rules.incident_summary(districts)}."
            self._record_scorecard_tab(f"Scorecard: {grade}", summary, state)
            self.status_var.set(report)
        except Exception as exc:
            self.status_var.set(f"Scorecard failed: {exc}")
        self.reload()

    def item_label(self, item):
        return f"{item.item_id} | {item.geometry_type} | {item.status} | {item.title}"

    def active_item(self):
        item_id = self.selected_item_id
        if not item_id and getattr(self, "view", None):
            item_id = self.view.selected_item_id()
        docket = read_docket(self.paths)
        open_items = [item for item in docket if item.status in rules.OPEN_DOCKET_STATUSES]
        for item in open_items:
            if item.item_id == item_id:
                return item
        return open_items[0] if open_items else None

    def refresh_detail(self):
        self.reload()

    def toggle_exhibit(self):
        """Hide or show the selected unresolved proposal exhibit."""

        item = self.active_item()
        if not item:
            return
        try:
            if case_proposal_visible(self.paths, item):
                changed = hide_case_proposal(self.paths, item.item_id)
                action = "Hid" if changed else "No exhibit found for"
                target_ids = item.target_cell_ids
            else:
                target_ids = ensure_case_proposal(self.paths, item, self.seed, self.messages)
                action = "Showed"
            self._redraw_exhibit_layer(item)
            suffix = f" for {', '.join(target_ids)}" if target_ids else ""
            self.status_var.set(f"{action} {item.title}{suffix}.")
            self.reload()
        except Exception as exc:
            self.status_var.set(f"Exhibit toggle failed: {exc}")
            _warn(self.messages, "DASH", traceback.format_exc().strip().splitlines()[-1])

    def _redraw_exhibit_layer(self, item):
        """Redraw only the feature layer holding this case's exhibit.

        RefreshLayer repaints the whole view (AR15), so known geometry types use
        the feature-query redraw that points-only decisions use.
        """

        layer = _GEOM_TYPE_TO_LAYER.get(getattr(item, "geometry_type", None))
        if layer is None:
            refresh_all(self.paths, self.messages)
            return
        rebuild_output_layers(self.paths, self.messages, layer_names={layer}, remove_scope_override={layer})

    def update_from_map(self):
        """Replace the selected case's proposed exhibit from current map selection."""

        item = self.active_item()
        if not item:
            return
        try:
            selected = selected_cell_ids(self.district_layer)
            target_ids = insert_or_replace_proposal(self.paths, item, selected, self.messages)
            self._redraw_exhibit_layer(item)
            self.status_var.set(f"Updated {item.title} from map selection: {', '.join(target_ids)}.")
            self.reload()
        except Exception as exc:
            self.status_var.set(f"Update from map failed: {exc}")
            _warn(self.messages, "DASH", traceback.format_exc().strip().splitlines()[-1])

    def inspect(self):
        """Resolve an inspection command for the active docket item."""

        item = self.active_item()
        if not item:
            return
        command_id = command_insert(self.paths, "inspect", item.item_id, item.target_cell_ids)
        reload_kwargs = {}
        with perf_session("turn=inspect", self.messages):
            try:
                # Inspection reads the live state, lets pure rules attach evidence,
                # then persists both the changed docket item and the command log.
                self._command_busy = True
                state = read_state(self.paths)
                districts = read_districts(self.paths)
                active_features = read_active_features(self.paths)
                result = rules.resolve_decision(state, item, districts, "inspect", item.target_cell_ids, seed=self.seed, active_features=active_features)
                with perf_block("writes"):
                    write_state(self.paths, state)
                    write_docket_item(self.paths, item)
                    action_log(self.paths, state, result)
                    command_finish(self.paths, command_id, result.command_status, result.report)
                self.status_var.set(result.report)
                self._record_receipt(item.title, result.report, result.affected_cell_ids, state, districts)
                # Only `state` is fully persisted by inspect (districts and active
                # features are read but not written), so only it is safe to reuse.
                reload_kwargs = {"state": state}
            except Exception as exc:
                command_finish(self.paths, command_id, "error", error=str(exc))
                self.status_var.set(f"Inspect failed: {exc}")
            finally:
                self._command_busy = False
        self.reload(**reload_kwargs)

    def apply_decision(self, action, mitigated):
        """Approve or approve-with-mitigation for the active docket item."""

        item = self.active_item()
        if not item:
            return
        command_id = None
        reload_kwargs = {}
        with perf_session(f"turn={action}", self.messages):
            try:
                self._command_busy = True
                # Approvals need current map proposal context before pure rules can
                # resolve target effects, spillover, active features, and projects.
                target_ids = list(item.target_cell_ids or ())
                if not target_ids:
                    target_ids = selected_cell_ids(self.district_layer)
                with perf_block("ensure"):
                    target_ids = ensure_case_proposal(self.paths, item, self.seed, self.messages, target_ids or None)
                command_id = command_insert(self.paths, action, item.item_id, target_ids)
                with perf_block("spillover"):
                    spillover = [] if item.template_id == rules.MAINTENANCE_TEMPLATE_ID else proposal_spillover(self.paths, item)
                with perf_block("reads"):
                    state = read_state(self.paths)
                    districts = read_districts(self.paths)
                    active_features = read_active_features(self.paths)
                    projects = read_projects(self.paths)
                with perf_block("resolve"):
                    result = rules.resolve_decision(
                        state,
                        item,
                        districts,
                        action,
                        item.target_cell_ids,
                        spillover,
                        seed=self.seed,
                        mitigated=mitigated,
                        active_features=active_features,
                        projects=projects,
                    )
                if not result.ok:
                    command_finish(self.paths, command_id, "error", result.report, result.report)
                    self.status_var.set(result.report)
                    return
                if result.feature_updates:
                    with perf_block("write_features"):
                        write_active_features(self.paths, active_features)
                with perf_block("activate"):
                    activated = activate_proposal(self.paths, item, result.report)
                if not activated:
                    _warn(self.messages, "DASH", f"approved {item.item_id} but no proposed map feature was activated")
                with perf_block("redraw_plan_hydration"):
                    hydrated_plan = hydrate_decision_redraw_plan(
                        result,
                        feature_layer_key=_feature_layer_key_for_item(item),
                        feature_layer_dirty=bool(activated or result.feature_updates),
                    )
                self._finish_decision(command_id, item, state, districts, projects, result, _decision_layer_names(item), hydrated_plan)
                # state and districts are fully persisted here; active_features is
                # re-read because activate_proposal mutated support rows in the GDB
                # directly, and items is re-read because triage just reselected.
                reload_kwargs = {"state": state, "districts": districts}
            except Exception as exc:
                if command_id:
                    command_finish(self.paths, command_id, "error", error=str(exc))
                self.status_var.set(f"Decision failed: {exc}")
                _warn(self.messages, "DASH", traceback.format_exc().strip().splitlines()[-1])
            finally:
                self._command_busy = False
                with perf_block("reload"):
                    self.reload(**reload_kwargs)

    def deny(self):
        """Deny the active docket item and persist resulting state changes."""

        item = self.active_item()
        if not item:
            return
        command_id = command_insert(self.paths, "deny", item.item_id, item.target_cell_ids)
        reload_kwargs = {}
        with perf_session("turn=deny", self.messages):
            try:
                self._command_busy = True
                # Denials use the same pure-rule resolver, but proposal features are
                # marked denied instead of activated on the map.
                with perf_block("reads"):
                    state = read_state(self.paths)
                    districts = read_districts(self.paths)
                    active_features = read_active_features(self.paths)
                    projects = read_projects(self.paths)
                with perf_block("resolve"):
                    result = rules.resolve_decision(state, item, districts, "deny", item.target_cell_ids, seed=self.seed, active_features=active_features, projects=projects)
                if not result.ok:
                    command_finish(self.paths, command_id, "error", result.report, result.report)
                    self.status_var.set(result.report)
                    return
                if result.feature_updates:
                    with perf_block("write_features"):
                        write_active_features(self.paths, active_features)
                proposal_status = item.status if item.status in ("denied", "deferred") else "denied"
                with perf_block("mark"):
                    mark_proposals(self.paths, item.item_id, proposal_status, result.report)
                with perf_block("redraw_plan_hydration"):
                    hydrated_plan = hydrate_decision_redraw_plan(
                        result,
                        feature_layer_key=_feature_layer_key_for_item(item),
                        feature_layer_dirty=True,
                    )
                self._finish_decision(command_id, item, state, districts, projects, result, _decision_layer_names(item), hydrated_plan)
                # state and districts are fully persisted; active_features is re-read
                # because mark_proposals mutated support rows in the GDB directly.
                reload_kwargs = {"state": state, "districts": districts}
            except Exception as exc:
                command_finish(self.paths, command_id, "error", error=str(exc))
                self.status_var.set(f"Deny failed: {exc}")
            finally:
                self._command_busy = False
                with perf_block("reload"):
                    self.reload(**reload_kwargs)

    def _finish_decision(self, command_id, item, state, districts, projects, result, layer_names=None, hydrated_plan=None):
        """Persist a successful decision result and show its filed report."""

        filed_report = _filed_report_text(result, districts)
        self._grade_dirty = True
        with perf_block("writes"):
            district_display_changed = write_district_updates(self.paths, districts, result.report, result.affected_cell_ids)
            write_state(self.paths, state)
            write_projects(self.paths, projects)
            write_docket_item(self.paths, item)
            action_log(self.paths, state, result)
            command_finish(self.paths, command_id, result.command_status, filed_report)
        # Read the next case now so the redraw's selection clear can leave alone
        # the base layers its NEW_SELECTION is about to replace (AR18).
        try:
            next_item = self._next_triage_item(item.item_id)
        except Exception:
            next_item = _NEXT_UNREAD
        keep_selections = selection_layers_for_item(None if next_item is _NEXT_UNREAD else next_item)
        if hydrated_plan is not None:
            redraw_names = layer_names_for_plan(hydrated_plan)
            remove_names = set(hydrated_plan.remove_readd_names)
            if district_display_changed is False:
                redraw_names.discard(DISTRICTS)
                remove_names.discard(DISTRICTS)
            rebuild_output_layers(
                self.paths,
                self.messages,
                layer_names=redraw_names,
                dirty_scope=(DIRTY_DESK_ONLY if not redraw_names else None) if district_display_changed is False else DIRTY_DISTRICTS,
                remove_scope_override=remove_names,
                keep_selections=keep_selections,
            )
        else:
            rebuild_output_layers(self.paths, self.messages, layer_names=layer_names, dirty_scope=DIRTY_DISTRICTS, keep_selections=keep_selections)
        self.district_layer = DISTRICTS
        self.status_var.set(filed_report)
        self._record_receipt(item.title, filed_report, result.affected_cell_ids, state, districts)
        self._advance_triage_selection(item.item_id, next_item)

    def _next_triage_item(self, resolved_item_id):
        """Return the next open application after a decision, or None when the queue is empty."""

        docket = read_docket(self.paths)
        active = [item for item in docket if item.status in rules.OPEN_DOCKET_STATUSES]
        return next((item for item in active if item.item_id != resolved_item_id), active[0] if active else None)

    def _advance_triage_selection(self, resolved_item_id, next_item=_NEXT_UNREAD):
        """Select the next open application or arm queue auto-close.

        ``next_item`` is the case ``_finish_decision`` already read before the
        redraw; the sentinel means read it here.
        """

        if next_item is _NEXT_UNREAD:
            try:
                next_item = self._next_triage_item(resolved_item_id)
            except Exception as exc:
                _warn(self.messages, "DASH", f"next selection skipped: {exc}")
                return
        self.selected_desk_tab = "applications"
        if next_item is None:
            self.selected_item_id = ""
            self._schedule_queue_autoclose()
            return
        self._pause_queue_autoclose()
        self.selected_item_id = next_item.item_id
        try:
            select_case_context(self.paths, self.district_layer, next_item, self.seed, self.messages)
        except Exception as exc:
            _warn(self.messages, "DASH", f"next selection map context failed: {exc}")

    def advance_turn(self, auto=False):
        """Advance the saved game one week and regenerate the docket."""

        command_id = command_insert(self.paths, "advance_turn", "", [])
        reload_kwargs = {}
        with perf_session("turn=advance", self.messages):
            try:
                self._command_busy = True
                # Turn advancement mutates open docket items, city systems, active
                # features, projects, and the next generated docket as one command.
                with perf_block("reads"):
                    state = read_state(self.paths)
                    items = read_docket(self.paths)
                    districts = read_districts(self.paths)
                    active_features = read_active_features(self.paths)
                    projects = read_projects(self.paths)
                if state.status == "complete" or state.turn > state.max_turns:
                    grade, final_report = self._record_final_audit_receipt(state, districts, active_features, items)
                    report = f"Final audit already filed. Scorecard: {grade}."
                    command_finish(self.paths, command_id, "applied", report)
                    rebuild_output_layers(self.paths, self.messages, remove_scope_override={DISTRICTS})
                    self.district_layer = DISTRICTS
                    self.status_var.set(report)
                    return
                with perf_block("resolve"):
                    turn_result = rules.advance_turn_result(state, items, districts, active_features, projects)
                report = turn_result.report
                self._grade_dirty = True
                generated_items = None
                with perf_block("writes"):
                    write_state(self.paths, state)
                    write_projects(self.paths, projects)
                    write_district_updates(self.paths, districts, report)
                    write_active_features(self.paths, active_features)
                    for item in items:
                        write_docket_item(self.paths, item)
                    if state.status != "complete":
                        generated_items = generate_docket_rows(self.paths, self.seed, self.messages)
                    command_finish(self.paths, command_id, "applied", report)
                redraw_layers = _week_close_redraw_layers(generated_items)
                rebuild_output_layers(
                    self.paths,
                    self.messages,
                    layer_names=redraw_layers,
                    remove_scope_override=redraw_layers,
                )
                self.district_layer = DISTRICTS
                prefix = "Auto-close: " if auto else ""
                if state.status == "complete":
                    _grade, final_report = self._record_final_audit_receipt(state, districts, active_features, items)
                    self.status_var.set(f"{prefix}{final_report}")
                else:
                    self._record_week_report("Week Closed", report, state, districts)
                    self.status_var.set(f"{prefix}{report}")
                # state, districts, and active_features are all fully rewritten
                # above; items is re-read because generate_docket_rows just added
                # next week's docket rows that the in-memory list does not hold.
                reload_kwargs = {"state": state, "districts": districts, "active_features": active_features}
            except Exception as exc:
                command_finish(self.paths, command_id, "error", error=str(exc))
                self.status_var.set(f"Advance failed: {exc}")
            finally:
                self._command_busy = False
                with perf_block("reload"):
                    self.reload(**reload_kwargs)

    def _record_week_report(self, title, report, state, districts=None):
        """Store the week-close report as the first report in the new week."""

        receipt = ReceiptModel(title=title, report=report, affected=(), metrics=receipt_metrics(state))
        self.last_receipt = receipt
        report_id = self._next_report_id("week", state)
        summary, sections = _report_tab_sections(report, districts)
        tab = ReportTab(report_id, title, "week", "week", True, report, (), receipt.metrics, sections, summary)
        self.report_tabs = [tab]
        self.selected_report_id = report_id
        self.selected_desk_tab = "reports"
        self._report_week = int(getattr(state, "turn", 0) or 0)




def _report_tab_sections(report, districts=None):
    """Return (summary, sections) for a report tab, naming districts instead of ids."""

    names = {cid: getattr(profile, "name", "") or cid for cid, profile in (districts or {}).items()}
    return report_sections(report, names)


def _filed_report_text(result, districts=None):
    """Append compact non-money local changes to a decision report."""

    local = _local_changes_fragment(result, districts)
    if not local:
        return result.report
    return f"{result.report} Local changes: {local}."


FINAL_AUDIT_FLAVOR = {
    "PASS": "Audit accepts the closing file. The city can keep issuing permits under the current desk model.",
    "CONDITIONAL": "Audit closes with conditions. Core services continue, but flagged pressure areas need a follow-up docket.",
    "FAIL": "Audit rejects the closing file. City hall enters remediation with unresolved pressure and exposure findings.",
}


def _final_audit_report(grade, scorecard):
    """Format final audit flavor plus the scorecard report for the receipt."""

    grade = grade or "UNKNOWN"
    flavor = FINAL_AUDIT_FLAVOR.get(grade, "Audit closes the current file.")
    return f"Final audit: {grade}. {flavor} {scorecard}"


def _report_status(report):
    """Classify report text for dashboard tab styling."""

    lower = str(report or "").lower()
    if "final audit" in lower or lower.startswith("audit "):
        return "scorecard"
    if lower.startswith(("approved", "issued")):
        return "approved"
    if lower.startswith(("denied", "deny")):
        return "denied"
    if lower.startswith(("inspected", "inspection")):
        return "inspected"
    if lower.startswith(("advanced", "final week", "auto-close")):
        return "week"
    return "filed"


def _receipt_from_tab(tab):
    """Create a legacy receipt object from a selected report tab."""

    return ReceiptModel(tab.title, tab.report, tab.affected, tab.metrics)


def _local_changes_fragment(result, districts=None):
    """Summarize district deltas and feature updates for filed receipts."""

    parts = []
    for cell_id, delta in sorted((result.district_deltas or {}).items())[:3]:
        text = _compact_delta(delta)
        if text:
            profile = districts.get(cell_id) if districts else None
            label = rules.district_label(profile) if profile is not None else cell_id
            parts.append(f"{label} {text}")
    extra = max(0, len(result.district_deltas or {}) - 3)
    if extra:
        parts.append(f"+{extra} district(s)")
    for feature_id, update in sorted((result.feature_updates or {}).items())[:2]:
        status = update.get("status") or update.get("display_state") or "updated"
        condition = update.get("condition")
        due = update.get("maintenance_due_turn")
        feature_text = f"{feature_id} {status}"
        if condition not in (None, ""):
            feature_text += f" condition {condition}"
        if due not in (None, "", -1):
            feature_text += f" due {due}"
        parts.append(feature_text)
    return "; ".join(parts)


def _compact_delta(delta):
    """Format a district delta map without burying the receipt."""

    if not delta:
        return ""
    ordered = sorted(delta.items(), key=lambda row: (row[0] not in ("activity", "friction", "trust", "exposure", "services", "dissatisfaction"), row[0]))
    parts = []
    for metric, amount in ordered:
        if not amount:
            continue
        label = _compact_metric_label(metric)
        parts.append(f"{label} {int(amount):+d}")
        if len(parts) == 4:
            break
    return ", ".join(parts)


def _compact_metric_label(metric):
    """Return stable short labels for filed local-change receipts."""

    return {
        "activity": "act",
        "friction": "fric",
        "trust": "trust",
        "exposure": "expo",
        "services": "serv",
        "dissatisfaction": "dissat",
    }.get(metric, metric[:4])
