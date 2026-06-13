"""Custom Tkinter desk surface for the Permit Office dashboard."""

from __future__ import annotations

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
    _hazard_summary,
    _maintenance_summary,
    _service_gap_summary,
)


# Floors used while the window is still sizing; the desk re-flows for a portrait
# pane that fills half of a 1920x1080 monitor beside ArcGIS Pro.
MIN_DESK_W = 1120
MIN_DESK_H = 860
FEATURE_KEY_GROUPS = ("Selection", "PermitPoints", "PermitLines", "PermitZones")
MAP_STATE_KEY_GROUPS = ("PermitDistricts", "District display", "Prosperity", "Community")
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
    """Desk colors gathered in one place for Canvas rendering."""

    DESK = "#e8edf0"
    DESK_DARK = "#1f3f3a"
    PAPER = "#ffffff"
    PAPER_ALT = "#f4f7f5"
    PAPER_SHADOW = "#c2cbc8"
    FOLDER = "#d8e1de"
    NOTE = "#eef4f1"
    NOTE_BLUE = "#e8f1f7"
    INK = "#142421"
    MUTED = "#5d6e69"
    LINE = "#c6d0cc"
    RED = "#b5423f"
    GREEN = "#2f6b53"
    BLUE = "#2f6488"
    GOLD = "#9f7028"
    TEAL = "#3f7470"
    CARD_SHADOW = "#b5c2be"
    LEDGER = "#f7faf8"
    LEDGER_LINE = "#9dafaa"
    WHITE = "#ffffff"


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

        self.canvas = tk.Canvas(root, bg=Palette.DESK, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)

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
        """Lay out fixed banner/status/rolodex/action/receipt bands around a flexible body.

        Bands are budgeted from the top and the bottom so the flexible body row
        in the middle (case card + city-health rail) gets exactly the leftover
        space. Every band clips its own content, so regions never overlap.
        """

        c = self.canvas
        c.delete("all")
        self._click_targets = []
        self._draw_background(c, width, height)

        margin = 20
        gap = 14

        banner_h = 64
        status_h = 28

        self._draw_top_banner(c, width, banner_h)
        status_y0 = banner_h + gap
        self._status_strip_box = (margin, status_y0, width - margin, status_y0 + status_h)
        self._draw_status_strip(c, self._status_strip_box)

        body_y0 = status_y0 + status_h + gap
        body_y1 = height - margin

        health_w = min(300, max(250, int((width - 2 * margin) * 0.25)))
        health_x1 = width - margin
        health_x0 = health_x1 - health_w
        workspace_x0 = margin
        workspace_x1 = health_x0 - gap

        external_table = bool(self.model.docket_rows) and self.model.selected_desk_tab == "applications"
        if external_table:
            body_h = body_y1 - body_y0
            table_h = min(330, max(220, int(body_h * 0.28)))
            top_y1 = body_y1 - table_h - gap
            table_y0 = top_y1 + gap
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

    def _draw_background(self, c, width, height):
        """Paint the desk surface (banner draws its own dark band on top)."""

        c.create_rectangle(0, 0, width, height, fill=Palette.DESK, outline="")

    def _draw_top_banner(self, c, width, h):
        """Draw the headline-metrics banner across the desk lip."""

        c.create_rectangle(0, 0, width, h, fill=Palette.DESK_DARK, outline="")
        c.create_rectangle(0, h - 3, width, h, fill=Palette.CARD_SHADOW, outline="")
        c.create_text(28, h // 2, text="PERMIT OFFICE", anchor="w", fill=Palette.PAPER, font=self._font(14, "bold"))
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
            tone = _tone_color(row.tone)
            c.create_text(x, h // 2 - 11, text=display, anchor="w", fill=Palette.LEDGER_LINE, font=self._font(8, "bold"))
            c.create_text(x, h // 2 + 9, text=_clip(row.value, 10), anchor="w", fill=Palette.PAPER if tone == Palette.INK else tone, font=self._font(12, "bold"))
            x += spacing
        if self.model.deadline_text:
            self._draw_deadline_clock(c, width, h)
        self._draw_menu_button(c, width - 60, 18, width - 20, h - 16)

    def _draw_deadline_clock(self, c, width, h):
        """Draw the live office clock in the top banner."""

        x1 = width - 72
        x0 = x1 - 170
        y0 = 10
        y1 = h - 9
        meter = max(0, min(100, int(self.model.deadline_meter or 0)))
        fill = Palette.GOLD if meter >= 75 else Palette.PAPER if self.model.deadline_running else Palette.LEDGER_LINE
        day, time = _deadline_day_time(self.model.deadline_text)
        c.create_rectangle(x0, y0, x1, y1, outline=fill, width=1)
        c.create_text(x0 + 10, y0 + 5, text="DAY", anchor="nw", fill=Palette.LEDGER_LINE, font=self._font(7, "bold"))
        c.create_text(x0 + 10, y0 + 20, text=_clip(day, 9), anchor="nw", fill=Palette.PAPER, font=self._font(9, "bold"))
        c.create_text(x0 + 76, y0 + 5, text="TIME", anchor="nw", fill=Palette.LEDGER_LINE, font=self._font(7, "bold"))
        c.create_text(x0 + 76, y0 + 20, text=_clip(time, 11), anchor="nw", fill=Palette.PAPER, font=self._font(9, "bold"))
        # Thin progress bar pinned to the inner bottom edge, clear of the text.
        c.create_rectangle(x0 + 1, y1 - 4, x0 + 1 + int((x1 - x0 - 2) * meter / 100), y1 - 1, fill=fill, outline="")

    def _draw_status_strip(self, c, box):
        """Draw the one-line ambient status (office-day note, selection, errors)."""

        x0, y0, x1, y1 = box
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.DESK_DARK, outline="", tags=("status-strip",))
        mid = (y0 + y1) // 2
        label_w = 78
        text_x0 = x0 + label_w
        if self.model.status_text:
            text = _clip(self.model.status_text, max(40, (x1 - x0 - 90) // 7))
            c.create_text(text_x0, mid, text=text, anchor="w", fill=Palette.PAPER, font=self._font(10), tags=("status-strip",))
        else:
            ticker = "     /     ".join(self.model.ticker_items or ("Ready.",))
            marquee = f"{ticker}     /     {ticker}"
            text_w = max(1, len(marquee) * 7)
            travel = max(1, text_w + (x1 - text_x0))
            start_x = x1 - (self._ticker_offset_px % travel)
            c.create_text(start_x, mid, text=marquee, anchor="w", fill=Palette.PAPER, font=self._font(10), tags=("status-strip", "status-marquee"))
        c.create_rectangle(x0, y0, text_x0 - 6, y1, fill=Palette.DESK_DARK, outline="", tags=("status-strip",))
        c.create_text(x0 + 12, mid, text="STATUS", anchor="w", fill=Palette.LEDGER_LINE, font=self._font(8, "bold"), tags=("status-strip",))

    def _draw_main_workspace(self, c, box):
        """Draw the primary Applications/Filed Reports tab workspace."""

        x0, y0, x1, y1 = box
        _shadow_rect(c, x0 + 6, y0 + 8, x1 + 6, y1 + 8)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_ALT, outline=Palette.LINE, width=2)
        content = (x0 + 12, y0 + 12, x1 - 12, y1 - 12)
        self._draw_application_tab_content(c, content)

    def _draw_primary_tab(self, c, x0, y0, x1, y1, label, selected, callback):
        """Draw a primary lower-tab header."""

        fill = Palette.WHITE if selected else Palette.PAPER
        outline = Palette.BLUE if selected else Palette.LINE
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2 if selected else 1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label.upper(), anchor="center", fill=Palette.INK if selected else Palette.MUTED, font=self._font(8, "bold"))
        self._add_target("desk-tab", label, (x0, y0, x1, y1), callback)

    def _draw_application_tab_content(self, c, box):
        """Draw the hybrid folder layout: left folder rail, center detail."""

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
        if self.model.selected_desk_tab == "reports":
            self._draw_report_detail_card(c, detail_box)
        else:
            external_table = getattr(self, "_external_attribute_table", None)
            if external_table:
                top_y1, table_y0, table_y1, table_x1 = external_table
                active_box = (detail_box[0], detail_box[1], detail_box[2], min(detail_box[3], top_y1 - 12))
                table_box = (detail_box[0], table_y0, table_x1, table_y1)
            else:
                panel_h = panel_y1 - panel_y0
                active_h = min(max(390, int(panel_h * 0.52)), 520)
                table_min_h = min(190, max(138, int(panel_h * 0.18)))
                active_h = min(active_h, max(300, panel_h - table_min_h - gap))
                active_box = (detail_box[0], detail_box[1], detail_box[2], detail_box[1] + active_h)
                table_box = (detail_box[0], active_box[3] + gap, detail_box[2], panel_y1)
            self._draw_active_card(c, active_box)
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
        available = max(260, y1 - list_y0)
        if apps_selected:
            key_h = min(260, max(170, int(available * 0.24)))
        elif available < 760:
            key_h = min(360, max(260, int(available * 0.45)))
        else:
            key_h = min(720, max(500, int(available * 0.50)))
        if apps_selected:
            desired_list_h = min(y1 - list_y0 - key_h - 10, max(260, min(620, 50 + len(rows) * 108)))
            key_y0 = min(y1 - key_h, list_y0 + desired_list_h + 10)
            key_y1 = min(y1, key_y0 + key_h)
        else:
            key_y0 = y1 - key_h
            key_y1 = y1
        list_box = (x0, list_y0, x1, key_y0 - 10)
        key_box = (x0, key_y0, x1, key_y1)
        if reports_selected:
            self._draw_history_rail(c, list_box)
        else:
            self._draw_inbox_rail(c, list_box, rows, active_id)
        self._draw_map_key_rail(c, key_box, groups=("Selection",) if apps_selected else FEATURE_KEY_GROUPS)

    def _draw_inbox_rail(self, c, box, rows, active_id):
        """Draw the compact left docket rail."""

        x0, y0, x1, y1 = box
        _shadow_rect(c, x0 + 3, y0 + 4, x1 + 3, y1 + 4)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE)
        c.create_text(x0 + 12, y0 + 14, text="INBOX", anchor="nw", fill=Palette.INK, font=self._font(10, "bold"))
        c.create_text(x1 - 12, y0 + 16, text=f"{len(rows)} OPEN", anchor="ne", fill=Palette.MUTED, font=self._font(7, "bold"))
        y = y0 + 42
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
            c.create_text(x0 + 12, y1 - 20, text=f"+{overflow} queued", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))

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
        fill = Palette.NOTE_BLUE if selected else Palette.WHITE if hover else Palette.PAPER_ALT
        outline = Palette.BLUE if selected else Palette.LINE
        state_color = _status_color(row.status)
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2 if selected else 1)
        c.create_rectangle(x0, y0, x0 + 5, y1, fill=state_color, outline="")
        state = "ACTIVE" if selected else row.status.upper()
        c.create_text(x0 + 12, y0 + 8, text=state, anchor="nw", fill=state_color, font=self._font(7, "bold"))
        title_lines = _fit_lines(row.title, max(16, (x1 - x0 - 22) // 7), 2)
        c.create_text(x0 + 12, y0 + 23, text="\n".join(title_lines), anchor="nw", fill=Palette.INK, font=self._font(8, "bold"), width=x1 - x0 - 22)
        self._add_target("docket", row.item_id, (x0, y0, x1, y1), lambda item_id=row.item_id: self.on_select_item(item_id))

    def _draw_history_rail(self, c, box):
        """Draw filed decisions and week reports as a selectable history inbox."""

        x0, y0, x1, y1 = box
        tabs = list(self.model.report_tabs or ())
        _shadow_rect(c, x0 + 3, y0 + 4, x1 + 3, y1 + 4)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE)
        c.create_text(x0 + 12, y0 + 14, text="HISTORY", anchor="nw", fill=Palette.INK, font=self._font(10, "bold"))
        c.create_text(x1 - 12, y0 + 16, text=f"{len(tabs)} FILED", anchor="ne", fill=Palette.MUTED, font=self._font(7, "bold"))
        if not tabs:
            c.create_text(x0 + 12, y0 + 46, text="No filed reports yet.", anchor="nw", fill=Palette.MUTED, font=self._font(8))
            return
        y = y0 + 42
        row_h = 46
        for tab in tabs[-max(1, (y1 - y - 24) // (row_h + 7)):]:
            selected = tab.report_id == self.model.selected_report_id
            hover = self._hover_key == f"report:{tab.report_id}"
            fill = Palette.NOTE_BLUE if selected else Palette.WHITE if hover else Palette.PAPER_ALT
            outline = _status_color(tab.status)
            c.create_rectangle(x0 + 8, y, x1 - 8, y + row_h, fill=fill, outline=Palette.BLUE if selected else Palette.LINE, width=2 if selected else 1)
            c.create_rectangle(x0 + 8, y, x0 + 13, y + row_h, fill=outline, outline="")
            c.create_text(x0 + 20, y + 7, text=tab.kind.upper(), anchor="nw", fill=outline, font=self._font(7, "bold"))
            c.create_text(x0 + 20, y + 23, text=self._fit_px(tab.title, 9, "bold", x1 - x0 - 36), anchor="nw", fill=Palette.INK, font=self._font(9, "bold"))
            self._add_target("report", tab.report_id, (x0 + 8, y, x1 - 8, y + row_h), lambda report_id=tab.report_id: self.callbacks.select_report(report_id))
            y += row_h + 7

    def _draw_map_key_rail(self, c, box, groups=None, title="MAP KEY"):
        """Draw the right map key/legend rail."""

        x0, y0, x1, y1 = box
        _shadow_rect(c, x0 + 3, y0 + 4, x1 + 3, y1 + 4)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.LEDGER, outline=Palette.LINE)
        if hasattr(c, "_record"):
            c._record("map-key", (x0, y0, x1, y1), {})
        c.create_text(x0 + 12, y0 + 14, text=title, anchor="nw", fill=Palette.INK, font=self._font(10, "bold"))
        case_rows = set(groups or ()) == {"Selection"} and bool(self.model.case_map_symbols)
        if case_rows:
            c.create_text(x1 - 12, y0 + 16, text="CASE LAYERS", anchor="ne", fill=Palette.MUTED, font=self._font(7, "bold"))
        c.create_line(x0 + 12, y0 + 38, x1 - 12, y0 + 38, fill=Palette.LINE)
        y = y0 + 52
        last_group = ""
        group_filter = set(groups or ())
        if case_rows:
            rows = list(self.model.case_map_symbols)
        else:
            rows = [row for row in self.model.map_legend_rows if not group_filter or row.group in group_filter]
        if groups:
            order = {group: index for index, group in enumerate(groups)}
            rows.sort(key=lambda row: order.get(getattr(row, "group", "Selection"), len(order)))
        for row in rows:
            if y > y1 - 30:
                break
            group = getattr(row, "group", "Selection")
            if not case_rows and group != last_group:
                c.create_text(x0 + 12, y, text=group, anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
                field = LEGEND_GROUP_FIELDS.get(group)
                if field:
                    c.create_text(x0 + 12, y + 14, text=field, anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
                    y += 31
                else:
                    y += 18
                last_group = group
            self._draw_map_key_row(c, x0 + 12, y, x1 - 12, row)
            y += 31

    def _draw_map_key_row(self, c, x0, y0, x1, row):
        """Draw one bounded legend row with swatch, label, and detail."""

        if hasattr(row, "group"):
            self._draw_legend_symbol(c, x0, y0 + 1, row)
        else:
            self._draw_case_symbol(c, x0, y0 + 1, row)
        state = getattr(row, "state", "")
        label_w = x1 - x0 - (52 if state else 20)
        c.create_text(x0 + 20, y0 - 1, text=self._fit_px(row.label, 8, "bold", label_w), anchor="nw", fill=Palette.INK, font=self._font(8, "bold"))
        c.create_text(x0 + 20, y0 + 13, text=self._fit_px(row.detail, 7, "normal", label_w), anchor="nw", fill=Palette.MUTED, font=self._font(7))
        if state:
            c.create_text(x1, y0 - 1, text=_clip(state, 5), anchor="ne", fill=_tone_color(getattr(row, "tone", "neutral")), font=self._font(7, "bold"))

    def _draw_case_symbol(self, c, x0, y0, row):
        """Draw a map symbol matching the row geometry when available."""

        shape = getattr(row, "shape", "")
        swatch = getattr(row, "swatch", Palette.LINE) or Palette.LINE
        if shape == "line":
            if hasattr(c, "_record"):
                c._record("symbol-line", (x0, y0 + 9, x0 + 18, y0 + 9), {})
            c.create_line(x0, y0 + 9, x0 + 18, y0 + 9, fill=swatch, width=4, capstyle="round")
            return
        if shape == "point":
            if hasattr(c, "_record"):
                c._record("symbol-point", (x0 + 2, y0 + 3, x0 + 14, y0 + 15), {})
            c.create_oval(x0 + 2, y0 + 3, x0 + 14, y0 + 15, fill=swatch, outline=Palette.LINE)
            return
        if shape == "zone":
            if hasattr(c, "_record"):
                c._record("symbol-zone", (x0 + 1, y0 + 2, x0 + 17, y0 + 16), {})
            c.create_rectangle(x0 + 1, y0 + 2, x0 + 17, y0 + 16, fill=swatch, outline=Palette.LINE)
            return
        self._draw_legend_symbol(c, x0, y0, row)

    def _draw_legend_symbol(self, c, x0, y0, row):
        """Draw a Contents-style symbol matched to the represented layer."""

        group = row.group
        label = row.label.lower()
        swatch = row.swatch
        if group == "PermitPoints":
            if "special interest" in label:
                c.create_oval(x0 - 1, y0, x0 + 17, y0 + 18, fill="#68471e", outline="")
                c.create_oval(x0 + 4, y0 + 5, x0 + 12, y0 + 13, fill="#e2b84d", outline="")
            elif "proposed" in label:
                c.create_oval(x0 - 1, y0, x0 + 17, y0 + 18, fill="#29d6d1", outline="")
            elif "expired" in label:
                c.create_oval(x0 + 5, y0 + 6, x0 + 11, y0 + 12, fill=Palette.WHITE, outline=Palette.INK, width=1)
            else:
                c.create_oval(x0 + 2, y0 + 3, x0 + 14, y0 + 15, fill=swatch, outline=Palette.LINE)
            return
        if group == "PermitLines":
            c.create_line(x0, y0 + 9, x0 + 18, y0 + 9, fill=swatch if "road" not in label else "#4f5653", width=4, capstyle="round")
            return
        if group in ("Prosperity", "Community", "Selection"):
            c.create_rectangle(x0 + 1, y0 + 2, x0 + 17, y0 + 16, fill=Palette.PAPER if group == "Prosperity" else swatch, outline=swatch, width=3 if group != "Selection" else 2)
            return
        c.create_rectangle(x0 + 1, y0 + 2, x0 + 17, y0 + 16, fill=swatch, outline="#6f7773", width=2)

    def _draw_collapsed_docket_row(self, c, x0, y0, x1, y1, row):
        """Draw one collapsed queued docket case as a slim selectable row."""

        hover = self._hover_key == f"docket:{row.item_id}"
        outline = _status_color(row.status)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.WHITE if hover else Palette.PAPER_ALT, outline=Palette.LINE)
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=outline, outline="")
        c.create_text(x0 + 14, (y0 + y1) // 2, text=row.status.upper(), anchor="w", fill=outline, font=self._font(7, "bold"))
        c.create_text(x0 + 96, (y0 + y1) // 2, text=self._fit_px(row.title, 10, "bold", x1 - x0 - 110), anchor="w", fill=Palette.INK, font=self._font(10, "bold"))
        self._add_target("docket", row.item_id, (x0, y0, x1, y1), lambda item_id=row.item_id: self.on_select_item(item_id))

    def _draw_queue_cleared_state(self, c, box):
        """Draw the empty-docket panel: a start prompt before a game exists, or
        the queue-cleared / End Week controls once one is running."""

        x0, y0, x1, y1 = box
        card_y0 = y0 + 34
        _shadow_rect(c, x0 + 4, card_y0 + 5, x1 + 4, y1 + 5)
        c.create_rectangle(x0, card_y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE, width=1)
        if not self.model.game_active:
            c.create_text(x0 + 24, card_y0 + 26, text="No game yet", anchor="nw", fill=Palette.INK, font=self._font(18, "bold"))
            start_body = (
                "This workspace has no active Permit Office board. Click New Game "
                "to generate a city and start the 12-week season."
            )
            c.create_text(x0 + 24, card_y0 + 66, text=start_body, anchor="nw", fill=Palette.MUTED, font=self._font(11), width=x1 - x0 - 48)
            by0 = y1 - 64
            self._draw_case_action(c, x0 + 24, by0, x0 + 164, by0 + 40, "New Game", Palette.BLUE, self.callbacks.new_game, primary=True)
            return
        c.create_text(x0 + 24, card_y0 + 26, text="Queue cleared", anchor="nw", fill=Palette.INK, font=self._font(18, "bold"))
        body = "All applications have been filed. End Week to process follow-ups."
        if self.model.auto_close_active:
            body = f"{body} Automatic close in {self.model.auto_close_seconds} seconds."
        c.create_text(x0 + 24, card_y0 + 66, text=body, anchor="nw", fill=Palette.MUTED, font=self._font(11), width=x1 - x0 - 48)
        by0 = y1 - 64
        self._draw_case_action(c, x0 + 24, by0, x0 + 164, by0 + 40, "End Week", Palette.INK, self.callbacks.advance_turn, primary=True)
        if self.model.auto_close_active:
            self._draw_case_action(c, x0 + 176, by0, x0 + 344, by0 + 40, "Cancel Auto Close", Palette.MUTED, self.callbacks.cancel_queue_autoclose, primary=False)

    def _draw_report_tab_content(self, c, box):
        """Draw filed-report nested tabs and the selected report body."""

        x0, y0, x1, y1 = box
        tabs = self.model.report_tabs or ()
        if not tabs:
            c.create_rectangle(x0, y0 + 18, min(x1, x0 + 180), y0 + 72, fill=Palette.WHITE, outline=Palette.BLUE, width=2)
            c.create_text(x0 + 10, y0 + 30, text="NO REPORT FILED", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
            c.create_text(x0 + 10, y0 + 50, text="Inspect, issue, deny, end week, or open Scorecard.", anchor="nw", fill=Palette.MUTED, font=self._font(8))
            return
        visible = list(tabs)[-4:]
        gap = 6
        tab_area_h = 62
        tab_w = max(112, (x1 - x0 - gap * (len(visible) - 1)) // max(1, len(visible)))
        tx = x0
        for tab in visible:
            selected = tab.selected
            hover = self._hover_key == f"report:{tab.report_id}"
            fill = Palette.WHITE if selected or hover else Palette.PAPER
            outline = _status_color(tab.status)
            ty0 = y0 + (14 if not selected else 8)
            tx1 = min(x1, tx + tab_w)
            c.create_rectangle(tx, ty0, tx1, y0 + tab_area_h, fill=fill, outline=outline, width=2 if selected else 1)
            c.create_rectangle(tx, ty0, tx1, ty0 + 5, fill=outline, outline="")
            c.create_text(tx + 8, ty0 + 14, text=self._fit_px(tab.title, 9, "bold", tx1 - tx - 16), anchor="nw", fill=Palette.INK, font=self._font(9, "bold"))
            c.create_text(tx + 8, y0 + tab_area_h - 18, text=tab.status.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
            self._add_target("report", tab.report_id, (tx, ty0, tx1, y0 + tab_area_h), lambda report_id=tab.report_id: self.callbacks.select_report(report_id))
            tx += tab_w + gap
        selected_report = next((tab for tab in tabs if tab.selected), tabs[-1])
        body_y0 = y0 + tab_area_h + 10
        c.create_rectangle(x0, body_y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE)
        bx0 = x0 + 14
        bx1 = x1 - 14
        yy = _text_bottom(c, bx0, body_y0 + 10, self._fit_px(selected_report.title, 11, "bold", bx1 - bx0), self._font(11, "bold"), Palette.INK) + 6
        max_lines = max(1, (y1 - yy - 34) // 16)
        line_w = max(24, (bx1 - bx0) // 7)
        body = "\n".join(_fit_lines(selected_report.report, line_w, max_lines))
        _text_bottom(c, bx0, yy, body, self._font(9), Palette.INK)
        if selected_report.metrics:
            self._draw_receipt_metrics(c, bx0, y1 - 28, bx1, selected_report.metrics)

    def _draw_report_detail_card(self, c, box):
        """Draw the selected filed report using the same center-detail structure."""

        x0, y0, x1, y1 = box
        tabs = list(self.model.report_tabs or ())
        selected_report = next((tab for tab in tabs if tab.report_id == self.model.selected_report_id), tabs[-1] if tabs else None)
        _shadow_rect(c, x0 + 4, y0 + 5, x1 + 4, y1 + 5)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE)
        c.create_rectangle(x0, y0, x1, y0 + 78, fill=Palette.WHITE, outline="")
        c.create_line(x0, y0 + 78, x1, y0 + 78, fill=Palette.LINE)
        pad = 22
        bx0 = x0 + pad
        bx1 = x1 - pad
        c.create_text(bx0, y0 + 18, text="REPORT DETAIL", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
        if selected_report is None:
            c.create_text(bx0, y0 + 44, text="No filed report yet", anchor="nw", fill=Palette.INK, font=self._font(16, "bold"))
            c.create_text(bx0, y0 + 88, text="Decisions, inspections, scorecards, and week close reports will appear here.", anchor="nw", fill=Palette.MUTED, font=self._font(10), width=bx1 - bx0)
            return
        c.create_text(bx0, y0 + 40, text=self._fit_px(selected_report.title, 16, "bold", bx1 - bx0 - 110), anchor="nw", fill=Palette.INK, font=self._font(16, "bold"))
        self._draw_status_badge(c, bx1 - 104, y0 + 34, bx1, y0 + 60, selected_report.status.upper(), _status_color(selected_report.status))
        yy = y0 + 94
        max_lines = max(1, (y1 - yy - 72) // 18)
        body = "\n".join(_fit_lines(selected_report.report, max(36, (bx1 - bx0) // 7), max_lines))
        _text_bottom(c, bx0, yy, body, self._font(10), Palette.INK, width=bx1 - bx0)
        if selected_report.metrics:
            self._draw_receipt_metrics(c, bx0, y1 - 34, bx1, selected_report.metrics)

    def _draw_active_card(self, c, box):
        """Draw the selected application as a modern decision brief."""

        x0, y0, x1, y1 = box
        case = self.model.case
        if hasattr(c, "_record"):
            c._record("active-card", (x0, y0, x1, y1), {})
        _shadow_rect(c, x0 + 4, y0 + 5, x1 + 4, y1 + 5)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE, width=1)
        c.create_rectangle(x0, y0, x1, y0 + 78, fill=Palette.WHITE, outline="")
        c.create_line(x0, y0 + 78, x1, y0 + 78, fill=Palette.LINE)

        pad = 22
        body_x0 = x0 + pad
        body_x1 = x1 - pad
        risk_known = (case.risk_band or "unknown").lower() not in ("", "unknown")
        risk_text = "RISK: " + case.risk_band.upper() if risk_known else "RISK: PENDING"
        c.create_text(body_x0, y0 + 18, text="DECISION BRIEF", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
        c.create_text(body_x0, y0 + 40, text=self._fit_px(case.title, 16, "bold", body_x1 - body_x0 - 150), anchor="nw", fill=Palette.INK, font=self._font(16, "bold"))
        self._draw_status_badge(c, body_x1 - 132, y0 + 34, body_x1, y0 + 60, risk_text, _risk_color(case.risk_band))
        content_y0 = y0 + 92
        content_y1 = y1 - 16
        available = max(260, content_y1 - content_y0)
        gap = 14
        if available < 430:
            min_action_h = 166
            evidence_h = max(64, min(96, int(available * 0.22)))
            info_h = max(62, min(90, available - (2 * gap) - min_action_h - evidence_h))
            action_h = max(120, available - (2 * gap) - info_h - evidence_h)
        else:
            info_h = min(max(130, int(available * 0.32)), 190)
            evidence_h = min(max(164, int(available * 0.30)), 190)
            action_h = min(max(170, int(available * 0.34)), 218)
        info_box = (body_x0, content_y0, body_x1, min(content_y1, content_y0 + info_h))
        evidence_y0 = info_box[3] + gap
        evidence_box = (body_x0, evidence_y0, body_x1, min(content_y1, evidence_y0 + evidence_h))
        action_y0 = evidence_box[3] + gap
        if hasattr(c, "_record"):
            c._record("decision-info", (info_box[0], info_box[1], evidence_box[2], evidence_box[3]), {})
        self._draw_application_info_panel(c, info_box)
        self._draw_case_evidence_row(c, evidence_box)
        self._draw_case_controls(c, body_x0, action_y0, body_x1, min(content_y1, action_y0 + action_h))

    def _draw_application_info_panel(self, c, box):
        """Draw the wireframe application information panel."""

        x0, y0, x1, y1 = box
        case = self.model.case
        if hasattr(c, "_record"):
            c._record("app-info", (x0, y0, x1, y1), {})
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_ALT, outline=Palette.LINE)
        pad = 14
        tx0 = x0 + pad
        tx1 = x1 - pad
        yy = y0 + pad
        preview = "\n".join(_fit_lines(case.preview, max(44, (tx1 - tx0) // 8), 3))
        yy = _text_bottom(c, tx0, yy, preview, self._font(10), Palette.MUTED, width=tx1 - tx0) + 12
        econ_text = ""
        econ_color = Palette.MUTED
        econ_y = y1
        if case.economy:
            econ_qualifier = "filed" if bool(case.inspection) and not case.inspection.lower().startswith("no inspection") else "est."
            econ_text = f"Budget ({econ_qualifier}): {case.economy}" if case.economy != "no recurring budget" else "Budget: no recurring revenue or upkeep"
            econ_color = Palette.GREEN if "net +" in case.economy else Palette.RED if "net -" in case.economy else Palette.MUTED
            econ_y = max(y0 + pad, y1 - 20)
            c.create_text(tx0, econ_y, text=self._fit_px(econ_text, 9, "bold", tx1 - tx0), anchor="nw", fill=econ_color, font=self._font(9, "bold"))
        inspected = bool(case.inspection) and not case.inspection.lower().startswith("no inspection")
        note = case.inspection if inspected else "Uninspected: decision impacts are estimates. Inspect File may reveal violations or stronger stakeholder reactions."
        note_color = Palette.RED if inspected and case.risk_band == "high" else Palette.GOLD if not inspected else Palette.BLUE
        note_h = min(52, (econ_y - 6 if econ_text else y1 - 10) - yy)
        if note_h >= 24:
            c.create_rectangle(tx0, yy, tx1, yy + note_h, fill="#fff8e9" if not inspected else Palette.NOTE_BLUE, outline="")
            c.create_rectangle(tx0, yy, tx0 + 4, yy + note_h, fill=note_color, outline="")
            note_lines = _fit_lines(note, max(30, (tx1 - tx0 - 24) // 7), 1 if note_h < 34 else 2)
            c.create_text(tx0 + 12, yy + 8, text="\n".join(note_lines), anchor="nw", fill=Palette.INK, font=self._font(9, "bold"))

    def _draw_case_evidence_row(self, c, box):
        """Draw compact evidence widgets for districts, cultures, and outcomes."""

        x0, y0, x1, y1 = box
        case = self.model.case
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_ALT, outline=Palette.LINE)
        pad = 10
        gap = 8
        col_w = max(100, (x1 - x0 - (2 * pad) - (2 * gap)) // 3)
        columns = (
            ("AFFECTED DISTRICTS", x0 + pad, x0 + pad + col_w),
            ("CULTURE PRESSURE", x0 + pad + col_w + gap, x0 + pad + 2 * col_w + gap),
            ("POSSIBLE OUTCOMES", x0 + pad + 2 * (col_w + gap), x1 - pad),
        )
        for title, cx0, cx1 in columns:
            c.create_text(cx0, y0 + 9, text=title, anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
        content_top = y0 + 30
        content_bottom = y1 - 8
        self._draw_district_grid_widget(c, columns[0][1], content_top, columns[0][2], content_bottom, case.district_grid)
        self._draw_trend_card_stack(c, columns[1][1], content_top, columns[1][2], content_bottom, case.culture_cards, "culture-card")
        self._draw_trend_card_stack(c, columns[2][1], content_top, columns[2][2], content_bottom, case.outcome_cards, "outcome-card")

    def _draw_district_grid_widget(self, c, x0, y0, x1, y1, cells):
        """Draw the selected-case district mini-grid."""

        if hasattr(c, "_record"):
            c._record("district-grid", (x0, y0, x1, y1), {})
        cells = tuple(cells or ())
        if not cells:
            c.create_text(x0, y0 + 4, text="No map targets", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
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
            fill = cell.swatch or Palette.NOTE_BLUE if cell.affected else Palette.WHITE
            outline = Palette.INK if cell.affected else Palette.LINE
            c.create_rectangle(cx0, cy0, cx1, cy1, fill=fill, outline=outline, width=2 if cell.affected else 1)
            c.create_text((cx0 + cx1) // 2, (cy0 + cy1) // 2, text=cell.label, anchor="center", fill=Palette.WHITE if cell.affected and cell.swatch else Palette.INK, font=self._font(7, "bold"))

    def _draw_trend_card_stack(self, c, x0, y0, x1, y1, cards, record_kind):
        """Draw compact trend cards for culture or outcome evidence."""

        cards = tuple(cards or ())
        if not cards:
            c.create_text(x0, y0 + 4, text="Unknown", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
            return
        gap = 5
        row_h = max(30, min(42, (y1 - y0 - gap * (min(len(cards), 3) - 1)) // max(1, min(len(cards), 3))))
        for index, card in enumerate(cards[:3]):
            yy = y0 + index * (row_h + gap)
            if yy >= y1:
                break
            if hasattr(c, "_record"):
                c._record(record_kind, (x0, yy, x1, min(y1, yy + row_h)), {})
            tone = _tone_color(card.tone)
            c.create_rectangle(x0, yy, x1, min(y1, yy + row_h), fill=Palette.WHITE, outline=Palette.LINE)
            c.create_rectangle(x0, yy, x0 + 4, min(y1, yy + row_h), fill=tone, outline="")
            if card.swatch:
                c.create_rectangle(x0 + 10, yy + 8, x0 + 20, yy + 18, fill=card.swatch, outline=Palette.LINE)
                text_x = x0 + 26
            else:
                text_x = x0 + 10
            marker_color = _trend_color(card.trend, card.tone)
            c.create_text(text_x, yy + 5, text=self._fit_px(card.label, 8, "bold", x1 - text_x - 28), anchor="nw", fill=Palette.INK, font=self._font(8, "bold"))
            c.create_text(x1 - 10, yy + 5, text=_trend_marker(card.trend), anchor="ne", fill=marker_color, font=self._font(8, "bold"))
            detail_y = yy + 21
            if detail_y < y1 - 8:
                c.create_text(text_x, detail_y, text=self._fit_px(card.detail, 7, "normal", x1 - text_x - 10), anchor="nw", fill=Palette.MUTED, font=self._font(7))

    def _draw_status_badge(self, c, x0, y0, x1, y1, text, color):
        """Draw a restrained status badge."""

        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_ALT, outline=color, width=1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=self._fit_px(text, 8, "bold", x1 - x0 - 10), anchor="center", fill=color, font=self._font(8, "bold"))

    def _draw_decision_lane(self, c, x0, y0, x1, y1, lane):
        """Draw one decision consequence lane."""

        tone = _tone_color(lane.tone)
        hover = self._hover_key == f"case-action:{lane.label}"
        outline = Palette.INK if hover and lane.enabled else Palette.LINE
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_ALT, outline=outline, width=2 if hover and lane.enabled else 1)
        lane_tone = tone if lane.enabled else Palette.MUTED
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=lane_tone, outline="")
        inner_x0 = x0 + 14
        inner_x1 = max(inner_x0 + 80, x1 - 14)
        content_w = max(80, inner_x1 - inner_x0)
        gap = 10
        cost_text = lane.cost if lane.enabled else lane.disabled_reason or lane.cost
        if content_w >= 430:
            label_w = min(190, max(132, int(content_w * 0.34)))
            remaining = max(120, content_w - label_w - (2 * gap))
            city_w = max(72, remaining // 2)
            local_w = max(72, content_w - label_w - city_w - (2 * gap))
            city_x = inner_x0 + label_w + gap
            local_x = min(inner_x1 - local_w, city_x + city_w + gap)
            label_y = y0 + 11
            cost_y = y0 + 38
            detail_label_y = y0 + 10
            detail_body_y = y0 + 29
        else:
            label_w = content_w
            city_w = max(56, (content_w - gap) // 2)
            local_w = max(56, content_w - city_w - gap)
            city_x = inner_x0
            local_x = min(inner_x1 - local_w, city_x + city_w + gap)
            label_y = y0 + 7
            cost_y = y0 + 25
            detail_label_y = y0 + 45
            detail_body_y = y0 + 59

        c.create_text(inner_x0, label_y, text=lane.label, anchor="nw", fill=Palette.INK if lane.enabled else Palette.MUTED, font=self._font(11, "bold"), width=label_w)
        c.create_text(inner_x0, cost_y, text=self._fit_px(cost_text, 9, "bold", label_w), anchor="nw", fill=lane_tone, font=self._font(9, "bold"), width=label_w)
        c.create_text(city_x, detail_label_y, text="City", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"), width=city_w)
        c.create_text(city_x, detail_body_y, text="\n".join(_fit_lines(lane.city_effect, max(12, city_w // 7), 2)), anchor="nw", fill=Palette.INK, font=self._font(9), width=city_w)
        c.create_text(local_x, detail_label_y, text="Local", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"), width=local_w)
        c.create_text(local_x, detail_body_y, text="\n".join(_fit_lines(lane.local_effect, max(12, local_w // 7), 2)), anchor="nw", fill=Palette.INK, font=self._font(9), width=local_w)
        callback = self._callback_for_decision_lane(lane.action_id)
        if lane.enabled and callback is not None:
            self._add_target("case-action", lane.label, (x0, y0, x1, y1), callback)

    def _callback_for_decision_lane(self, action_id):
        """Return the controller callback for a decision lane action id."""

        if action_id == "approve":
            return self.callbacks.approve
        if action_id == "approve_mitigated":
            return self.callbacks.approve_mitigated
        if action_id == "deny":
            return self.callbacks.deny
        return None

    def _draw_decision_info_panel(self, c, x0, y0, x1, y1):
        """Draw non-clickable decision context above the action grid."""

        if hasattr(c, "_record"):
            c._record("decision-info", (x0, y0, x1, y1), {})
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_ALT, outline=Palette.LINE)
        lanes = list(self.model.action_lanes)
        if not lanes:
            return
        pad = 10
        gap = 6
        row_h = max(42, (y1 - y0 - 2 * pad - gap * (len(lanes) - 1)) // max(1, len(lanes)))
        yy = y0 + pad
        for lane in lanes:
            self._draw_decision_summary_row(c, x0 + pad, yy, x1 - pad, min(y1 - pad, yy + row_h), lane)
            yy += row_h + gap

    def _draw_decision_summary_row(self, c, x0, y0, x1, y1, lane):
        """Draw one compact, non-clickable decision consequence summary."""

        tone = _tone_color(lane.tone) if lane.enabled else Palette.MUTED
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.WHITE, outline=Palette.LINE)
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=tone, outline="")
        label_w = min(170, max(120, int((x1 - x0) * 0.24)))
        city_x = x0 + label_w + 12
        local_x = city_x + max(150, (x1 - city_x - 12) // 2)
        c.create_text(x0 + 12, y0 + 7, text=lane.label, anchor="nw", fill=Palette.INK if lane.enabled else Palette.MUTED, font=self._font(9, "bold"))
        c.create_text(x0 + 12, y0 + 25, text=self._fit_px(lane.cost if lane.enabled else lane.disabled_reason or lane.cost, 8, "bold", label_w - 16), anchor="nw", fill=tone, font=self._font(8, "bold"))
        c.create_text(city_x, y0 + 7, text="City", anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
        c.create_text(city_x, y0 + 22, text=self._fit_px(lane.city_effect, 8, "normal", max(90, local_x - city_x - 10)), anchor="nw", fill=Palette.INK, font=self._font(8))
        c.create_text(local_x, y0 + 7, text="Local", anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
        c.create_text(local_x, y0 + 22, text=self._fit_px(lane.local_effect, 8, "normal", x1 - local_x - 8), anchor="nw", fill=Palette.INK, font=self._font(8))

    def _draw_case_controls(self, c, x0, y0, x1, y1):
        """Draw selected-case map, inspect, and stamp controls inside the card."""

        if hasattr(c, "_record"):
            c._record("action-grid", (x0, y0 - 8, x1, y1), {})
        c.create_rectangle(x0, y0 - 8, x1, y1, fill=Palette.PAPER_ALT, outline=Palette.LINE)
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
        controls = (
            (exhibit_label, "0 AP", "map layer visibility", Palette.BLUE, self.callbacks.toggle_exhibit, False, True, "V", "Toggle proposed feature visibility.", ""),
            ("Retarget Map", "0 AP", "use selected map features", Palette.TEAL, self.callbacks.update_from_map, False, True, "T", "Use selected map features as targets.", ""),
            ("Inspect File", "1 AP", "reveal filed risk", Palette.GOLD, self.callbacks.inspect, False, True, "I", "Spend AP to reveal risk and outcomes.", ""),
            (approve.label if approve else "Issue Permit", approve.cost if approve else "1 AP", (approve.city_effect, approve.local_effect) if approve else ("City: file permit", "Local: update target"), Palette.GREEN, self.callbacks.approve, True, approve.enabled if approve else True, approve.hotkey if approve else "A", approve.tooltip if approve else "Issue the permit and file the selected map change.", approve.disabled_reason if approve else ""),
            (mitigate.label if mitigate else "Add Conditions", mitigate.cost if mitigate else "1 AP", (mitigate.city_effect, mitigate.local_effect) if mitigate else ("City: add terms", "Local: reduce risk"), "#527d65", self.callbacks.approve_mitigated, True, mitigate.enabled if mitigate else True, mitigate.hotkey if mitigate else "M", mitigate.tooltip if mitigate else "Issue the permit with mitigation conditions.", mitigate.disabled_reason if mitigate else ""),
            (deny.label if deny else "Deny", deny.cost if deny else "0 AP", (deny.city_effect, deny.local_effect) if deny else ("City: reject filing", "Local: unresolved pressure"), Palette.RED, self.callbacks.deny, True, deny.enabled if deny else True, deny.hotkey if deny else "D", deny.tooltip if deny else "Deny the filing.", deny.disabled_reason if deny else ""),
        )
        gap = 8
        row_gap = 8
        card_w = max(88, (x1 - x0 - gap * 2) // 3)
        card_h = max(64, min(96, (y1 - y0 - row_gap) // 2))
        for index, (label, cost, detail, color, callback, primary, enabled, hotkey, tooltip, disabled_reason) in enumerate(controls):
            cx = x0 + (index % 3) * (card_w + gap)
            cy = y0 + (index // 3) * (card_h + row_gap)
            self._draw_case_action_card(
                c,
                cx,
                cy,
                min(x1, cx + card_w),
                min(y1, cy + card_h),
                label,
                cost,
                detail,
                color,
                callback,
                primary,
                enabled,
                hotkey,
                tooltip,
                disabled_reason,
            )

    def _draw_case_action_card(self, c, x0, y0, x1, y1, label, cost, detail, color, callback, primary=False, enabled=True, hotkey="", tooltip="", disabled_reason=""):
        """Draw one larger in-card action with its visible AP/cost line."""

        hover = self._hover_key == f"case-action:{label}"
        fill = Palette.WHITE if enabled else Palette.PAPER_ALT
        outline = Palette.INK if enabled and hover else color if enabled else Palette.LINE
        tone = color if enabled else Palette.MUTED
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2 if hover else 1)
        c.create_rectangle(x0, y0, x0 + 4, y1, fill=tone, outline="")
        label_w = x1 - x0 - (52 if hotkey else 24)
        c.create_text(x0 + 12, y0 + 8, text=self._fit_px(label, 9, "bold", label_w), anchor="nw", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(9, "bold"))
        if hotkey:
            hk_x0 = x1 - 26
            c.create_rectangle(hk_x0, y0 + 7, x1 - 8, y0 + 25, fill=Palette.PAPER_ALT, outline=Palette.LINE)
            c.create_text((hk_x0 + x1 - 8) // 2, y0 + 10, text=hotkey, anchor="n", fill=Palette.INK if enabled else Palette.MUTED, font=self._font(7, "bold"))
        c.create_text(x0 + 12, y0 + 27, text=cost, anchor="nw", fill=tone, font=self._font(8, "bold"), width=x1 - x0 - 24)
        if not enabled and disabled_reason:
            c.create_text(x0 + 12, y1 - 18, text=self._fit_px(disabled_reason, 7, "normal", x1 - x0 - 24), anchor="nw", fill=Palette.MUTED, font=self._font(7))
        elif isinstance(detail, tuple):
            city, local = detail
            label_w = 34
            detail_w = max(40, x1 - x0 - label_w - 28)
            c.create_text(x0 + 12, y0 + 45, text="City", anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
            c.create_text(x0 + 12 + label_w, y0 + 45, text=self._fit_px(city, 7, "normal", detail_w), anchor="nw", fill=Palette.INK, font=self._font(7), width=detail_w)
            local_y = y0 + 64 if y1 - y0 >= 78 else y0 + 60
            c.create_text(x0 + 12, local_y, text="Local", anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
            c.create_text(x0 + 12 + label_w, local_y, text=self._fit_px(local, 7, "normal", detail_w), anchor="nw", fill=Palette.INK, font=self._font(7), width=detail_w)
        elif y1 - y0 >= 68:
            detail_lines = _fit_lines(detail, max(16, (x1 - x0 - 24) // 7), 3)
            c.create_text(x0 + 12, y0 + 44, text="\n".join(detail_lines), anchor="nw", fill=Palette.MUTED, font=self._font(7), width=x1 - x0 - 24)
        elif tooltip:
            c.create_text(x0 + 12, y1 - 18, text=self._fit_px(tooltip, 7, "normal", x1 - x0 - 24), anchor="nw", fill=Palette.MUTED, font=self._font(7))
        if enabled:
            self._add_target("case-action", label, (x0, y0, x1, y1), callback)

    def _draw_case_action(self, c, x0, y0, x1, y1, label, color, callback, primary=False, enabled=True):
        """Draw an in-card action button and register it."""

        hover = self._hover_key == f"case-action:{label}"
        fill = color if primary and enabled else Palette.PAPER_ALT
        text_color = Palette.WHITE if primary and enabled else color if enabled else Palette.MUTED
        outline = color if enabled and not hover else Palette.INK if enabled else Palette.LINE
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2 if hover else 1)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label, anchor="center", fill=text_color, font=self._font(8, "bold"), width=x1 - x0 - 8)
        if enabled:
            self._add_target("case-action", label, (x0, y0, x1, y1), callback)

    def _draw_ledger_rail(self, c, box):
        """Draw the slim city pulse rail for triage context."""

        x0, y0, x1, y1 = box
        if hasattr(c, "_record"):
            c._record("city-pulse", (x0, y0, x1, y1), {})
        _shadow_rect(c, x0 + 4, y0 + 6, x1 + 4, y1 + 6)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.LEDGER, outline=Palette.LINE, width=1)
        c.create_text(x0 + 14, y0 + 18, text="CITY PULSE", anchor="w", fill=Palette.INK, font=self._font(11, "bold"))
        c.create_text(x1 - 14, y0 + 18, text="OFFICE CONTEXT", anchor="e", fill=Palette.MUTED, font=self._font(7, "bold"))
        c.create_line(x0 + 14, y0 + 38, x1 - 14, y0 + 38, fill=Palette.LINE)

        inner_x0 = x0 + 14
        inner_x1 = x1 - 14
        self._ensure_lookups()
        by_label = self._ledger_by_label

        # Headline: one Office Standing gauge folds the four core metrics together.
        y = y0 + 50
        health = by_label.get("Office Standing")
        if health:
            tone = _tone_color(health.tone)
            c.create_text(inner_x0, y, text="OFFICE STANDING", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
            c.create_text(inner_x0, y + 18, text=_clip(health.value, 20), anchor="nw", fill=tone, font=self._font(14, "bold"))
            bar_y0 = y + 58
            pct = max(0, min(100, int(health.meter or 0)))
            c.create_rectangle(inner_x0, bar_y0, inner_x1, bar_y0 + 10, fill="#dce5e1", outline="")
            if pct:
                c.create_rectangle(inner_x0, bar_y0, inner_x0 + int((inner_x1 - inner_x0) * pct / 100), bar_y0 + 10, fill=tone, outline="")
            y = bar_y0 + 10 + 18

        c.create_line(inner_x0, y, inner_x1, y, fill=Palette.LINE)
        y += 12

        # Core-metric breakdown remains available, but compressed into a 2x2 stat grid.
        pulse = [by_label[name] for name in ("Activity", "Trust", "Friction", "Exposure") if name in by_label]
        if pulse:
            y = self._draw_pulse_grid(c, inner_x0, inner_x1, y, pulse) + 14

        if self.model.district_group_rows and y < y1 - 178:
            c.create_text(inner_x0, y, text="DISTRICT GROUPS", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
            y += 18
            for row in self.model.district_group_rows[:4]:
                if y + 38 > y1 - 190:
                    break
                y = self._draw_group_row(c, inner_x0, inner_x1, y, row) + 8

        if y < y1 - 90:
            y += 8
            self._draw_map_state_key(c, inner_x0, y, inner_x1, y1 - 12)

    def _draw_map_state_key(self, c, x0, y0, x1, y1):
        """Draw district and map-state symbology in the right rail."""

        c.create_text(x0, y0, text="MAP STATE", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
        y = y0 + 18
        rows = [row for row in self.model.map_legend_rows if row.group in MAP_STATE_KEY_GROUPS]
        last_group = ""
        for row in rows:
            if row.group != last_group:
                if y + 28 > y1:
                    break
                c.create_text(x0, y, text=row.group, anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
                field = LEGEND_GROUP_FIELDS.get(row.group)
                if field:
                    c.create_text(x0, y + 12, text=field, anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
                    y += 26
                else:
                    y += 14
                last_group = row.group
            if y + 24 > y1:
                break
            self._draw_compact_legend_row(c, x0, y, x1, row)
            y += 27

    def _draw_compact_legend_row(self, c, x0, y0, x1, row):
        """Draw one compact right-rail legend row."""

        c.create_rectangle(x0, y0 + 4, x0 + 12, y0 + 16, fill=row.swatch, outline=Palette.LINE)
        c.create_text(x0 + 20, y0, text=self._fit_px(row.label, 8, "bold", x1 - x0 - 20), anchor="nw", fill=Palette.INK, font=self._font(8, "bold"))
        c.create_text(x0 + 20, y0 + 14, text=self._fit_px(row.detail, 7, "normal", x1 - x0 - 20), anchor="nw", fill=Palette.MUTED, font=self._font(7))

    def _draw_pulse_grid(self, c, x0, x1, y, rows):
        """Draw Activity/Trust/Friction/Exposure as a compact 2x2 grid."""

        gap = 8
        cell_w = max(80, (x1 - x0 - gap) // 2)
        cell_h = 52
        for index, row in enumerate(rows[:4]):
            cx0 = x0 + (index % 2) * (cell_w + gap)
            cy0 = y + (index // 2) * (cell_h + gap)
            cx1 = min(x1, cx0 + cell_w)
            tone = _tone_color(row.tone)
            c.create_rectangle(cx0, cy0, cx1, cy0 + cell_h, fill=Palette.PAPER_ALT, outline=Palette.LINE)
            c.create_text(cx0 + 8, cy0 + 7, text=row.label.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
            c.create_text(cx1 - 8, cy0 + 7, text=_clip(row.value, 8), anchor="ne", fill=tone, font=self._font(10, "bold"))
            self._draw_sparkline(c, cx0 + 8, cy0 + 29, cx1 - 28, cy0 + 43, row.points, tone)
            c.create_text(cx1 - 8, cy0 + 30, text=_trend_marker(row.trend), anchor="ne", fill=tone, font=self._font(9, "bold"))
        return y + (cell_h * 2) + gap

    def _draw_group_row(self, c, x0, x1, y, row):
        """Draw one district group health row."""

        tone = _tone_color(row.tone)
        c.create_rectangle(x0, y, x1, y + 34, fill=Palette.PAPER_ALT, outline=Palette.LINE)
        if hasattr(c, "_record"):
            c._record("group-swatch", (x0 + 8, y + 10, x0 + 18, y + 20), {})
        c.create_rectangle(x0 + 8, y + 10, x0 + 18, y + 20, fill=row.swatch or Palette.LINE, outline=Palette.LINE)
        c.create_text(x0 + 26, y + 5, text=self._fit_px(row.label, 8, "bold", (x1 - x0) // 2), anchor="nw", fill=Palette.INK, font=self._font(8, "bold"))
        self._draw_sparkline(c, x1 - 62, y + 10, x1 - 22, y + 24, row.points, tone)
        c.create_text(x1 - 8, y + 5, text=_trend_marker(row.trend), anchor="ne", fill=tone, font=self._font(8, "bold"))
        c.create_text(x0 + 26, y + 20, text=self._fit_px(row.detail, 7, "normal", x1 - x0 - 90), anchor="nw", fill=Palette.MUTED, font=self._font(7))
        return y + 34

    def _draw_sparkline(self, c, x0, y0, x1, y1, points, color):
        """Draw a compact static sparkline from 0-100 point values."""

        if hasattr(c, "_record"):
            c._record("sparkline", (x0, y0, x1, y1), {})
        pts = tuple(points or ())
        if len(pts) < 2:
            c.create_text(x1, y0, text="?", anchor="ne", fill=color, font=self._font(8, "bold"))
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

    def _draw_pulse_row(self, c, x0, x1, y, row):
        """Draw one city pulse signal."""

        tone = _tone_color(row.tone)
        c.create_text(x0, y, text=row.label.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
        c.create_text(x1, y, text=_clip(row.value, 20), anchor="ne", fill=tone, font=self._font(9, "bold"))
        bar_y0 = y + 20
        pct = max(0, min(100, int(row.meter or 0)))
        c.create_rectangle(x0, bar_y0, x1, bar_y0 + 7, fill="#dce5e1", outline="")
        if pct:
            c.create_rectangle(x0, bar_y0, x0 + int((x1 - x0) * pct / 100), bar_y0 + 7, fill=tone, outline="")
        return bar_y0 + 7

    def _draw_receipt_metrics(self, c, x0, y0, x1, metrics):
        """Draw the compact metric strip pinned to the foot of the receipt."""

        if not metrics:
            return
        c.create_line(x0, y0 - 4, x1, y0 - 4, fill="#c4b798")
        mw = (x1 - x0) // max(1, len(metrics))
        for idx, (label, value) in enumerate(metrics):
            mx = x0 + idx * mw
            c.create_text(mx, y0, text=label, anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
            c.create_text(mx, y0 + 13, text=_clip(value, 9), anchor="nw", fill=Palette.INK, font=self._font(10, "bold"))

    def _draw_district_attribute_table(self, c, box):
        """Draw the bottom live district attribute table from the wireframe."""

        x0, y0, x1, y1 = box
        if hasattr(c, "_record"):
            c._record("district-table", (x0, y0, x1, y1), {})
        _shadow_rect(c, x0 + 3, y0 + 4, x1 + 3, y1 + 4)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.LINE)
        pad = 12
        inner_x0 = x0 + pad
        inner_x1 = x1 - pad
        c.create_text(inner_x0, y0 + 10, text="DISTRICT ATTRIBUTES", anchor="nw", fill=Palette.MUTED, font=self._font(8, "bold"))
        c.create_text(inner_x1, y0 + 10, text="LIVE MAP STATE", anchor="ne", fill=Palette.MUTED, font=self._font(7, "bold"))
        header_y = y0 + 34
        row_y = header_y + 22
        cols = (
            ("District", 0.00, 0.16),
            ("Type", 0.16, 0.30),
            ("Prosperity", 0.30, 0.47),
            ("Pressure", 0.47, 0.66),
            ("Community", 0.66, 0.84),
            ("Target", 0.84, 1.00),
        )
        width = max(1, inner_x1 - inner_x0)
        c.create_rectangle(inner_x0, header_y, inner_x1, header_y + 20, fill=Palette.PAPER_ALT, outline=Palette.LINE)
        for label, start, end in cols:
            cx = inner_x0 + int(width * start) + 8
            c.create_text(cx, header_y + 5, text=label.upper(), anchor="nw", fill=Palette.MUTED, font=self._font(7, "bold"))
            if end < 1.0:
                line_x = inner_x0 + int(width * end)
                c.create_line(line_x, header_y, line_x, y1 - 10, fill=Palette.LINE)

        rows = list(self.model.district_table_rows)
        available_h = max(0, y1 - row_y - 10)
        row_h = 22 if available_h < 120 else 24
        visible_count = max(1, min(len(rows), available_h // row_h)) if rows else 0
        for index, row in enumerate(rows[:visible_count]):
            ry0 = row_y + index * row_h
            ry1 = min(y1 - 10, ry0 + row_h)
            fill = Palette.NOTE_BLUE if row.selected else Palette.WHITE if index % 2 == 0 else Palette.PAPER_ALT
            c.create_rectangle(inner_x0, ry0, inner_x1, ry1, fill=fill, outline=Palette.LINE)
            if row.selected:
                c.create_rectangle(inner_x0, ry0, inner_x0 + 4, ry1, fill=Palette.BLUE, outline="")
            values = (
                row.district,
                row.district_type,
                row.prosperity,
                row.pressure,
                row.community,
                "TARGET" if row.selected else "",
            )
            for value, (_label, start, end) in zip(values, cols):
                cx = inner_x0 + int(width * start) + 8
                max_w = max(28, int(width * (end - start)) - 14)
                font = self._font(8, "bold") if row.selected and start == 0.0 else self._font(8)
                c.create_text(cx, ry0 + 5, text=self._fit_px(value, 8, "bold" if row.selected and start == 0.0 else "normal", max_w), anchor="nw", fill=Palette.INK, font=font)
        if rows and visible_count < len(rows):
            c.create_text(inner_x1, y1 - 18, text=f"+{len(rows) - visible_count} more", anchor="ne", fill=Palette.MUTED, font=self._font(7, "bold"))

    def _draw_menu_button(self, c, x0, y0, x1, y1):
        """Draw the compact utility menu trigger."""

        hover = self._hover_key == "session:Menu"
        fill = Palette.WHITE if hover or self._menu_open else Palette.PAPER
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=Palette.BLUE, width=2)
        mid = (y0 + y1) // 2
        line_x0 = x0 + 11
        line_x1 = x1 - 11
        for offset in (-5, 0, 5):
            c.create_line(line_x0, mid + offset, line_x1, mid + offset, fill=Palette.BLUE, width=2)
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
            ("New Game", Palette.BLUE, self.callbacks.new_game),
            ("Scorecard", Palette.GOLD, self.callbacks.scorecard),
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
        _shadow_rect(c, x0 + 5, y0 + 6, x1 + 5, y1 + 6)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.INK, width=2)
        for idx, (label, color, callback) in enumerate(entries):
            ry0 = y0 + idx * row_h
            ry1 = ry0 + row_h
            hover = self._hover_key == f"session:{label}"
            c.create_rectangle(x0, ry0, x1, ry1, fill=Palette.WHITE if hover else Palette.PAPER, outline=Palette.LINE)
            c.create_rectangle(x0, ry0, x0 + 5, ry1, fill=color, outline="")
            c.create_text(x0 + 14, (ry0 + ry1) // 2, text=label.upper(), anchor="w", fill=color, font=self._font(8, "bold"))
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
        fill = Palette.WHITE if hover else Palette.PAPER
        c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=color, width=2)
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=label.upper(), anchor="center", fill=color, font=self._font(8, "bold"))
        self._add_target("session", label, (x0, y0, x1, y1), callback)

    def _draw_start_help_overlay(self, c, width, height):
        """Draw the compact in-window start/help card."""

        ow = min(520, width - 80)
        oh = min(420, height - 120)
        x0 = (width - ow) // 2
        y0 = (height - oh) // 2
        x1 = x0 + ow
        y1 = y0 + oh
        _shadow_rect(c, x0 + 8, y0 + 10, x1 + 8, y1 + 10)
        c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER, outline=Palette.INK, width=2)
        c.create_rectangle(x0, y0, x1, y0 + 46, fill=Palette.BLUE, outline="")
        title = "PERMIT OFFICE"
        c.create_text(x0 + 22, y0 + 23, text=title, anchor="w", fill=Palette.PAPER, font=self._font(15, "bold"))
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
            c.create_text(body_x0, yy, text=section_title, anchor="nw", fill=Palette.INK, font=self._font(9, "bold"))
            yy = _text_bottom(c, body_x0 + 92, yy, text, self._font(8), Palette.MUTED, width=body_x1 - body_x0 - 92) + 8
        bw = 150
        self._draw_session_button(c, body_x0, y1 - 58, body_x0 + bw, y1 - 22, "New Game", Palette.BLUE, self.callbacks.new_game)
        self._draw_session_button(c, body_x0 + bw + 12, y1 - 58, body_x0 + 2 * bw + 12, y1 - 22, "Help", Palette.MUTED, self.callbacks.show_help)

    def _add_target(self, kind, ident, bbox, callback):
        """Record a clickable canvas rectangle for later event dispatch."""

        self._click_targets.append((kind, ident, bbox, callback))

    def _font(self, size, weight="normal"):
        """Return the Segoe UI font tuple used by the desk canvas."""

        return ("Segoe UI", size, weight)

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

    def _px_measurer(self, size, weight):
        """Return a cached Tk font ``measure`` callable, or None if unavailable."""

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
        font = cache[key]
        return font.measure if font is not None else None


def _draw_ruled_block(c, x0, y0, x1, y1, label, text, accent, font_factory, max_y=None):
    """Draw a labeled ruled-paper block, pre-wrapping text so it never overruns.

    ``max_y`` clamps the block bottom; the body is wrapped to the exact number of
    lines that fit, so canvas text can never spill past the block.
    """

    if max_y is not None:
        y1 = min(y1, max_y)
    if y1 - y0 < 24:
        y1 = y0 + 24
    c.create_rectangle(x0, y0, x1, y1, fill="#fbf3dc", outline="#d4c7aa")
    c.create_rectangle(x0, y0, x1, y0 + 20, fill="#efe3c8", outline="#d4c7aa")
    c.create_text(x0 + 8, y0 + 5, text=label, anchor="nw", fill=accent, font=font_factory(8, "bold"))
    for yy in range(y0 + 40, y1 - 6, 18):
        c.create_line(x0 + 8, yy, x1 - 8, yy, fill="#e3d7bd")
    line_w = max(16, (x1 - x0 - 22) // 7)
    max_lines = max(1, (y1 - (y0 + 28)) // 18)
    body = "\n".join(_fit_lines(text, line_w, max_lines))
    c.create_text(x0 + 10, y0 + 26, text=body, anchor="nw", fill=Palette.INK, font=font_factory(10))


def _draw_impact_buckets(c, x, y, width, buckets, font_factory, max_y=None):
    """Draw compact impact buckets in a two-column grid, stopping at ``max_y``."""

    if not buckets:
        return 0
    gap = 8
    bucket_h = 56
    col_w = max(118, (width - gap) // 2)
    drawn = 0
    for idx, bucket in enumerate(buckets):
        col = idx % 2
        row = idx // 2
        by0 = y + row * (bucket_h + 7)
        by1 = by0 + bucket_h
        if max_y is not None and by1 > max_y:
            break
        bx0 = x + col * (col_w + gap)
        bx1 = min(x + width, bx0 + col_w)
        tone = _tone_color(bucket.tone)
        c.create_rectangle(bx0, by0, bx1, by1, fill="#f6edd6", outline="#d4c7aa")
        c.create_rectangle(bx0, by0, bx0 + 5, by1, fill=tone, outline="")
        c.create_text(bx0 + 12, by0 + 6, text=bucket.label.upper(), anchor="nw", fill=Palette.MUTED, font=font_factory(7, "bold"))
        # Pre-wrap to a bounded two lines so the value never bleeds into the row below.
        value_lines = _fit_lines(bucket.value, max(18, (bx1 - bx0 - 24) // 6), 2)
        c.create_text(bx0 + 12, by0 + 19, text="\n".join(value_lines), anchor="nw", fill=Palette.INK, font=font_factory(8, "bold"))
        drawn = idx + 1
    rows = (drawn + 1) // 2
    return rows * bucket_h + max(0, rows - 1) * 7


def _shadow_rect(c, x0, y0, x1, y1):
    """Draw a simple rectangular paper shadow."""

    c.create_rectangle(x0, y0, x1, y1, fill=Palette.PAPER_SHADOW, outline="")


def _inside(x, y, bbox):
    """Return whether a point is inside a canvas bounding box."""

    x0, y0, x1, y1 = bbox
    return x0 <= x <= x1 and y0 <= y <= y1


def _clip(value, width):
    """Shorten text to a single normalized line for canvas rendering."""

    return shorten(" ".join(str(value or "").split()), width=width, placeholder="...")


def _deadline_day_time(value):
    """Return day and time tokens from the controller's office-clock text."""

    tokens = str(value or "").split()
    day = tokens[0] if tokens else ""
    time = next((token for token in reversed(tokens) if ":" in token), tokens[-1] if tokens else "")
    return day, time


def _fit_lines(value, line_width, max_lines):
    """Wrap text to a bounded number of lines for fixed-size canvas panels."""

    lines = wrap(" ".join(str(value or "").split()), width=line_width)
    if len(lines) <= max_lines:
        return lines
    return lines[: max_lines - 1] + [_clip(f"{lines[max_lines - 1]} ...", line_width)]


def _status_color(status):
    """Map docket or feature status to a palette color."""

    value = (status or "").lower()
    if value in ("open", "carried"):
        return Palette.BLUE
    if value in ("inspected", "settled", "maintained"):
        return Palette.GOLD
    if value in ("active", "approved", "responded", "enforced"):
        return Palette.GREEN
    if value in ("denied", "deferred", "failed"):
        return Palette.RED
    if value in ("week", "filed", "scorecard"):
        return Palette.BLUE if value != "scorecard" else Palette.GOLD
    return Palette.MUTED


def _risk_color(exposure):
    """Map inspection exposure bands to a palette color."""

    value = (exposure or "").lower()
    if value == "high":
        return Palette.RED
    if value == "medium":
        return Palette.GOLD
    if value == "low":
        return Palette.GREEN
    return Palette.MUTED


def _tone_color(tone):
    """Map ledger row tone names to palette colors."""

    if tone == "good":
        return Palette.GREEN
    if tone == "bad":
        return Palette.RED
    if tone == "watch":
        return Palette.GOLD
    return Palette.INK


def _trend_marker(trend):
    """Return a compact trend glyph for evidence cards."""

    value = (trend or "").lower()
    if value == "up":
        return "up"
    if value == "down":
        return "down"
    if value == "flat":
        return "flat"
    return "?"


def _trend_color(trend, tone="neutral"):
    """Return marker color from an explicit tone or the trend direction."""

    if tone and tone != "neutral":
        return _tone_color(tone)
    value = (trend or "").lower()
    if value == "up":
        return Palette.GREEN
    if value == "down":
        return Palette.RED
    if value == "unknown":
        return Palette.GOLD
    return Palette.MUTED
