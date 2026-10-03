"""Carry out redraw plans against the ArcGIS Pro map."""

from __future__ import annotations

import os

import arcpy

from ._perf import perf_active, perf_block, perf_enabled
from .map_layers import add_outputs_to_map, apply_ring_redraw, refresh_all, remove_outputs_from_map
from .messages import _log, _warn
from .redraw_plan import _redraw_plan
from .schema import DISTRICTS, LINES, POINTS, ZONES


def clear_output_selections(paths, messages=None, keep=()):
    """Clear nonempty base-layer selections without creating path-based aliases.

    ``keep`` names base layers the next case is about to overwrite with a
    NEW_SELECTION; clearing those first only buys one extra repaint per layer
    (AR18), so they are left alone. Under PERF one ``SELECT`` line names the
    kept and cleared layers.
    """

    keep = frozenset(keep or ())
    with perf_block("sel"):
        try:
            active_map = arcpy.mp.ArcGISProject("CURRENT").activeMap
        except Exception:
            return
        if active_map is None:
            return
        sources = {name: paths.get(key) for name, key in ((DISTRICTS, "districts"), (POINTS, "points"), (LINES, "lines"), (ZONES, "zones"))}
        kept = []
        cleared = []
        for layer in active_map.listLayers():
            name = getattr(layer, "name", "")
            if name not in sources or not sources[name]:
                continue
            try:
                if os.path.normcase(os.path.normpath(layer.dataSource)) != os.path.normcase(os.path.normpath(sources[name])):
                    continue
                if not layer.getSelectionSet():
                    continue
                if name in keep:
                    kept.append(name)
                    continue
                layer.setSelectionSet([], "NEW")
                cleared.append(name)
            except Exception:
                # Never turn an unresolved map layer into a GP feature-class input.
                continue
        if messages is not None and perf_enabled() and (kept or cleared):
            _log(messages, "SELECT", f"clear kept={sorted(kept)} cleared={sorted(cleared)}")


def rebuild_output_layers(paths, messages, layer_names=None, force_readd=False, dirty_scope=None, remove_scope_override=None, keep_selections=None):
    """Refresh (or, when forced, recreate) map layers after GDB edits.

    layer_names: optional iterable restricting the work to those names. None
    covers all four (plus district overlays). Unknown values pass through.

    force_readd: remove and re-add *every* in-scope layer from scratch. Needed
    when the layer set or symbology changes (e.g. a new game).

    keep_selections: base layers whose selection the caller is about to replace
    with a NEW_SELECTION; the selection clear skips them (AR18).

    The selection clear runs after the redraw, not before: a clear repaints its
    layer, and run ahead of that layer's requery the two drops landed back to
    back (zones ~1 s at week close, points twice on a decision). Right after the
    requery they overlap into one.

    The default district path is district-ring: one numeric predrawn district
    slot is re-added from the GDB, symbolized, made visible, then refreshed.
    This preserves the district re-add correctness requirement while avoiding
    the full district family remove/add cycle on every decision.
    ``force_readd=True`` still uses the full legacy path when the layer set or
    symbology changes. On Pro 3.7+ the district step first tries flipping the
    visible slot's definition query (see QUERY_FLIP_MIN_PRO in geometry.py).
    """

    plan = _redraw_plan(layer_names=layer_names, force_readd=force_readd, dirty_scope=dirty_scope)
    if plan.mode == "desk-only":
        _log(messages, "REBUILD", f"desk-only mode=desk-only dirty={dirty_scope}")
        return plan
    effective_layer_names = None if layer_names is None and plan.mode in ("force-readd", "district-readd") else set(plan.layer_names)
    perf_messages = None if perf_active() else messages
    with perf_block("rebuild", perf_messages):
        mode = plan.mode
        scope = "all" if effective_layer_names is None else f"targeted={sorted(effective_layer_names)}"
        dirty = dirty_scope or "layers"
        _log(messages, "REBUILD", f"{scope} mode={mode} dirty={dirty}")
        if plan.mode in ("district-readd", "refresh-only"):
            remove_scope = set(remove_scope_override) if remove_scope_override is not None else None if plan.remove_scope is None else set(plan.remove_scope)
            with perf_block("ring_redraw"):
                handled = apply_ring_redraw(
                    paths,
                    messages,
                    layer_names=effective_layer_names,
                    remove_scope=remove_scope,
                )
            if handled:
                if plan.clear_selections:
                    clear_output_selections(paths, messages, keep=keep_selections or ())
                return plan
            _warn(messages, "REBUILD", f"district-ring failed; falling back to {mode}")
        fallback_remove_scope = set(remove_scope_override) if remove_scope_override is not None else None if plan.remove_scope is None else set(plan.remove_scope)
        if fallback_remove_scope is not None or plan.mode == "force-readd":
            with perf_block("remove"):
                remove_outputs_from_map(messages, layer_names=fallback_remove_scope)
        with perf_block("add"):
            add_outputs_to_map(paths, messages, layer_names=effective_layer_names)
        with perf_block("refresh"):
            refresh_all(paths, messages, layer_names=effective_layer_names)
        if plan.clear_selections:
            clear_output_selections(paths, messages, keep=keep_selections or ())
    return plan
