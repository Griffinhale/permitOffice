"""Three-slot predrawn district layer ring for ArcGIS display updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


RING_PREFIX = "Permit Office Predrawn"
RING_SIZE = 3


@dataclass(frozen=True)
class RingSlot:
    """One discovered or seeded district display slot."""

    index: int
    layer: object


class DisplayLayerRing:
    """Manage a small reusable ring of predrawn display layers."""

    def __init__(
        self,
        active_map,
        *,
        prefix: str = RING_PREFIX,
        arcpy_module,
        style_copier: Callable[[object], None] | None = None,
        phase_marker: Callable[[str], None] | None = None,
        layer_adder: Callable[[str], object] | None = None,
    ):
        self.active_map = active_map
        self.prefix = prefix
        self.arcpy = arcpy_module
        self.style_copier = style_copier or (lambda _layer: None)
        self.phase_marker = phase_marker or (lambda _name: None)
        self.layer_adder = layer_adder or active_map.addDataFromPath

    def discover_or_seed(self, district_path: str) -> list[RingSlot]:
        """Return discovered slots, adding only the first visible slot if needed."""

        by_name = {getattr(layer, "name", ""): layer for layer in self.active_map.listLayers()}
        existing_slot_names = {name for name in by_name if self._is_slot(name)}
        slots: list[RingSlot] = []
        for index in range(RING_SIZE):
            name = self._slot_name(index)
            layer = by_name.get(name)
            if layer is None:
                if not slots and not existing_slot_names:
                    layer = self.layer_adder(district_path)
                    layer.name = name
                    self.style_copier(layer)
                    self.phase_marker("seed_visible_slot")
                else:
                    continue
            slots.append(RingSlot(index, layer))
        visible_slot = next((slot for slot in slots if slot.layer.name in existing_slot_names and bool(getattr(slot.layer, "visible", False))), None)
        if visible_slot is None:
            visible_slot = next((slot for slot in slots if bool(getattr(slot.layer, "visible", False))), slots[0])
        for slot in slots:
            slot.layer.visible = slot is visible_slot
        return slots

    def prepare_and_swap(self, district_path: str):
        """Rehydrate one hidden slot from the GDB and make it visible after success."""

        had_slots = any(self._is_slot(getattr(layer, "name", "")) for layer in self.active_map.listLayers())
        slots = self.discover_or_seed(district_path)
        if not had_slots and len(slots) == 1:
            only = slots[0].layer
            if bool(getattr(only, "visible", False)) and getattr(only, "name", "") == self._slot_name(0):
                self.arcpy.RefreshLayer(getattr(only, "name", self._slot_name(0)))
                self.phase_marker("RefreshLayer")
                return only
        visible = next((slot.layer for slot in slots if bool(getattr(slot.layer, "visible", False))), slots[0].layer)
        prepare_slot = _prepare_slot(slots, visible)
        prepare_name = self._slot_name(_next_prepare_index(slots, visible))
        old_visibility = [(slot.layer, bool(getattr(slot.layer, "visible", False))) for slot in slots]
        try:
            if prepare_slot is None:
                prepared = self.layer_adder(district_path)
                self.phase_marker("add_layer")
            else:
                self.active_map.removeLayer(prepare_slot.layer)
                self.phase_marker("remove_prepare_slot")
                prepared = self.layer_adder(district_path)
                self.phase_marker("add_layer")
            prepared.name = prepare_name
            prepared.visible = False
            self.style_copier(prepared)
            self.phase_marker("style_application")
            for slot in self._current_slots():
                slot.layer.visible = getattr(slot.layer, "name", "") == prepare_name
            self.phase_marker("visibility_swap")
            self.arcpy.RefreshLayer(prepare_name)
            self.phase_marker("RefreshLayer")
            return prepared
        except Exception:
            for layer, was_visible in old_visibility:
                try:
                    layer.visible = was_visible
                except Exception:
                    pass
            raise

    def _current_slots(self) -> list[RingSlot]:
        slots = []
        by_name = {getattr(layer, "name", ""): layer for layer in self.active_map.listLayers()}
        for index in range(RING_SIZE):
            layer = by_name.get(self._slot_name(index))
            if layer is not None:
                slots.append(RingSlot(index, layer))
        return slots

    def _slot_name(self, index: int) -> str:
        return f"{self.prefix} {index}"

    def _is_slot(self, name: str) -> bool:
        suffix = name.removeprefix(self.prefix).strip()
        return suffix.isdigit()


class DistrictLayerRing(DisplayLayerRing):
    """Manage a small reusable ring of predrawn district layers."""

    def __init__(self, active_map, *, arcpy_module, style_copier=None, phase_marker=None, layer_adder=None):
        super().__init__(
            active_map,
            prefix=RING_PREFIX,
            arcpy_module=arcpy_module,
            style_copier=style_copier,
            phase_marker=phase_marker,
            layer_adder=layer_adder,
        )


def _slot_name(index: int) -> str:
    return f"{RING_PREFIX} {index}"


def _is_slot(name: str) -> bool:
    suffix = name.removeprefix(RING_PREFIX).strip()
    return suffix.isdigit()


def _next_prepare_index(slots: list[RingSlot], visible_layer: object) -> int:
    hidden = _prepare_slot(slots, visible_layer)
    if hidden is not None:
        return hidden.index
    used = {slot.index for slot in slots}
    for index in range(RING_SIZE):
        if index not in used:
            return index
    return next(slot.index for slot in slots if slot.layer is not visible_layer)


def _prepare_slot(slots: list[RingSlot], visible_layer: object) -> RingSlot | None:
    return next((slot for slot in slots if slot.layer is not visible_layer and not bool(getattr(slot.layer, "visible", False))), None)
