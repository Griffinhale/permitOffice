"""Custom Tkinter desk surface for the Permit Office dashboard."""

from __future__ import annotations

import sys
from dataclasses import replace
from textwrap import shorten, wrap
from typing import Callable

from .rules_loader import rules
from .desk_model import (
    HEADLINE_METRICS,
    DeskCallbacks,
    DeskViewModel,
    ReceiptModel,
    ReportTab,
    build_desk_model,
    report_sections,
    _hazard_summary,
    _maintenance_summary,
    _service_gap_summary,
)


# The pane opens at DEFAULT_DESK_SIZE beside ArcGIS Pro and can shrink to the
# MIN_DESK floors; below them the canvas keeps drawing at the floor size.
DEFAULT_DESK_SIZE = (480, 820)
MIN_DESK_W = 400
MIN_DESK_H = 560
# Fixed bands: header (top row + goal, audit and patience lines), status strip,
# tab strip, and the End Week footer. The tab body scrolls between them.
HEADER_H = 106
HEADER_LINE_H = 22
HEADER_LABEL_W = 70
STATUS_H = 26
TAB_H = 30
FOOTER_H = 56
# How far the ticker masks reach past the strip ends: past the edge of any screen.
MASK_REACH = 100_000
# Control is 0x4 everywhere; Alt is 0x20000 on Windows (where 0x8 is NumLock) and Mod1 0x8 on X11.
KEY_MODIFIER_MASK = 0x4 | (0x20000 if sys.platform == "win32" else 0x8)


def receipt_metrics(state):
    """Return the compact (label, value) metric snapshot for a filed receipt."""

    return (
        ("WEEK", f"{state.turn}/{state.max_turns}"),
        ("AP", f"{state.ap}/{state.max_ap}"),
        ("$", str(state.money)),
        ("NET", f"{state.last_net:+d}"),
        ("HEAT", str(rules.heat_summary(state))),
    )


class Palette:
    """Desk colors as ArcGIS Pro light-theme tokens (sampled from Pro 3.7, see ADR-17).

    Surfaces: FRAME behind panes, PANE and SUBTLE for pane bodies, CONTENT for
    cards, tables, and inputs. Tones (GOOD, WATCH, BAD, ACCENT, TEAL) pass 4.5:1
    on every surface, so tone-colored text stays readable anywhere.
    """

    FRAME = "#eff0f2"
    PANE = "#f7f9f8"
    SUBTLE = "#f9f9f9"
    CONTENT = "#ffffff"
    HEADER = "#ffffff"
    DIVIDER = "#e3e4e6"
    BORDER = "#d4d5d8"
    SELECT = "#e1edf8"
    SUCCESS_TINT = "#e3eede"
    WARN_TINT = "#fbf1de"
    METER_TRACK = "#e3e4e6"
    INK = "#262626"
    MUTED = "#5c5d60"
    ACCENT = "#005daa"
    GOOD = "#2a7a3b"
    WATCH = "#8f5b00"
    BAD = "#b42318"
    TEAL = "#00756f"
    MITIGATE = "#3d7a5a"
    # Legend glyph parts with no symbology_config entry (outline and ring).
    SYMBOL_OUTLINE = "#6f7773"
    SYMBOL_RING = "#68471e"


class Type:
    """The desk type scale (points): small captions, body, pane titles, one display size."""

    SMALL = 8
    BODY = 9
    TITLE = 11
    DISPLAY = 14


class Space:
    """The desk spacing scale (pixels)."""

    XS = 4
    S = 8
    M = 12
    L = 16
    XL = 24


def desk_font(size, weight="normal"):
    """Return the Segoe UI font tuple shared by the desk canvas and its Tk overlays."""

    return ("Segoe UI", size, weight)


class _Stacker:
    """Place canvas blocks top-to-bottom, measuring real heights to avoid overlap.

    Each block is a callable ``draw_fn(canvas, x0, x1, y, max_y) -> bottom_y``.
    The stacker advances past the measured bottom (clamped to ``bottom``) and
    inserts ``pad`` before the next block, so no block can ever be drawn over its
    neighbour or past the region's hard bottom edge.
    """

    def __init__(self, canvas, x0, x1, top, bottom, pad=8):
        """Bind the stacker to a canvas column with a hard top and bottom."""

        self.canvas = canvas
        self.x0 = x0
        self.x1 = x1
        self.y = top
        self.bottom = bottom
        self.pad = pad

    def room(self):
        """Return the vertical space left before the hard bottom edge."""

        return self.bottom - self.y

    def add(self, draw_fn, min_h=0):
        """Draw one block from the current y and advance; skip if no room.

        Returns the block's bottom y, or ``None`` when less than ``min_h`` space
        remains so callers can render an overflow affordance instead.
        """

        if self.room() < max(1, min_h):
            return None
        bottom = draw_fn(self.canvas, self.x0, self.x1, self.y, self.bottom)
        bottom = min(int(bottom), self.bottom)
        self.y = bottom + self.pad
        return bottom


def _text_bottom(c, x, y, text, font, fill, width=None, anchor="nw", justify="left"):
    """Draw a text item and return its real bottom y (measured, never guessed)."""

    kwargs = {"text": text, "anchor": anchor, "fill": fill, "font": font, "justify": justify}
    if width is not None:
        kwargs["width"] = width
    item = c.create_text(x, y, **kwargs)
    box = c.bbox(item)
    return box[3] if box else y + font[1] + 6


class PermitDeskView:
    """Canvas desk for the Permit Office dashboard: one narrow, resizable column.

    Top to bottom: a header (week, AP, money, the season goal, the next audit
    and the patience ladder), a status strip, Desk / Reports / City tabs, a
    scrolling tab body, and an End Week footer. The header, strip, tabs and
    footer are drawn after the body so they cover anything scrolled under them.
    """

    def __init__(self, root, callbacks: DeskCallbacks, on_select_item: Callable[[str], None]):
        """Create the canvas and bind mouse and key events to view callbacks."""

        import tkinter as tk

        self.root = root
        self.callbacks = callbacks
        self.on_select_item = on_select_item
        self.model = DeskViewModel()
        self._click_targets: list[tuple[str, str, tuple[int, int, int, int], Callable[[], None]]] = []
        self._hover_key = ""
        self._last_size = (0, 0)
        self._font_cache: dict = {}
        self._fit_cache: dict = {}
        self._lookup_model = None
        self._ledger_by_label: dict = {}
        self._lane_by_action: dict = {}
        self._menu_open = False
        self._menu_anchor = None
        self._ticker_offset_px = 0
        # Lowercase hotkey -> callback for the actions drawn enabled this frame.
        self._hotkey_targets: dict[str, Callable[[], None]] = {}
        self._body_box = None
        self._body_scroll = 0
        self._body_tab = ""
        self._earmark_choice = ""

        self.canvas = tk.Canvas(root, bg=Palette.FRAME, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", self._on_mouse_wheel)
        self.canvas.bind("<Button-5>", self._on_mouse_wheel)
        root.bind("<Key>", self._on_key, add="+")

    # ----- public surface used by the controller -----------------------------

    def render(self, model: DeskViewModel):
        """Store and draw the latest view model."""

        self.model = model
        self._fit_cache.clear()
        self._redraw_current()

    def update_status_strip(self, status_text: str):
        """Redraw only the status strip text using the current model frame."""

        if self.model.status_text == status_text:
            return
        self.model = replace(self.model, status_text=status_text)
        if status_text:
            self._ticker_offset_px = 0
        self._redraw_status_strip()

    def update_status_marquee(self, offset_px: int):
        """Redraw only the ambient ticker marquee at a new scroll offset."""

        if self.model.status_text:
            self.model = replace(self.model, status_text="")
        self._ticker_offset_px = max(0, int(offset_px or 0))
        self._redraw_status_strip()

    def selected_item_id(self) -> str:
        """Return the currently rendered selected item id."""

        return self.model.selected_item_id

    # ----- events ------------------------------------------------------------

    def _redraw_status_strip(self):
        """Redraw the status strip without touching the rest of the desk."""

        box = getattr(self, "_status_strip_box", None)
        if not box:
            self._redraw_current()
            return
        try:
            self.canvas.delete("status-strip")
        except Exception:
            self._redraw_current()
            return
        self._draw_status_strip(self.canvas, box)
        self.canvas.tag_lower("status-strip", "status-strip-top")

    def _on_configure(self, event):
        """Redraw the canvas when the window size changes."""

        size = (event.width, event.height)
        if size != self._last_size:
            self._last_size = size
            self._draw(max(event.width, MIN_DESK_W), max(event.height, MIN_DESK_H))

    def _on_click(self, event):
        """Dispatch a click to the topmost registered hit target."""

        for _kind, _ident, bbox, callback in reversed(self._click_targets):
            if _inside(event.x, event.y, bbox):
                callback()
                return "break"
        if self._menu_open:
            self._menu_open = False
            self._redraw_current()
        return None

    def _on_key(self, event):
        """Run the enabled action whose hotkey is shown, or the help card's keys.

        Keys typed into another widget (the New Game seed entry) or held with
        Control/Alt are ignored, the help card answers only ``?``, and the main
        menu answers only its own keys (C continue, N new game, ? help).
        """

        if event.widget is not self.canvas and event.widget is not self.root:
            return None
        if int(getattr(event, "state", 0) or 0) & KEY_MODIFIER_MASK:
            return None
        key = str(getattr(event, "char", "") or "").lower()
        if self.model.show_start_help and key != "?":
            return None
        if self.model.main_menu is not None and not self.model.show_start_help:
            callback = self._main_menu_keys().get(key)
            if callback is None:
                return None
            callback()
            return "break"
        callback = self._hotkey_targets.get(key)
        if callback is None:
            return None
        callback()
        return "break"

    def _on_motion(self, event):
        """Update hover state and cursor for registered hit targets."""

        hover = ""
        for kind, ident, bbox, _callback in reversed(self._click_targets):
            if _inside(event.x, event.y, bbox):
                hover = f"{kind}:{ident}"
                break
        if hover != self._hover_key:
            self._hover_key = hover
            self.canvas.configure(cursor="hand2" if hover else "")
            self._redraw_current()

    def _on_mouse_wheel(self, event):
        """Scroll the tab body when the pointer is over it."""

        if getattr(event, "num", 0) in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            steps = -1 if getattr(event, "delta", 0) > 0 else 1
        if not self._body_box or not _inside(event.x, event.y, self._body_box):
            return None
        self._body_scroll = max(0, self._body_scroll + steps * 3 * self._line_h(Type.BODY))
        self._redraw_current()
        return "break"

    def _on_leave(self, _event):
        """Clear hover state when the pointer leaves the canvas."""

        if self._hover_key:
            self._hover_key = ""
            self.canvas.configure(cursor="")
            self._redraw_current()

    def _redraw_current(self):
        """Redraw using the current canvas size."""

        width = max(self.canvas.winfo_width(), MIN_DESK_W)
        height = max(self.canvas.winfo_height(), MIN_DESK_H)
        self._draw(width, height)

    # ----- frame -------------------------------------------------------------

    def _draw(self, width, height):
        """Lay out the pane: body first (scrolled), then the fixed bands over it."""

        c = self.canvas
        c.delete("all")
        self._click_targets = []
        self._hotkey_targets = {}
        # A full page, not a card: it covers the desk until the player picks.
        c.create_rectangle(0, 0, width, height, fill=Palette.CONTENT, outline="")

        margin = Space.M
        header_h = HEADER_H
        status_y0 = header_h + Space.S
        status_box = (margin, status_y0, width - margin, status_y0 + STATUS_H)
        tabs_y0 = status_box[3] + Space.S
        tabs_box = (margin, tabs_y0, width - margin, tabs_y0 + TAB_H)
        footer_box = (0, height - FOOTER_H, width, height)
        body_box = (margin, tabs_box[3] + Space.S, width - margin, footer_box[1] - Space.S)
        self._body_box = body_box

        tab = self.model.selected_desk_tab
        if tab != self._body_tab:
            self._body_tab = tab
            self._body_scroll = 0
        content_h = self._draw_body(c, body_box, tab)
        view_h = max(1, body_box[3] - body_box[1])
        max_scroll = max(0, content_h - view_h)
        if self._body_scroll > max_scroll:
            self._body_scroll = max_scroll
            c.delete("all")
            self._click_targets = []
            self._hotkey_targets = {}
            c.create_rectangle(0, 0, width, height, fill=Palette.FRAME, outline="")
            self._draw_body(c, body_box, tab)
        if max_scroll:
            self._draw_scrollbar(c, body_box, content_h)

        # Fixed bands cover anything the body scrolled under them.
        c.create_rectangle(0, 0, width, body_box[1], fill=Palette.FRAME, outline="")
        c.create_rectangle(0, body_box[3], width, height, fill=Palette.FRAME, outline="")
        self._draw_header(c, width, header_h)
        self._status_strip_box = status_box
        self._draw_status_strip(c, status_box)
        c.create_line(0, 0, 0, 0, state="hidden", tags=("status-strip-top",))
        self._draw_tabs(c, tabs_box)
        self._draw_footer(c, footer_box)
        self._register_global_hotkeys()
        if self._menu_open:
            self._draw_session_menu(c, width)
        if self.model.main_menu is not None:
            self._draw_main_menu(c, width, height)
        if self.model.show_start_help:
            self._draw_start_help_overlay(c, width, height)

    def _register_global_hotkeys(self):
        """Keys that work from any tab: W end week, S scorecard, ? help."""

        if self.model.game_active and not self.model.outcome:
            self._hotkey_targets["w"] = self.callbacks.advance_turn
        if self.model.game_active:
            self._hotkey_targets["s"] = self.callbacks.scorecard
        self._hotkey_targets["?"] = self.callbacks.show_help

    # ----- header ------------------------------------------------------------

    def _draw_header(self, c, width, h):
        """Draw week, AP, money, then the goal, audit and patience lines."""

        m = self.model
        c.create_rectangle(0, 0, width, h, fill=Palette.HEADER, outline="")
        c.create_line(0, h - 1, width, h - 1, fill=Palette.DIVIDER)
        x0 = Space.M
        menu_x0 = width - Space.M - 34
        y = Space.S
        top_h = 30
        cells = (
            ("WEEK", m.week_label or "-", Palette.INK),
            ("AP", m.ap_label or "-", Palette.GOOD if not m.ap_label.startswith("0/") else Palette.WATCH),
            ("$", f"{m.money} ({m.money_net:+d})" if m.game_active else "-", Palette.GOOD if m.money >= 20 else Palette.WATCH),
        )
        cx = x0
        for (label, value, color), cell_w in zip(cells, self._header_cell_widths(cells, menu_x0 - x0 - Space.S)):
            c.create_text(cx, y + 2, text=label, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            label_w = (self._text_w(label, Type.SMALL, "bold") or 24) + Space.XS
            c.create_text(cx + label_w, y, text=self._fit_px(value, Type.TITLE, "bold", cell_w - label_w - Space.S), anchor="nw", fill=color, font=self._font(Type.TITLE, "bold"))
            cx += cell_w
        self._draw_menu_button(c, menu_x0, y, menu_x0 + 34, y + 26)
        y += top_h
        line_w = width - x0 - Space.M
        self._draw_header_line(c, x0, y, line_w, "GOAL", self._goal_text(), Palette.GOOD if m.goal_met else Palette.INK)
        y += HEADER_LINE_H
        self._draw_header_line(c, x0, y, line_w, "AUDIT", self._audit_text(), _grade_color(m.audit_grade))
        y += HEADER_LINE_H
        self._draw_ladder(c, x0, y, line_w)

    def _header_cell_widths(self, cells, avail):
        """Give each top header cell its measured width plus an even share of the rest.

        A long value such as 12/12 CLOSED takes room the short AP cell does not
        need. Falls back to equal cells when fonts are unmeasured or text overflows.
        """

        equal = [max(70, avail // len(cells))] * len(cells)
        needs = []
        for label, value, _color in cells:
            label_w = self._text_w(label, Type.SMALL, "bold")
            value_w = self._text_w(value, Type.TITLE, "bold")
            if label_w is None or value_w is None:
                return equal
            needs.append(label_w + Space.XS + value_w + Space.S)
        spare = avail - sum(needs)
        if spare < 0:
            return equal
        return [need + spare // len(cells) for need in needs]

    def _goal_text(self):
        m = self.model
        if not m.game_active:
            return "Start a new game to draw this season's goals."
        if m.outcome:
            return {"won": "Season won.", "lost": "Season lost.", "dismissed": "Office dismissed."}.get(m.outcome, m.outcome)
        if not m.goal_title:
            return "Not filed yet. Pick one on the Desk tab."
        mark = "met" if m.goal_met else "open"
        # Title first, so a narrow pane cuts the progress, not the goal's name (D15).
        return f"{m.goal_title}: {m.goal_progress} ({mark})."

    def _audit_text(self):
        m = self.model
        if not m.game_active:
            return "-"
        grade = "COND" if m.audit_grade == "CONDITIONAL" else (m.audit_grade or "-")
        gap = "PASS line met" if not m.audit_points_short and not m.audit_criticals else f"{m.audit_points_short} to PASS"
        if m.audit_criticals:
            gap += f", {m.audit_criticals} critical"
        when = f"week {m.next_checkpoint} checkpoint" if m.next_checkpoint else "no checkpoints left"
        return f"{when}: {grade} {m.audit_score}, {gap}"

    def _draw_header_line(self, c, x0, y, width, label, text, color):
        c.create_text(x0, y + 2, text=label, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
        tx = x0 + HEADER_LABEL_W
        c.create_text(tx, y, text=self._fit_px(text, Type.BODY, "bold", width - HEADER_LABEL_W), anchor="nw", fill=color, font=self._font(Type.BODY, "bold"))

    def _draw_ladder(self, c, x0, y, width):
        """Draw the patience ladder as four steps with the current one filled."""

        c.create_text(x0, y + 2, text="PATIENCE", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
        rungs = rules.AUDIT_RUNGS
        current = self.model.ladder_rung if self.model.game_active else ""
        step_x0 = x0 + HEADER_LABEL_W
        step_w = max(48, (width - HEADER_LABEL_W) // len(rungs))
        for index, rung in enumerate(rungs):
            sx0 = step_x0 + index * step_w
            active = rung == current
            color = Palette.BAD if index >= 2 else Palette.WATCH if index == 1 else Palette.GOOD
            c.create_rectangle(sx0, y + 1, sx0 + step_w - 4, y + 15, fill=color if active else Palette.SUBTLE, outline=color if active else Palette.BORDER, tags=("ladder-step",))
            c.create_text(sx0 + (step_w - 4) // 2, y + 8, text=self._fit_px(rung, Type.SMALL, "bold", step_w - 8), anchor="center", fill=Palette.CONTENT if active else Palette.MUTED, font=self._font(Type.SMALL, "bold"))

    def _draw_status_strip(self, c, box):
        """Draw the one-line status: the last action's result, else the scrolling wire ticker."""

        x0, y0, x1, y1 = box
        tags = ("status-strip",)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PANE, outline=Palette.DIVIDER, tags=tags)
        mid = (y0 + y1) // 2
        text_x0 = x0 + Space.S
        if self.model.status_text:
            text = self._fit_px(self.model.status_text, Type.BODY, "normal", x1 - text_x0 - Space.S)
            c.create_text(text_x0, mid, text=text, anchor="w", fill=Palette.INK, font=self._font(Type.BODY), tags=tags)
            return
        items = [str(item or "").strip() for item in (self.model.ticker_items or ()) if str(item or "").strip()]
        if not items:
            return
        marquee = "    •    ".join(items)
        window_w = max(1, x1 - text_x0 - Space.S)
        text_w = self._text_w(marquee, Type.BODY) or len(marquee) * 7
        start_x = x1 - (self._ticker_offset_px % max(1, text_w + window_w))
        c.create_text(start_x, mid, text=marquee, anchor="w", fill=Palette.INK, font=self._font(Type.BODY), tags=tags + ("status-marquee",))
        # Mask both ends: nothing scrolls past the strip into the desk margin.
        c.create_rectangle(x0 - MASK_REACH, y0, x0, y1, fill=Palette.FRAME, outline="", tags=tags)
        c.create_rectangle(x1, y0, x1 + MASK_REACH, y1, fill=Palette.FRAME, outline="", tags=tags)

    def _draw_tabs(self, c, box):
        x0, y0, x1, y1 = box
        report_count = len(self.model.report_tabs or ())
        tabs = (
            ("applications", "Desk"),
            ("reports", f"Reports {report_count}" if report_count else "Reports"),
            ("city", "City"),
        )
        tab_w = (x1 - x0) // len(tabs)
        c.create_line(x0, y1 - 1, x1, y1 - 1, fill=Palette.DIVIDER)
        for index, (tab_id, label) in enumerate(tabs):
            tx0 = x0 + index * tab_w
            tx1 = x1 if index == len(tabs) - 1 else tx0 + tab_w
            selected = self.model.selected_desk_tab == tab_id
            hover = self._hover_key == f"desk-tab:{tab_id}"
            if selected or hover:
                c.create_rectangle(tx0, y0, tx1, y1, fill=Palette.CONTENT if selected else Palette.PANE, outline="")
            c.create_line(tx0, y1 - 1, tx1, y1 - 1, fill=Palette.ACCENT if selected else Palette.DIVIDER, width=3 if selected else 1)
            c.create_text((tx0 + tx1) // 2, (y0 + y1) // 2, text=label, anchor="center", fill=Palette.INK if selected else Palette.MUTED, font=self._font(Type.BODY, "bold" if selected else "normal"))
            self._add_target("desk-tab", tab_id, (tx0, y0, tx1, y1), lambda tab_id=tab_id: self.callbacks.select_desk_tab(tab_id))

    def _draw_footer(self, c, box):
        """Draw End Week with the close forecast, or the season result."""

        x0, y0, x1, y1 = box
        m = self.model
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.HEADER, outline="")
        c.create_line(x0, y0, x1, y0, fill=Palette.DIVIDER)
        bx0 = x0 + Space.M
        by0 = y0 + (y1 - y0 - 34) // 2
        if not m.game_active or m.outcome:
            self._draw_button(c, bx0, by0, bx0 + 132, by0 + 34, "New Game", "", Palette.ACCENT, self.callbacks.new_game, kind="footer")
            note = "No game in this workspace." if not m.game_active else "The season is over. See Reports for the final audit."
        else:
            self._draw_button(c, bx0, by0, bx0 + 132, by0 + 34, "End Week", "W", Palette.INK, self.callbacks.advance_turn, kind="footer", primary=True)
            note = m.close_forecast
            if m.auto_close_active:
                note = f"Closes in {m.auto_close_seconds} s."
                self._draw_button(c, x1 - Space.M - 96, by0, x1 - Space.M, by0 + 34, "Keep open", "", Palette.MUTED, self.callbacks.cancel_queue_autoclose, kind="footer")
        note_x0 = bx0 + 132 + Space.M
        note_x1 = x1 - Space.M - (104 if m.auto_close_active else 0)
        lines = self._fit_lines_px(note, Type.SMALL, "normal", max(40, note_x1 - note_x0), 2)
        c.create_text(note_x0, (y0 + y1) // 2, text="\n".join(lines), anchor="w", fill=Palette.MUTED, font=self._font(Type.SMALL))

    # ----- body --------------------------------------------------------------

    def _draw_body(self, c, box, tab):
        """Draw the selected tab's content from the scroll offset; return its full height."""

        x0, y0, x1, _y1 = box
        top = y0 - self._body_scroll
        if tab == "reports":
            bottom = self._draw_reports_tab(c, x0, x1, top)
        elif tab == "city":
            bottom = self._draw_city_tab(c, x0, x1, top)
        else:
            bottom = self._draw_desk_tab(c, x0, x1, top)
        return bottom - top

    def _draw_scrollbar(self, c, box, content_h):
        x0, y0, x1, y1 = box
        view_h = y1 - y0
        track_x0 = x1 + 2
        c.create_rectangle(track_x0, y0, track_x0 + 4, y1, fill=Palette.SUBTLE, outline="", tags=("body-scrollbar",))
        thumb_h = max(Space.XL, int(view_h * view_h / max(1, content_h)))
        max_scroll = max(1, content_h - view_h)
        thumb_y0 = y0 + int((view_h - thumb_h) * self._body_scroll / max_scroll)
        c.create_rectangle(track_x0, thumb_y0, track_x0 + 4, thumb_y0 + thumb_h, fill=Palette.BORDER, outline="", tags=("body-scrollbar",))

    def _add_body_target(self, kind, ident, bbox, callback, hotkey=""):
        """Register a click target (and hotkey) only for the part visible in the body."""

        if hotkey:
            self._hotkey_targets[hotkey.lower()] = callback
        body = self._body_box
        if body is None:
            self._add_target(kind, ident, bbox, callback)
            return
        x0, y0, x1, y1 = bbox
        clipped = (max(x0, body[0]), max(y0, body[1]), min(x1, body[2]), min(y1, body[3]))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            self._add_target(kind, ident, clipped, callback)

    def _section_title(self, c, x0, y, text, note=""):
        c.create_text(x0, y, text=text, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
        if note:
            c.create_text(x0 + (self._text_w(text, Type.SMALL, "bold") or 60) + Space.S, y, text=note, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
        return y + self._line_h(Type.SMALL, "bold") + Space.XS

    def _wrapped_text(self, c, x0, y, width, text, size=Type.BODY, weight="normal", fill=None, max_lines=0):
        lines = self._wrap_px(text, size, weight, width)
        if max_lines and len(lines) > max_lines:
            lines = self._fit_lines_px(text, size, weight, width, max_lines)
        if not lines:
            return y
        c.create_text(x0, y, text="\n".join(lines), anchor="nw", fill=fill or Palette.INK, font=self._font(size, weight))
        return y + len(lines) * self._line_h(size, weight)

    def _card(self, c, x0, y0, x1, y1, fill=None, outline=None):
        c.create_rectangle(x0, y0, x1, y1, fill=fill or Palette.CONTENT, outline=outline or Palette.BORDER)

    def _draw_desk_tab(self, c, x0, x1, top):
        m = self.model
        if not m.game_active:
            return self._draw_message_card(c, x0, x1, top, "No game yet", "This workspace has no Permit Office board. Click New Game to generate a city and draw this season's goals.")
        if m.outcome:
            verdict = {"won": "Season won", "lost": "Season lost", "dismissed": "Office dismissed"}.get(m.outcome, "Season over")
            goal = f"Goal: {m.goal_title}, {m.goal_progress}." if m.goal_title else "No goal was filed."
            return self._draw_message_card(c, x0, x1, top, verdict, f"{goal} The final audit is filed under Reports.")
        if not m.goal_title and m.goal_offer:
            return self._draw_goal_picker(c, x0, x1, top)
        y = self._draw_inbox(c, x0, x1, top)
        if m.docket_rows and m.selected_item_id:
            y = self._draw_brief(c, x0, x1, y + Space.M)
        y = self._draw_initiative_card(c, x0, x1, y + Space.M)
        return y + Space.S

    def _draw_message_card(self, c, x0, x1, top, title, body):
        pad = Space.L
        y = top + pad
        title_h = self._line_h(Type.DISPLAY, "bold")
        body_lines = self._wrap_px(body, Type.BODY, "normal", x1 - x0 - 2 * pad)
        height = pad + title_h + Space.S + len(body_lines) * self._line_h(Type.BODY) + pad
        self._card(c, x0, top, x1, top + height)
        c.create_text(x0 + pad, y, text=title, anchor="nw", fill=Palette.INK, font=self._font(Type.DISPLAY, "bold"))
        y += title_h + Space.S
        c.create_text(x0 + pad, y, text="\n".join(body_lines), anchor="nw", fill=Palette.MUTED, font=self._font(Type.BODY))
        return top + height

    def _draw_goal_picker(self, c, x0, x1, top):
        """Draw the three seeded goals as cards; clicking one files it as the season goal."""

        y = self._section_title(c, x0, top, "PICK THIS SEASON'S GOAL", "one of three, kept all season")
        pad = Space.M
        for index, (key, title, brief, progress) in enumerate(self.model.goal_offer):
            card_y0 = y + Space.XS
            inner_w = x1 - x0 - 2 * pad
            text_y = card_y0 + pad
            hover = self._hover_key == f"goal:{key}"
            brief_lines = self._wrap_px(brief, Type.BODY, "normal", inner_w)
            height = pad + self._line_h(Type.TITLE, "bold") + Space.XS + len(brief_lines) * self._line_h(Type.BODY) + self._line_h(Type.SMALL) + Space.XS + pad
            self._card(c, x0, card_y0, x1, card_y0 + height, outline=Palette.ACCENT if hover else Palette.BORDER)
            c.create_rectangle(x0, card_y0, x0 + 4, card_y0 + height, fill=Palette.ACCENT, outline="")
            c.create_text(x0 + pad, text_y, text=self._fit_px(f"{index + 1}. {title}", Type.TITLE, "bold", inner_w), anchor="nw", fill=Palette.INK, font=self._font(Type.TITLE, "bold"))
            text_y += self._line_h(Type.TITLE, "bold") + Space.XS
            c.create_text(x0 + pad, text_y, text="\n".join(brief_lines), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY))
            text_y += len(brief_lines) * self._line_h(Type.BODY) + Space.XS
            c.create_text(x0 + pad, text_y, text=self._fit_px(f"Now: {progress}", Type.SMALL, "normal", inner_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
            self._add_body_target("goal", key, (x0, card_y0, x1, card_y0 + height), lambda key=key: self.callbacks.choose_mandate(key), hotkey=str(index + 1))
            y = card_y0 + height + Space.S
        return y

    def _draw_inbox(self, c, x0, x1, top):
        m = self.model
        rows = list(m.docket_rows)
        filed = list(m.filed_rows)
        y = self._section_title(c, x0, top, f"OPEN ({len(rows)})")
        row_h = self._line_h(Type.BODY, "bold") + self._line_h(Type.SMALL) + 10
        if not rows:
            text = "Queue cleared. End Week to process follow-ups."
            y = self._wrapped_text(c, x0 + Space.XS, y + Space.XS, x1 - x0, text, fill=Palette.MUTED) + Space.XS
        for row in rows:
            ry0, ry1 = y, y + row_h
            hover = self._hover_key == f"docket:{row.item_id}"
            fill = Palette.SELECT if row.selected else Palette.CONTENT
            c.create_rectangle(x0, ry0, x1, ry1, fill=fill, outline=Palette.ACCENT if hover else Palette.BORDER)
            c.create_rectangle(x0, ry0, x0 + 4, ry1, fill=_status_color(row.status), outline="")
            tag = _geometry_tag(row.geometry_type)
            tag_w = (self._text_w(tag, Type.SMALL, "bold") or 40) + Space.S
            c.create_text(x0 + Space.M, ry0 + 4, text=self._fit_px(row.title, Type.BODY, "bold", x1 - x0 - 2 * Space.M - tag_w), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
            c.create_text(x1 - Space.S, ry0 + 5, text=tag, anchor="ne", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            status = row.status.upper() if row.status != "open" else ""
            note = f"{status}  if ignored: {row.if_ignored}" if status else f"If ignored: {row.if_ignored}"
            c.create_text(x0 + Space.M, ry0 + 6 + self._line_h(Type.BODY, "bold"), text=self._fit_px(note, Type.SMALL, "normal", x1 - x0 - 2 * Space.M), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
            self._add_body_target("docket", row.item_id, (x0, ry0, x1, ry1), lambda item_id=row.item_id: self.on_select_item(item_id))
            y = ry1 + Space.XS
        if filed:
            y = self._section_title(c, x0, y + Space.XS, f"FILED ({len(filed)})")
            for row in filed:
                line_h = self._line_h(Type.BODY) + 4
                label = _filed_label(row.status)
                label_w = (self._text_w(label, Type.SMALL, "bold") or 50) + Space.S
                c.create_text(x0 + Space.M, y, text=self._fit_px(row.title, Type.BODY, "normal", x1 - x0 - 2 * Space.M - label_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.BODY), tags=("filed-row",))
                c.create_text(x1 - Space.S, y + 1, text=label, anchor="ne", fill=_status_color(row.status), font=self._font(Type.SMALL, "bold"))
                y += line_h
        return y

    def _draw_brief(self, c, x0, x1, top):
        """Draw the selected case: facts, the four actions, the forecast, exhibit links."""

        m = self.model
        case = m.case
        pad = Space.M
        inner_x0 = x0 + pad
        inner_w = x1 - x0 - 2 * pad
        y = top + pad
        risk_known = (case.risk_band or "unknown").lower() not in ("", "unknown")
        risk_text = f"RISK: {case.risk_band.upper()}" if risk_known else "RISK: ?"
        risk_w = (self._text_w(risk_text, Type.SMALL, "bold") or 60) + 2 * Space.S
        title_y = y
        y += self._line_h(Type.TITLE, "bold") + Space.XS
        fields = {field.label.lower(): field.value for field in case.fields}
        facts = []
        if fields.get("applicant"):
            facts.append(fields["applicant"])
        if case.districts:
            facts.append(f"Targets: {case.districts}")
        inspected = bool(case.inspection) and not case.inspection.lower().startswith("no inspection")
        note = case.inspection if inspected else "Uninspected: impacts are estimates. Inspect File can reveal violations."
        row = next((r for r in m.docket_rows if r.item_id == m.selected_item_id), None)
        body_lines = []
        for fact in facts:
            body_lines += [(line, Palette.INK, "normal") for line in self._fit_lines_px(fact, Type.BODY, "normal", inner_w, 2)]
        if case.preview:
            body_lines += [(line, Palette.MUTED, "normal") for line in self._fit_lines_px(case.preview, Type.BODY, "normal", inner_w, 3)]
        if case.economy:
            budget = "Budget: no recurring revenue or upkeep" if case.economy == "no recurring budget" else f"Budget: {case.economy}"
            tone = Palette.GOOD if "net +" in case.economy else Palette.BAD if "net -" in case.economy else Palette.MUTED
            body_lines.append((self._fit_px(budget, Type.BODY, "bold", inner_w), tone, "bold"))
        body_lines += [(line, Palette.WATCH if not inspected else Palette.ACCENT, "bold") for line in self._fit_lines_px(note, Type.BODY, "bold", inner_w, 2)]
        if row is not None:
            body_lines.append((f"If ignored: {row.if_ignored}", Palette.MUTED, "normal"))
        lines_h = sum(self._line_h(Type.BODY, weight) for _text, _fill, weight in body_lines)
        controls = self._case_controls()
        button_h = 40
        grid_h = 2 * button_h + Space.XS
        lane = self._lane_by_action.get("approve")
        forecast = [f"City: {lane.city_effect}", f"{lane.local_effect}"] if lane else []
        forecast_h = len(forecast) * self._line_h(Type.SMALL)
        links_h = self._line_h(Type.SMALL, "bold") + Space.XS
        height = pad + (y - top - pad) + lines_h + Space.S + grid_h + Space.S + forecast_h + Space.XS + links_h + pad
        self._card(c, x0, top, x1, top + height)
        c.create_text(inner_x0, title_y, text=self._fit_px(case.title, Type.TITLE, "bold", inner_w - risk_w - Space.S), anchor="nw", fill=Palette.INK, font=self._font(Type.TITLE, "bold"))
        c.create_rectangle(x1 - pad - risk_w, title_y, x1 - pad, title_y + 18, fill=Palette.CONTENT, outline=_risk_color(case.risk_band))
        c.create_text(x1 - pad - risk_w // 2, title_y + 9, text=risk_text, anchor="center", fill=_risk_color(case.risk_band), font=self._font(Type.SMALL, "bold"))
        for text, fill, weight in body_lines:
            c.create_text(inner_x0, y, text=text, anchor="nw", fill=fill, font=self._font(Type.BODY, weight))
            y += self._line_h(Type.BODY, weight)
        y += Space.S
        col_w = (inner_w - Space.XS) // 2
        for index, (label, cost, color, callback, enabled, hotkey, reason) in enumerate(controls):
            bx0 = inner_x0 + (index % 2) * (col_w + Space.XS)
            by0 = y + (index // 2) * (button_h + Space.XS)
            self._draw_action_button(c, bx0, by0, bx0 + col_w, by0 + button_h, label, cost, color, callback, enabled, hotkey, reason)
        y += grid_h + Space.S
        for line in forecast:
            c.create_text(inner_x0, y, text=self._fit_px(line, Type.SMALL, "normal", inner_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
            y += self._line_h(Type.SMALL)
        y += Space.XS
        exhibit = "Hide exhibit" if m.exhibit_visible else "Show exhibit"
        link_x = inner_x0
        for label, hotkey, callback in ((exhibit, "V", self.callbacks.toggle_exhibit), ("Retarget from map", "T", self.callbacks.update_from_map)):
            text = f"[{hotkey}] {label}"
            w = (self._text_w(text, Type.SMALL, "bold") or len(text) * 6)
            hover = self._hover_key == f"case-link:{label}"
            c.create_text(link_x, y, text=text, anchor="nw", fill=Palette.INK if hover else Palette.ACCENT, font=self._font(Type.SMALL, "bold"))
            self._add_body_target("case-link", label, (link_x, y, link_x + w, y + self._line_h(Type.SMALL, "bold")), callback, hotkey=hotkey)
            link_x += w + Space.L
        return top + height

    def _case_controls(self):
        """Return (label, cost, color, callback, enabled, hotkey, reason) for the four case actions."""

        self._ensure_lookups()
        lanes = self._lane_by_action
        controls = [("Inspect File", "1 AP", Palette.WATCH, self.callbacks.inspect, True, "I", "")]
        for action, color, callback, fallback in (
            ("approve", Palette.GOOD, self.callbacks.approve, ("Issue Permit", "A")),
            ("approve_mitigated", Palette.MITIGATE, self.callbacks.approve_mitigated, ("Add Conditions", "M")),
            ("deny", Palette.BAD, self.callbacks.deny, ("Deny", "D")),
        ):
            lane = lanes.get(action)
            if lane is None:
                controls.append((fallback[0], "", color, callback, False, fallback[1], "No case selected"))
                continue
            cost = lane.cost.split(";")[0]
            controls.append((lane.label, cost, color, callback, lane.enabled, lane.hotkey, lane.disabled_reason))
        return controls

    def _draw_action_button(self, c, x0, y0, x1, y1, label, cost, color, callback, enabled, hotkey, reason):
        hover = enabled and self._hover_key == f"case-action:{label}"
        tone = color if enabled else Palette.MUTED
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SELECT if hover else (Palette.CONTENT if enabled else Palette.SUBTLE), outline=tone if enabled else Palette.BORDER, tags=("action-button",))
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=tone, outline="")
        tx = x0 + Space.S + 2
        if hotkey:
            c.create_rectangle(tx, y0 + 5, tx + 16, y0 + 19, fill=Palette.SUBTLE, outline=Palette.BORDER, tags=("hotkey-badge",))
            c.create_text(tx + 8, y0 + 12, text=hotkey, anchor="center", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(Type.SMALL, "bold"), tags=("hotkey-badge",))
            tx += 22
        c.create_text(tx, y0 + 4, text=self._fit_px(label, Type.BODY, "bold", x1 - tx - Space.XS), anchor="nw", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(Type.BODY, "bold"))
        detail = cost if enabled or not reason else reason
        c.create_text(x0 + Space.S + 2, y1 - 4, text=self._fit_px(detail, Type.SMALL, "bold", x1 - x0 - 2 * Space.S), anchor="sw", fill=tone, font=self._font(Type.SMALL, "bold"), tags=("cost-line",))
        if enabled:
            self._add_body_target("case-action", label, (x0, y0, x1, y1), callback, hotkey=hotkey)

    def _draw_initiative_card(self, c, x0, x1, top):
        """Draw this week's initiative: earmark a district type, or act on the map selection."""

        m = self.model
        pad = Space.M
        inner_x0 = x0 + pad
        inner_w = x1 - x0 - 2 * pad
        y = top + pad
        title_y = y
        y += self._line_h(Type.SMALL, "bold") + Space.XS
        types = list(m.earmark_types)
        if types and self._earmark_choice not in types:
            self._earmark_choice = types[0]
        chip_h = 22
        chips_per_row = 3
        chip_w = (inner_w - Space.XS * (chips_per_row - 1)) // chips_per_row
        chip_rows = (len(types) + chips_per_row - 1) // chips_per_row
        button_h = 40
        height = (y - top) + chip_rows * (chip_h + Space.XS) + Space.XS + 2 * (button_h + Space.XS) + self._line_h(Type.SMALL) + pad
        self._card(c, x0, top, x1, top + height)
        note = m.initiative_note or "one a week, 1 AP"
        self._section_title(c, inner_x0, title_y, "INITIATIVE", note)
        enabled = m.initiative_open
        for index, dtype in enumerate(types):
            cx0 = inner_x0 + (index % chips_per_row) * (chip_w + Space.XS)
            cy0 = y + (index // chips_per_row) * (chip_h + Space.XS)
            chosen = dtype == self._earmark_choice
            c.create_rectangle(cx0, cy0, cx0 + chip_w, cy0 + chip_h, fill=Palette.SELECT if chosen else Palette.SUBTLE, outline=Palette.ACCENT if chosen else Palette.BORDER, tags=("earmark-chip",))
            c.create_text(cx0 + chip_w // 2, cy0 + chip_h // 2, text=self._fit_px(dtype.replace("_", " ").title(), Type.SMALL, "bold" if chosen else "normal", chip_w - 6), anchor="center", fill=Palette.INK, font=self._font(Type.SMALL, "bold" if chosen else "normal"))
            self._add_body_target("earmark-type", dtype, (cx0, cy0, cx0 + chip_w, cy0 + chip_h), lambda dtype=dtype: self._choose_earmark(dtype))
        y += chip_rows * (chip_h + Space.XS) + Space.XS
        choice = (self._earmark_choice or "type").replace("_", " ")
        buttons = (
            (f"Earmark {choice}", f"${rules.EARMARK_COST}, 3 weeks", lambda: self.callbacks.start_initiative("earmark", self._earmark_choice)),
            ("Civic action", f"${rules.CIVIC_ACTION_COST}, map selection", lambda: self.callbacks.start_initiative("civic_action", None)),
            ("Market push", f"${rules.MARKET_PUSH_COST}, map selection", lambda: self.callbacks.start_initiative("market_push", None)),
        )
        col_w = (inner_w - Space.XS) // 2
        for index, (label, cost, callback) in enumerate(buttons):
            if index == 0:
                bx0, bx1, by0 = inner_x0, inner_x0 + inner_w, y
            else:
                bx0 = inner_x0 + (index - 1) * (col_w + Space.XS)
                bx1, by0 = bx0 + col_w, y + button_h + Space.XS
            self._draw_action_button(c, bx0, by0, bx1, by0 + button_h, label, cost, Palette.TEAL, callback, enabled, "", m.initiative_note)
        y += 2 * (button_h + Space.XS)
        c.create_text(inner_x0, y, text=self._fit_px("Civic action and Market push use the districts selected on the map.", Type.SMALL, "normal", inner_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
        return top + height

    def _choose_earmark(self, dtype):
        self._earmark_choice = dtype
        self._redraw_current()

    def _draw_city_tab(self, c, x0, x1, top):
        """Draw the four stats with their week-start trends, city services, and district types."""

        self._ensure_lookups()
        by_label = self._ledger_by_label
        y = self._section_title(c, x0, top, "CITY STATS", "change since the week began")
        gap = Space.XS
        cell_w = (x1 - x0 - gap) // 2
        cell_h = 48
        for index, name in enumerate(("Activity", "Trust", "Friction", "Exposure")):
            row = by_label.get(name)
            if row is None:
                continue
            cx0 = x0 + (index % 2) * (cell_w + gap)
            cy0 = y + (index // 2) * (cell_h + gap)
            tone = _tone_color(row.tone)
            self._card(c, cx0, cy0, cx0 + cell_w, cy0 + cell_h, fill=Palette.SUBTLE)
            c.create_text(cx0 + Space.S, cy0 + 6, text=name.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            c.create_text(cx0 + Space.S, cy0 + 22, text=row.value, anchor="nw", fill=tone, font=self._font(Type.TITLE, "bold"))
            if (row.trend or "").lower() in ("up", "down", "flat"):
                c.create_text(cx0 + cell_w - Space.S, cy0 + 26, text=row.trend, anchor="ne", fill=tone, font=self._font(Type.SMALL, "bold"))
        y += 2 * (cell_h + gap) + Space.S
        for name in ("Economy", "Heat", "Services", "Incidents"):
            row = by_label.get(name)
            if row is None:
                continue
            label_w = 76
            c.create_text(x0, y, text=name.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            y = self._wrapped_text(c, x0 + label_w, y, x1 - x0 - label_w, row.value, fill=_tone_color(row.tone)) + Space.XS
        y = self._section_title(c, x0, y + Space.S, "DISTRICT TYPES", "districts held")
        for row in self.model.district_type_rows:
            row_h = self._line_h(Type.BODY) + 4
            _record_box(c, "group-swatch", (x0 + 2, y + 3, x0 + 12, y + 13))
            c.create_rectangle(x0 + 2, y + 3, x0 + 12, y + 13, fill=row.swatch or Palette.BORDER, outline=Palette.BORDER)
            c.create_text(x0 + 20, y, text=row.label, anchor="nw", fill=Palette.INK, font=self._font(Type.BODY))
            note = f"{row.count}   earmarked to week {row.earmarked_until}" if row.earmarked_until else str(row.count)
            c.create_text(x1, y, text=note, anchor="ne", fill=Palette.ACCENT if row.earmarked_until else Palette.MUTED, font=self._font(Type.BODY, "bold"))
            y += row_h
        return y + Space.S

    def _draw_reports_tab(self, c, x0, x1, top):
        """Draw the filed report list, then the selected report's sections."""

        tabs = list(self.model.report_tabs or ())
        if not tabs:
            return self._draw_message_card(c, x0, x1, top, "No filed report yet", "Decisions, inspections, initiatives, and week closes file their reports here.")
        y = self._section_title(c, x0, top, f"FILED ({len(tabs)})")
        selected = next((tab for tab in tabs if tab.report_id == self.model.selected_report_id), tabs[-1])
        row_h = self._line_h(Type.BODY, "bold") + 8
        for tab in reversed(tabs):
            chosen = tab.report_id == selected.report_id
            hover = self._hover_key == f"report:{tab.report_id}"
            c.create_rectangle(x0, y, x1, y + row_h, fill=Palette.SELECT if chosen else Palette.CONTENT, outline=Palette.ACCENT if hover else Palette.BORDER)
            c.create_rectangle(x0, y, x0 + 4, y + row_h, fill=_status_color(tab.status), outline="")
            status = tab.status.upper()
            status_w = (self._text_w(status, Type.SMALL, "bold") or 50) + Space.S
            c.create_text(x0 + Space.M, y + 4, text=self._fit_px(tab.title, Type.BODY, "bold", x1 - x0 - 2 * Space.M - status_w), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
            c.create_text(x1 - Space.S, y + 5, text=status, anchor="ne", fill=_status_color(tab.status), font=self._font(Type.SMALL, "bold"))
            self._add_body_target("report", tab.report_id, (x0, y, x1, y + row_h), lambda report_id=tab.report_id: self.callbacks.select_report(report_id))
            y += row_h + Space.XS
        y += Space.S
        pad = Space.M
        rows = self._report_rows(selected, x1 - x0 - 2 * pad)
        body_h = sum(row[5] for row in rows)
        title_h = self._line_h(Type.TITLE, "bold") + Space.S
        metrics = tuple(selected.metrics or ())
        metrics_h = (self._line_h(Type.SMALL, "bold") + self._line_h(Type.BODY, "bold") + Space.S) if metrics else 0
        height = pad + title_h + body_h + metrics_h + pad
        self._card(c, x0, y, x1, y + height)
        ty = y + pad
        c.create_text(x0 + pad, ty, text=self._fit_px(selected.title, Type.TITLE, "bold", x1 - x0 - 2 * pad), anchor="nw", fill=Palette.INK, font=self._font(Type.TITLE, "bold"))
        ty += title_h
        for text, size, weight, fill, indent, row_height in rows:
            if text:
                c.create_text(x0 + pad + indent, ty, text=text, anchor="nw", fill=fill, font=self._font(size, weight))
            ty += row_height
        if metrics:
            cell_w = (x1 - x0 - 2 * pad) // max(1, len(metrics))
            for index, (label, value) in enumerate(metrics):
                mx = x0 + pad + index * cell_w
                c.create_text(mx, ty + Space.XS, text=label, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
                c.create_text(mx, ty + Space.XS + self._line_h(Type.SMALL, "bold"), text=self._fit_px(str(value), Type.BODY, "bold", cell_w - 4), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
        return y + height

    def _report_rows(self, report, width):
        """Return the report body as rows of (text, size, weight, fill, indent, height)."""

        summary, sections = report.summary, report.sections
        if not sections and not summary:
            summary, sections = report_sections(report.report)
        rows = []
        body_h = self._line_h(Type.BODY)
        if summary:
            for line in self._wrap_px(summary, Type.BODY, "bold", width):
                rows.append((line, Type.BODY, "bold", Palette.INK, 0, self._line_h(Type.BODY, "bold")))
            rows.append(("", Type.BODY, "normal", Palette.INK, 0, Space.S))
        bullet = "•"
        indent = Space.L
        for heading, lines in sections:
            rows.append((heading, Type.SMALL, "bold", Palette.ACCENT, 0, self._line_h(Type.SMALL, "bold") + 2))
            for line in lines:
                for index, part in enumerate(self._wrap_px(line, Type.BODY, "normal", width - indent)):
                    if index == 0:
                        rows.append((bullet, Type.BODY, "normal", Palette.MUTED, 2, 0))
                    rows.append((part, Type.BODY, "normal", Palette.INK, indent, body_h))
            rows.append(("", Type.BODY, "normal", Palette.INK, 0, Space.S))
        return rows

    # ----- shared controls ---------------------------------------------------

    def _draw_button(self, c, x0, y0, x1, y1, label, hotkey, color, callback, kind="button", primary=False):
        hover = self._hover_key == f"{kind}:{label}"
        fill = color if primary else (Palette.SELECT if hover else Palette.CONTENT)
        ink = Palette.CONTENT if primary else color
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=color, width=2 if hover else 1)
        text = f"{label}  [{hotkey}]" if hotkey else label
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=text, anchor="center", fill=ink, font=self._font(Type.BODY, "bold"))
        self._add_target(kind, label, (x0, y0, x1, y1), callback)

    def _draw_menu_button(self, c, x0, y0, x1, y1):
        """Draw the compact utility menu trigger."""

        hover = self._hover_key == "session:Menu"
        fill = Palette.SELECT if hover or self._menu_open else Palette.HEADER
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=Palette.BORDER, width=1)
        mid = (y0 + y1) // 2
        for offset in (-5, 0, 5):
            c.create_line(x0 + 9, mid + offset, x1 - 9, mid + offset, fill=Palette.INK, width=2)
        self._menu_anchor = (x0, y0, x1, y1)
        self._add_target("session", "Menu", (x0, y0, x1, y1), self._toggle_session_menu)

    def _toggle_session_menu(self):
        """Open or close the utility command dropdown."""

        self._menu_open = not self._menu_open
        if self._menu_open:
            self.callbacks.pause_queue_autoclose()
        self._redraw_current()

    def _draw_session_menu(self, c, width):
        """Draw dropdown utility actions over the desk surface."""

        entries = (
            ("End Week", Palette.INK, self.callbacks.advance_turn),
            ("New Game", Palette.ACCENT, self.callbacks.new_game),
            ("Scorecard", Palette.WATCH, self.callbacks.scorecard),
            ("Help", Palette.TEAL, self.callbacks.show_help),
            ("End Game", Palette.MUTED, self.callbacks.end_game),
        )
        ax0, _ay0, ax1, ay1 = self._menu_anchor or (width - 50, 8, width - 16, 34)
        x1 = min(width - Space.S, ax1)
        x0 = max(Space.S, x1 - 176)
        y0 = ay1
        row_h = 34
        c.create_rectangle(x0, y0, x1, y0 + row_h * len(entries), fill=Palette.CONTENT, outline=Palette.BORDER, width=1)
        for idx, (label, color, callback) in enumerate(entries):
            ry0 = y0 + idx * row_h
            ry1 = ry0 + row_h
            hover = self._hover_key == f"session:{label}"
            c.create_rectangle(x0, ry0, x1, ry1, fill=Palette.SELECT if hover else Palette.CONTENT, outline=Palette.BORDER)
            c.create_rectangle(x0, ry0, x0 + 5, ry1, fill=color, outline="")
            c.create_text(x0 + 14, (ry0 + ry1) // 2, text=label.upper(), anchor="w", fill=color, font=self._font(Type.SMALL, "bold"))
            self._add_target("session", label, (x0, ry0, x1, ry1), self._close_menu_callback(callback))

    def _close_menu_callback(self, callback):
        """Wrap a menu callback so the dropdown closes before the action runs."""

        def _wrapped():
            self._menu_open = False
            callback()

        return _wrapped

    def _draw_session_button(self, c, x0, y0, x1, y1, label, color, callback):
        """Draw a compact dashboard-level command button."""

        hover = self._hover_key == f"session:{label}"
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SELECT if hover else Palette.CONTENT, outline=color, width=1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label.upper(), anchor="center", fill=color, font=self._font(Type.SMALL, "bold"))
        self._add_target("session", label, (x0, y0, x1, y1), callback)

    def _main_menu_actions(self):
        """Map main-menu entry ids to their callbacks."""

        return {
            "continue": self.callbacks.continue_game,
            "new_game": self.callbacks.new_game,
            "help": self.callbacks.show_help,
        }

    def _main_menu_keys(self):
        """Return the keys the main menu answers, limited to its shown entries."""

        actions = self._main_menu_actions()
        keys = {"continue": "c", "new_game": "n", "help": "?"}
        return {keys[action]: actions[action] for action, _label in self.model.main_menu.entries}

    def _draw_main_menu(self, c, width, height):
        """Draw the card every Run opens on: save line, menu buttons, map guidance."""

        menu = self.model.main_menu
        # A full page, modal: the desk drawn underneath takes no clicks.
        self._click_targets = []
        c.create_rectangle(0, 0, width, height, fill=Palette.CONTENT, outline="")
        ow = min(400, width - 2 * Space.L)
        x0 = (width - ow) // 2
        x1 = x0 + ow
        y0 = max(Space.L, height // 8)
        body_x0 = x0 + Space.L
        body_x1 = x1 - Space.L
        c.create_rectangle(x0, y0, x1, y0 + 40, fill=Palette.ACCENT, outline="")
        c.create_text(body_x0, y0 + 20, text="PERMIT OFFICE", anchor="w", fill=Palette.CONTENT, font=self._font(Type.DISPLAY, "bold"))
        yy = y0 + 40 + Space.M
        yy = _text_bottom(c, body_x0, yy, "Run a city permit desk for twelve weeks. Meet your goal and pass the audits.", self._font(Type.BODY), Palette.INK, width=body_x1 - body_x0) + Space.S
        yy = _text_bottom(c, body_x0, yy, menu.save_line, self._font(Type.BODY, "bold"), Palette.INK, width=body_x1 - body_x0) + Space.M
        actions = self._main_menu_actions()
        keys = {"continue": "C", "new_game": "N", "help": "?"}
        for idx, (action, label) in enumerate(menu.entries):
            primary = idx == 0
            color = Palette.ACCENT if action != "help" else Palette.MUTED
            hover = self._hover_key == f"main-menu:{action}"
            fill = Palette.INK if primary and hover else color if primary else Palette.SELECT if hover else Palette.CONTENT
            c.create_rectangle(body_x0, yy, body_x1, yy + 40, fill=fill, outline=color, width=1)
            ink = Palette.CONTENT if primary else color
            c.create_text(body_x0 + Space.M, yy + 20, text=label.upper(), anchor="w", fill=ink, font=self._font(Type.TITLE, "bold"))
            c.create_text(body_x1 - Space.M, yy + 20, text=keys[action], anchor="e", fill=ink, font=self._font(Type.SMALL, "bold"))
            self._add_target("main-menu", action, (body_x0, yy, body_x1, yy + 40), actions[action])
            yy += 40 + Space.S
        yy += Space.S
        yy = _text_bottom(c, body_x0, yy, menu.map_note, self._font(Type.SMALL), Palette.MUTED, width=body_x1 - body_x0) + Space.S
        if menu.map_warning:
            _text_bottom(c, body_x0, yy, menu.map_warning, self._font(Type.SMALL, "bold"), Palette.WATCH, width=body_x1 - body_x0)

    def _draw_start_help_overlay(self, c, width, height):
        """Draw the start/help card: how a season works and the keys.

        Modal like the menu: only the card's own buttons take clicks.
        """

        self._click_targets = []

        ow = min(460, width - 2 * Space.L)
        oh = min(520, height - 2 * Space.L)
        x0 = (width - ow) // 2
        y0 = (height - oh) // 2
        x1 = x0 + ow
        y1 = y0 + oh
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER, width=1)
        c.create_rectangle(x0, y0, x1, y0 + 40, fill=Palette.ACCENT, outline="")
        c.create_text(x0 + Space.L, y0 + 20, text="PERMIT OFFICE", anchor="w", fill=Palette.CONTENT, font=self._font(Type.DISPLAY, "bold"))
        body_x0 = x0 + Space.L
        body_x1 = x1 - Space.L
        label_w = 76
        sections = (
            ("Goal", "Pick one of three goals when the season starts. Meet it by week 12 to win."),
            ("Audits", "Weeks 4, 8 and 12 close with an audit. A FAIL costs council patience; a PASS wins it back. Sanctioned offices get one AP less; dismissed ends the season."),
            ("Cases", "Two new cases a week. Inspect, issue, add conditions, or deny. Each case says what happens if you ignore it."),
            ("Initiative", "Once a week: earmark a district type, or fund a civic action or market push on the districts selected on the map."),
            ("City", "Stats show the change since the week began. Pro's Contents pane is the map key."),
            ("Keys", "1-3 pick a goal. I inspect, A issue, M conditions, D deny, V exhibit, T retarget, W end week, S scorecard, ? help."),
        )
        yy = y0 + 52
        for title, text in sections:
            c.create_text(body_x0, yy, text=title, anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
            yy = _text_bottom(c, body_x0 + label_w, yy, text, self._font(Type.SMALL), Palette.MUTED, width=body_x1 - body_x0 - label_w) + Space.S
        bw = min(150, (body_x1 - body_x0 - Space.M) // 2)
        self._draw_session_button(c, body_x0, y1 - 50, body_x0 + bw, y1 - 16, "New Game", Palette.ACCENT, self.callbacks.new_game)
        self._draw_session_button(c, body_x0 + bw + Space.M, y1 - 50, body_x0 + 2 * bw + Space.M, y1 - 16, "Close", Palette.MUTED, self.callbacks.show_help)

    # ----- measuring ---------------------------------------------------------

    def _add_target(self, kind, ident, bbox, callback):
        """Record a clickable canvas rectangle for later event dispatch."""

        self._click_targets.append((kind, ident, bbox, callback))

    def _font(self, size, weight="normal"):
        """Return the Segoe UI font tuple used by the desk canvas."""

        return desk_font(size, weight)

    def _ensure_lookups(self):
        """Rebuild the label->ledger-row and action->lane maps only when the model changes."""

        if self._lookup_model is self.model:
            return
        model = self.model
        self._ledger_by_label = {row.label: row for row in model.ledger_rows}
        self._lane_by_action = {lane.action_id: lane for lane in model.action_lanes}
        self._lookup_model = model

    def _wrap_px(self, text, size, weight, max_px):
        """Greedy word wrap by measured pixel width; never truncates."""

        words = " ".join(str(text or "").split()).split(" ")
        if words == [""]:
            return []
        measure = self._px_measurer(size, weight)
        if measure is None:  # headless, no Tk fonts: wrap by a conservative character count
            return wrap(" ".join(words), width=max(12, int(max_px // max(1, size))))
        lines = []
        line = ""
        for word in words:
            candidate = f"{line} {word}" if line else word
            if measure(candidate) <= max_px or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines

    def _fit_lines_px(self, text, size, weight, max_px, max_lines):
        """Wrap text by measured width to at most ``max_lines`` lines; a cut ends in an ellipsis."""

        lines = self._wrap_px(text, size, weight, max_px)
        if len(lines) <= max_lines:
            return lines
        return lines[: max_lines - 1] + [self._fit_px(" ".join(lines[max_lines - 1 :]), size, weight, max_px)]

    def _fit_px(self, text, size, weight, max_px):
        """Truncate text with an ellipsis so it renders within ``max_px`` pixels.

        Memoized on ``(text, size, weight, max_px)``: the fit is a pure function of
        its args (runtime font metrics are fixed), so the hover/redraw path reuses
        results instead of re-running the binary search every frame. The cache is
        cleared on model swap (`render`) to bound memory to one frame's strings.
        """

        text = " ".join(str(text or "").split())
        key = (text, size, weight, max_px)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached
        result = self._fit_px_compute(text, size, weight, max_px)
        self._fit_cache[key] = result
        return result

    def _fit_px_compute(self, text, size, weight, max_px):
        """Run the (uncached) text-fit for already-normalized ``text``."""

        measure = self._px_measurer(size, weight)
        if measure is None:
            return _clip(text, max(4, int(max_px // (size * 0.62))))
        if measure(text) <= max_px:
            return text
        # Binary-search the largest prefix length whose text+"..." still fits.
        # Invariant: lo = a known-fitting length, hi = an upper bound; the
        # `mid = (lo+hi+1)//2` rounding-up biases toward lo so the loop can't
        # stall when hi == lo+1. Measuring per-candidate (not estimating) keeps
        # it exact across proportional fonts.
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if measure(text[:mid] + "...") <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo] + "...") if lo else "..."

    def _tk_font(self, size, weight):
        """Return a cached Tk Font for the desk family, or None without a Tk root."""

        cache = getattr(self, "_font_cache", None)
        if cache is None:
            cache = self._font_cache = {}
        key = (size, weight)
        if key not in cache:
            try:
                import tkinter.font as tkfont

                cache[key] = tkfont.Font(root=self.root, family="Segoe UI", size=size, weight=weight)
            except Exception:
                cache[key] = None
        return cache[key]

    def _px_measurer(self, size, weight):
        """Return a cached Tk font ``measure`` callable, or None if unavailable."""

        font = self._tk_font(size, weight)
        return font.measure if font is not None else None

    def _text_w(self, text, size, weight="normal"):
        """Return the measured pixel width of ``text``, or None when fonts are unavailable."""

        measure = self._px_measurer(size, weight)
        return measure(text) if measure is not None else None

    def _line_h(self, size, weight="normal"):
        """Return the font's line height in pixels (Tk linespace when available)."""

        font = self._tk_font(size, weight)
        if font is not None:
            try:
                return int(font.metrics("linespace"))
            except Exception:
                pass
        # Headless fallback: points to 96-dpi pixels, times Segoe UI's ~1.33 linespace.
        return int(round(abs(size) * 96 / 72 * 1.33)) + 1


def _record_box(c, kind, box, kwargs=None):
    """Log a drawn region on the recording test canvas; real Tk canvases have no ``_record``."""

    if hasattr(c, "_record"):
        c._record(kind, box, kwargs or {})


def _inside(x, y, bbox):
    """Return whether a point is inside a canvas bounding box."""

    x0, y0, x1, y1 = bbox
    return x0 <= x <= x1 and y0 <= y <= y1


def _clip(value, width):
    """Shorten text to a single normalized line for canvas rendering."""

    return shorten(" ".join(str(value or "").split()), width=width, placeholder="...")


def _status_color(status):
    """Map docket or feature status to a palette color."""

    value = (status or "").lower()
    if value in ("open", "carried", "week", "filed"):
        return Palette.ACCENT
    if value in ("inspected", "settled", "maintained", "scorecard"):
        return Palette.WATCH
    if value in ("active", "approved", "responded", "enforced"):
        return Palette.GOOD
    if value in ("denied", "deferred", "failed"):
        return Palette.BAD
    return Palette.MUTED


def _risk_color(exposure):
    """Map inspection exposure bands to a palette color."""

    value = (exposure or "").lower()
    if value == "high":
        return Palette.BAD
    if value == "medium":
        return Palette.WATCH
    if value == "low":
        return Palette.GOOD
    return Palette.MUTED


def _tone_color(tone):
    """Map ledger row tone names to palette colors."""

    if tone == "good":
        return Palette.GOOD
    if tone == "bad":
        return Palette.BAD
    if tone == "watch":
        return Palette.WATCH
    return Palette.INK


def _grade_color(grade):
    """Map an audit grade to the header's tone."""

    return {"PASS": Palette.GOOD, "CONDITIONAL": Palette.WATCH, "FAIL": Palette.BAD}.get(grade or "", Palette.INK)


def _geometry_tag(geometry_type):
    """Return the short inbox tag for a case's map geometry."""

    return {"POINT": "POINT", "LINE": "LINE", "POLYGON": "ZONE"}.get(str(geometry_type or "").upper(), str(geometry_type or "").upper())


def _filed_label(status):
    """Return the inbox label for a decided case."""

    return {"active": "ISSUED", "failed": "FAILED", "denied": "DENIED", "deferred": "DEFERRED"}.get(str(status or ""), str(status or "").upper())
