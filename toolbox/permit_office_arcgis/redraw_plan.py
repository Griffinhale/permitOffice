"""Redraw planning: which map layers a command changes and how to redraw them.

ArcPy-free. ``map_redraw.rebuild_output_layers`` carries out these plans.
"""

from __future__ import annotations

from dataclasses import dataclass

DISTRICTS = "PermitDistricts"
POINTS = "PermitPoints"
LINES = "PermitLines"
ZONES = "PermitZones"

DIRTY_DESK_ONLY = "desk"
DIRTY_SELECTION_ONLY = "selection"
DIRTY_DISTRICTS = "districts"
FEATURE_READD_LAYERS = frozenset((POINTS,))
WEEK_CLOSE_READD_LAYERS = frozenset((DISTRICTS, POINTS, LINES, ZONES))
GEOMETRY_LAYER_BY_TYPE = {
    "POINT": POINTS,
    "LINE": LINES,
    "POLYGON": ZONES,
    "ZONE": ZONES,
}
_GEOM_TYPE_TO_LAYER = {"POINT": POINTS, "LINE": LINES, "POLYGON": ZONES}


@dataclass(frozen=True)
class HydratedRedrawPlan:
    """Map-work plan built from an authoritative decision result."""

    affected_cell_ids: tuple[str, ...] = ()
    feature_layer_key: str = ""
    feature_ids: tuple[str, ...] = ()
    refresh_names: frozenset[str] = frozenset()
    remove_readd_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RedrawPlan:
    """Concrete map work chosen for a dirty scope."""

    mode: str
    layer_names: frozenset[str]
    remove_scope: frozenset[str] | None = None
    clear_selections: bool = True


def hydrate_decision_redraw_plan(
    result,
    *,
    feature_layer_key: str = "",
    feature_layer_dirty: bool = True,
) -> HydratedRedrawPlan:
    """Return redraw intent for a decision result.

    Districts are always in the re-add scope; the decision's feature layer
    joins it only when the decision changed that layer.
    """

    feature_layer = _feature_layer_name(feature_layer_key) if feature_layer_dirty else ""
    affected = tuple(getattr(result, "affected_cell_ids", ()) or ())
    feature_ids = tuple(sorted((getattr(result, "feature_updates", {}) or {}).keys()))
    refresh = frozenset({feature_layer} if feature_layer else ())
    remove_readd = {DISTRICTS}
    if feature_layer:
        remove_readd.add(feature_layer)
    return HydratedRedrawPlan(
        affected_cell_ids=affected,
        feature_layer_key=feature_layer_key,
        feature_ids=feature_ids,
        refresh_names=refresh,
        remove_readd_names=frozenset(remove_readd),
    )


def layer_names_for_plan(plan: HydratedRedrawPlan) -> set[str]:
    """Return the combined layer scope expected by existing rebuild code."""

    return set(plan.refresh_names | plan.remove_readd_names)


def _feature_layer_name(layer_key: str) -> str:
    return {
        "points": POINTS,
        "lines": LINES,
        "zones": ZONES,
    }.get(str(layer_key or "").lower(), "")


def _decision_layer_names(item):
    """Return the layer set a decision on this item actually changes, or None.

    None signals "rebuild all" so unknown geometry types fall back safely.
    """

    feature_layer = _GEOM_TYPE_TO_LAYER.get(getattr(item, "geometry_type", None))
    if feature_layer is None:
        return None
    return {DISTRICTS, feature_layer}


UNRESOLVED_CASE_STATUSES = frozenset(("open", "inspected", "carried"))


def selection_layers_for_item(item) -> frozenset[str]:
    """Return the base layers ``select_case_context`` gives a NEW_SELECTION for this case.

    Districts when the case has targets (an unresolved case without targets gets
    a proposal with suggested targets, so it counts too), plus the feature layer
    holding its proposal or subject feature. NEW_SELECTION replaces, so a
    pre-redraw clear on these layers only adds a repaint (AR18).
    """

    if item is None:
        return frozenset()
    layers = set()
    if getattr(item, "target_cell_ids", None) or getattr(item, "status", "") in UNRESOLVED_CASE_STATUSES:
        layers.add(DISTRICTS)
    feature_layer = _GEOM_TYPE_TO_LAYER.get(str(getattr(item, "geometry_type", "") or "").upper())
    if feature_layer and (getattr(item, "item_id", "") or getattr(item, "subject_feature_id", "")):
        layers.add(feature_layer)
    return frozenset(layers)


def _feature_layer_key_for_item(item):
    return {
        "POINT": "points",
        "LINE": "lines",
        "POLYGON": "zones",
    }.get(getattr(item, "geometry_type", None), "")


def _week_close_redraw_layers(generated_items):
    """Return precise week-close redraw layers, falling back broad if unknown."""

    if generated_items is None:
        return WEEK_CLOSE_READD_LAYERS
    layers = {DISTRICTS}
    for item in generated_items:
        layer = GEOMETRY_LAYER_BY_TYPE.get(str(getattr(item, "geometry_type", "")).upper())
        if layer:
            layers.add(layer)
    return frozenset(layers)


def _normalize_layer_names(layer_names):
    """Return an immutable layer-name scope, preserving None as all layers."""

    if layer_names is None:
        return None
    return frozenset(layer_names)


def _redraw_plan(layer_names=None, force_readd=False, dirty_scope=None):
    """Return concrete map work for a dirty scope."""

    if dirty_scope in (DIRTY_DESK_ONLY, DIRTY_SELECTION_ONLY):
        return RedrawPlan("desk-only", frozenset(), clear_selections=False)
    names = _normalize_layer_names(layer_names)
    if force_readd:
        return RedrawPlan("force-readd", frozenset() if names is None else names, None if names is None else names)
    district_in_scope = names is None or DISTRICTS in names or dirty_scope == DIRTY_DISTRICTS
    explicit_names = names or frozenset()
    effective_names = names
    if district_in_scope and effective_names is None:
        effective_names = frozenset((DISTRICTS, POINTS, LINES, ZONES))
    if district_in_scope:
        remove_scope = frozenset((DISTRICTS,)) | (explicit_names & FEATURE_READD_LAYERS)
        return RedrawPlan("district-readd", effective_names or frozenset((DISTRICTS,)), remove_scope)
    return RedrawPlan("refresh-only", effective_names or frozenset())
