"""Pure tests for layer styling from the shipped .lyrx files."""

from __future__ import annotations

from types import SimpleNamespace
import sys


sys.modules.setdefault(
    "arcpy",
    SimpleNamespace(
        AddMessage=lambda text: None,
        AddWarning=lambda text: None,
        AddError=lambda text: None,
    ),
)

import json
from pathlib import Path

import pytest

from toolbox.permit_office_arcgis.geometry import remove_outputs_from_map, _order_output_layers
from toolbox.permit_office_arcgis import city_features, map_layers, proposals

def _patch_split(monkeypatch, name, value):
    """Patch a name in each module split out of geometry.py that holds it."""

    for module in (proposals, city_features, map_layers):
        if name in vars(module):
            monkeypatch.setattr(module, name, value)



class FakeMessages:
    """Capture ArcPy-style info and warning messages."""

    def __init__(self) -> None:
        """Initialize captured message buffers."""

        self.messages: list[str] = []
        self.warnings: list[str] = []

    def addMessage(self, text: str) -> None:
        """Record an informational ArcPy message."""

        self.messages.append(text)

    def addWarningMessage(self, text: str) -> None:
        """Record a warning ArcPy message."""

        self.warnings.append(text)


class FakeSymbology:
    """Minimal symbology object exposing renderer update state."""

    def __init__(self, renderer) -> None:
        """Store the renderer under test."""

        self.renderer = renderer
        self.updated_renderer: str | None = None

    def updateRenderer(self, renderer_name: str) -> None:
        """Record the requested ArcGIS renderer type."""

        self.updated_renderer = renderer_name


class FakeLayer:
    """Minimal ArcGIS layer double with symbology and label APIs."""

    def __init__(self, renderer, supports_symbology: bool = True) -> None:
        """Initialize fake layer state around one renderer."""

        self.name = "Fake Layer"
        self._supports_symbology = supports_symbology
        self._symbology = FakeSymbology(renderer)
        self.assigned_symbology = None
        self.symbology_assignment_count = 0
        self.transparency = None
        self.showLabels = False
        self.label_classes = [SimpleNamespace(expression="", visible=False)]

    def supports(self, capability: str) -> bool:
        """Return whether the fake layer claims a capability."""

        return capability == "SYMBOLOGY" and self._supports_symbology

    @property
    def symbology(self):
        """Return the fake symbology object."""

        return self._symbology

    @symbology.setter
    def symbology(self, value) -> None:
        """Record symbology assignments made by the helper."""

        self.assigned_symbology = value
        self.symbology_assignment_count += 1

    def listLabelClasses(self):
        """Return mutable fake label classes."""

        return self.label_classes


class FakeMap:
    """Minimal active-map double supporting layer removal and ordering."""

    def __init__(self, layers) -> None:
        """Store a mutable list of fake layers."""

        self.layers = list(layers)
        self.removed = []

    def listLayers(self):
        """Return the current fake layer list."""

        return list(self.layers)

    def removeLayer(self, layer) -> None:
        """Remove a layer and record its name."""

        self.removed.append(layer.name)
        self.layers.remove(layer)

    def moveLayer(self, reference_layer, move_layer, insert_position) -> None:
        """Move one fake layer before or after another."""

        self.layers.remove(move_layer)
        reference_index = self.layers.index(reference_layer)
        insert_index = reference_index if insert_position == "BEFORE" else reference_index + 1
        self.layers.insert(insert_index, move_layer)


class FieldsListRenderer:
    """Renderer variant accepting list-valued fields."""

    def __init__(self) -> None:
        """Initialize renderer field and default-symbol state."""

        self.fields = None
        self.useDefaultSymbol = False


def test_district_layers_render_by_district_type_by_default():
    """Verify district layers default to identity-first district-type rendering."""

    from toolbox.permit_office_arcgis.symbology_config import RENDER_FIELD_BY_LAYER_KEY

    assert RENDER_FIELD_BY_LAYER_KEY["districts"] == "district_type"


def test_identity_transition_symbol_values_are_configured():
    """Verify identity transition render values are available."""

    from toolbox.permit_office_arcgis.symbology_config import SYMBOLS_BY_FIELD

    identity_symbols = SYMBOLS_BY_FIELD["identity_state"]
    assert {"stable", "vulnerable", "contested", "converted", "overextended"} <= set(identity_symbols)
    assert "district_type" in SYMBOLS_BY_FIELD


def test_support_feature_symbol_values_cover_seeded_city_detail_classes():
    """Verify support layers have symbol classes for generated city-detail features."""

    from toolbox.permit_office_arcgis.symbology_config import SYMBOLS_BY_FIELD

    display_symbols = SYMBOLS_BY_FIELD["display_state"]

    assert {
        "road",
        "utility",
        "park",
        "housing",
        "commerce",
        "civic",
        "industry",
        "campus",
        "proposed",
        "active",
        "maintenance_due",
        "degraded",
    } <= set(display_symbols)
    assert display_symbols["road"][1] == "Road"
    assert display_symbols["utility"][1] == "Utility"
    assert display_symbols["housing"][1] == "Housing"


def test_remove_outputs_from_map_removes_stale_permit_layers(monkeypatch):
    """Verify stale Permit Office layers are removed from the active map."""

    stale = [FakeLayer(FieldsListRenderer()) for _ in range(5)]
    stale[0].name = "PermitDistricts"
    stale[1].name = "PermitPoints"
    stale[2].name = "PermitLines"
    stale[3].name = "PermitZones"
    stale[4].name = "OtherLayer"
    fake_map = FakeMap(stale)
    fake_arcpy = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map)),
        AddMessage=lambda text: None,
        AddWarning=lambda text: None,
    )
    _patch_split(monkeypatch, "arcpy", fake_arcpy)
    messages = FakeMessages()

    remove_outputs_from_map(messages)

    assert fake_map.removed == ["PermitDistricts", "PermitPoints", "PermitLines", "PermitZones"]
    assert [layer.name for layer in fake_map.layers] == ["OtherLayer"]
    assert messages.messages == ["[MAP] removed 4 stale Permit Office layer(s)"]


def test_order_output_layers_keeps_lines_on_top_and_districts_on_bottom():
    """Verify map layer ordering keeps districts as the base layer."""

    layers = [FakeLayer(FieldsListRenderer()) for _ in range(4)]
    for layer, name in zip(layers, ["PermitDistricts", "PermitZones", "PermitPoints", "PermitLines"]):
        layer.name = name
    existing = {layer.name: layer for layer in layers}
    fake_map = FakeMap(layers)

    _order_output_layers(fake_map, existing)

    assert [layer.name for layer in fake_map.layers] == [
        "PermitLines",
        "PermitPoints",
        "PermitZones",
        "PermitDistricts",
    ]


def test_order_output_layers_stacks_overlays_above_base_district_fill():
    """Verify identity/prosperity overlays sit above the base district fill."""

    from toolbox.permit_office_arcgis.geometry import DISTRICT_IDENTITY, DISTRICT_PROSPERITY

    names = ["PermitDistricts", DISTRICT_PROSPERITY, DISTRICT_IDENTITY, "PermitZones", "PermitPoints", "PermitLines"]
    layers = [FakeLayer(FieldsListRenderer()) for _ in names]
    for layer, name in zip(layers, names):
        layer.name = name
    existing = {layer.name: layer for layer in layers}
    fake_map = FakeMap(layers)

    _order_output_layers(fake_map, existing)

    assert [layer.name for layer in fake_map.layers] == [
        "PermitLines",
        "PermitPoints",
        "PermitZones",
        DISTRICT_IDENTITY,
        DISTRICT_PROSPERITY,
        "PermitDistricts",
    ]


class FakeLyrxLayer(FakeLayer):
    """Layer double returned by Map.addLayer for a .lyrx file."""

    def __init__(self, layer_file) -> None:
        super().__init__(FieldsListRenderer())
        self.name = f"exported {layer_file}"
        self.visible = True
        self.definitionQuery = ""
        self.connectionProperties = {
            "dataset": "ExportedDistricts",
            "workspace_factory": "File Geodatabase",
            "connection_info": {"database": r"C:\owner\export.gdb"},
        }
        self.dataSource = r"C:\owner\export.gdb\ExportedDistricts"
        self.connection_updates = []
        self.definitions_set = 0
        self.cim = None

    def updateConnectionProperties(self, current, new) -> None:
        self.connection_updates.append((current, new))
        self.connectionProperties = new
        self.dataSource = new["connection_info"]["database"] + "/" + new["dataset"]

    def getDefinition(self, version):
        """Return a CIM-shaped double whose data connection mirrors the .lyrx export."""

        assert version == "V3"
        connection = SimpleNamespace(
            workspaceConnectionString=r"DATABASE=..\..\docs\dags\spikes\fresh.gdb",
            workspaceFactory="FileGDB",
            dataset=self.connectionProperties["dataset"],
        )
        return SimpleNamespace(featureTable=SimpleNamespace(dataConnection=connection))

    def setDefinition(self, cim) -> None:
        """Apply the CIM data connection the way Pro resolves it."""

        self.definitions_set += 1
        self.cim = cim
        connection = cim.featureTable.dataConnection
        workspace = connection.workspaceConnectionString.split("DATABASE=", 1)[1]
        self.connectionProperties = {
            "dataset": connection.dataset,
            "workspace_factory": "File Geodatabase",
            "connection_info": {"database": workspace},
        }
        self.dataSource = workspace + "/" + connection.dataset


class FakeLyrxMap(FakeMap):
    """Active map double that records addLayer and addDataFromPath calls."""

    def __init__(self, layers=()) -> None:
        super().__init__(layers)
        self.added_layer_files = []
        self.added_paths = []

    def addLayer(self, layer_file):
        self.added_layer_files.append(layer_file.path)
        layer = FakeLyrxLayer(layer_file.path)
        self.layers.append(layer)
        return [layer]

    def addDataFromPath(self, path):
        self.added_paths.append(path)
        layer = FakeLayer(FieldsListRenderer())
        layer.visible = True
        layer.definitionQuery = ""
        layer.dataSource = path
        self.layers.append(layer)
        return layer


def _fake_arcpy_with_layer_files(active_map):
    return SimpleNamespace(
        mp=SimpleNamespace(
            LayerFile=lambda path: SimpleNamespace(path=path),
            ArcGISProject=lambda name: SimpleNamespace(activeMap=active_map),
        ),
        RefreshLayer=lambda name: None,
    )


def _write_layer_files(tmp_path, names=("districts", "prosperity", "identity", "points", "lines", "zones")):
    for name in names:
        (tmp_path / f"{name}.lyrx").write_text("{}")
    return tmp_path


def test_add_outputs_loads_each_layer_from_its_lyrx_and_points_it_at_the_save(monkeypatch, tmp_path):
    """Verify a shipped .lyrx gives the layer its full style with no renderer rebuild."""

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path)))
    active_map = FakeLyrxMap()
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))
    paths = {key: f"/saves/game.gdb/Permit{key.title()}" for key in ("districts", "points", "lines", "zones")}
    messages = FakeMessages()

    geometry.add_outputs_to_map(paths, messages)

    assert active_map.added_paths == []
    assert sorted(path.rsplit("/", 1)[-1] for path in active_map.added_layer_files) == [
        "districts.lyrx", "districts.lyrx", "identity.lyrx", "lines.lyrx", "points.lyrx", "prosperity.lyrx", "zones.lyrx",
    ]
    by_name = {layer.name: layer for layer in active_map.layers}
    assert set(by_name) == {
        "PermitDistricts", "PermitPoints", "PermitLines", "PermitZones", "District Prosperity", "District Identity", "District Underlay",
    }
    districts = by_name["PermitDistricts"]
    assert districts.connectionProperties["dataset"] == "PermitDistricts"
    assert districts.connectionProperties["connection_info"]["database"] == "/saves/game.gdb"
    assert by_name["District Identity"].connectionProperties["dataset"] == "PermitDistricts"
    assert all(layer.symbology.updated_renderer is None for layer in active_map.layers)
    assert not any("unique-value" in text for text in messages.messages)
    assert any("styled districts from districts.lyrx" in text for text in messages.messages)


def test_add_outputs_puts_an_unlabeled_district_underlay_below_the_district_fill(monkeypatch, tmp_path):
    """Verify the district underlay sits last in drawing order, reads the save, and has labels off.

    AR18 run v5: a requeried district fill drops for ~0.2 s and showed white
    with nothing under it; an untouched copy beneath showed the old fill. The
    copy's labels doubled every district name, so they are turned off.
    """

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path)))
    active_map = FakeLyrxMap()
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))
    paths = {key: f"/saves/game.gdb/Permit{key.title()}" for key in ("districts", "points", "lines", "zones")}

    geometry.add_outputs_to_map(paths, FakeMessages())

    names = [layer.name for layer in active_map.layers]
    assert names[-2:] == ["PermitDistricts", "District Underlay"]
    underlay = active_map.layers[-1]
    assert underlay.connectionProperties["dataset"] == "PermitDistricts"
    assert underlay.showLabels is False
    assert underlay.visible is True


def test_remove_outputs_removes_the_district_underlay_with_districts(monkeypatch):
    """Verify the underlay goes whenever the district layers are rebuilt."""

    from toolbox.permit_office_arcgis import geometry

    layers = [SimpleNamespace(name=name) for name in ("PermitDistricts", "District Underlay", "PermitPoints")]
    active_map = FakeMap(layers)
    _patch_split(monkeypatch, "arcpy", SimpleNamespace(mp=SimpleNamespace(ArcGISProject=lambda name: SimpleNamespace(activeMap=active_map))))

    geometry.remove_outputs_from_map(FakeMessages(), layer_names={"PermitPoints"})
    assert "District Underlay" not in active_map.removed

    geometry.remove_outputs_from_map(FakeMessages(), layer_names={"PermitDistricts"})
    assert "District Underlay" in active_map.removed


def test_ring_slot_styled_from_lyrx_without_update_renderer(monkeypatch, tmp_path):
    """Verify a district ring swap adds its slot from the .lyrx and never rebuilds the renderer."""

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path)))
    visible = FakeLayer(FieldsListRenderer())
    visible.name = "Permit Office Predrawn 0"
    visible.visible = True
    visible.definitionQuery = "1=1"
    visible.dataSource = "/saves/game.gdb/PermitDistricts"
    active_map = FakeLyrxMap([visible])
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))
    _patch_split(monkeypatch, "_query_flip_supported", lambda: False)

    geometry._apply_district_ring_redraw({"districts": "/saves/game.gdb/PermitDistricts"}, FakeMessages(), {"PermitDistricts"})

    slot = next(layer for layer in active_map.layers if layer.name == "Permit Office Predrawn 1")
    assert active_map.added_paths == []
    assert [path.rsplit("/", 1)[-1] for path in active_map.added_layer_files] == ["districts.lyrx"]
    assert slot.visible is True
    assert slot.definitionQuery == "1=1"
    assert slot.connectionProperties["dataset"] == "PermitDistricts"
    assert all(layer.symbology.updated_renderer is None for layer in active_map.layers)


def test_layer_file_path_maps_keys_to_shipped_file_names(monkeypatch, tmp_path):
    from toolbox.permit_office_arcgis import symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path, names=("prosperity",))))

    assert symbology_config.layer_file_path("district_prosperity") == str(tmp_path / "prosperity.lyrx")
    assert symbology_config.layer_file_path("districts") is None
    assert symbology_config.layer_file_path("unknown") is None


def test_missing_lyrx_fails_without_code_styling(monkeypatch, tmp_path):
    """Verify a missing .lyrx is reported, not drawn unstyled (ruling D7)."""

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path, names=("districts",))))
    active_map = FakeLyrxMap()
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))
    messages = FakeMessages()

    geometry.add_outputs_to_map({"points": "/saves/game.gdb/PermitPoints"}, messages, layer_names={"PermitPoints"})

    assert active_map.added_paths == []
    assert active_map.layers == []
    assert any("missing toolbox/layers/points.lyrx" in text for text in messages.warnings)


def test_lyrx_layer_that_cannot_be_repointed_is_removed_and_reported(monkeypatch, tmp_path):
    """Verify a .lyrx layer left on the export's data is not kept on the map."""

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path, names=("points",))))
    active_map = FakeLyrxMap()
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))
    # Pro can accept a definition edit without changing the source; the check after must catch it.
    monkeypatch.setattr(FakeLyrxLayer, "setDefinition", lambda self, cim: None)
    messages = FakeMessages()

    geometry.add_outputs_to_map({"points": "/saves/game.gdb/PermitPoints"}, messages, layer_names={"PermitPoints"})

    assert active_map.layers == []
    assert active_map.added_paths == []
    assert any("could not point points.lyrx at /saves/game.gdb/PermitPoints: layer still reads" in text for text in messages.warnings)


@pytest.mark.parametrize("layer_key", sorted(map_layers.LAYER_FILE_NAMES))
def test_shipped_lyrx_has_a_class_for_every_value_the_game_writes(layer_key):
    """Verify each shipped style renders its field with no value left to the default symbol."""

    from toolbox.permit_office_arcgis import symbology_config

    path = Path(symbology_config.LAYER_FILE_DIR) / symbology_config.LAYER_FILE_NAMES[layer_key]
    document = json.loads(path.read_text(encoding="utf-8"))
    (definition,) = document["layerDefinitions"]
    renderer = definition["renderer"]
    field_name = symbology_config.RENDER_FIELD_BY_LAYER_KEY[layer_key]
    values = {
        value["fieldValues"][0]
        for group in renderer.get("groups") or []
        for item in group.get("classes") or []
        for value in item.get("values") or []
    }

    assert renderer["type"] == "CIMUniqueValueRenderer"
    assert renderer["fields"] == [field_name]
    assert set(symbology_config.SYMBOLS_BY_FIELD[field_name]) <= values
    assert not definition["featureTable"].get("definitionExpression")


def test_lyrx_layer_is_repointed_through_its_cim_definition(monkeypatch, tmp_path):
    """Verify the repoint survives updateConnectionProperties raising.

    Pro 3.7 raised a bare AttributeError from updateConnectionProperties on 22
    of 24 standalone adds (docs/dags/spikes/AR20-lyrx-repoint/probe-run2.txt),
    while editing the CIM data connection pointed every layer at the save.
    """

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path, names=("zones",))))
    active_map = FakeLyrxMap()
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))

    def raise_attribute_error(self, current, new):
        raise AttributeError

    monkeypatch.setattr(FakeLyrxLayer, "updateConnectionProperties", raise_attribute_error)
    messages = FakeMessages()

    geometry.add_outputs_to_map({"zones": "/saves/game.gdb/PermitZones"}, messages, layer_names={"PermitZones"})

    zones = next(layer for layer in active_map.layers if layer.name == "PermitZones")
    assert zones.definitions_set == 1
    assert zones.cim.featureTable.dataConnection.workspaceConnectionString == "DATABASE=/saves/game.gdb"
    assert zones.cim.featureTable.dataConnection.dataset == "PermitZones"
    assert zones.dataSource == "/saves/game.gdb/PermitZones"
    assert messages.warnings == []
    assert any("styled zones from zones.lyrx" in text for text in messages.messages)


def test_one_unpointable_lyrx_layer_does_not_stop_the_other_outputs(monkeypatch, tmp_path):
    """Verify a layer that cannot be repointed is reported and the rest still load."""

    from toolbox.permit_office_arcgis import geometry, symbology_config

    monkeypatch.setattr(symbology_config, "LAYER_FILE_DIR", str(_write_layer_files(tmp_path)))
    active_map = FakeLyrxMap()
    _patch_split(monkeypatch, "arcpy", _fake_arcpy_with_layer_files(active_map))

    real_set_definition = FakeLyrxLayer.setDefinition

    def set_definition_unless_points(self, cim):
        if cim.featureTable.dataConnection.dataset == "PermitPoints":
            raise AttributeError
        real_set_definition(self, cim)

    monkeypatch.setattr(FakeLyrxLayer, "setDefinition", set_definition_unless_points)
    paths = {key: f"/saves/game.gdb/Permit{key.title()}" for key in ("districts", "points", "lines", "zones")}
    messages = FakeMessages()

    geometry.add_outputs_to_map(paths, messages)

    names = [layer.name for layer in active_map.layers]
    assert set(names) == {"PermitDistricts", "PermitLines", "PermitZones", "District Prosperity", "District Identity", "District Underlay"}
    # Full ordering is skipped with points missing; the underlay must still sit under the fill.
    assert names.index("District Underlay") == names.index("PermitDistricts") + 1
    assert len(messages.warnings) == 1
    assert "could not point points.lyrx at /saves/game.gdb/PermitPoints" in messages.warnings[0]
    assert "AttributeError" in messages.warnings[0]
