"""Tests for the ArcGIS district display ring with fake map objects."""

from __future__ import annotations

from toolbox.permit_office_arcgis import layer_ring


class FakeLayer:
    __hash__ = None

    def __init__(self, name, visible=False):
        self.name = name
        self.visible = visible
        self.style = ""


class FakeMap:
    def __init__(self, layers=()):
        self.layers = list(layers)
        self.removed = []
        self.added_paths = []

    def listLayers(self):
        return list(self.layers)

    def removeLayer(self, layer):
        self.removed.append(layer.name)
        self.layers.remove(layer)

    def addDataFromPath(self, path):
        self.added_paths.append(path)
        layer = FakeLayer(path)
        self.layers.append(layer)
        return layer


class FakeArcpy:
    def __init__(self):
        self.refreshed = []

    def RefreshLayer(self, name):
        self.refreshed.append(name)


def test_layer_ring_seeds_one_visible_slot_when_missing():
    fake_map = FakeMap()
    arcpy = FakeArcpy()
    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=arcpy, style_copier=lambda layer: setattr(layer, "style", "district"))

    prepared = ring.prepare_and_swap("PermitDistricts")

    assert fake_map.added_paths == ["PermitDistricts"]
    assert prepared.name == "Permit Office Predrawn 0"
    assert prepared.visible is True
    assert prepared.style == "district"
    assert arcpy.refreshed == ["Permit Office Predrawn 0"]


def test_layer_ring_ignores_legacy_predrawn_rehydrate_layers_when_seeding():
    """Verify Active/Idle predrawn snapshots do not count as numeric ring slots."""

    legacy_active = FakeLayer("Permit Office Predrawn Active", True)
    legacy_idle = FakeLayer("Permit Office Predrawn Idle", False)
    fake_map = FakeMap([legacy_active, legacy_idle])
    arcpy = FakeArcpy()
    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=arcpy, style_copier=lambda layer: setattr(layer, "style", "district"))

    prepared = ring.prepare_and_swap("PermitDistricts")

    assert prepared.name == "Permit Office Predrawn 0"
    assert prepared.visible is True
    assert prepared.style == "district"
    assert fake_map.added_paths == ["PermitDistricts"]
    assert arcpy.refreshed == ["Permit Office Predrawn 0"]
    assert legacy_active.visible is True


def test_layer_ring_rehydrates_hidden_prepare_slot_and_swaps_after_success():
    visible = FakeLayer("Permit Office Predrawn 0", True)
    hidden = FakeLayer("Permit Office Predrawn 1", False)
    spare = FakeLayer("Permit Office Predrawn 2", False)
    fake_map = FakeMap([visible, hidden, spare])
    arcpy = FakeArcpy()
    styled = []
    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=arcpy, style_copier=lambda layer: styled.append(layer.name))

    prepared = ring.prepare_and_swap("PermitDistricts")

    assert fake_map.removed == ["Permit Office Predrawn 1"]
    assert fake_map.added_paths == ["PermitDistricts"]
    assert prepared.name == "Permit Office Predrawn 1"
    assert visible.visible is False
    assert prepared.visible is True
    assert spare.visible is False
    assert styled == ["Permit Office Predrawn 1"]
    assert arcpy.refreshed == ["Permit Office Predrawn 1"]


def test_layer_ring_refreshes_prepare_slot_after_visibility_swap():
    """Verify ArcGIS refresh sees the newly prepared slot as visible."""

    visible = FakeLayer("Permit Office Predrawn 0", True)
    hidden = FakeLayer("Permit Office Predrawn 1", False)
    fake_map = FakeMap([visible, hidden])
    refresh_visibility = []

    class VisibilityArcpy:
        def RefreshLayer(self, name):
            layer = next(layer for layer in fake_map.layers if layer.name == name)
            refresh_visibility.append((name, layer.visible, visible.visible))

    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=VisibilityArcpy(), style_copier=lambda _layer: None)

    ring.prepare_and_swap("PermitDistricts")

    assert refresh_visibility == [("Permit Office Predrawn 1", True, False)]


def test_layer_ring_supports_custom_prefixes_for_feature_layers():
    """Verify the ring primitive can be reused for support feature layers."""

    visible = FakeLayer("Permit Office Predrawn Points 0", True)
    hidden = FakeLayer("Permit Office Predrawn Points 1", False)
    fake_map = FakeMap([visible, hidden])
    arcpy = FakeArcpy()
    styled = []
    ring = layer_ring.DisplayLayerRing(
        fake_map,
        prefix="Permit Office Predrawn Points",
        arcpy_module=arcpy,
        style_copier=lambda layer: styled.append(layer.name),
    )

    prepared = ring.prepare_and_swap("PermitPoints")

    assert fake_map.removed == ["Permit Office Predrawn Points 1"]
    assert fake_map.added_paths == ["PermitPoints"]
    assert prepared.name == "Permit Office Predrawn Points 1"
    assert visible.visible is False
    assert prepared.visible is True
    assert styled == ["Permit Office Predrawn Points 1"]
    assert arcpy.refreshed == ["Permit Office Predrawn Points 1"]


def test_layer_ring_preserves_visible_slot_when_prepare_fails():
    visible = FakeLayer("Permit Office Predrawn 0", True)
    hidden = FakeLayer("Permit Office Predrawn 1", False)
    fake_map = FakeMap([visible, hidden, FakeLayer("Permit Office Predrawn 2", False)])

    def fail_style(_layer):
        raise RuntimeError("style failed")

    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=FakeArcpy(), style_copier=fail_style)

    try:
        ring.prepare_and_swap("PermitDistricts")
    except RuntimeError:
        pass

    assert visible.visible is True


def test_layer_ring_handles_unhashable_arcgis_layers():
    """Verify visibility rollback does not require hashable Layer objects."""

    visible = FakeLayer("Permit Office Predrawn 0", True)
    hidden = FakeLayer("Permit Office Predrawn 1", False)
    spare = FakeLayer("Permit Office Predrawn 2", False)
    fake_map = FakeMap([visible, hidden, spare])
    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=FakeArcpy(), style_copier=lambda _layer: None)

    prepared = ring.prepare_and_swap("PermitDistricts")

    assert prepared.name == "Permit Office Predrawn 1"
    assert prepared.visible is True
    assert visible.visible is False


def test_layer_ring_adds_hidden_prepare_slot_when_only_visible_slot_exists():
    """Verify second ring use adds one prepare slot instead of seeding all slots."""

    visible = FakeLayer("Permit Office Predrawn 0", True)
    fake_map = FakeMap([visible])
    arcpy = FakeArcpy()
    styled = []
    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=arcpy, style_copier=lambda layer: styled.append(layer.name))

    prepared = ring.prepare_and_swap("PermitDistricts")

    assert fake_map.removed == []
    assert fake_map.added_paths == ["PermitDistricts"]
    assert prepared.name == "Permit Office Predrawn 1"
    assert visible.visible is False
    assert prepared.visible is True
    assert styled == ["Permit Office Predrawn 1"]
    assert arcpy.refreshed == ["Permit Office Predrawn 1"]


def test_layer_ring_seeds_missing_slots_without_creating_two_visible_layers():
    """Verify partially seeded rings keep exactly the existing visible slot."""

    class VisibleAddMap(FakeMap):
        def addDataFromPath(self, path):
            self.added_paths.append(path)
            layer = FakeLayer(path, True)
            self.layers.append(layer)
            return layer

    hidden_zero = FakeLayer("Permit Office Predrawn 0", False)
    visible_one = FakeLayer("Permit Office Predrawn 1", True)
    fake_map = VisibleAddMap([hidden_zero, visible_one])
    ring = layer_ring.DistrictLayerRing(fake_map, arcpy_module=FakeArcpy(), style_copier=lambda _layer: None)

    slots = ring.discover_or_seed("PermitDistricts")

    assert [slot.layer.name for slot in slots] == [
        "Permit Office Predrawn 0",
        "Permit Office Predrawn 1",
    ]
    assert [slot.layer.visible for slot in slots] == [False, True]


def test_layer_ring_adds_slots_through_layer_adder():
    """Verify seeded and prepared slots come from the supplied layer adder."""

    active_map = FakeMap([FakeLayer("Permit Office Predrawn 0", visible=True)])
    adder_paths = []

    def add_layer(path):
        adder_paths.append(path)
        layer = FakeLayer(f"from-lyrx:{path}")
        active_map.layers.append(layer)
        return layer

    ring = layer_ring.DistrictLayerRing(active_map, arcpy_module=FakeArcpy(), layer_adder=add_layer)
    prepared = ring.prepare_and_swap("districts.gdb/PermitDistricts")

    assert adder_paths == ["districts.gdb/PermitDistricts"]
    assert active_map.added_paths == []
    assert prepared.name == "Permit Office Predrawn 1"
    assert prepared.visible is True
