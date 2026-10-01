"""Layer symbology, labels, and transparency applied in code."""

from __future__ import annotations

from .messages import _log, _warn
from .symbology_config import (
    LAYER_TRANSPARENCY,
    RENDER_FIELD_BY_LAYER_KEY,
    SYMBOLS_BY_FIELD,
    apply_default_symbol_style,
    apply_symbol_style,
    layer_file_path,
)


def _style_in_code(layer, key, messages):
    """Style a layer in code; a no-op for keys styled by a shipped .lyrx."""

    if layer_file_path(key) is not None:
        return
    _apply_code_style(layer, key, messages)


def _apply_code_style(layer, key, messages):
    _tune_layer_visibility(layer, key)
    _configure_labels(layer, key)
    apply_simple_symbology(layer, key, messages)


def apply_simple_symbology(layer, key, messages):
    """Apply unique-value symbology when the layer supports it."""

    try:
        if not layer.supports("SYMBOLOGY"):
            return
        field_name = RENDER_FIELD_BY_LAYER_KEY.get(key, "display_state")
        sym = layer.symbology
        sym.updateRenderer("UniqueValueRenderer")
        field_set_via_cim = False
        field_set, last_error = _set_unique_value_renderer_field(sym.renderer, field_name)
        if not field_set:
            direct_error = last_error
            field_set, cim_error = _set_unique_value_renderer_field_with_cim(layer, sym, field_name)
            field_set_via_cim = field_set
            last_error = cim_error if field_set else direct_error or cim_error
        if not field_set:
            _warn(
                messages,
                "SYM",
                "unique-value symbology skipped for "
                f"{getattr(layer, 'name', key)}: renderer field API unavailable"
                f" ({last_error})",
            )
            return
        try:
            sym.renderer.useDefaultSymbol = True
        except Exception:
            pass
        if not field_set_via_cim:
            _configure_unique_value_renderer(sym.renderer, field_name, key)
            layer.symbology = sym
        else:
            _try_configure_layer_unique_value_items(layer, field_name, key)
        _log(messages, "SYM", f"set {field_name} unique-value symbology on {getattr(layer, 'name', key)}")
    except Exception as exc:
        _warn(messages, "SYM", f"symbology setup skipped for {getattr(layer, 'name', key)}: {exc}")


def _set_unique_value_renderer_field(renderer, field_name):
    """Set a unique-value renderer field across ArcGIS Pro API variants."""

    last_error = None
    attempts = (
        ("fields", [field_name]),
        ("field", field_name),
        ("fields", (field_name,)),
    )
    for attr, value in attempts:
        if attr == "field" and not _renderer_has_attr(renderer, attr):
            continue
        try:
            setattr(renderer, attr, value)
            return True, None
        except Exception as exc:
            last_error = exc
    return False, last_error or "no supported field setter"


def _renderer_has_attr(renderer, attr):
    """Return whether an ArcGIS renderer exposes an attribute safely."""

    try:
        getattr(renderer, attr)
    except Exception:
        return False
    return True


def _set_unique_value_renderer_field_with_cim(layer, sym, field_name):
    """Fallback through CIM when arcpy.mp renderer properties are unavailable."""

    last_error = None
    if not hasattr(layer, "getDefinition") or not hasattr(layer, "setDefinition"):
        return False, "CIM definition API unavailable"
    try:
        layer.symbology = sym
    except Exception as exc:
        last_error = exc
    for cim_version in ("V3", "V2"):
        try:
            # ArcGIS Pro has shipped renderer field setters under different
            # arcpy.mp surfaces; CIM keeps this path working across versions.
            definition = layer.getDefinition(cim_version)
            renderer = getattr(definition, "renderer", None)
            if renderer is None:
                last_error = "CIM renderer unavailable"
                continue
            _set_cim_attr(renderer, ("fields", "Fields"), [field_name])
            _try_set_cim_attr(renderer, ("useDefaultSymbol", "UseDefaultSymbol"), True)
            _try_set_cim_attr(renderer, ("isDefaultSymbolVisible", "IsDefaultSymbolVisible"), True)
            layer.setDefinition(definition)
            return True, None
        except Exception as exc:
            last_error = exc
    return False, last_error or "CIM renderer field setter unavailable"


def _set_cim_attr(target, names, value):
    """Set the first matching CIM attribute, or the first name as a fallback."""

    for name in names:
        try:
            getattr(target, name)
            setattr(target, name, value)
            return
        except AttributeError:
            continue
    setattr(target, names[0], value)


def _try_set_cim_attr(target, names, value):
    """Best-effort CIM attribute write used for optional renderer flags."""

    try:
        _set_cim_attr(target, names, value)
    except Exception:
        pass


def _try_configure_layer_unique_value_items(layer, field_name, key):
    """Best-effort item styling after a CIM renderer-field fallback."""

    try:
        sym = layer.symbology
        if not _renderer_uses_field(sym.renderer, field_name):
            return
        _configure_unique_value_renderer(sym.renderer, field_name, key)
        layer.symbology = sym
    except Exception:
        pass


def _configure_unique_value_renderer(renderer, field_name, key=None):
    """Seed and style known unique-value classes when ArcGIS exposes item APIs."""
    try:
        renderer.useDefaultSymbol = True
    except Exception:
        pass
    _add_unique_values(renderer, field_name)
    _style_default_symbol(renderer, key)
    _style_unique_value_items(renderer, field_name, key)


def _add_unique_values(renderer, field_name):
    """Seed expected unique-value classes when the renderer supports it."""

    if not hasattr(renderer, "addValues"):
        return
    heading = field_name
    try:
        groups = getattr(renderer, "groups", None)
        if groups:
            heading = groups[0].heading or heading
    except Exception:
        pass
    try:
        renderer.addValues({heading: list(SYMBOLS_BY_FIELD.get(field_name, {}))})
    except Exception:
        pass


def _style_default_symbol(renderer, key):
    """Apply the configured fallback style to a renderer default symbol."""

    for attr in ("defaultSymbol", "default_symbol"):
        try:
            symbol = getattr(renderer, attr)
        except Exception:
            continue
        if symbol is not None:
            apply_default_symbol_style(symbol, key)
            return


def _style_unique_value_items(renderer, field_name, key=None):
    """Apply configured labels and symbols to known unique-value items."""

    symbol_map = SYMBOLS_BY_FIELD.get(field_name, {})
    try:
        groups = renderer.groups
    except Exception:
        return
    for group in groups or []:
        for item in getattr(group, "items", []) or []:
            value = _unique_value_item_value(item)
            if value not in symbol_map:
                continue
            color, label = symbol_map[value]
            try:
                item.label = label
            except Exception:
                pass
            try:
                item.symbol.color = {"RGB": color}
            except Exception:
                pass
            apply_symbol_style(item.symbol, key, value)


def _unique_value_item_value(item):
    """Extract the string value from an ArcGIS unique-value item."""

    try:
        values = item.values
        if values and values[0]:
            return str(values[0][0])
    except Exception:
        pass
    return ""


def _renderer_uses_field(renderer, field_name):
    """Return whether a renderer is already keyed by the requested field."""

    try:
        fields = renderer.fields
        return field_name in list(fields or [])
    except Exception:
        pass
    try:
        return renderer.field == field_name
    except Exception:
        return False


def _tune_layer_visibility(layer, key):
    """Apply configured transparency when the ArcGIS layer allows it."""

    try:
        layer.transparency = LAYER_TRANSPARENCY[key]
    except Exception:
        pass


def _configure_labels(layer, key):
    """Enable district-name labels when label APIs are available."""

    if key != "districts":
        return
    try:
        layer.showLabels = True
    except Exception:
        pass
    try:
        label_classes = layer.listLabelClasses()
    except Exception:
        return
    for label_class in label_classes or []:
        try:
            label_class.expression = "$feature.district_name"
        except Exception:
            pass
        try:
            label_class.visible = True
        except Exception:
            pass
