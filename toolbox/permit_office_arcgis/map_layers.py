"""Map layer add/remove, refresh, and the district/support display rings."""

from __future__ import annotations

import os
import time

import arcpy

from .layer_ring import DisplayLayerRing, DistrictLayerRing
from .messages import _log, _warn
from .schema import DISTRICTS, LINES, POINTS, ZONES
from .symbology_config import LAYER_FILE_NAMES, layer_file_path


# District overlay layers reuse the PermitDistricts feature class with different
# render fields so land-use type, prosperity, and buyout identity each get their
# own visual channel without one fill having to encode all three.
DISTRICT_PROSPERITY = "District Prosperity"
DISTRICT_IDENTITY = "District Identity"
# An untouched, unlabeled copy of the district fill drawn beneath it. A requery
# drops the fill for ~0.2 s; without this the interiors flash white (AR18).
# Nothing requeries or selects it, so it may show a stale type color briefly.
DISTRICT_UNDERLAY = "District Underlay"
# The same idea for lines, points and zones (AR24): Pro redraws feature layers
# one after another, so a week close left the map near-empty for ~0.5 s. Unlike
# the district copy these are requeried just before their live layer, because
# nothing covers them: a stale copy would keep showing removed proposals.
LINES_UNDERLAY = "Lines Underlay"
POINTS_UNDERLAY = "Points Underlay"
ZONES_UNDERLAY = "Zones Underlay"
PREDRAWN_LAYER_PREFIX = "Permit Office Predrawn"
PREDRAWN_POINTS_PREFIX = "Permit Office Predrawn Points"
PREDRAWN_LINES_PREFIX = "Permit Office Predrawn Lines"
PREDRAWN_ZONES_PREFIX = "Permit Office Predrawn Zones"

# Lowest Pro version where flipping a district slot's definition query was seen
# to show new GDB attribute values without flicker (AR5 spike, Pro 3.7). Older
# builds keep the ring rehydrate. Lower this only after the probe passes there.
QUERY_FLIP_MIN_PRO = (3, 7)
QUERY_FLIP_VALUES = ("1=1", "2=2")
_PRO_VERSION_CACHE: dict = {}


def refresh_all(paths, messages, layer_names=None):
    """Refresh known map layers; layer_names limits to a subset when given."""
    targets = [n for n in (DISTRICTS, POINTS, LINES, ZONES) if layer_names is None or n in layer_names]
    for name in targets:
        try:
            arcpy.RefreshLayer(name)
            _log(messages, "REFRESH", f"RefreshLayer({name!r}) OK")
        except Exception as exc:
            _warn(messages, "REFRESH", f"RefreshLayer({name!r}) failed: {exc}")


def output_layers_present():
    """Return True if any Permit Office output layer is on the active map.

    Used to decide whether to auto-resume a saved board: a map with none of our
    layers is treated as a fresh-start session even when the .gdb still holds a
    save. Conservative -- returns True unless it can positively confirm an active
    map that lacks every output layer, so a probe failure never suppresses a
    normal resume.
    """

    names = {DISTRICTS, POINTS, LINES, ZONES, DISTRICT_PROSPERITY, DISTRICT_IDENTITY}
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        active_map = aprx.activeMap
        if active_map is None:
            return True
        return any(layer.name in names for layer in active_map.listLayers())
    except Exception:
        return True


def ensure_active_map(messages, map_name="Permit Office"):
    """Ensure the project has an open map for Permit Office layer operations."""

    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
    except Exception as exc:
        _warn(messages, "MAP", f"ArcGISProject('CURRENT') unavailable; cannot ensure map: {exc}")
        return None
    active_map = getattr(aprx, "activeMap", None)
    if active_map is not None:
        return active_map
    try:
        maps = list(aprx.listMaps()) if hasattr(aprx, "listMaps") else []
    except Exception as exc:
        _warn(messages, "MAP", f"could not list project maps: {exc}")
        maps = []
    if maps:
        target = maps[0]
        _open_map_view(target, messages)
        _log(messages, "MAP", f"opened existing map: {getattr(target, 'name', 'Map')}")
        return target
    create_map = getattr(aprx, "createMap", None)
    if create_map is None:
        _warn(messages, "MAP", "project has no active map and ArcGISProject.createMap is unavailable")
        return None
    try:
        target = create_map(map_name)
        _open_map_view(target, messages)
        _log(messages, "MAP", f"created and opened map: {getattr(target, 'name', map_name)}")
        return target
    except Exception as exc:
        _warn(messages, "MAP", f"could not create map: {exc}")
        return None


def _open_map_view(map_obj, messages):
    opener = getattr(map_obj, "openView", None)
    if opener is None:
        return
    try:
        opener()
    except Exception as exc:
        _warn(messages, "MAP", f"could not open map view: {exc}")


def add_outputs_to_map(paths, messages, layer_names=None):
    """Add active game outputs to the map; layer_names limits to a subset when given."""
    try:
        active_map = ensure_active_map(messages)
        if active_map is None:
            return
        existing = {lyr.name: lyr for lyr in active_map.listLayers()}
        # (layer name, .lyrx key, paths key); the overlays read the districts source.
        wanted = [(DISTRICTS, "districts", "districts"), (POINTS, "points", "points"), (LINES, "lines", "lines"), (ZONES, "zones", "zones")]
        wanted = [w for w in wanted if layer_names is None or w[0] in layer_names]
        wanted += [(_feature_underlays()[name], key, path_key) for name, key, path_key in wanted if name in _feature_underlays()]
        if layer_names is None or DISTRICTS in layer_names:
            wanted += [
                (DISTRICT_PROSPERITY, "district_prosperity", "districts"),
                (DISTRICT_IDENTITY, "district_identity", "districts"),
                (DISTRICT_UNDERLAY, "districts", "districts"),
            ]
        for name, key, path_key in wanted:
            if name in existing:
                continue
            # One layer that will not load must not keep the others off the map.
            try:
                lyr = _add_styled_layer(active_map, paths[path_key], key, messages)
            except Exception as exc:
                _warn(messages, "MAP", f"add {name} failed: {exc}")
                continue
            lyr.name = name
            if name == DISTRICT_UNDERLAY or name in _feature_underlays().values():
                try:
                    lyr.showLabels = False
                except Exception as exc:
                    _warn(messages, "MAP", f"{name} labels stay on: {exc}")
            if name in _feature_underlays().values():
                _turn_off_selection(lyr, name, messages)
            existing[name] = lyr
            _log(messages, "MAP", f"added {name}")
        _order_output_layers(active_map, existing)
    except Exception as exc:
        _warn(messages, "MAP", f"add outputs failed: {exc}")


def _add_styled_layer(active_map, data_path, layer_key, messages):
    """Add a layer for data_path from its shipped .lyrx, pointed at this save.

    Raises when the .lyrx is missing or cannot be pointed at data_path, so no
    layer is left unstyled or drawing the export's data (ruling D7).
    """

    layer_file = layer_file_path(layer_key)
    if layer_file is None:
        raise RuntimeError(f"missing toolbox/layers/{LAYER_FILE_NAMES.get(layer_key, layer_key + '.lyrx')}")
    layer = active_map.addLayer(arcpy.mp.LayerFile(layer_file))[0]
    problem = _point_layer_at(layer, data_path)
    if problem is None:
        _log(messages, "SYM", f"styled {layer_key} from {os.path.basename(layer_file)}")
        return layer
    active_map.removeLayer(layer)
    raise RuntimeError(f"could not point {os.path.basename(layer_file)} at {data_path}: {problem}")


def _point_layer_at(layer, data_path):
    """Repoint a layer loaded from a .lyrx at this save's feature class.

    Edits the layer's CIM data connection rather than calling
    updateConnectionProperties, which on Pro 3.7 raised a bare AttributeError
    on most adds (AR20 probe, 22 of 24) while the CIM edit pointed every layer.
    Returns None when the layer now reads data_path, else the reason it does
    not; the source is checked after because Pro can skip a bad edit quietly.
    """

    workspace, dataset = os.path.split(data_path)
    try:
        cim = layer.getDefinition("V3")
        connection = cim.featureTable.dataConnection
        connection.workspaceConnectionString = f"DATABASE={workspace}"
        connection.workspaceFactory = "FileGDB"
        connection.dataset = dataset
        layer.setDefinition(cim)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}".rstrip(": ")
    if _same_source(layer, data_path):
        return None
    try:
        source = layer.dataSource
    except Exception as exc:
        source = f"dataSource raised {type(exc).__name__}"
    return f"layer still reads {source}"


def remove_outputs_from_map(messages, layer_names=None):
    """Remove stale Permit Office layers; layer_names limits to a subset when given."""
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        active_map = aprx.activeMap
        if active_map is None:
            return
        base_outputs = {DISTRICTS, POINTS, LINES, ZONES}
        # The district overlays are tied to the districts source, so they clear
        # whenever districts (or a full refresh) are being removed.
        overlay_outputs = {DISTRICT_PROSPERITY, DISTRICT_IDENTITY, DISTRICT_UNDERLAY}
        underlays = _feature_underlays()
        if layer_names is None:
            output_names = base_outputs | overlay_outputs | set(underlays.values())
        else:
            output_names = (base_outputs & set(layer_names))
            if DISTRICTS in layer_names:
                output_names |= overlay_outputs
            # A feature copy goes with its live layer, as the district one does.
            output_names |= {underlays[name] for name in layer_names if name in underlays}
        removed = 0
        for layer in list(active_map.listLayers()):
            layer_name = getattr(layer, "name", None)
            remove_predrawn = (layer_names is None or DISTRICTS in set(layer_names or ())) and _is_predrawn_district_layer_name(layer_name or "")
            remove_feature_ring = _should_remove_predrawn_feature_layer(layer_name or "", layer_names)
            if layer_name in output_names or remove_predrawn or remove_feature_ring:
                active_map.removeLayer(layer)
                removed += 1
        if removed:
            _log(messages, "MAP", f"removed {removed} stale Permit Office layer(s)")
    except Exception as exc:
        _warn(messages, "MAP", f"remove stale outputs failed: {exc}")


def _active_map():
    """Return the current active ArcGIS map, or None when unavailable."""

    aprx = arcpy.mp.ArcGISProject("CURRENT")
    return aprx.activeMap


def _layers_by_name(active_map):
    """Return active map layers keyed by their display name."""

    if active_map is None:
        return {}
    return {getattr(layer, "name", ""): layer for layer in active_map.listLayers()}


def _log_redraw(messages, path, status, started, detail=""):
    elapsed = time.perf_counter() - started
    suffix = f" {detail}" if detail else ""
    _log(messages, "REDRAW", f"path={path} status={status} elapsed={elapsed:.3f}{suffix}")


class _PhaseTimer:
    def __init__(self):
        self._last = time.perf_counter()
        self.parts = []

    def mark(self, name):
        now = time.perf_counter()
        self.parts.append((name, now - self._last))
        self._last = now

    def summary(self):
        return " ".join(f"{name}={elapsed:.3f}" for name, elapsed in self.parts)


def _set_definition_query(layer, definition_query):
    if definition_query is None:
        return
    try:
        layer.definitionQuery = definition_query
    except Exception:
        pass


def _is_predrawn_district_layer_name(name):
    return name.startswith(PREDRAWN_LAYER_PREFIX) and not _is_predrawn_feature_layer_name(name)


def _is_predrawn_feature_layer_name(name):
    return any(name.startswith(prefix) for prefix in _feature_ring_prefixes().values())


def _should_remove_predrawn_feature_layer(name, layer_names):
    if not name:
        return False
    if layer_names is None:
        return _is_predrawn_feature_layer_name(name)
    scoped = set(layer_names or ())
    return any(layer in scoped and name.startswith(prefix) for layer, prefix in _feature_ring_prefixes().items())


def _is_ring_slot_name(name):
    suffix = name.removeprefix(PREDRAWN_LAYER_PREFIX).strip()
    return suffix.isdigit()


def _feature_layer_scope(layer_names):
    feature_layers = frozenset((POINTS, LINES, ZONES))
    if layer_names is None:
        return set(feature_layers)
    return set(layer_names) & feature_layers


def _refresh_feature_scope(paths, messages, layer_names, remove_scope=None, phase_marker=None):
    """Redraw in-scope feature layers, cheapest proven path first.

    A layer with no visible ring slot requeries its visible base layer in place
    on Pro 3.7+ instead of seeding a ring: the seed adds a slot, hides the base
    and calls RefreshLayer, which AR18 run 7 recorded as a double points drop
    with stray symbols. Below 3.7 the ring seed is unchanged.

    Every query toggle runs back to back once all layers are resolved, so at
    week close the lines, points and zones requeries overlap (AR21).
    """

    phase_marker = phase_marker or (lambda _name: None)
    feature_scope = _feature_layer_scope(layer_names)
    if not feature_scope:
        return
    # Copies first, whichever path the live layers take below (flip, ring,
    # readd, refresh): they redraw while the live layers still show the old picture.
    _requery_feature_underlays(paths, messages, feature_scope)
    phase_marker("feature_underlays")
    readd_scope = feature_scope & set(remove_scope or ())
    fallback_readd = set()
    toggled = _toggle_feature_queries_together(paths, messages, readd_scope)
    for layer_name, path in toggled:
        phase_marker(f"feature_{layer_name}_{path}")
    for layer_name in sorted(readd_scope - {name for name, _path in toggled}):
        if not _rehydrate_feature_display_ring(paths, messages, layer_name):
            fallback_readd.add(layer_name)
        phase_marker(f"feature_{layer_name}_ring_rehydrate")
    if fallback_readd:
        remove_outputs_from_map(messages, layer_names=fallback_readd)
        add_outputs_to_map(paths, messages, layer_names=fallback_readd)
        phase_marker("feature_fallback_readd")
    refresh_scope = (feature_scope - readd_scope) | fallback_readd
    if refresh_scope:
        refresh_all(paths, messages, layer_names=refresh_scope)
        for layer_name in sorted(refresh_scope):
            phase_marker(f"feature_{layer_name}_refresh")


def _rehydrate_feature_display_ring(paths, messages, layer_name):
    prefix = _feature_ring_prefixes().get(layer_name)
    path_key = _feature_path_keys().get(layer_name)
    layer_key = _feature_layer_keys().get(layer_name)
    if not prefix or not path_key or not layer_key:
        return False
    active_map = ensure_active_map(messages)
    if active_map is None:
        return False

    try:
        ring = DisplayLayerRing(
            active_map,
            prefix=prefix,
            arcpy_module=arcpy,
            style_copier=lambda layer: _set_definition_query(layer, "1=1"),
            layer_adder=lambda path: _add_styled_layer(active_map, path, layer_key, messages),
        )
        ring.prepare_and_swap(paths[path_key])
        _place_ring_slots_above_base(active_map)
        _hide_base_feature_layer(active_map.listLayers(), layer_name)
        return True
    except Exception as exc:
        _warn(messages, "REDRAW", f"feature-ring {layer_name} failed: {exc}")
        return False


def _feature_requery_target(layers, paths, layer_name):
    """Return the visible layer a query toggle should requery, or None.

    A visible ring slot reading this save wins, then a visible base layer
    reading it. None sends the layer to the ring rehydrate.
    """

    prefix = _feature_ring_prefixes().get(layer_name)
    path_key = _feature_path_keys().get(layer_name)
    if not prefix or not path_key:
        return None
    for layer in layers:
        name = getattr(layer, "name", "")
        suffix = name.removeprefix(prefix + " ")
        if name.startswith(prefix + " ") and suffix.isdigit() and bool(getattr(layer, "visible", False)):
            if _same_source(layer, paths[path_key]):
                return layer
            break
    base = next((layer for layer in layers if getattr(layer, "name", "") == layer_name), None)
    if base is not None and bool(getattr(base, "visible", False)) and _same_source(base, paths[path_key]):
        return base
    return None


def _toggle_feature_queries_together(paths, messages, layer_names):
    """Requery in-scope feature layers with their query toggles back to back.

    Resolving a layer (active map, layer list, source check) cost ~0.2 s per
    layer, so toggling each right after its own lookup staggered the drops
    (AR18 run11 D3). All lookups and base hides run first. Returns
    (layer_name, path) for each toggled layer; the rest need a rehydrate.
    """

    if not layer_names or not _query_flip_supported():
        return []
    active_map = ensure_active_map(messages)
    if active_map is None:
        return []
    layers = list(active_map.listLayers())
    targets = []
    for layer_name in sorted(layer_names):
        target = _feature_requery_target(layers, paths, layer_name)
        if target is None:
            continue
        on_ring = getattr(target, "name", "") != layer_name
        if on_ring:
            _hide_base_feature_layer(layers, layer_name)
        targets.append((layer_name, target, "ring_refresh" if on_ring else "base_query"))
    errors = [_flip_feature_query(target) for _layer_name, target, _path in targets]
    toggled = []
    for (layer_name, target, path), error in zip(targets, errors):
        if error is None:
            _log(messages, "REDRAW", f"feature-query target={target.name!r}")
            toggled.append((layer_name, path))
        else:
            _warn(messages, "REDRAW", f"feature-query {layer_name} failed: {error}")
    return toggled


def _requery_feature_underlays(paths, messages, layer_names):
    """Toggle the query of each in-scope feature copy reading this save."""

    try:
        active_map = ensure_active_map(messages)
        layers = {getattr(layer, "name", ""): layer for layer in active_map.listLayers()} if active_map is not None else {}
    except Exception as exc:
        _warn(messages, "REDRAW", f"feature underlays skipped: {exc}")
        return
    path_keys = _feature_path_keys()
    for layer_name in sorted(layer_names):
        copy = layers.get(_feature_underlays().get(layer_name, ""))
        if copy is None or not _same_source(copy, paths[path_keys[layer_name]]):
            continue
        error = _flip_feature_query(copy)
        if error is None:
            _log(messages, "REDRAW", f"feature-query target={copy.name!r}")
        else:
            _warn(messages, "REDRAW", f"feature-query {copy.name} failed: {error}")


def _turn_off_selection(layer, name, messages):
    """Keep map clicks from selecting a copy layer's features."""

    try:
        cim = layer.getDefinition("V3")
        cim.selectable = False
        layer.setDefinition(cim)
    except Exception as exc:
        _warn(messages, "MAP", f"{name} stays selectable: {exc}")


def _flip_feature_query(layer):
    """Toggle a no-op marker on a layer's query; return the error, or None."""

    try:
        # RefreshLayer invalidates every visible layer in the containing view.
        # Change only this layer's query, preserving any existing filter.
        current = layer.definitionQuery or ""
        marker = " AND 271828=271828"
        if current.endswith(marker) and current.startswith("("):
            layer.definitionQuery = current[1:-len(marker)-1]
        else:
            layer.definitionQuery = f"({current or '1=1'}){marker}"
        return None
    except Exception as exc:
        return exc


def _toggle_feature_query(layer, messages, layer_name):
    """Toggle a no-op marker on a layer's query so Pro requeries just that layer."""

    error = _flip_feature_query(layer)
    if error is not None:
        _warn(messages, "REDRAW", f"feature-query {layer_name} failed: {error}")
        return False
    _log(messages, "REDRAW", f"feature-query target={layer.name!r}")
    return True


def _feature_ring_prefixes():
    return {
        POINTS: PREDRAWN_POINTS_PREFIX,
        LINES: PREDRAWN_LINES_PREFIX,
        ZONES: PREDRAWN_ZONES_PREFIX,
    }


def _feature_underlays():
    return {
        LINES: LINES_UNDERLAY,
        POINTS: POINTS_UNDERLAY,
        ZONES: ZONES_UNDERLAY,
    }


def _feature_path_keys():
    return {
        POINTS: "points",
        LINES: "lines",
        ZONES: "zones",
    }


def _feature_layer_keys():
    return {
        POINTS: "points",
        LINES: "lines",
        ZONES: "zones",
    }


def _hide_base_feature_layer(layers, layer_name):
    for layer in layers:
        if getattr(layer, "name", "") == layer_name:
            try:
                layer.visible = False
            except Exception:
                pass


def _pro_version():
    """Return the running Pro version as an int tuple, or () when unknown."""

    if "version" not in _PRO_VERSION_CACHE:
        try:
            text = str(arcpy.GetInstallInfo().get("Version", ""))
            _PRO_VERSION_CACHE["version"] = tuple(int(part) for part in text.split(".")[:2] if part.isdigit())
        except Exception:
            _PRO_VERSION_CACHE["version"] = ()
    return _PRO_VERSION_CACHE["version"]


def _query_flip_supported():
    """Return True when this Pro build is proven to requery on a query flip."""

    version = _pro_version()
    return bool(version) and version >= QUERY_FLIP_MIN_PRO


def _same_source(layer, path):
    """Return True when a layer reads the given feature class."""

    try:
        source = layer.dataSource
    except Exception:
        return False
    return os.path.normcase(os.path.normpath(source or "")) == os.path.normcase(os.path.normpath(path))


def _flip_visible_district_slot(active_map, district_path, messages):
    """Flip the visible ring slot's query so Pro redraws new attribute values.

    Returns the flipped layer, or None when there is no visible slot reading
    this save's districts (callers then use the ring rehydrate).
    """

    for layer in active_map.listLayers():
        name = getattr(layer, "name", "")
        if not (_is_ring_slot_name(name) and bool(getattr(layer, "visible", False))):
            continue
        if not _same_source(layer, district_path):
            return None
        try:
            current = layer.definitionQuery or ""
            layer.definitionQuery = QUERY_FLIP_VALUES[1] if current == QUERY_FLIP_VALUES[0] else QUERY_FLIP_VALUES[0]
            return layer
        except Exception as exc:
            _warn(messages, "REDRAW", f"district query flip failed on {name!r}: {exc}")
            return None
    return None


def _flip_visible_base_district_layer(active_map, district_path, messages):
    """Requery a visible PermitDistricts layer in place; return it, or None."""

    for layer in active_map.listLayers():
        if getattr(layer, "name", "") != DISTRICTS:
            continue
        if not bool(getattr(layer, "visible", False)) or not _same_source(layer, district_path):
            return None
        return layer if _toggle_feature_query(layer, messages, DISTRICTS) else None
    return None


def _apply_district_ring_redraw(paths, messages, layer_names, remove_scope=None):
    started = time.perf_counter()
    phases = _PhaseTimer()
    active_map = ensure_active_map(messages)
    if active_map is None:
        _log_redraw(messages, "district-ring", "no-active-map", started)
        return

    if _query_flip_supported():
        flipped = _flip_visible_district_slot(active_map, paths["districts"], messages)
        if flipped is None:
            # No ring slot yet (a fresh game): requery the base layer in place.
            # Seeding a slot calls RefreshLayer, which flashes the whole map.
            flipped = _flip_visible_base_district_layer(active_map, paths["districts"], messages)
        phases.mark("query_flip")
        if flipped is not None:
            if getattr(flipped, "name", "") != DISTRICTS:
                _hide_non_ring_district_family(active_map, keep_overlays=True)
            _flip_district_overlays(active_map, paths["districts"], messages)
            _place_ring_slots_above_base(active_map)
            phases.mark("hide_base_districts")
            _refresh_feature_scope(paths, messages, layer_names, remove_scope=remove_scope, phase_marker=phases.mark)
            phases.mark("feature_layers")
            _log_redraw(messages, "district-flip", "ok", started, f"target={getattr(flipped, 'name', '')!r} {phases.summary()}")
            return

    ring = DistrictLayerRing(
        active_map,
        arcpy_module=arcpy,
        style_copier=lambda layer: _set_definition_query(layer, "1=1"),
        phase_marker=phases.mark,
        layer_adder=lambda path: _add_styled_layer(active_map, path, "districts", messages),
    )
    phases.mark("ring_discovery")
    prepared = ring.prepare_and_swap(paths["districts"])
    phases.mark("prepare_visibility_swap")
    _place_ring_slots_above_base(active_map)
    _hide_non_ring_district_family(active_map)
    phases.mark("hide_base_districts")
    _refresh_feature_scope(paths, messages, layer_names, remove_scope=remove_scope, phase_marker=phases.mark)
    phases.mark("feature_layers")
    _log_redraw(messages, "district-ring", "ok", started, f"target={getattr(prepared, 'name', '')!r} {phases.summary()}")


def apply_ring_redraw(paths, messages, layer_names=None, remove_scope=None):
    """Apply production display-ring redraw without raising into gameplay.

    Returns True when the path handled the redraw request, or False when callers
    should fall back to the legacy remove/add/refresh path.
    """

    try:
        if layer_names is not None and DISTRICTS not in layer_names:
            _refresh_feature_scope(paths, messages, layer_names, remove_scope=set(layer_names))
            return True
        _apply_district_ring_redraw(paths, messages, layer_names, remove_scope=remove_scope)
        return True
    except Exception as exc:
        _warn(messages, "REDRAW", f"ring redraw failed: {exc}")
        return False


def _hide_non_ring_district_family(active_map, keep_overlays=False):
    """Hide district layers superseded by the active predrawn ring slot.

    The ring rehydrate cannot refresh the prosperity/identity overlays, so they
    hide; the Pro 3.7+ query flip refreshes them and passes keep_overlays.
    """

    names = {DISTRICTS} if keep_overlays else {DISTRICTS, DISTRICT_PROSPERITY, DISTRICT_IDENTITY}
    for layer in active_map.listLayers():
        layer_name = getattr(layer, "name", "")
        legacy_predrawn = _is_predrawn_district_layer_name(layer_name) and not _is_ring_slot_name(layer_name)
        if layer_name in names or legacy_predrawn:
            try:
                layer.visible = False
            except Exception:
                pass


def _flip_district_overlays(active_map, district_path, messages):
    """Flip and show the prosperity/identity overlays so they requery fresh values."""

    for layer in active_map.listLayers():
        name = getattr(layer, "name", "")
        if name not in (DISTRICT_PROSPERITY, DISTRICT_IDENTITY) or not _same_source(layer, district_path):
            continue
        try:
            current = layer.definitionQuery or ""
            layer.definitionQuery = QUERY_FLIP_VALUES[1] if current == QUERY_FLIP_VALUES[0] else QUERY_FLIP_VALUES[0]
            layer.visible = True
        except Exception as exc:
            _warn(messages, "REDRAW", f"overlay query flip failed on {name!r}: {exc}")


def _place_ring_slots_above_base(active_map):
    """Keep each ring slot directly above its hidden base layer.

    addDataFromPath puts a new polygon layer above the existing polygons, so a
    district slot added mid-game would otherwise cover the Zones layer.
    """

    move = getattr(active_map, "moveLayer", None)
    if move is None:
        return
    prefixes = {DISTRICTS: PREDRAWN_LAYER_PREFIX, **_feature_ring_prefixes()}
    for base_name, prefix in prefixes.items():

        def is_slot(layer, prefix=prefix):
            name = getattr(layer, "name", "")
            return name.startswith(prefix) and name.removeprefix(prefix).strip().isdigit()

        layers = list(active_map.listLayers())
        base = next((layer for layer in layers if getattr(layer, "name", "") == base_name), None)
        if base is None:
            continue
        for slot in [layer for layer in layers if is_slot(layer)]:
            layers = list(active_map.listLayers())
            index, base_index = layers.index(slot), layers.index(base)
            # In place when only sibling slots sit between this slot and the base.
            if index < base_index and all(is_slot(other) for other in layers[index + 1:base_index]):
                continue
            try:
                move(base, slot, "BEFORE")
            except Exception:
                pass


def _order_output_layers(active_map, existing):
    """Draw district colors as the base, identity/prosperity overlays just above
    it, feature lines/points/zones on top, each over its copy, and the district
    underlay beneath all."""
    ordered_names = [
        LINES, LINES_UNDERLAY, POINTS, POINTS_UNDERLAY, ZONES, ZONES_UNDERLAY,
        DISTRICT_IDENTITY, DISTRICT_PROSPERITY, DISTRICTS, DISTRICT_UNDERLAY,
    ]
    if all(existing.get(name) for name in (LINES, POINTS, ZONES, DISTRICTS)):
        present = [existing[name] for name in ordered_names if existing.get(name) is not None]
        try:
            for reference_layer, move_layer in zip(present, present[1:]):
                active_map.moveLayer(reference_layer, move_layer, "AFTER")
        except Exception:
            pass
    # Above the fill the 90%-opaque underlay would hide the live colors, so it
    # goes directly beneath PermitDistricts even when the full order is skipped.
    pairs = [(DISTRICTS, DISTRICT_UNDERLAY)] + list(_feature_underlays().items())
    for live_name, copy_name in pairs:
        if existing.get(live_name) is not None and existing.get(copy_name) is not None:
            try:
                active_map.moveLayer(existing[live_name], existing[copy_name], "AFTER")
            except Exception:
                pass
