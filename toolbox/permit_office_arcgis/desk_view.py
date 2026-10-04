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
    MapLegendRow,
    ReceiptModel,
    ReportTab,
    build_desk_model,
    report_sections,
    _hazard_summary,
    _maintenance_summary,
    _service_gap_summary,
    _symbol_hex,
)
from .symbology_config import (
    DISPLAY_STATE_SYMBOLS,
    DISTRICT_TYPE_SYMBOLS,
    IDENTITY_STATE_SYMBOLS,
    PROSPERITY_BAND_SYMBOLS,
)


# Floors used while the window is still sizing; the desk re-flows for a portrait
# pane that fills half of a 1920x1080 monitor beside ArcGIS Pro.
MIN_DESK_W = 1120
MIN_DESK_H = 860
# How far the ticker masks reach past the strip ends: past the edge of any screen.
MASK_REACH = 100_000
# Legend glyphs are drawn in an 18 px square.
MAP_KEY_SYMBOL_H = 18
# Every panel opens with the same pane header (see _draw_pane_header).
PANE_HEADER_H = 32
# History rows are a fixed height so the rail can be sized to its row count.
HISTORY_ROW_H = 46
HISTORY_ROW_GAP = 7
# The decision brief never gets less than this: header and title row (86), card
# padding (32), info panel (186), stamp grid (190), and one gap. The district
# table below gives up height for it, down to TABLE_MIN_H (it scrolls).
DECISION_BRIEF_MIN_H = 508
TABLE_MIN_H = 140
# Stamp cards narrower than this stack label, cost, and impacts in one column.
STACKED_CARD_W = 230
# Control is 0x4 everywhere; Alt is 0x20000 on Windows (where 0x8 is NumLock) and Mod1 0x8 on X11.
KEY_MODIFIER_MASK = 0x4 | (0x20000 if sys.platform == "win32" else 0x8)
MAP_CHEAT_GROUPS = ("MapCheat",)
LEGEND_GROUP_FIELDS = {
    "PermitDistricts": "district_type",
    "District display": "display_state",
    "Prosperity": "prosperity_band",
    "Community": "identity_state",
    "PermitPoints": "display_state",
    "PermitLines": "display_state",
    "PermitZones": "display_state",
    "Selection": "display_state",
}


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
    """Canvas-based dashboard view for the overworked permit clerk desk."""

    def __init__(self, root, callbacks: DeskCallbacks, on_select_item: Callable[[str], None]):
        """Create the canvas and bind mouse events to view callbacks."""

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
        # Per-model lookup maps (label->row, action_id->lane), rebuilt only when
        # the model object changes (see _ensure_lookups).
        self._lookup_model = None
        self._ledger_by_label: dict = {}
        self._lane_by_action: dict = {}
        self._menu_open = False
        self._menu_anchor = None
        self._ticker_offset_px = 0
        # Lowercase hotkey -> callback for the actions drawn enabled this frame.
        self._hotkey_targets: dict[str, Callable[[], None]] = {}

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

    def render(self, model: DeskViewModel):
        """Store and draw the latest view model."""

        self.model = model
        # New frame content: bound the text-fit memo to this model's strings.
        # (Left warm across hover redraws and deadline ticks, which keep the model.)
        self._fit_cache.clear()
        width = max(self.canvas.winfo_width(), MIN_DESK_W)
        height = max(self.canvas.winfo_height(), MIN_DESK_H)
        self._draw(width, height)

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

    def selected_item_id(self) -> str:
        """Return the currently rendered selected item id."""

        return self.model.selected_item_id

    def update_deadline(self, text: str, meter: int, running: bool, status_text: str | None = None):
        """Update the live filing-deadline presentation without reloading ArcGIS rows."""

        if (
            self.model.deadline_text == text
            and self.model.deadline_meter == meter
            and self.model.deadline_running == running
            and (status_text is None or self.model.status_text == status_text)
        ):
            return
        next_status = self.model.status_text if status_text is None else status_text
        self.model = replace(
            self.model,
            status_text=next_status,
            deadline_text=text,
            deadline_meter=meter,
            deadline_running=running,
        )
        width = max(self.canvas.winfo_width(), MIN_DESK_W)
        height = max(self.canvas.winfo_height(), MIN_DESK_H)
        self._draw(width, height)

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
        """Run the enabled action whose hotkey badge or help-card key was pressed.

        Keys typed into another widget (the New Game seed entry) or held with
        Control/Alt are ignored, and the start/help card answers only ``?``.
        """

        if event.widget is not self.canvas and event.widget is not self.root:
            return None
        if int(getattr(event, "state", 0) or 0) & KEY_MODIFIER_MASK:
            return None
        key = str(getattr(event, "char", "") or "").lower()
        if self.model.show_start_help and key != "?":
            return None
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
        """Scroll the filed report body or the district table under the pointer."""

        if getattr(event, "num", 0) in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            steps = -1 if getattr(event, "delta", 0) > 0 else 1
        report_box = getattr(self, "_report_scroll_box", None)
        table_box = getattr(self, "_table_scroll_box", None)
        if report_box and _inside(event.x, event.y, report_box):
            self._scroll_report_by(steps * 3 * self._line_h(Type.BODY))
        elif table_box and _inside(event.x, event.y, table_box):
            self._scroll_table_by(steps * 3)
        else:
            return None
        self._redraw_current()
        return "break"

    def _scroll_report_by(self, dy):
        """Move the report body scroll offset; the next draw clamps it to the content."""

        self._report_scroll = max(0, int(getattr(self, "_report_scroll", 0)) + int(dy))

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

    def _draw(self, width, height):
        """Lay out the banner, status strip, tab workspace, and city pulse rail.

        The banner and status strip have fixed heights; the workspace and the
        pulse rail share the rest. With applications open, the district table
        spans under both, so the rail stops above it.
        """

        c = self.canvas
        c.delete("all")
        self._click_targets = []
        self._hotkey_targets = {}
        self._draw_background(c, width, height)

        margin = Space.L
        gap = Space.L

        banner_h = 64
        status_h = 28

        self._draw_top_banner(c, width, banner_h)
        status_y0 = banner_h + gap
        self._status_strip_box = (margin, status_y0, width - margin, status_y0 + status_h)
        self._draw_status_strip(c, self._status_strip_box)
        # Z-order mark: a ticker redraw goes back under it, below the panes, menu, and help card.
        c.create_line(0, 0, 0, 0, state="hidden", tags=("status-strip-top",))

        body_y0 = status_y0 + status_h + gap
        body_y1 = height - margin

        health_w = min(300, max(250, int((width - 2 * margin) * 0.25)))
        health_x1 = width - margin
        health_x0 = health_x1 - health_w
        workspace_x0 = margin
        workspace_x1 = health_x0 - gap

        external_table = bool(self.model.docket_rows) and self.model.selected_desk_tab in ("applications", "reports")
        if external_table:
            brief = self.model.selected_desk_tab == "applications"
            table_y0 = _workspace_table_top((workspace_x0, body_y0, workspace_x1, body_y1), brief)
            top_y1 = table_y0 - gap
            self._external_attribute_table = (top_y1, table_y0, body_y1, health_x1)
            self._draw_main_workspace(c, (workspace_x0, body_y0, workspace_x1, body_y1))
            self._draw_ledger_rail(c, (health_x0, body_y0, health_x1, top_y1))
            self._external_attribute_table = None
        else:
            self._draw_main_workspace(c, (workspace_x0, body_y0, workspace_x1, body_y1))
            self._draw_ledger_rail(c, (health_x0, body_y0, health_x1, body_y1))
        if self._menu_open:
            self._draw_session_menu(c, width)
        if self.model.show_start_help:
            self._draw_start_help_overlay(c, width, height)
        # The utility menu always offers these, so the help card's keys can too.
        if self.model.game_active:
            self._hotkey_targets["w"] = self.callbacks.advance_turn
            self._hotkey_targets["s"] = self.callbacks.scorecard
        self._hotkey_targets["?"] = self.callbacks.show_help

    def _draw_background(self, c, width, height):
        """Paint the desk surface behind every pane (the banner paints its own row on top)."""

        c.create_rectangle(0, 0, width, height, fill=Palette.FRAME, outline="")

    def _draw_top_banner(self, c, width, h):
        """Draw the headline metrics as a light Pro-style title row."""

        c.create_rectangle(0, 0, width, h, fill=Palette.HEADER, outline="")
        c.create_line(0, h - 1, width, h - 1, fill=Palette.DIVIDER)
        c.create_text(Space.XL, h // 2, text="PERMIT OFFICE", anchor="w", fill=Palette.INK, font=self._font(Type.DISPLAY, "bold"))
        self._ensure_lookups()
        metrics = self._ledger_by_label
        headlines = HEADLINE_METRICS
        x_start = 310
        right_pad = 292 if self.model.deadline_text else 84
        spacing = max(70, (width - x_start - right_pad) // len(headlines))
        x = x_start
        for key, display in headlines:
            row = metrics.get(key)
            if row is None:
                continue
            c.create_text(x, h // 2 - 10, text=display, anchor="w", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            value = self._fit_px(row.value, Type.TITLE, "bold", spacing - Space.M)
            c.create_text(x, h // 2 + 9, text=value, anchor="w", fill=_tone_color(row.tone), font=self._font(Type.TITLE, "bold"))
            x += spacing
        if self.model.deadline_text:
            self._draw_deadline_clock(c, width, h)
        self._draw_menu_button(c, width - 56, 16, width - Space.L, h - 16)

    def _draw_deadline_clock(self, c, width, h):
        """Draw the live office clock in the top banner."""

        x1 = width - 72
        x0 = x1 - 170
        y0 = Space.S
        y1 = h - Space.S
        meter = max(0, min(100, int(self.model.deadline_meter or 0)))
        fill = Palette.WATCH if meter >= 75 else Palette.ACCENT if self.model.deadline_running else Palette.MUTED
        day, time, held = _deadline_day_time(self.model.deadline_text)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SUBTLE, outline=Palette.BORDER, width=1)
        c.create_text(x0 + Space.M, y0 + 6, text="DAY", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
        c.create_text(x0 + Space.M, y0 + 22, text=self._fit_px(day, Type.BODY, "bold", 60), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
        # A probe hold (PERMIT_OFFICE_HOLD_DAY) swaps the TIME caption for HELD.
        caption, caption_fill = ("HELD", Palette.WATCH) if held else ("TIME", Palette.MUTED)
        c.create_text(x0 + 80, y0 + 6, text=caption, anchor="nw", fill=caption_fill, font=self._font(Type.SMALL, "bold"))
        c.create_text(x0 + 80, y0 + 22, text=self._fit_px(time, Type.BODY, "bold", 80), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
        # Thin progress bar pinned to the inner bottom edge, clear of the text.
        c.create_rectangle(x0 + 1, y1 - 4, x0 + 1 + int((x1 - x0 - 2) * meter / 100), y1 - 1, fill=fill, outline="")

    def _draw_status_strip(self, c, box):
        """Draw the one-line ambient status (office-day note, selection, errors).

        Canvas text cannot be clipped, so the scrolling marquee is masked on both
        sides after it is drawn: desk-colored blocks outside the strip and a
        strip-colored block under the STATUS label.
        """

        x0, y0, x1, y1 = box
        tags = ("status-strip",)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PANE, outline=Palette.DIVIDER, tags=tags)
        mid = (y0 + y1) // 2
        label_x = x0 + 12
        label_w = self._text_w("STATUS", Type.SMALL, "bold")
        text_x0 = x0 + max(78, (label_w or 0) + 28)
        if self.model.status_text:
            text = self._fit_px(self.model.status_text, Type.BODY, "normal", x1 - text_x0 - 12)
            c.create_text(text_x0, mid, text=text, anchor="w", fill=Palette.INK, font=self._font(Type.BODY), tags=tags)
        else:
            ticker = "     /     ".join(self.model.ticker_items or ("Ready.",))
            marquee = f"{ticker}     /     {ticker}"
            window_w = max(1, x1 - text_x0)
            text_w = self._text_w(marquee, Type.BODY, "normal")
            if text_w is None:  # headless (no Tk fonts): scroll one window width per loop
                text_w = window_w
            start_x = x1 - (self._ticker_offset_px % max(1, text_w + window_w))
            c.create_text(start_x, mid, text=marquee, anchor="w", fill=Palette.INK, font=self._font(Type.BODY), tags=tags + ("status-marquee",))
            # Mask both ends: nothing scrolls past the strip into the desk margin.
            c.create_rectangle(x0 - MASK_REACH, y0, x0, y1, fill=Palette.FRAME, outline="", tags=tags)
            c.create_rectangle(x1, y0, x1 + MASK_REACH, y1, fill=Palette.FRAME, outline="", tags=tags)
        c.create_rectangle(x0, y0, text_x0 - 6, y1, fill=Palette.PANE, outline=Palette.DIVIDER, tags=tags)
        c.create_line(text_x0 - 6, y0 + 6, text_x0 - 6, y1 - 6, fill=Palette.DIVIDER, tags=tags)
        c.create_text(label_x, mid, text="STATUS", anchor="w", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"), tags=tags)

    def _draw_main_workspace(self, c, box):
        """Draw the primary Applications/Filed Reports tab workspace."""

        x0, y0, x1, y1 = box
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SUBTLE, outline=Palette.BORDER, width=1)
        content = (x0 + 12, y0 + 12, x1 - 12, y1 - 12)
        self._draw_application_tab_content(c, content)

    def _draw_primary_tab(self, c, x0, y0, x1, y1, label, selected, callback):
        """Draw a Pro-style tab: plain label, accent underline when selected."""

        hover = self._hover_key == f"desk-tab:{label}"
        if selected or hover:
            c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT if selected else Palette.PANE, outline="")
        c.create_line(x0, y1 - 1, x1, y1 - 1, fill=Palette.ACCENT if selected else Palette.DIVIDER, width=3 if selected else 1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label, anchor="center", fill=Palette.INK if selected else Palette.MUTED, font=self._font(Type.BODY, "bold" if selected else "normal"))
        self._add_target("desk-tab", label, (x0, y0, x1, y1), callback)

    def _draw_application_tab_content(self, c, box):
        """Draw the folder rail, the selected tab's detail card, and the district table."""

        x0, y0, x1, y1 = box
        active_id = self.model.selected_item_id
        rows = list(self.model.docket_rows)
        total_open = len(rows)

        if not rows and self.model.selected_desk_tab != "reports":
            self._draw_queue_cleared_state(c, box)
            return

        gap = 12
        panel_y0 = y0 + 8
        panel_y1 = y1
        rail_w = min(252, max(230, int((x1 - x0) * 0.25)))
        rail_box = (x0, panel_y0, x0 + rail_w, panel_y1)
        detail_box = (rail_box[2] + gap, panel_y0, x1, panel_y1)

        self._draw_folder_rail(c, rail_box, rows, active_id)
        external_table = getattr(self, "_external_attribute_table", None)
        work_y0 = detail_box[1] + 26
        if external_table:
            _top_y1, table_y0, table_y1, table_x1 = external_table
            work_box = (detail_box[0], work_y0, detail_box[2], min(detail_box[3], table_y0 - 14))
            table_box = (detail_box[0], table_y0, table_x1, table_y1)
        else:
            panel_h = panel_y1 - panel_y0
            work_h = min(max(390, int(panel_h * 0.52)), 520)
            table_min_h = min(190, max(138, int(panel_h * 0.18)))
            work_h = min(work_h, max(300, panel_h - table_min_h - gap))
            brief = self.model.selected_desk_tab != "reports"
            table_y0 = _table_top(rail_box, brief)
            work_y1 = min(detail_box[1] + work_h, table_y0 - 44)
            if brief:
                work_y1 = max(work_y1, min(work_y0 + DECISION_BRIEF_MIN_H, table_y0 - 14))
            work_box = (detail_box[0], work_y0, detail_box[2], work_y1)
            table_box = (detail_box[0], table_y0, detail_box[2], panel_y1)
        if self.model.selected_desk_tab == "reports":
            self._draw_report_detail_card(c, work_box)
        else:
            self._draw_active_card(c, work_box)
        self._draw_district_attribute_table(c, table_box)

    def _draw_folder_rail(self, c, box, rows, active_id):
        """Draw folder tabs, inbox/history list, and map key in one left rail."""

        x0, y0, x1, y1 = box
        tab_h = 38
        tab_gap = 8
        tab_w = (x1 - x0 - tab_gap) // 2
        apps_selected = self.model.selected_desk_tab == "applications"
        reports_selected = self.model.selected_desk_tab == "reports"
        self._draw_primary_tab(c, x0, y0, x0 + tab_w, y0 + tab_h, "Applications", apps_selected, lambda: self.callbacks.select_desk_tab("applications"))
        self._draw_primary_tab(c, x0 + tab_w + tab_gap, y0, x1, y0 + tab_h, "Filed Reports", reports_selected, lambda: self.callbacks.select_desk_tab("reports"))

        list_y0 = y0 + tab_h + 10
        key_y0 = _folder_key_top(box)
        if reports_selected:
            # History is sized to its rows; the map key takes the rest of the rail,
            # but never less than the inbox split gives it.
            count = len(self.model.report_tabs or ())
            rows_h = count * (HISTORY_ROW_H + HISTORY_ROW_GAP) if count else self._line_h(Type.SMALL) + Space.S
            key_y0 = min(key_y0, list_y0 + PANE_HEADER_H + Space.S + rows_h + Space.S + 10)
        list_box = (x0, list_y0, x1, key_y0 - 10)
        key_box = (x0, key_y0, x1, y1)
        if reports_selected:
            self._draw_history_rail(c, list_box)
        else:
            self._draw_inbox_rail(c, list_box, rows, active_id)
        self._draw_map_key_rail(c, key_box, groups=MAP_CHEAT_GROUPS)

    def _draw_pane_header(self, c, x0, y0, x1, title, context=""):
        """Draw a Pro-style pane header (title left, context right, divider) and return the content top."""

        y1 = y0 + PANE_HEADER_H
        _record_box(c, "pane-header", (x0, y0, x1, y1), {"title": title})
        mid = y0 + PANE_HEADER_H // 2
        title_w = self._text_w(title, Type.TITLE) or 0
        c.create_text(x0 + Space.M, mid, text=title, anchor="w", fill=Palette.INK, font=self._font(Type.TITLE))
        if context:
            context_w = max(24, x1 - x0 - 2 * Space.M - title_w - Space.M)
            c.create_text(x1 - Space.M, mid, text=self._fit_px(context, Type.SMALL, "normal", context_w), anchor="e", fill=Palette.MUTED, font=self._font(Type.SMALL))
        c.create_line(x0 + 1, y1, x1 - 1, y1, fill=Palette.DIVIDER)
        return y1

    def _draw_inbox_rail(self, c, box, rows, active_id):
        """Draw the compact left docket rail."""

        x0, y0, x1, y1 = box
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER)
        y = self._draw_pane_header(c, x0, y0, x1, "Inbox", f"{len(rows)} open") + Space.S
        rail_h = y1 - y0
        base_row_h = 60
        max_row_h = 150 if rail_h > 520 else 94
        row_gap = 7
        visible = max(1, (y1 - y - 18) // (base_row_h + row_gap))
        visible_rows = self._visible_inbox_rows(rows, active_id, visible)
        if visible_rows:
            fill_h = max(base_row_h, (y1 - y - 18 - row_gap * (len(visible_rows) - 1)) // len(visible_rows))
            row_h = min(max_row_h, max(base_row_h, fill_h))
        else:
            row_h = base_row_h
        for row in visible_rows:
            self._draw_inbox_row(c, x0 + 8, y, x1 - 8, y + row_h, row, row.item_id == active_id)
            y += row_h + row_gap
        overflow = len(rows) - len(visible_rows)
        if overflow > 0:
            c.create_text(x0 + 12, y1 - 20, text=f"+{overflow} queued", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))

    def _visible_inbox_rows(self, rows, active_id, visible):
        """Return inbox rows with the active item pinned into the visible set."""

        count = max(1, int(visible or 1))
        active = next((row for row in rows if row.item_id == active_id), None)
        if active is None:
            return list(rows[:count])
        first_slice = list(rows[:count])
        if active in first_slice:
            return first_slice
        return [active] + [row for row in rows if row.item_id != active_id][: count - 1]

    def _draw_inbox_row(self, c, x0, y0, x1, y1, row, selected):
        """Draw one selectable inbox row."""

        hover = self._hover_key == f"docket:{row.item_id}"
        fill = Palette.SELECT if selected else Palette.CONTENT if hover else Palette.SUBTLE
        outline = Palette.ACCENT if selected else Palette.BORDER
        state_color = _status_color(row.status)
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=1)
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=state_color, outline="")
        state = "ACTIVE" if selected else row.status.upper()
        c.create_text(x0 + 12, y0 + 8, text=state, anchor="nw", fill=state_color, font=self._font(Type.SMALL, "bold"))
        title_lines = self._fit_lines_px(row.title, Type.SMALL, "bold", x1 - x0 - 22, 2)
        c.create_text(x0 + 12, y0 + 23, text="\n".join(title_lines), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL, "bold"), width=x1 - x0 - 22)
        self._add_target("docket", row.item_id, (x0, y0, x1, y1), lambda item_id=row.item_id: self.on_select_item(item_id))

    def _draw_history_rail(self, c, box):
        """Draw filed decisions and week reports as a selectable history inbox."""

        x0, y0, x1, y1 = box
        tabs = list(self.model.report_tabs or ())
        _record_box(c, "history-rail", (x0, y0, x1, y1))
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER)
        top = self._draw_pane_header(c, x0, y0, x1, "History", f"{len(tabs)} filed")
        if not tabs:
            c.create_text(x0 + Space.M, top + Space.S, text="No filed reports yet.", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
            return
        y = top + Space.S
        row_h = HISTORY_ROW_H
        for tab in tabs[-max(1, (y1 - y) // (row_h + HISTORY_ROW_GAP)):]:
            selected = tab.report_id == self.model.selected_report_id
            hover = self._hover_key == f"report:{tab.report_id}"
            fill = Palette.SELECT if selected else Palette.CONTENT if hover else Palette.SUBTLE
            outline = _status_color(tab.status)
            c.create_rectangle(x0 + 8, y, x1 - 8, y + row_h, fill=fill, outline=Palette.ACCENT if selected else Palette.BORDER, width=1)
            c.create_rectangle(x0 + 8, y, x0 + 13, y + row_h, fill=outline, outline="")
            c.create_text(x0 + 20, y + 7, text=tab.kind.upper(), anchor="nw", fill=outline, font=self._font(Type.SMALL, "bold"))
            c.create_text(x0 + 20, y + 23, text=self._fit_px(tab.title, Type.BODY, "bold", x1 - x0 - 36), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
            self._add_target("report", tab.report_id, (x0 + 8, y, x1 - 8, y + row_h), lambda report_id=tab.report_id: self.callbacks.select_report(report_id))
            y += row_h + HISTORY_ROW_GAP

    def _draw_map_key_rail(self, c, box, groups=None, title="Map key"):
        """Draw a map key pane: the cheat sheet, the case layers, or legend rows for ``groups``."""

        x0, y0, x1, y1 = box
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PANE, outline=Palette.BORDER)
        _record_box(c, "map-key", (x0, y0, x1, y1))
        cheat_rows = set(groups or ()) == set(MAP_CHEAT_GROUPS)
        case_rows = set(groups or ()) == {"Selection"} and bool(self.model.case_map_symbols)
        context = "Cheat sheet" if cheat_rows else "Case layers" if case_rows else ""
        top = self._draw_pane_header(c, x0, y0, x1, title, context)
        if cheat_rows:
            self._draw_map_cheat_sheet(c, x0 + 12, top + Space.M, x1 - 12, y1 - 10)
            return
        y = top + Space.M
        bottom = y1 - 10
        last_group = ""
        group_filter = set(groups or ())
        if case_rows:
            rows = list(self.model.case_map_symbols)
        else:
            rows = [row for row in self.model.map_legend_rows if not group_filter or row.group in group_filter]
        if groups:
            order = {group: index for index, group in enumerate(groups)}
            rows.sort(key=lambda row: order.get(getattr(row, "group", "Selection"), len(order)))
        row_h = self._map_key_row_h(detail=True)
        for row in rows:
            group = getattr(row, "group", "Selection")
            if not case_rows and group != last_group:
                field = LEGEND_GROUP_FIELDS.get(group)
                heading_h = self._line_h(Type.SMALL, "bold") + (self._line_h(Type.SMALL, "bold") if field else 0) + 2
                if y + heading_h + row_h > bottom:
                    break
                c.create_text(x0 + 12, y, text=group, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
                if field:
                    c.create_text(x0 + 12, y + self._line_h(Type.SMALL, "bold"), text=field, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
                y += heading_h
                last_group = group
            if y + row_h > bottom:
                break
            self._draw_map_key_row(c, x0 + 12, y, x1 - 12, row)
            y += row_h

    def _draw_map_cheat_sheet(self, c, x0, y0, x1, y1):
        """Draw one player-facing map symbology cheat sheet.

        Row heights come from measured line heights. When everything does not
        fit, sublabels go first, then rows from the end of the longest section,
        so rows never overlap or cross the bottom edge.
        """

        rows = list(self.model.map_legend_rows)
        selected = [row for row in rows if row.group == "Selection" and row.label == "Selected target"][:1]
        features = (
            selected
            + _legend_subset(rows, "PermitPoints", ("Proposed",))
            + _legend_subset(rows, "PermitLines", ("Road",))
            + _legend_subset(rows, "PermitZones", ("Active",))
            + _legend_subset(rows, "PermitPoints", ("Maintained", "Special Interest"))
        )
        fills = _legend_subset(rows, "PermitDistricts", ("Residential", "Mercantile", "Industrial", "Civic", "Academic", "Natural"))
        overlays = (
            _legend_subset(rows, "District display", ("Service Gap", "Civic Incident", "Local Grievance"))
            + _legend_subset(rows, "Prosperity", ("Stable",))
            + _legend_subset(rows, "Community", ("Stable Identity", "Contested Buyout"))
        )
        sections = [
            ["FEATURES", [_friendly_legend_row(row) for row in features]],
            ["DISTRICT FILLS", [_friendly_legend_row(row) for row in fills]],
            ["OVERLAYS", [_friendly_legend_row(row) for row in overlays]],
        ]
        heading_h = self._line_h(Type.SMALL, "bold") + 2
        section_gap = 6

        def height(detail):
            row_h = self._map_key_row_h(detail)
            return sum(heading_h + row_h * len(section_rows) for _title, section_rows in sections) + section_gap * (len(sections) - 1)

        available = y1 - y0
        detail = height(True) <= available
        while not detail and height(False) > available:
            # Ties trim the later section first, so FEATURES keeps the most rows.
            longest = max(reversed(sections), key=lambda section: len(section[1]))
            if len(longest[1]) <= 1:
                break
            longest[1].pop()
        row_h = self._map_key_row_h(detail)
        y = y0
        for title, section_rows in sections:
            if y + heading_h + row_h > y1:
                break
            c.create_text(x0, y, text=title, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            y += heading_h
            for row in section_rows:
                if y + row_h > y1:
                    break
                self._draw_map_key_row(c, x0, y, x1, row, detail=detail)
                y += row_h
            y += section_gap

    def _map_key_row_h(self, detail=True):
        """Return one map key row's height: symbol or label (+ sublabel), plus a gap."""

        text_h = self._line_h(Type.SMALL, "bold") + (self._line_h(Type.SMALL) if detail else 0)
        return max(MAP_KEY_SYMBOL_H, text_h) + (4 if detail else 3)

    def _draw_map_key_row(self, c, x0, y0, x1, row, detail=True):
        """Draw one bounded legend row with swatch, label, and optional detail."""

        label_h = self._line_h(Type.SMALL, "bold")
        text_h = label_h + (self._line_h(Type.SMALL) if detail else 0)
        symbol_y = y0 + max(0, (text_h - MAP_KEY_SYMBOL_H) // 2)
        if hasattr(row, "group"):
            self._draw_legend_symbol(c, x0, symbol_y, row)
        else:
            self._draw_case_symbol(c, x0, symbol_y, row)
        text_y = y0 + max(0, (MAP_KEY_SYMBOL_H - text_h) // 2)
        state = getattr(row, "state", "")
        label_w = x1 - x0 - (52 if state else 24)
        c.create_text(x0 + 24, text_y, text=self._fit_px(row.label, Type.SMALL, "bold", label_w), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL, "bold"))
        if detail:
            c.create_text(x0 + 24, text_y + label_h, text=self._fit_px(row.detail, Type.SMALL, "normal", label_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
        if state:
            c.create_text(x1, text_y, text=self._fit_px(state, Type.SMALL, "bold", 44), anchor="ne", fill=_tone_color(getattr(row, "tone", "neutral")), font=self._font(Type.SMALL, "bold"))

    def _draw_case_symbol(self, c, x0, y0, row):
        """Draw a map symbol matching the row geometry when available."""

        shape = getattr(row, "shape", "")
        swatch = getattr(row, "swatch", Palette.BORDER) or Palette.BORDER
        if shape == "line":
            _record_box(c, "symbol-line", (x0, y0 + 9, x0 + 18, y0 + 9))
            c.create_line(x0, y0 + 9, x0 + 18, y0 + 9, fill=swatch, width=4, capstyle="round")
            return
        if shape == "point":
            _record_box(c, "symbol-point", (x0 + 2, y0 + 3, x0 + 14, y0 + 15))
            c.create_oval(x0 + 2, y0 + 3, x0 + 14, y0 + 15, fill=swatch, outline=Palette.BORDER)
            return
        if shape == "zone":
            _record_box(c, "symbol-zone", (x0 + 1, y0 + 2, x0 + 17, y0 + 16))
            c.create_rectangle(x0 + 1, y0 + 2, x0 + 17, y0 + 16, fill=swatch, outline=Palette.BORDER)
            return
        self._draw_legend_symbol(c, x0, y0, row)

    def _draw_legend_symbol(self, c, x0, y0, row):
        """Draw a Contents-style symbol matched to the represented layer."""

        group = row.group
        label = row.label.lower()
        swatch = row.swatch
        if group == "PermitPoints":
            if "special interest" in label:
                c.create_oval(x0 - 1, y0, x0 + 17, y0 + 18, fill=Palette.SYMBOL_RING, outline="")
                c.create_oval(x0 + 4, y0 + 5, x0 + 12, y0 + 13, fill=_symbol_hex(DISPLAY_STATE_SYMBOLS["special_interest"]), outline="")
            elif "proposed" in label:
                c.create_oval(x0 - 1, y0, x0 + 17, y0 + 18, fill=_symbol_hex(DISPLAY_STATE_SYMBOLS["proposed"]), outline="")
            elif "expired" in label:
                c.create_oval(x0 + 5, y0 + 6, x0 + 11, y0 + 12, fill=Palette.CONTENT, outline=Palette.INK, width=1)
            else:
                c.create_oval(x0 + 2, y0 + 3, x0 + 14, y0 + 15, fill=swatch, outline=Palette.BORDER)
            return
        if group == "PermitLines":
            c.create_line(x0, y0 + 9, x0 + 18, y0 + 9, fill=swatch if "road" not in label else _symbol_hex(DISPLAY_STATE_SYMBOLS["road"]), width=4, capstyle="round")
            return
        if group in ("Prosperity", "Community", "Selection"):
            c.create_rectangle(x0 + 1, y0 + 2, x0 + 17, y0 + 16, fill=Palette.CONTENT if group == "Prosperity" else swatch, outline=swatch, width=3 if group != "Selection" else 2)
            return
        c.create_rectangle(x0 + 1, y0 + 2, x0 + 17, y0 + 16, fill=swatch, outline=Palette.SYMBOL_OUTLINE, width=2)

    def _draw_queue_cleared_state(self, c, box):
        """Draw the empty-docket panel: a start prompt before a game exists, or
        the queue-cleared / End Week controls once one is running."""

        x0, y0, x1, y1 = box
        card_y0 = y0 + 34
        c.create_rectangle(x0, card_y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER, width=1)
        if not self.model.game_active:
            c.create_text(x0 + 24, card_y0 + 26, text="No game yet", anchor="nw", fill=Palette.INK, font=self._font(Type.DISPLAY, "bold"))
            start_body = (
                "This workspace has no active Permit Office board. Click New Game "
                "to generate a city and start the 12-week season."
            )
            c.create_text(x0 + 24, card_y0 + 66, text=start_body, anchor="nw", fill=Palette.MUTED, font=self._font(Type.TITLE), width=x1 - x0 - 48)
            by0 = y1 - 64
            self._draw_case_action(c, x0 + 24, by0, x0 + 164, by0 + 40, "New Game", Palette.ACCENT, self.callbacks.new_game, primary=True)
            return
        c.create_text(x0 + 24, card_y0 + 26, text="Queue cleared", anchor="nw", fill=Palette.INK, font=self._font(Type.DISPLAY, "bold"))
        body = "All applications have been filed. End Week to process follow-ups."
        if self.model.auto_close_active:
            body = f"{body} Automatic close in {self.model.auto_close_seconds} seconds."
        c.create_text(x0 + 24, card_y0 + 66, text=body, anchor="nw", fill=Palette.MUTED, font=self._font(Type.TITLE), width=x1 - x0 - 48)
        by0 = y1 - 64
        self._draw_case_action(c, x0 + 24, by0, x0 + 164, by0 + 40, "End Week", Palette.INK, self.callbacks.advance_turn, primary=True)
        if self.model.auto_close_active:
            self._draw_case_action(c, x0 + 176, by0, x0 + 344, by0 + 40, "Cancel Auto Close", Palette.MUTED, self.callbacks.cancel_queue_autoclose, primary=False)

    def _draw_report_detail_card(self, c, box):
        """Draw the selected filed report: title, summary, headed sections, metrics.

        Sections wrap by measured width. When they are taller than the body,
        the body scrolls with the mouse wheel; rows are drawn whole or not at
        all, so nothing is cut off or overlaps the metric strip.
        """

        x0, y0, x1, y1 = box
        _record_box(c, "report-detail", (x0, y0, x1, y1))
        tabs = list(self.model.report_tabs or ())
        selected_report = next((tab for tab in tabs if tab.report_id == self.model.selected_report_id), tabs[-1] if tabs else None)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER)
        top = self._draw_pane_header(c, x0, y0, x1, "Report detail")
        c.create_line(x0, y0 + 78, x1, y0 + 78, fill=Palette.DIVIDER)
        pad = Space.XL
        bx0 = x0 + pad
        bx1 = x1 - pad
        self._report_scroll_box = None
        if selected_report is None:
            c.create_text(bx0, top + 10, text="No filed report yet", anchor="nw", fill=Palette.INK, font=self._font(Type.DISPLAY, "bold"))
            c.create_text(bx0, y0 + 94, text="Decisions, inspections, scorecards, and week close reports will appear here.", anchor="nw", fill=Palette.MUTED, font=self._font(Type.BODY), width=bx1 - bx0)
            return
        c.create_text(bx0, top + 10, text=self._fit_px(selected_report.title, Type.DISPLAY, "bold", bx1 - bx0 - 116), anchor="nw", fill=Palette.INK, font=self._font(Type.DISPLAY, "bold"))
        self._draw_status_badge(c, bx1 - 104, top + 8, bx1, top + 34, selected_report.status.upper(), _status_color(selected_report.status))

        metrics_y = y1 - 34
        body_top = y0 + 78 + Space.L
        body_bottom = (metrics_y - Space.M) if selected_report.metrics else (y1 - Space.M)
        if getattr(self, "_report_scroll_id", None) != selected_report.report_id:
            self._report_scroll_id = selected_report.report_id
            self._report_scroll = 0
        scrollbar_w = Space.S
        rows = self._report_rows(selected_report, bx1 - bx0 - scrollbar_w - Space.S)
        tops = []
        content_h = 0
        for _text, _size, _weight, _fill, _indent, height in rows:
            tops.append(content_h)
            content_h += height
        view_h = max(1, body_bottom - body_top)
        max_scroll = max(0, content_h - view_h)
        # Snap to a row top so the first visible row is whole.
        wanted = min(max(0, int(getattr(self, "_report_scroll", 0))), max_scroll)
        offset = max((top for top in tops if top <= wanted), default=0)
        if max_scroll and offset < wanted:
            offset = min((top for top in tops if top >= wanted), default=offset)
        self._report_scroll = offset
        self._report_scroll_box = (x0, body_top, x1, body_bottom)
        visible = [
            body_top <= body_top + top - offset and body_top + top - offset + self._line_h(size, weight) <= body_bottom
            for top, (_text, size, weight, _fill, _indent, _height) in zip(tops, rows)
        ]
        for index, (top, (text, size, weight, fill, indent, _height)) in enumerate(zip(tops, rows)):
            # A heading shows only with its first line (bullet + text rows follow it).
            is_heading = fill == Palette.ACCENT
            if not visible[index] or (is_heading and not all(visible[index + 1 : index + 3])):
                continue
            if text:
                c.create_text(bx0 + indent, body_top + top - offset, text=text, anchor="nw", fill=fill, font=self._font(size, weight))
        if max_scroll:
            track_x0 = bx1 - scrollbar_w + 2
            c.create_rectangle(track_x0, body_top, bx1, body_bottom, fill=Palette.SUBTLE, outline="", tags=("report-scrollbar",))
            thumb_h = max(Space.XL, int(view_h * view_h / content_h))
            thumb_y0 = body_top + int((view_h - thumb_h) * offset / max_scroll)
            c.create_rectangle(track_x0, thumb_y0, bx1, thumb_y0 + thumb_h, fill=Palette.BORDER, outline="", tags=("report-scrollbar",))
        if selected_report.metrics:
            self._draw_receipt_metrics(c, bx0, metrics_y, bx1, selected_report.metrics)

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
        bullet = "\u2022"
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

    def _wrap_px(self, text, size, weight, max_px):
        """Greedy word wrap by measured pixel width; never truncates."""

        words = " ".join(str(text or "").split()).split(" ")
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

    def _draw_active_card(self, c, box):
        """Draw the selected application as a modern decision brief."""

        x0, y0, x1, y1 = box
        case = self.model.case
        _record_box(c, "active-card", (x0, y0, x1, y1))
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER, width=1)
        top = self._draw_pane_header(c, x0, y0, x1, "Decision brief")
        header_h = top - y0 + 54
        c.create_line(x0, y0 + header_h, x1, y0 + header_h, fill=Palette.BORDER)

        pad = 22
        body_x0 = x0 + pad
        body_x1 = x1 - pad
        risk_known = (case.risk_band or "unknown").lower() not in ("", "unknown")
        risk_text = "RISK: " + case.risk_band.upper() if risk_known else "RISK: PENDING"
        title_x1 = body_x1 - 150
        c.create_rectangle(body_x0, top + 11, title_x1, top + 43, fill=Palette.SUBTLE, outline=Palette.BORDER, tags=("application-title-frame",))
        c.create_text(body_x0 + 10, top + 18, text=self._fit_px(case.title, Type.TITLE, "bold", title_x1 - body_x0 - 20), anchor="nw", fill=Palette.INK, font=self._font(Type.TITLE, "bold"))
        self._draw_status_badge(c, body_x1 - 132, top + 14, body_x1, top + 40, risk_text, _risk_color(case.risk_band))
        content_y0 = y0 + header_h + 16
        content_y1 = y1 - 16
        available = max(260, content_y1 - content_y0)
        gap = 14 if available < 430 else 26
        if available < 430:
            # Short brief: no evidence row (the map and district table show the same facts).
            evidence_h = 0
            action_h = 190
            info_h = available - gap - action_h
        else:
            action_h = min(max(178, int(available * 0.30)), 214)
            evidence_h = min(max(116, int(available * 0.21)), 150)
            info_h = available - (2 * gap) - action_h - evidence_h
            if info_h < 180:
                need = 180 - info_h
                take = min(need, max(0, evidence_h - 108))
                evidence_h -= take
                need -= take
                if need > 0:
                    action_h = max(166, action_h - need)
                info_h = available - (2 * gap) - action_h - evidence_h
            info_h = min(info_h, 270)
            overflow = (info_h + evidence_h + action_h + (2 * gap)) - available
            if overflow > 0:
                take = min(overflow, max(0, evidence_h - 108))
                evidence_h -= take
                overflow -= take
            if overflow > 0:
                take = min(overflow, max(0, info_h - 180))
                info_h -= take
                overflow -= take
            if overflow > 0:
                action_h = max(166, action_h - overflow)
        info_box = (body_x0, content_y0, body_x1, min(content_y1, content_y0 + info_h))
        action_y0 = info_box[3] + gap
        _record_box(c, "decision-info", (info_box[0], info_box[1], body_x1, min(content_y1, action_y0 + 8)))
        self._draw_application_info_panel(c, info_box)
        if evidence_h:
            evidence_box = (body_x0, action_y0, body_x1, min(content_y1, action_y0 + evidence_h))
            self._draw_case_evidence_row(c, evidence_box)
            action_y0 = evidence_box[3] + gap
        self._draw_case_controls(c, body_x0, action_y0, body_x1, min(content_y1, action_y0 + action_h))

    def _draw_application_info_panel(self, c, box):
        """Draw the applicant strip, description, inspection note, and budget line."""

        x0, y0, x1, y1 = box
        case = self.model.case
        _record_box(c, "app-info", (x0, y0, x1, y1))
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SUBTLE, outline=Palette.BORDER)
        pad = 14
        tx0 = x0 + pad
        tx1 = x1 - pad
        applicant = next((field.value for field in case.fields if field.label.lower() == "applicant"), "not assigned")
        contact = next((field.value for field in case.fields if field.label.lower() == "contact"), "")
        strip_y0 = y0 + pad
        strip_y1 = strip_y0 + 28
        c.create_rectangle(tx0, strip_y0, tx1, strip_y1, fill=Palette.CONTENT, outline=Palette.BORDER, tags=("applicant-strip",))
        c.create_text(tx0 + 10, strip_y0 + 8, text="APPLICANT", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
        contact_x = tx0 + max(260, int((tx1 - tx0) * 0.45))
        applicant_x = tx0 + max(88, 10 + (self._text_w("APPLICANT", Type.SMALL, "bold") or 0) + Space.S)
        c.create_text(applicant_x, strip_y0 + 7, text=self._fit_px(applicant, Type.BODY, "bold", contact_x - applicant_x - 12), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
        if contact:
            c.create_text(contact_x, strip_y0 + 8, text="CONTACT", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            value_x = contact_x + max(66, (self._text_w("CONTACT", Type.SMALL, "bold") or 0) + Space.S)
            c.create_text(value_x, strip_y0 + 7, text=self._fit_px(contact, Type.BODY, "bold", tx1 - value_x - 10), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
        econ_text = ""
        econ_color = Palette.MUTED
        econ_y = y1
        if case.economy:
            econ_qualifier = "filed" if bool(case.inspection) and not case.inspection.lower().startswith("no inspection") else "est."
            econ_text = f"Budget ({econ_qualifier}): {case.economy}" if case.economy != "no recurring budget" else "Budget: no recurring revenue or upkeep"
            econ_color = Palette.GOOD if "net +" in case.economy else Palette.BAD if "net -" in case.economy else Palette.MUTED
            econ_y = max(y0 + pad, y1 - 20)
            c.create_text(tx0, econ_y, text=self._fit_px(econ_text, Type.BODY, "bold", tx1 - tx0), anchor="nw", fill=econ_color, font=self._font(Type.BODY, "bold"))
        desc_y0 = strip_y1 + 10
        note_top_limit = (econ_y - 8) if econ_text else (y1 - 10)
        desc_h = max(44, min(96, note_top_limit - desc_y0 - 58))
        desc_y1 = min(note_top_limit - 12, desc_y0 + desc_h)
        if desc_y1 > desc_y0:
            c.create_rectangle(tx0, desc_y0, tx1, desc_y1, fill=Palette.CONTENT, outline=Palette.BORDER, tags=("description-block",))
            preview_rows = max(1, (desc_y1 - desc_y0 - 14) // self._line_h(Type.BODY))
            preview_lines = self._fit_lines_px(case.preview, Type.BODY, "normal", tx1 - tx0 - 24, preview_rows)
            c.create_text(tx0 + 12, desc_y0 + 8, text="\n".join(preview_lines), anchor="nw", fill=Palette.MUTED, font=self._font(Type.BODY), width=tx1 - tx0 - 24)
        yy = desc_y1 + 10
        inspected = bool(case.inspection) and not case.inspection.lower().startswith("no inspection")
        note = case.inspection if inspected else "Uninspected: decision impacts are estimates. Inspect File may reveal violations or stronger stakeholder reactions."
        note_color = Palette.BAD if inspected and case.risk_band == "high" else Palette.WATCH if not inspected else Palette.ACCENT
        note_h = min(52, (econ_y - 6 if econ_text else y1 - 10) - yy)
        if note_h >= 24:
            c.create_rectangle(tx0, yy, tx1, yy + note_h, fill=Palette.WARN_TINT if not inspected else Palette.SELECT, outline="")
            c.create_rectangle(tx0, yy, tx0 + 4, yy + note_h, fill=note_color, outline="")
            note_rows = max(1, min(2, (note_h - 8) // self._line_h(Type.BODY, "bold")))
            note_lines = self._fit_lines_px(note, Type.BODY, "bold", tx1 - tx0 - 24, note_rows)
            c.create_text(tx0 + 12, yy + 8, text="\n".join(note_lines), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))

    def _draw_case_evidence_row(self, c, box):
        """Draw compact evidence widgets for districts, cultures, and outcomes."""

        x0, y0, x1, y1 = box
        case = self.model.case
        _record_box(c, "evidence-row", (x0, y0, x1, y1))
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SUBTLE, outline=Palette.BORDER)
        pad = 10
        gap = 22
        col_w = max(100, (x1 - x0 - (2 * pad) - (2 * gap)) // 3)
        columns = (
            ("AFFECTED DISTRICTS", x0 + pad, x0 + pad + col_w),
            ("CULTURE PRESSURE", x0 + pad + col_w + gap, x0 + pad + 2 * col_w + gap),
            ("POSSIBLE OUTCOMES", x0 + pad + 2 * (col_w + gap), x1 - pad),
        )
        for title, cx0, cx1 in columns:
            c.create_text(cx0, y0 + 9, text=title, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
        content_top = y0 + 30
        content_bottom = y1 - 8
        self._draw_district_grid_widget(c, columns[0][1], content_top, columns[0][2], content_bottom, case.district_grid)
        self._draw_trend_card_stack(c, columns[1][1], content_top, columns[1][2], content_bottom, case.culture_cards, "culture-card")
        self._draw_trend_card_stack(c, columns[2][1], content_top, columns[2][2], content_bottom, case.outcome_cards, "outcome-card")

    def _draw_district_grid_widget(self, c, x0, y0, x1, y1, cells):
        """Draw the selected-case district mini-grid."""

        _record_box(c, "district-grid", (x0, y0, x1, y1))
        cells = tuple(cells or ())
        if not cells:
            c.create_text(x0, y0 + 4, text="No map targets", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            return
        cols = 4
        gap = 4
        cell_w = max(24, min(40, (x1 - x0 - gap * (cols - 1)) // cols))
        cell_h = max(20, min(30, (y1 - y0 - gap * 2) // 3))
        for index, cell in enumerate(cells[:12]):
            col = index % cols
            row = index // cols
            cx0 = x0 + col * (cell_w + gap)
            cy0 = y0 + row * (cell_h + gap)
            cx1 = min(x1, cx0 + cell_w)
            cy1 = min(y1, cy0 + cell_h)
            fill = cell.swatch or Palette.SELECT if cell.affected else Palette.CONTENT
            outline = Palette.INK if cell.affected else Palette.BORDER
            c.create_rectangle(cx0, cy0, cx1, cy1, fill=fill, outline=outline, width=2 if cell.affected else 1)
            c.create_text((cx0 + cx1) // 2, (cy0 + cy1) // 2, text=cell.label, anchor="center", fill=Palette.CONTENT if cell.affected and cell.swatch else Palette.INK, font=self._font(Type.SMALL, "bold"))

    def _draw_trend_card_stack(self, c, x0, y0, x1, y1, cards, record_kind):
        """Draw compact trend cards for culture or outcome evidence."""

        cards = tuple(cards or ())
        if not cards:
            c.create_text(x0, y0 + 4, text="Unknown", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            return
        gap = 5
        available_h = max(1, y1 - y0)
        visible = max(1, min(len(cards), 3, (available_h + gap) // 35))
        row_h = max(30, min(42, (available_h - gap * (visible - 1)) // max(1, visible)))
        for index, card in enumerate(cards[:visible]):
            yy = y0 + index * (row_h + gap)
            if yy >= y1:
                break
            _record_box(c, record_kind, (x0, yy, x1, min(y1, yy + row_h)))
            tone = _tone_color(card.tone)
            c.create_rectangle(x0, yy, x1, min(y1, yy + row_h), fill=Palette.CONTENT, outline=Palette.BORDER)
            c.create_rectangle(x0, yy, x0 + 4, min(y1, yy + row_h), fill=tone, outline="")
            if card.swatch:
                c.create_rectangle(x0 + 10, yy + 8, x0 + 20, yy + 18, fill=card.swatch, outline=Palette.BORDER)
                text_x = x0 + 26
            else:
                text_x = x0 + 10
            marker_color = _trend_color(card.trend, card.tone)
            c.create_text(text_x, yy + 5, text=self._fit_px(card.label, Type.SMALL, "bold", x1 - text_x - 28), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL, "bold"))
            c.create_text(x1 - 10, yy + 5, text=_trend_marker(card.trend), anchor="ne", fill=marker_color, font=self._font(Type.SMALL, "bold"))
            detail_y = yy + 21
            if detail_y < y1 - 8:
                c.create_text(text_x, detail_y, text=self._fit_px(card.detail, Type.SMALL, "normal", x1 - text_x - 10), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))

    def _draw_status_badge(self, c, x0, y0, x1, y1, text, color):
        """Draw a restrained status badge."""

        c.create_rectangle(x0, y0, x1, y1, fill=Palette.SUBTLE, outline=color, width=1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=self._fit_px(text, Type.SMALL, "bold", x1 - x0 - 10), anchor="center", fill=color, font=self._font(Type.SMALL, "bold"))

    def _draw_case_controls(self, c, x0, y0, x1, y1):
        """Draw selected-case map, inspect, and stamp controls inside the card."""

        _record_box(c, "action-grid", (x0, y0 - 8, x1, y1))
        c.create_rectangle(x0, y0 - 8, x1, y1, fill=Palette.SUBTLE, outline=Palette.BORDER)
        x0 += 10
        x1 -= 10
        y0 += 8
        y1 -= 10
        exhibit_label = "Hide Proposed Feature" if self.model.exhibit_visible else "Show Proposed Feature"
        self._ensure_lookups()
        lanes = self._lane_by_action
        approve = lanes.get("approve")
        mitigate = lanes.get("approve_mitigated")
        deny = lanes.get("deny")
        # Each control: (label, cost, detail, color, callback, primary, enabled, hotkey, tooltip, disabled_reason).
        controls = (
            (exhibit_label, "0 AP", "map layer visibility", Palette.ACCENT, self.callbacks.toggle_exhibit, False, True, "V", "Toggle proposed feature visibility.", ""),
            ("Retarget Map", "0 AP", "use selected map features", Palette.TEAL, self.callbacks.update_from_map, False, True, "T", "Use selected map features as targets.", ""),
            ("Inspect File", "1 AP", "reveal filed risk", Palette.WATCH, self.callbacks.inspect, False, True, "I", "Spend AP to reveal risk and outcomes.", ""),
            _stamp_control(
                approve, Palette.GOOD, self.callbacks.approve,
                ("Issue Permit", "1 AP", ("file permit", "update target"), "A", "Issue the permit and file the selected map change."),
            ),
            _stamp_control(
                mitigate, Palette.MITIGATE, self.callbacks.approve_mitigated,
                ("Add Conditions", "1 AP", ("add terms", "reduce risk"), "M", "Issue the permit with mitigation conditions."),
            ),
            _stamp_control(
                deny, Palette.BAD, self.callbacks.deny,
                ("Deny", "0 AP", ("reject filing", "unresolved pressure"), "D", "Deny the filing."),
            ),
        )
        gap = 8
        row_gap = 8
        inner_w = max(1, x1 - x0)
        raw_card_w = (inner_w - gap * 2) // 3
        card_w = max(88, min(316, raw_card_w))
        grid_w = card_w * 3 + gap * 2
        grid_x0 = x0 + max(0, (inner_w - grid_w) // 2)
        card_h = max(64, min(96, (y1 - y0 - row_gap) // 2))
        for index, control in enumerate(controls):
            cx = grid_x0 + (index % 3) * (card_w + gap)
            cy = y0 + (index // 3) * (card_h + row_gap)
            self._draw_case_action_card(c, cx, cy, min(x1, cx + card_w), min(y1, cy + card_h), *control)

    def _draw_case_action_card(self, c, x0, y0, x1, y1, label, cost, detail, color, callback, primary=False, enabled=True, hotkey="", tooltip="", disabled_reason=""):
        """Draw one larger in-card action with its visible AP/cost line."""

        hover = self._hover_key == f"case-action:{label}"
        fill = Palette.CONTENT if enabled else Palette.SUBTLE
        outline = Palette.INK if enabled and hover else color if enabled else Palette.BORDER
        tone = color if enabled else Palette.MUTED
        _record_box(c, "stamp-card", (x0, y0, x1, y1), {"label": label})
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2 if hover else 1)
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=tone, outline="")
        if enabled:
            self._add_target("case-action", label, (x0, y0, x1, y1), callback)
            if hotkey:
                self._hotkey_targets[hotkey.lower()] = callback
        if x1 - x0 < STACKED_CARD_W:
            self._draw_stacked_card_body(c, x0, y0, x1, y1, label, cost, detail, tone, enabled, hotkey, disabled_reason)
            return
        impact_w = max(118, min(160, int((x1 - x0) * 0.44)))
        impact_x0 = max(x0 + 104, x1 - impact_w)
        c.create_line(impact_x0, y0 + 7, impact_x0, y1 - 7, fill=Palette.BORDER, tags=("impact-divider",))
        c.create_rectangle(impact_x0 + 4, y0 + 10, x1 - 6, y1 - 10, fill=Palette.SUBTLE if enabled else Palette.CONTENT, outline=Palette.BORDER, tags=("impact-box",))
        label_w = impact_x0 - x0 - 24
        c.create_text(x0 + 12, y0 + 8, text=label, anchor="nw", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(Type.BODY, "bold"), width=label_w)
        cost_x = x0 + 12
        if hotkey:
            hk_x0 = x0 + 12
            c.create_rectangle(hk_x0, y0 + 30, hk_x0 + 20, y0 + 48, fill=Palette.SUBTLE, outline=Palette.BORDER, tags=("hotkey-badge",))
            c.create_text(hk_x0 + 10, y0 + 33, text=hotkey, anchor="n", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(Type.SMALL, "bold"), tags=("hotkey-badge",))
            cost_x = hk_x0 + 28
        c.create_text(cost_x, y0 + 31, text=cost, anchor="nw", fill=tone, font=self._font(Type.SMALL, "bold"), width=max(40, impact_x0 - cost_x - 12), tags=("cost-line",))
        impact_label_x = impact_x0 + 18
        impact_w_text = max(48, x1 - impact_label_x - 14)
        if isinstance(detail, tuple):
            city, local = detail
            city_tone = _impact_tone(city)
            local_tone = _impact_tone(local)
            _draw_impact_marker(c, impact_x0 + 14, y0 + 21, city_tone)
            c.create_text(impact_label_x, y0 + 16, text="City", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            c.create_text(impact_label_x, y0 + 29, text=self._fit_px(city, Type.SMALL, "normal", impact_w_text), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL), width=impact_w_text)
            local_y = y0 + 50 if y1 - y0 >= 78 else y0 + 46
            _draw_impact_marker(c, impact_x0 + 14, local_y + 5, local_tone)
            c.create_text(impact_label_x, local_y, text="Local", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            c.create_text(impact_label_x, local_y + 13, text=self._fit_px(local, Type.SMALL, "normal", impact_w_text), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL), width=impact_w_text)
            if not enabled and disabled_reason:
                c.create_text(x0 + 12, y1 - 17, text=self._fit_px(disabled_reason, Type.SMALL, "normal", label_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
        elif y1 - y0 >= 68:
            detail_lines = self._fit_lines_px(detail, Type.SMALL, "normal", impact_w_text, 3)
            _draw_impact_marker(c, impact_x0 + 14, y0 + 26, _impact_tone(detail))
            c.create_text(impact_label_x, y0 + 18, text="\n".join(detail_lines), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL), width=impact_w_text)
            if not enabled and disabled_reason:
                c.create_text(x0 + 12, y1 - 17, text=self._fit_px(disabled_reason, Type.SMALL, "normal", label_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
        elif tooltip:
            c.create_text(impact_label_x, y1 - 18, text=self._fit_px(tooltip, Type.SMALL, "normal", impact_w_text), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))

    def _draw_stacked_card_body(self, c, x0, y0, x1, y1, label, cost, detail, tone, enabled, hotkey, disabled_reason):
        """Stack a narrow stamp card: label, hotkey and cost, then impact lines while they fit."""

        tx0 = x0 + 12
        text_w = x1 - tx0 - 8
        small_h = self._line_h(Type.SMALL)
        y = y0 + 6
        c.create_text(tx0, y, text=self._fit_px(label, Type.BODY, "bold", text_w), anchor="nw", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(Type.BODY, "bold"))
        y += self._line_h(Type.BODY, "bold") + 3
        badge_h = small_h + 4
        cost_x = tx0
        if hotkey:
            c.create_rectangle(tx0, y, tx0 + 20, y + badge_h, fill=Palette.SUBTLE, outline=Palette.BORDER, tags=("hotkey-badge",))
            c.create_text(tx0 + 10, y + 2, text=hotkey, anchor="n", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(Type.SMALL, "bold"), tags=("hotkey-badge",))
            cost_x = tx0 + 28
        c.create_text(cost_x, y + 2, text=self._fit_px(cost, Type.SMALL, "bold", x1 - cost_x - 8), anchor="nw", fill=tone, font=self._font(Type.SMALL, "bold"), tags=("cost-line",))
        y += badge_h + 3
        if not enabled and disabled_reason:
            lines = [(None, line) for line in self._wrap_px(disabled_reason, Type.SMALL, "normal", text_w)]
        elif isinstance(detail, tuple):
            lines = [(_impact_tone(detail[0]), f"City: {detail[0]}"), (_impact_tone(detail[1]), f"Local: {detail[1]}")]
        else:
            lines = [(_impact_tone(detail), detail)]
        for marker, line in lines:
            if y + small_h > y1 - 3:
                break
            line_x = tx0
            if marker is not None:
                _draw_impact_marker(c, tx0 + 4, y + small_h // 2, marker)
                line_x = tx0 + 14
            c.create_text(line_x, y, text=self._fit_px(line, Type.SMALL, "normal", x1 - line_x - 8), anchor="nw", fill=Palette.MUTED if marker is None else Palette.INK, font=self._font(Type.SMALL))
            y += small_h

    def _draw_case_action(self, c, x0, y0, x1, y1, label, color, callback, primary=False, enabled=True):
        """Draw an in-card action button and register it."""

        hover = self._hover_key == f"case-action:{label}"
        fill = color if primary and enabled else Palette.SUBTLE
        text_color = Palette.CONTENT if primary and enabled else color if enabled else Palette.MUTED
        outline = color if enabled and not hover else Palette.INK if enabled else Palette.BORDER
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2 if hover else 1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label, anchor="center", fill=text_color, font=self._font(Type.SMALL, "bold"), width=x1 - x0 - 8)
        if enabled:
            self._add_target("case-action", label, (x0, y0, x1, y1), callback)

    def _draw_ledger_rail(self, c, box):
        """Draw the slim city pulse rail for triage context."""

        x0, y0, x1, y1 = box
        _record_box(c, "city-pulse", (x0, y0, x1, y1))
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PANE, outline=Palette.BORDER, width=1)
        top = self._draw_pane_header(c, x0, y0, x1, "City pulse", "Office context")

        inner_x0 = x0 + 14
        inner_x1 = x1 - 14
        self._ensure_lookups()
        by_label = self._ledger_by_label

        # Headline: one Office Standing gauge folds the four core metrics together.
        y = top + Space.M
        health = by_label.get("Office Standing")
        if health:
            tone = _tone_color(health.tone)
            c.create_text(inner_x0, y, text="OFFICE STANDING", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            c.create_text(inner_x0, y + 18, text=self._fit_px(health.value, Type.DISPLAY, "bold", inner_x1 - inner_x0), anchor="nw", fill=tone, font=self._font(Type.DISPLAY, "bold"))
            bar_y0 = y + 58
            pct = max(0, min(100, int(health.meter or 0)))
            c.create_rectangle(inner_x0, bar_y0, inner_x1, bar_y0 + 10, fill=Palette.METER_TRACK, outline="")
            if pct:
                c.create_rectangle(inner_x0, bar_y0, inner_x0 + int((inner_x1 - inner_x0) * pct / 100), bar_y0 + 10, fill=tone, outline="")
            y = bar_y0 + 10 + 18

        c.create_line(inner_x0, y, inner_x1, y, fill=Palette.BORDER)
        y += 12

        pulse = [by_label[name] for name in ("Activity", "Trust", "Friction", "Exposure") if name in by_label]
        if pulse:
            y = self._draw_pulse_grid(c, inner_x0, inner_x1, y, pulse) + 18

        if self.model.district_group_rows and y < y1 - 70:
            c.create_text(inner_x0, y, text="DISTRICT GROUPS", anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            y += 18
            group_rows = tuple(self.model.district_group_rows)
            available = max(0, y1 - 14 - y)
            row_gap = 6
            content_h = 5 + self._line_h(Type.SMALL, "bold") + self._line_h(Type.SMALL) + 6
            row_h = max(30, min(content_h, (available - row_gap * max(0, len(group_rows) - 1)) // max(1, len(group_rows))))
            for row in group_rows:
                if y + row_h > y1 - 14:
                    break
                y = self._draw_group_row(c, inner_x0, inner_x1, y, row, row_h=row_h) + row_gap

    def _draw_pulse_grid(self, c, x0, x1, y, rows):
        """Draw Activity/Trust/Friction/Exposure as a compact 2x2 grid."""

        gap = 8
        cell_w = max(80, (x1 - x0 - gap) // 2)
        cell_h = 68
        for index, row in enumerate(rows[:4]):
            cx0 = x0 + (index % 2) * (cell_w + gap)
            cy0 = y + (index // 2) * (cell_h + gap)
            cx1 = min(x1, cx0 + cell_w)
            tone = _tone_color(row.tone)
            c.create_rectangle(cx0, cy0, cx1, cy0 + cell_h, fill=Palette.SUBTLE, outline=Palette.BORDER)
            c.create_text(cx0 + 8, cy0 + 7, text=row.label.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            value_w = cx1 - cx0 - 16 - (self._text_w(row.label.upper(), Type.SMALL, "bold") or 0) - Space.S
            c.create_text(cx1 - 8, cy0 + 7, text=self._fit_px(row.value, Type.BODY, "bold", value_w), anchor="ne", fill=tone, font=self._font(Type.BODY, "bold"))
            self._draw_sparkline(c, cx0 + 8, cy0 + 34, cx1 - 14, cy0 + 54, row.points, tone)
            if (row.trend or "").lower() in ("up", "down", "flat"):  # unknown before a week-start snapshot: draw nothing
                c.create_text(cx1 - 8, cy0 + 54, text=row.trend, anchor="ne", fill=tone, font=self._font(Type.SMALL, "bold"))
        return y + (cell_h * 2) + gap

    def _draw_group_row(self, c, x0, x1, y, row, row_h=46):
        """Draw one district group: swatch, name, state word, detail, and a trend only when known."""

        tone = _tone_color(row.tone)
        c.create_rectangle(x0, y, x1, y + row_h, fill=Palette.SUBTLE, outline=Palette.BORDER)
        _record_box(c, "group-swatch", (x0 + 8, y + 10, x0 + 18, y + 20))
        c.create_rectangle(x0 + 8, y + 10, x0 + 18, y + 20, fill=row.swatch or Palette.BORDER, outline=Palette.BORDER)
        state_w = self._text_w(row.state, Type.SMALL, "bold") or 56
        label_w = x1 - x0 - 26 - state_w - Space.M
        c.create_text(x0 + 26, y + 5, text=self._fit_px(row.label, Type.SMALL, "bold", label_w), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL, "bold"))
        c.create_text(x1 - 8, y + 5, text=row.state, anchor="ne", fill=tone, font=self._font(Type.SMALL, "bold"))
        detail_y = y + 5 + self._line_h(Type.SMALL, "bold")
        detail_w = x1 - x0 - 34
        known_trend = (row.trend or "").lower() in ("up", "down", "flat")
        if len(tuple(row.points or ())) >= 2 and known_trend:
            self._draw_sparkline(c, x1 - 96, detail_y + 2, x1 - 22, min(y + row_h - 6, detail_y + 14), row.points, tone)
            detail_w -= 100
        if detail_y + self._line_h(Type.SMALL) <= y + row_h:
            c.create_text(x0 + 26, detail_y, text=self._fit_px(row.detail, Type.SMALL, "normal", detail_w), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL))
        return y + row_h

    def _draw_sparkline(self, c, x0, y0, x1, y1, points, color):
        """Draw a compact static sparkline from 0-100 point values."""

        _record_box(c, "sparkline", (x0, y0, x1, y1))
        pts = tuple(points or ())
        if len(pts) < 2:  # no history: draw nothing rather than a placeholder
            return
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        coords = []
        for index, value in enumerate(pts):
            px = x0 + int(width * index / max(1, len(pts) - 1))
            py = y1 - int(height * max(0, min(100, int(value))) / 100)
            coords.append((px, py))
        for start, end in zip(coords, coords[1:]):
            c.create_line(start[0], start[1], end[0], end[1], fill=color, width=2)

    def _draw_receipt_metrics(self, c, x0, y0, x1, metrics):
        """Draw the compact metric strip pinned to the foot of the receipt."""

        if not metrics:
            return
        c.create_line(x0, y0 - 4, x1, y0 - 4, fill=Palette.BORDER)
        mw = (x1 - x0) // max(1, len(metrics))
        for idx, (label, value) in enumerate(metrics):
            mx = x0 + idx * mw
            c.create_text(mx, y0, text=label, anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            c.create_text(mx, y0 + 13, text=self._fit_px(value, Type.BODY, "bold", mw - Space.S), anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))

    def _draw_district_attribute_table(self, c, box):
        """Draw the live district attribute table: names, fitted rows, wheel scroll."""

        x0, y0, x1, y1 = box
        _record_box(c, "district-table", (x0, y0, x1, y1))
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER)
        pad = Space.M
        inner_x0 = x0 + pad
        inner_x1 = x1 - pad
        header_y = self._draw_pane_header(c, x0, y0, x1, "District attributes", "Live map state") + Space.S
        header_h = 20
        row_y = header_y + header_h + 2
        cols = (
            ("District", 0.00, 0.20),
            ("Type", 0.20, 0.33),
            ("Prosperity", 0.33, 0.48),
            ("Pressure", 0.48, 0.66),
            ("Community", 0.66, 0.84),
            ("Target", 0.84, 1.00),
        )
        rows = list(self.model.district_table_rows)
        row_h = self._line_h(Type.SMALL) + Space.S + 2
        bottom = y1 - 10
        visible_count = max(1, (bottom - row_y) // row_h) if rows else 0
        max_scroll = max(0, len(rows) - visible_count)
        first = min(max(0, int(getattr(self, "_table_scroll", 0))), max_scroll)
        self._table_scroll = first
        self._table_scroll_box = (x0, row_y, x1, bottom)
        scrollbar_w = Space.S if max_scroll else 0
        table_x1 = inner_x1 - scrollbar_w
        width = max(1, table_x1 - inner_x0)
        shown = rows[first : first + visible_count]
        last_y = row_y + len(shown) * row_h
        c.create_rectangle(inner_x0, header_y, table_x1, header_y + header_h, fill=Palette.SUBTLE, outline=Palette.BORDER)
        for label, start, end in cols:
            cx = inner_x0 + int(width * start) + Space.S
            c.create_text(cx, header_y + 4, text=label.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(Type.SMALL, "bold"))
            if end < 1.0:
                line_x = inner_x0 + int(width * end)
                c.create_line(line_x, header_y, line_x, last_y, fill=Palette.BORDER)
        for index, row in enumerate(shown):
            ry0 = row_y + index * row_h
            ry1 = ry0 + row_h
            fill = Palette.SELECT if row.selected else Palette.CONTENT if (first + index) % 2 == 0 else Palette.SUBTLE
            c.create_rectangle(inner_x0, ry0, table_x1, ry1, fill=fill, outline=Palette.BORDER)
            if row.selected:
                c.create_rectangle(inner_x0, ry0, inner_x0 + 4, ry1, fill=Palette.ACCENT, outline="")
            values = (
                row.name,
                row.district_type,
                row.prosperity,
                row.pressure,
                row.community,
                "Target" if row.selected else "",
            )
            text_y = ry0 + (row_h - self._line_h(Type.SMALL)) // 2
            for value, (_label, start, end) in zip(values, cols):
                cx = inner_x0 + int(width * start) + Space.S
                max_w = max(28, int(width * (end - start)) - 14)
                weight = "bold" if row.selected and start == 0.0 else "normal"
                c.create_text(cx, text_y, text=self._fit_px(value, Type.SMALL, weight, max_w), anchor="nw", fill=Palette.INK, font=self._font(Type.SMALL, weight))
        if max_scroll:
            track_x0 = inner_x1 - scrollbar_w + 2
            track_h = max(1, bottom - row_y)
            c.create_rectangle(track_x0, row_y, inner_x1, bottom, fill=Palette.SUBTLE, outline="", tags=("table-scrollbar",))
            thumb_h = max(Space.XL, track_h * visible_count // len(rows))
            thumb_y0 = row_y + (track_h - thumb_h) * first // max_scroll
            c.create_rectangle(track_x0, thumb_y0, inner_x1, thumb_y0 + thumb_h, fill=Palette.BORDER, outline="", tags=("table-scrollbar",))

    def _scroll_table_by(self, rows):
        """Move the district table scroll by whole rows; the next draw clamps it."""

        self._table_scroll = max(0, int(getattr(self, "_table_scroll", 0)) + int(rows))

    def _draw_menu_button(self, c, x0, y0, x1, y1):
        """Draw the compact utility menu trigger."""

        hover = self._hover_key == "session:Menu"
        fill = Palette.SELECT if hover or self._menu_open else Palette.HEADER
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=Palette.BORDER, width=1)
        mid = (y0 + y1) // 2
        line_x0 = x0 + 11
        line_x1 = x1 - 11
        for offset in (-5, 0, 5):
            c.create_line(line_x0, mid + offset, line_x1, mid + offset, fill=Palette.INK, width=2)
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
        anchor = getattr(self, "_menu_anchor", None)
        if anchor:
            ax0, _ay0, ax1, ay1 = anchor
            x1 = min(width - 18, ax1)
            x0 = max(18, x1 - 176)
            if x0 < ax0 - 176:
                x0 = max(18, ax0 - 176)
                x1 = x0 + 176
            y0 = ay1
        else:
            x1 = width - 32
            x0 = x1 - 176
            y0 = 142
        row_h = 34
        y1 = y0 + row_h * len(entries)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER, width=1)
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

    def _draw_start_help_overlay(self, c, width, height):
        """Draw the compact in-window start/help card."""

        ow = min(520, width - 80)
        oh = min(420, height - 120)
        x0 = (width - ow) // 2
        y0 = (height - oh) // 2
        x1 = x0 + ow
        y1 = y0 + oh
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.CONTENT, outline=Palette.BORDER, width=1)
        c.create_rectangle(x0, y0, x1, y0 + 46, fill=Palette.ACCENT, outline="")
        title = "PERMIT OFFICE"
        c.create_text(x0 + 22, y0 + 23, text=title, anchor="w", fill=Palette.CONTENT, font=self._font(Type.DISPLAY, "bold"))
        body_x0 = x0 + 26
        body_x1 = x1 - 26
        sections = (
            ("Symbology", "Shapes show geometry: circle point, line stroke, square zone. Swatches match district or culture color. Counts and ON mirror live map layers."),
            ("Hotkeys", "V show, T retarget, I inspect, A approve, M mitigate, D deny, W end week, S scorecard, ? help."),
            ("Stats", "Activity, trust, friction, exposure, and culture rows show current value plus week-over-week direction."),
            ("Unknowns", "? means the office lacks evidence. Inspect File can reveal outcomes and replace unknown cards."),
            ("Controls", "Disabled actions explain what is missing, such as AP, money, inspection state, or map selection."),
        )
        yy = y0 + 64
        for section_title, text in sections:
            c.create_text(body_x0, yy, text=section_title, anchor="nw", fill=Palette.INK, font=self._font(Type.BODY, "bold"))
            yy = _text_bottom(c, body_x0 + 92, yy, text, self._font(Type.SMALL), Palette.MUTED, width=body_x1 - body_x0 - 92) + 8
        bw = 150
        self._draw_session_button(c, body_x0, y1 - 58, body_x0 + bw, y1 - 22, "New Game", Palette.ACCENT, self.callbacks.new_game)
        self._draw_session_button(c, body_x0 + bw + 12, y1 - 58, body_x0 + 2 * bw + 12, y1 - 22, "Help", Palette.MUTED, self.callbacks.show_help)

    def _add_target(self, kind, ident, bbox, callback):
        """Record a clickable canvas rectangle for later event dispatch."""

        self._click_targets.append((kind, ident, bbox, callback))

    def _font(self, size, weight="normal"):
        """Return the Segoe UI font tuple used by the desk canvas."""

        return desk_font(size, weight)

    def _ensure_lookups(self):
        """Rebuild the per-model lookup maps only when the model object changed.

        The label->ledger-row and action_id->lane maps are pure functions of the
        current model, so they are cached by model identity and reused across
        hover/deadline redraws (which keep the same model) instead of rebuilt by
        each draw method every frame.
        """

        if self._lookup_model is self.model:
            return
        model = self.model
        self._ledger_by_label = {row.label: row for row in model.ledger_rows}
        self._lane_by_action = {lane.action_id: lane for lane in model.action_lanes}
        self._lookup_model = model

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


def _deadline_day_time(value):
    """Return day and time tokens, and whether the clock is held, from the controller's office-clock text."""

    tokens = str(value or "").split()
    held = "HELD" in tokens
    tokens = [token for token in tokens if token != "HELD"]
    day = tokens[0] if tokens else ""
    time = next((token for token in reversed(tokens) if ":" in token), tokens[-1] if tokens else "")
    return day, time, held


def _folder_key_top(rail_box):
    """Return the map-key top used by the folder rail."""

    _x0, y0, _x1, y1 = rail_box
    list_y0 = y0 + 38 + 10
    available = max(260, y1 - list_y0)
    if available < 760:
        key_h = min(360, max(280, int(available * 0.40)))
    else:
        key_h = min(720, max(380, int(available * 0.47)))
    return y1 - key_h


def _table_top(rail_box, brief):
    """Return the district table top: level with the map key unless the decision brief needs the room."""

    if not brief:
        return _folder_key_top(rail_box)
    _x0, y0, _x1, y1 = rail_box
    # The decision brief starts 26 px under the panel top and ends 14 px above the table.
    brief_floor = y0 + 26 + DECISION_BRIEF_MIN_H + 14
    return min(max(_folder_key_top(rail_box), brief_floor), y1 - TABLE_MIN_H)


def _workspace_table_top(workspace_box, brief):
    """Return the shared top edge for the live attribute table; ``brief`` when it sits under the decision brief."""

    x0, y0, x1, y1 = workspace_box
    content = (x0 + 12, y0 + 12, x1 - 12, y1 - 12)
    panel_y0 = content[1] + 8
    panel_y1 = content[3]
    rail_w = min(252, max(230, int((content[2] - content[0]) * 0.25)))
    rail_box = (content[0], panel_y0, content[0] + rail_w, panel_y1)
    return _table_top(rail_box, brief)


def _legend_subset(rows, group, labels):
    """Return legend rows from a group in a specific public order."""

    lookup = {(row.group, row.label): row for row in rows}
    return [lookup.get((group, label), _fallback_legend_row(group, label)) for label in labels]


def _fallback_legend_row(group, label):
    """Return a stable cheat-sheet row for possible map states not currently visible."""

    symbols = {
        "Residential": DISTRICT_TYPE_SYMBOLS["residential"],
        "Mercantile": DISTRICT_TYPE_SYMBOLS["mercantile"],
        "Industrial": DISTRICT_TYPE_SYMBOLS["industrial"],
        "Civic": DISTRICT_TYPE_SYMBOLS["civic"],
        "Academic": DISTRICT_TYPE_SYMBOLS["academic"],
        "Natural": DISTRICT_TYPE_SYMBOLS["natural"],
        "Service Gap": DISPLAY_STATE_SYMBOLS["service_gap"],
        "Civic Incident": DISPLAY_STATE_SYMBOLS["incident"],
        "Local Grievance": DISPLAY_STATE_SYMBOLS["grievance"],
        "Stable": PROSPERITY_BAND_SYMBOLS["stable"],
        "Stable Identity": IDENTITY_STATE_SYMBOLS["stable"],
        "Contested Buyout": IDENTITY_STATE_SYMBOLS["contested"],
    }
    group_detail = {
        "PermitDistricts": "base district fill",
        "District display": "district pressure overlay",
        "Prosperity": "district prosperity outline",
        "Community": "identity overlay",
    }
    return MapLegendRow(group, label, group_detail.get(group, "map symbol"), _symbol_hex(symbols.get(label, PROSPERITY_BAND_SYMBOLS["stable"])), "neutral")


def _friendly_legend_row(row):
    """Replace internal layer-field detail with player-facing cheat-sheet copy."""

    details = {
        ("Selection", "Selected target"): "outlined district or edge",
        ("PermitPoints", "Proposed"): "pending point feature",
        ("PermitPoints", "Maintained"): "existing support point",
        ("PermitPoints", "Special Interest"): "notable point risk",
        ("PermitLines", "Road"): "line feature or route",
        ("PermitZones", "Active"): "filled zoning footprint",
        ("PermitDistricts", "Residential"): "housing district fill",
        ("PermitDistricts", "Mercantile"): "market district fill",
        ("PermitDistricts", "Industrial"): "industry district fill",
        ("PermitDistricts", "Civic"): "office/service district fill",
        ("PermitDistricts", "Academic"): "campus district fill",
        ("PermitDistricts", "Natural"): "reserve district fill",
        ("District display", "Service Gap"): "needs utility/service attention",
        ("District display", "Civic Incident"): "new civic incident overlay",
        ("District display", "Local Grievance"): "resident pressure overlay",
        ("Prosperity", "Stable"): "prosperity outline",
        ("Community", "Stable Identity"): "identity overlay",
        ("Community", "Contested Buyout"): "conversion pressure overlay",
    }
    detail = details.get((row.group, row.label), row.detail)
    return replace(row, detail=detail)


def _stamp_control(lane, color, callback, fallback):
    """Return a stamp control from its action lane, or from ``fallback`` when the model has no lane.

    ``fallback`` is (label, cost, (city, local), hotkey, tooltip).
    """

    if lane is None:
        label, cost, effects, hotkey, tooltip = fallback
        return (label, cost, effects, color, callback, True, True, hotkey, tooltip, "")
    effects = (lane.city_effect, lane.local_effect)
    return (lane.label, lane.cost, effects, color, callback, True, lane.enabled, lane.hotkey, lane.tooltip, lane.disabled_reason)


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


def _impact_tone(value):
    """Infer a tiny visual impact tone from a lane summary string."""

    text = str(value or "").lower()
    if "?" in text or "unknown" in text or "reveal" in text:
        return "watch"
    if any(token in text for token in ("-", "risk", "pressure", "friction", "exposure", "unresolved", "return", "heat")):
        return "bad"
    if any(token in text for token in ("+", "trust", "stabil", "reduce", "conditions")):
        return "good"
    return "neutral"


def _draw_impact_marker(c, x, y, tone):
    """Draw a small colored impact direction marker."""

    color = _tone_color(tone)
    if tone == "good":
        c.create_line(x, y + 6, x, y - 6, fill=color, width=2, arrow="last", tags=("impact-marker",))
    elif tone == "bad":
        c.create_line(x, y - 6, x, y + 6, fill=color, width=2, arrow="last", tags=("impact-marker",))
    elif tone == "watch":
        c.create_rectangle(x - 4, y - 4, x + 4, y + 4, fill=color, outline=color, tags=("impact-marker",))
    else:
        c.create_line(x - 5, y, x + 5, y, fill=color, width=2, tags=("impact-marker",))


def _trend_marker(trend):
    """Return a compact trend glyph for evidence cards."""

    value = (trend or "").lower()
    return value if value in ("up", "down", "flat") else "?"


def _trend_color(trend, tone="neutral"):
    """Return marker color from an explicit tone or the trend direction."""

    if tone and tone != "neutral":
        return _tone_color(tone)
    value = (trend or "").lower()
    if value == "up":
        return Palette.GOOD
    if value == "down":
        return Palette.BAD
    if value == "unknown":
        return Palette.WATCH
    return Palette.MUTED
