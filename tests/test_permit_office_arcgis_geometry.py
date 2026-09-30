"""Fake-ArcPy coverage for Permit Office map-selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
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

from toolbox import arcpy_permit_office_rules as rules
from toolbox.permit_office_arcgis import geometry
from toolbox.permit_office_arcgis.schema import DISTRICT_FIELDS


@dataclass
class FakeField:
    """Minimal ArcPy field object exposing only a name."""

    name: str


class FakeMessages:
    """Capture warning messages emitted by geometry helpers."""

    def __init__(self):
        """Initialize captured warning storage."""

        self.warnings = []

    def addWarningMessage(self, text):
        """Record an ArcPy-style warning message."""

        self.warnings.append(text)


class CapturingMessages(FakeMessages):
    """Capture informational and warning messages emitted by geometry helpers."""

    def __init__(self):
        """Initialize captured message storage."""

        super().__init__()
        self.messages = []

    def addMessage(self, text):
        """Record an ArcPy-style informational message."""

        self.messages.append(text)


class FakeManagement:
    """Record ArcPy management selection calls."""

    def __init__(self):
        """Initialize captured selections."""

        self.selections = []

    def SelectLayerByAttribute(self, layer, selection_type, where_clause=None):
        """Capture a layer selection operation."""

        self.selections.append((layer, selection_type, where_clause))


class FakeDA:
    """Provide fake ArcPy data-access cursor factories."""

    def __init__(self, rows):
        """Store shared fake table rows."""

        self.rows = rows

    def SearchCursor(self, path, fields, where_clause=None):
        """Return a read cursor over one fake table.

        where_clause is accepted to match real arcpy; the production code keeps
        an equivalent Python guard, so the fake can leave filtering to it.
        """

        return FakeSearchCursor(self.rows[path], fields)

    def UpdateCursor(self, path, fields, where_clause=None):
        """Return a mutable cursor over one fake table (where_clause ignored)."""

        return FakeUpdateCursor(self.rows[path], fields)


class FakeArcpy:
    """Small ArcPy module stand-in for geometry tests."""

    def __init__(self, rows):
        """Wire fake rows into management and data-access namespaces."""

        self.rows = rows
        self.da = FakeDA(rows)
        self.management = FakeManagement()

    def ListFields(self, path):
        """Return field names discovered from fake row dictionaries."""

        names = set()
        for row in self.rows.get(path, []):
            names.update(row)
        return [FakeField(name) for name in sorted(names)]


class FakeSearchCursor:
    """Context-manager search cursor for fake row dictionaries."""

    def __init__(self, rows, fields):
        """Project fake rows to the requested field order."""

        self.projected = [[row.get(field) for field in fields] for row in rows]
        self.index = 0

    def __enter__(self):
        """Enter the cursor context."""

        return self

    def __exit__(self, *exc):
        """Leave the cursor context without suppressing errors."""

        return False

    def __iter__(self):
        """Return this cursor as its own iterator."""

        return self

    def __next__(self):
        """Return the next projected row."""

        if self.index >= len(self.projected):
            raise StopIteration
        row = self.projected[self.index]
        self.index += 1
        return row


class FakeUpdateCursor:
    """Mutable fake cursor that supports update and delete calls."""

    def __init__(self, rows, fields):
        """Store rows and the ArcPy cursor field order."""

        self.rows = rows
        self.fields = fields
        self.index = 0
        self.current = None

    def __enter__(self):
        """Enter the cursor context."""

        return self

    def __exit__(self, *exc):
        """Leave the cursor context without suppressing errors."""

        return False

    def __iter__(self):
        """Return this cursor as its own iterator."""

        return self

    def __next__(self):
        """Return the next row values in cursor field order."""

        if self.index >= len(self.rows):
            raise StopIteration
        self.current = self.rows[self.index]
        self.index += 1
        return [self.current.get(field) for field in self.fields]

    def updateRow(self, values):
        """Write values back to the current fake row."""

        for field, value in zip(self.fields, values):
            self.current[field] = value

    def deleteRow(self):
        """Delete the current fake row."""

        if self.current in self.rows:
            self.rows.remove(self.current)
            self.index -= 1


def _paths():
    """Return canonical fake geodatabase paths."""

    return {"districts": "districts", "points": "points", "lines": "lines", "zones": "zones"}


def _rows():
    """Return empty fake feature-class row containers."""

    return {"districts": [], "points": [], "lines": [], "zones": []}


class _SelectionArcpy:
    """Tailored ArcPy stub for selected_cell_ids fast-path tests."""

    def __init__(self, fidset, cursor_rows, legacy_field=None, fail_cell_id=False):
        """Configure selection state, cursor rows, and an optional legacy field name."""

        self.da = self
        self._fidset = fidset
        self._cursor_rows = cursor_rows
        self._legacy_field = legacy_field
        self._fail_cell_id = fail_cell_id
        self.listfields_calls = 0

    def Describe(self, _layer):
        """Report the configured FIDSet so has_selection is True."""

        return {"FIDSet": self._fidset}

    def SearchCursor(self, _layer, fields):
        """Return rows for the resolved field, or fail the direct cell_id read."""

        if fields == ["cell_id"] and self._fail_cell_id:
            raise RuntimeError("Field cell_id does not exist")
        return iter([[value] for value in self._cursor_rows])

    def ListFields(self, _layer):
        """Count field scans and expose the legacy field name on fallback."""

        self.listfields_calls += 1
        return [FakeField(self._legacy_field)] if self._legacy_field else []


def test_selected_cell_ids_reads_cell_id_directly_without_field_scan(monkeypatch):
    """Verify the common path reads the known cell_id column and skips ListFields."""

    fake = _SelectionArcpy(fidset="0;1", cursor_rows=["D0000", "D0001"])
    monkeypatch.setattr(geometry, "arcpy", fake)

    result = geometry.selected_cell_ids("districts_layer")

    assert result == ["D0000", "D0001"]
    assert fake.listfields_calls == 0  # fast path avoided the metadata round-trip


def test_selected_cell_ids_falls_back_to_field_scan_on_cursor_error(monkeypatch):
    """Verify a legacy/renamed column still resolves via the ListFields fallback."""

    fake = _SelectionArcpy(
        fidset="0", cursor_rows=["D0000"], legacy_field="CELL_ID", fail_cell_id=True
    )
    monkeypatch.setattr(geometry, "arcpy", fake)

    result = geometry.selected_cell_ids("districts_layer")

    assert result == ["D0000"]
    assert fake.listfields_calls == 1  # fell back exactly once


def _map_arcpy(layer_names, active=True, raise_err=False):
    """Return an ArcPy stub whose active map carries the given layer names."""

    def _project(_name):
        if raise_err:
            raise RuntimeError("no CURRENT project")
        if not active:
            return SimpleNamespace(activeMap=None)
        layers = [SimpleNamespace(name=name) for name in layer_names]
        return SimpleNamespace(activeMap=SimpleNamespace(listLayers=lambda: layers))

    return SimpleNamespace(mp=SimpleNamespace(ArcGISProject=_project))


def test_output_layers_present_true_when_an_output_layer_is_on_map(monkeypatch):
    """Verify a map carrying a Permit Office layer reports present (resume)."""

    monkeypatch.setattr(geometry, "arcpy", _map_arcpy([geometry.DISTRICTS, "Topographic"]))

    assert geometry.output_layers_present() is True


def test_output_layers_present_false_when_map_has_no_output_layers(monkeypatch):
    """Verify a map with only basemaps reports absent (offer fresh start)."""

    monkeypatch.setattr(geometry, "arcpy", _map_arcpy(["Topographic", "World Imagery"]))

    assert geometry.output_layers_present() is False


def test_output_layers_present_true_when_probe_unavailable(monkeypatch):
    """Verify a probe failure is conservative and never suppresses a resume."""

    monkeypatch.setattr(geometry, "arcpy", _map_arcpy([], raise_err=True))

    assert geometry.output_layers_present() is True


def test_ensure_active_map_creates_and_opens_map_when_project_has_none(monkeypatch):
    """Verify startup gives layer operations a map in empty ArcGIS projects."""

    calls = []
    created = SimpleNamespace(name="Permit Office", openView=lambda: calls.append("open"))

    class FakeProject:
        activeMap = None

        def listMaps(self):
            return []

        def createMap(self, name):
            calls.append(("create", name))
            return created

    monkeypatch.setattr(geometry, "arcpy", SimpleNamespace(mp=SimpleNamespace(ArcGISProject=lambda _name: FakeProject())))
    messages = CapturingMessages()

    active_map = geometry.ensure_active_map(messages)

    assert active_map is created
    assert calls == [("create", "Permit Office"), "open"]
    assert any("created and opened map" in line for line in messages.messages)


def test_ensure_active_map_opens_existing_map_when_no_active_map(monkeypatch):
    """Verify startup can recover when a project has maps but none active."""

    calls = []
    existing = SimpleNamespace(name="Existing", openView=lambda: calls.append("open"))

    class FakeProject:
        activeMap = None

        def listMaps(self):
            return [existing]

    monkeypatch.setattr(geometry, "arcpy", SimpleNamespace(mp=SimpleNamespace(ArcGISProject=lambda _name: FakeProject())))

    assert geometry.ensure_active_map(CapturingMessages()) is existing
    assert calls == ["open"]


def test_legacy_predrawn_rehydrate_helpers_are_retired():
    """Verify the removed rehydrate probe no longer lives in production geometry."""

    assert not hasattr(geometry, "run_redraw_experiment")
    assert not hasattr(geometry, "_experiment_predrawn_rehydrate")
    assert not hasattr(geometry, "PREDRAWN_ACTIVE_LAYER")
    assert not hasattr(geometry, "PREDRAWN_IDLE_LAYER")


def test_district_display_style_hash_is_stable():
    """Verify the district display style cache key changes only with style inputs."""

    first = geometry._district_display_style_hash("1=1")
    second = geometry._district_display_style_hash("1=1")
    changed_query = geometry._district_display_style_hash("display_state = 'daily_pressure'")

    assert first == second
    assert first != changed_query
    assert "districts" in first


def test_prepare_district_display_layer_can_skip_style_when_hash_matches(monkeypatch):
    """Verify style-cache probe can skip label/symbology transforms."""

    calls = []
    layer = SimpleNamespace(definitionQuery="", _permit_office_style_hash=geometry._district_display_style_hash("1=1"))
    monkeypatch.setattr(geometry, "_configure_labels", lambda target, key: calls.append(("labels", key)))
    monkeypatch.setattr(geometry, "apply_simple_symbology", lambda target, key, messages: calls.append(("sym", key)))
    monkeypatch.setattr(geometry, "_tune_layer_visibility", lambda target, key: calls.append(("tune", key)))

    skipped = geometry._prepare_district_display_layer(layer, CapturingMessages(), "1=1", skip_if_style_matches=True)

    assert skipped is True
    assert calls == []
    assert layer.definitionQuery == "1=1"


def test_apply_ring_redraw_reports_failure_to_caller(monkeypatch):
    """Verify callers can fall back when production ring redraw fails."""

    messages = CapturingMessages()
    monkeypatch.setattr(geometry, "ensure_active_map", lambda messages: (_ for _ in ()).throw(RuntimeError("map unavailable")))

    handled = geometry.apply_ring_redraw(_paths(), messages, layer_names={geometry.DISTRICTS})

    assert handled is False
    assert any("ring redraw failed: map unavailable" in line for line in messages.warnings)


def test_refresh_feature_scope_refreshes_without_readd_when_not_in_remove_scope(monkeypatch):
    """Verify broad rehydrate rebuilds do not re-symbolize unchanged feature layers."""

    calls = []
    monkeypatch.setattr(geometry, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(geometry, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(geometry, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))

    geometry._refresh_feature_scope(
        _paths(),
        CapturingMessages(),
        layer_names={geometry.DISTRICTS, geometry.POINTS, geometry.LINES, geometry.ZONES},
        remove_scope={geometry.DISTRICTS},
    )

    assert calls == [("refresh", {geometry.POINTS, geometry.LINES, geometry.ZONES})]


def test_refresh_feature_scope_uses_feature_ring_for_features_in_remove_scope(monkeypatch):
    """Verify point decisions use reusable feature display slots."""

    calls = []
    monkeypatch.setattr(geometry, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(geometry, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(geometry, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(geometry, "_rehydrate_feature_display_ring", lambda paths, messages, layer_name: calls.append(("ring", layer_name)) or True)

    geometry._refresh_feature_scope(
        _paths(),
        CapturingMessages(),
        layer_names={geometry.DISTRICTS, geometry.POINTS},
        remove_scope={geometry.DISTRICTS, geometry.POINTS},
    )

    assert calls == [
        ("ring", geometry.POINTS),
    ]


def test_refresh_feature_scope_marks_each_feature_layer_phase(monkeypatch):
    """Verify live logs can split feature ring cost by support layer."""

    marks = []
    monkeypatch.setattr(geometry, "_rehydrate_feature_display_ring", lambda paths, messages, layer_name: True)
    monkeypatch.setattr(geometry, "refresh_all", lambda *args, **kwargs: None)

    geometry._refresh_feature_scope(
        _paths(),
        CapturingMessages(),
        layer_names={geometry.POINTS, geometry.LINES},
        remove_scope={geometry.POINTS},
        phase_marker=marks.append,
    )

    assert marks == ["feature_PermitPoints_ring_rehydrate", "feature_PermitLines_refresh"]


def test_refresh_feature_scope_falls_back_to_readd_when_feature_ring_fails(monkeypatch):
    """Verify feature ring failures keep the old safe remove/add path."""

    calls = []
    monkeypatch.setattr(geometry, "remove_outputs_from_map", lambda messages, layer_names=None: calls.append(("remove", layer_names)))
    monkeypatch.setattr(geometry, "add_outputs_to_map", lambda paths, messages, layer_names=None: calls.append(("add", layer_names)))
    monkeypatch.setattr(geometry, "refresh_all", lambda paths, messages, layer_names=None: calls.append(("refresh", layer_names)))
    monkeypatch.setattr(geometry, "_rehydrate_feature_display_ring", lambda paths, messages, layer_name: calls.append(("ring", layer_name)) or False)

    geometry._refresh_feature_scope(
        _paths(),
        CapturingMessages(),
        layer_names={geometry.DISTRICTS, geometry.POINTS},
        remove_scope={geometry.DISTRICTS, geometry.POINTS},
    )

    assert calls == [
        ("ring", geometry.POINTS),
        ("remove", {geometry.POINTS}),
        ("add", {geometry.POINTS}),
        ("refresh", {geometry.POINTS}),
    ]


def test_feature_display_ring_rehydrates_points_and_hides_base_layer(monkeypatch):
    """Verify support feature redraw uses a visible refreshed ring slot."""

    calls = []
    base_points = SimpleNamespace(name=geometry.POINTS, visible=True, definitionQuery="", transparency=None)
    visible = SimpleNamespace(name="Permit Office Predrawn Points 0", visible=True, definitionQuery="1=1", transparency=None)
    hidden = SimpleNamespace(name="Permit Office Predrawn Points 1", visible=False, definitionQuery="1=1", transparency=None)
    layers = [base_points, visible, hidden]

    def remove_layer(layer):
        calls.append(("remove", layer.name))
        layers.remove(layer)

    def add_data(source):
        layer = SimpleNamespace(name="raw", visible=True, definitionQuery="", transparency=None)
        layers.append(layer)
        calls.append(("add", source))
        return layer

    fake_map = SimpleNamespace(listLayers=lambda: list(layers), removeLayer=remove_layer, addDataFromPath=add_data)
    fake = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map)),
        RefreshLayer=lambda name: calls.append(("refresh", name, next(layer.visible for layer in layers if layer.name == name))),
    )
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "apply_simple_symbology", lambda target, key, messages: calls.append(("sym", target.name, key)))

    handled = geometry._rehydrate_feature_display_ring(_paths(), CapturingMessages(), geometry.POINTS)

    assert handled is True
    assert ("remove", "Permit Office Predrawn Points 1") in calls
    assert ("add", "points") in calls
    assert ("sym", "Permit Office Predrawn Points 1", "points") in calls
    assert ("refresh", "Permit Office Predrawn Points 1", True) in calls
    assert base_points.visible is False


def test_feature_display_ring_refreshes_visible_slot_without_rehydrate(monkeypatch):
    """Verify existing support rings can avoid expensive remove/add/style."""

    calls = []
    base_points = SimpleNamespace(name=geometry.POINTS, visible=True)
    visible = SimpleNamespace(name="Permit Office Predrawn Points 0", visible=True)
    hidden = SimpleNamespace(name="Permit Office Predrawn Points 1", visible=False)
    layers = [base_points, visible, hidden]
    fake_map = SimpleNamespace(
        listLayers=lambda: list(layers),
        removeLayer=lambda layer: calls.append(("remove", layer.name)),
        addDataFromPath=lambda source: calls.append(("add", source)),
    )
    fake = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map)),
        RefreshLayer=lambda name: calls.append(("refresh", name)),
    )
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "apply_simple_symbology", lambda target, key, messages: calls.append(("sym", target.name, key)))

    handled = geometry._refresh_visible_feature_display_ring(_paths(), CapturingMessages(), geometry.POINTS)

    assert handled is True
    assert calls == [("refresh", "Permit Office Predrawn Points 0")]
    assert base_points.visible is False


def test_feature_display_ring_refresh_failure_falls_back_to_rehydrate(monkeypatch):
    """Verify failed cheap refresh does not abort the feature ring fallback."""

    calls = []
    base_points = SimpleNamespace(name=geometry.POINTS, visible=True)
    visible = SimpleNamespace(name="Permit Office Predrawn Points 0", visible=True)
    layers = [base_points, visible]
    fake_map = SimpleNamespace(listLayers=lambda: list(layers))
    fake = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map)),
        RefreshLayer=lambda name: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "_rehydrate_feature_display_ring", lambda paths, messages, layer_name: calls.append(("rehydrate", layer_name)) or True)

    geometry._refresh_feature_scope(
        _paths(),
        CapturingMessages(),
        layer_names={geometry.POINTS},
        remove_scope={geometry.POINTS},
    )

    assert calls == [("rehydrate", geometry.POINTS)]


def test_apply_ring_redraw_dispatches_to_district_ring(monkeypatch):
    """Verify production redraw dispatches to the district ring."""

    calls = []
    monkeypatch.setattr(
        geometry,
        "_apply_district_ring_redraw",
        lambda paths, messages, layer_names, remove_scope=None: calls.append((layer_names, remove_scope)),
    )

    handled = geometry.apply_ring_redraw(
        _paths(),
        CapturingMessages(),
        layer_names={geometry.DISTRICTS, geometry.POINTS},
        remove_scope={geometry.DISTRICTS},
    )

    assert handled is True
    assert calls == [({geometry.DISTRICTS, geometry.POINTS}, {geometry.DISTRICTS})]


def test_district_ring_styles_slots_with_base_district_symbology(monkeypatch):
    """Verify ring slots render district_type like PermitDistricts, not display_state."""

    calls = []
    visible = SimpleNamespace(name="Permit Office Predrawn 0", visible=True, definitionQuery="1=1", transparency=None)
    hidden = SimpleNamespace(name="Permit Office Predrawn 1", visible=False, definitionQuery="1=1", transparency=None)
    spare = SimpleNamespace(name="Permit Office Predrawn 2", visible=False, definitionQuery="1=1", transparency=None)
    layers = [visible, hidden, spare]

    def remove_layer(layer):
        layers.remove(layer)

    def add_data(source):
        layer = SimpleNamespace(name="raw", visible=True, definitionQuery="", transparency=None)
        layers.append(layer)
        return layer

    fake_map = SimpleNamespace(listLayers=lambda: list(layers), removeLayer=remove_layer, addDataFromPath=add_data)
    fake = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map)),
        RefreshLayer=lambda name: calls.append(("refresh", name)),
    )
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "apply_simple_symbology", lambda target, key, messages: calls.append(("sym", target.name, key)))

    handled = geometry.apply_ring_redraw(_paths(), CapturingMessages(), layer_names={geometry.DISTRICTS})

    assert handled is True
    assert ("sym", "Permit Office Predrawn 1", "districts") in calls
    assert ("sym", "Permit Office Predrawn 1", "district_display") not in calls


def test_district_ring_hides_base_district_family_layers(monkeypatch):
    """Verify ring mode prevents duplicate district labels and fills."""

    calls = []
    base = SimpleNamespace(name=geometry.DISTRICTS, visible=True, definitionQuery="", transparency=None)
    prosperity = SimpleNamespace(name=geometry.DISTRICT_PROSPERITY, visible=True, definitionQuery="", transparency=None)
    identity = SimpleNamespace(name=geometry.DISTRICT_IDENTITY, visible=True, definitionQuery="", transparency=None)
    visible = SimpleNamespace(name="Permit Office Predrawn 0", visible=True, definitionQuery="1=1", transparency=None)
    hidden = SimpleNamespace(name="Permit Office Predrawn 1", visible=False, definitionQuery="1=1", transparency=None)
    layers = [base, prosperity, identity, visible, hidden]

    def remove_layer(layer):
        layers.remove(layer)

    def add_data(source):
        layer = SimpleNamespace(name="raw", visible=True, definitionQuery="", transparency=None)
        layers.append(layer)
        return layer

    fake_map = SimpleNamespace(listLayers=lambda: list(layers), removeLayer=remove_layer, addDataFromPath=add_data)
    fake = SimpleNamespace(
        mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map)),
        RefreshLayer=lambda name: calls.append(("refresh", name)),
    )
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "apply_simple_symbology", lambda target, key, messages: calls.append(("sym", target.name, key)))

    handled = geometry.apply_ring_redraw(_paths(), CapturingMessages(), layer_names={geometry.DISTRICTS})

    assert handled is True
    assert base.visible is False
    assert prosperity.visible is False
    assert identity.visible is False
    assert sum(1 for layer in layers if getattr(layer, "name", "").startswith("Permit Office Predrawn") and layer.visible) == 1


def test_remove_outputs_removes_predrawn_district_layers_on_full_cleanup(monkeypatch):
    """Verify New Game/full cleanup clears legacy and ring predrawn district layers."""

    removed = []
    layers = [
        SimpleNamespace(name=geometry.DISTRICTS),
        SimpleNamespace(name=geometry.DISTRICT_PROSPERITY),
        SimpleNamespace(name="Permit Office Predrawn Active"),
        SimpleNamespace(name="Permit Office Predrawn 2"),
        SimpleNamespace(name=geometry.POINTS),
    ]

    def remove_layer(layer):
        removed.append(layer.name)

    fake_map = SimpleNamespace(listLayers=lambda: list(layers), removeLayer=remove_layer)
    monkeypatch.setattr(geometry, "arcpy", SimpleNamespace(mp=SimpleNamespace(ArcGISProject=lambda current: SimpleNamespace(activeMap=fake_map))))

    geometry.remove_outputs_from_map(CapturingMessages())

    assert geometry.DISTRICTS in removed
    assert geometry.DISTRICT_PROSPERITY in removed
    assert "Permit Office Predrawn Active" in removed
    assert "Permit Office Predrawn 2" in removed


def test_district_identity_persistence_field_aliases_are_configured():
    """Verify district identity persistence fields use Task 7 aliases."""

    fields = {name: (field_type, alias, length) for name, field_type, alias, length in DISTRICT_FIELDS}

    assert fields["prior_district_type"] == ("TEXT", "Prior District Type", 32)
    assert fields["identity_state"] == ("TEXT", "Identity State", 32)
    assert fields["contesting_cell_id"] == ("TEXT", "Contesting District ID", 32)
    assert fields["contesting_type"] == ("TEXT", "Contesting Type", 32)
    assert fields["transition_due_turn"] == ("LONG", "Transition Due Week", None)
    assert fields["buyout_pressure"] == ("LONG", "Buyout Pressure", None)
    assert fields["last_buyout_report"] == ("TEXT", "Last Buyout Report", 512)


def test_select_case_context_creates_proposal_and_selects_support_feature(monkeypatch):
    """Verify selecting a case creates and selects its proposal context."""

    rows = _rows()
    fake = FakeArcpy(rows)
    monkeypatch.setattr(geometry, "arcpy", fake)
    item = rules.DocketItem(
        "CASE-1",
        "street_vendor_compact",
        "Street Vendor Compact",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )

    def insert(paths, docket_item, target_ids, messages):
        """Fake proposal insertion used by the selection flow."""

        rows["points"].append(
            {
                "item_id": docket_item.item_id,
                "status": "proposed",
                "target_cell_ids": ",".join(target_ids),
                "feature_id": "P-1",
            }
        )
        return list(target_ids)

    monkeypatch.setattr(geometry, "insert_or_replace_proposal", insert)

    geometry.select_case_context(_paths(), "district_layer", item, 2026, FakeMessages())

    assert rows["points"][0]["item_id"] == "CASE-1"
    assert ("district_layer", "NEW_SELECTION", "cell_id = 'D0000'") in fake.management.selections
    assert ("PermitPoints", "NEW_SELECTION", "item_id = 'CASE-1'") in fake.management.selections


def test_select_case_context_falls_back_to_visible_predrawn_district_layer(monkeypatch):
    """Verify ring-mode selection uses the visible predrawn district layer."""

    rows = _rows()
    rows["points"].append({"item_id": "CASE-ring", "status": "proposed", "target_cell_ids": "D0000"})
    fake = FakeArcpy(rows)
    real_select = fake.management.SelectLayerByAttribute
    attempts = []

    def select(layer, selection_type, where_clause=None):
        attempts.append(layer)
        if layer == geometry.DISTRICTS:
            raise RuntimeError("Dataset PermitDistricts does not exist or is not supported")
        real_select(layer, selection_type, where_clause)

    fake.management.SelectLayerByAttribute = select
    visible = SimpleNamespace(name="Permit Office Predrawn 1", visible=True)
    hidden = SimpleNamespace(name="Permit Office Predrawn 2", visible=False)
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "_active_map", lambda: SimpleNamespace(listLayers=lambda: [hidden, visible]))
    messages = FakeMessages()
    item = rules.DocketItem(
        "CASE-ring",
        "street_vendor_compact",
        "Street Vendor Compact",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )

    geometry.select_case_context(_paths(), geometry.DISTRICTS, item, 2026, messages)

    assert ("Permit Office Predrawn 1", "NEW_SELECTION", "cell_id = 'D0000'") in fake.management.selections
    assert geometry.DISTRICTS not in attempts
    assert not any("district targets selection failed" in warning for warning in messages.warnings)


def test_maintenance_selection_includes_referenced_active_feature(monkeypatch):
    """Verify maintenance cases select both proposal and subject feature."""

    rows = _rows()
    rows["points"].append({"item_id": "CASE-2", "status": "proposed", "target_cell_ids": "D0000", "feature_id": "P-2"})
    fake = FakeArcpy(rows)
    monkeypatch.setattr(geometry, "arcpy", fake)
    item = rules.DocketItem(
        "CASE-2",
        rules.MAINTENANCE_TEMPLATE_ID,
        "Maintenance Order",
        "POINT",
        1,
        target_cell_ids=["D0000"],
        subject_feature_id="FEATURE-77",
    )

    geometry.select_case_context(_paths(), "district_layer", item, 2026, FakeMessages())

    support_wheres = [where for layer, _mode, where in fake.management.selections if layer == "PermitPoints"]
    assert support_wheres == ["item_id = 'CASE-2' OR feature_id = 'FEATURE-77'"]


def test_support_selection_prefers_visible_feature_ring_layer(monkeypatch):
    """Verify hidden base support layers do not swallow visible selection highlights."""

    rows = _rows()
    rows["points"].append({"item_id": "CASE-ring-point", "status": "proposed", "target_cell_ids": "D0000", "feature_id": "P-ring"})
    fake = FakeArcpy(rows)
    base = SimpleNamespace(name=geometry.POINTS, visible=False)
    ring = SimpleNamespace(name="Permit Office Predrawn Points 1", visible=True)
    monkeypatch.setattr(geometry, "arcpy", fake)
    monkeypatch.setattr(geometry, "_active_map", lambda: SimpleNamespace(listLayers=lambda: [base, ring]))
    item = rules.DocketItem(
        "CASE-ring-point",
        "street_vendor_compact",
        "Street Vendor Compact",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )

    geometry.select_case_context(_paths(), "district_layer", item, 2026, FakeMessages())

    assert ("Permit Office Predrawn Points 1", "NEW_SELECTION", "item_id = 'CASE-ring-point'") in fake.management.selections


def test_hide_show_affects_only_selected_proposed_feature(monkeypatch):
    """Verify hiding a proposal leaves active and context features intact."""

    rows = _rows()
    rows["points"].extend(
        [
            {"item_id": "CASE-3", "status": "proposed", "target_cell_ids": "D0000", "feature_id": "P-3"},
            {"item_id": "CASE-3", "status": "active", "target_cell_ids": "D0000", "feature_id": "A-3"},
            {"item_id": "", "status": "context", "target_cell_ids": "D0001", "feature_id": "CITY-1"},
        ]
    )
    fake = FakeArcpy(rows)
    monkeypatch.setattr(geometry, "arcpy", fake)
    item = rules.DocketItem(
        "CASE-3",
        "street_vendor_compact",
        "Street Vendor Compact",
        "POINT",
        1,
        target_cell_ids=["D0000"],
    )

    assert geometry.case_proposal_visible(_paths(), item) is True
    assert geometry.hide_case_proposal(_paths(), item.item_id) is True

    assert geometry.case_proposal_visible(_paths(), item) is False
    assert [(row["feature_id"], row["status"]) for row in rows["points"]] == [
        ("A-3", "active"),
        ("CITY-1", "context"),
    ]


class _SpillGeom:
    """Fake geometry whose buffer() reports which districts it touches."""

    def __init__(self, touches, meters_per_unit=1.0):
        """Store the district ids the buffer reaches and the SR's linear unit."""

        self.touches = set(touches)
        self.spatialReference = SimpleNamespace(metersPerUnit=meters_per_unit)
        self.radii = []

    def buffer(self, distance):
        """Record the buffer distance and return a disjoint-aware fake."""

        self.radii.append(distance)
        touches = self.touches
        return SimpleNamespace(disjoint=lambda other: other.cell_id not in touches)


def _spill_arcpy(rows):
    """ArcPy stub with cursors only, so any GP tool call raises AttributeError."""

    return SimpleNamespace(da=FakeDA(rows))


def _spill_setup(monkeypatch, proposal_geom, radius=None):
    """Seed five districts, one real proposal, and two decoy rows."""

    rows = _rows()
    rows["districts"].extend(
        {"cell_id": f"D000{index}", "SHAPE@": SimpleNamespace(cell_id=f"D000{index}")} for index in range(5)
    )
    decoy = _SpillGeom({"D0000", "D0001", "D0002", "D0003", "D0004"})
    rows["points"].extend(
        [
            {"item_id": "CASE-9", "status": "active", "SHAPE@": decoy},
            {"item_id": "OTHER", "status": "proposed", "SHAPE@": decoy},
            {"item_id": "CASE-9", "status": "proposed", "SHAPE@": proposal_geom},
        ]
    )
    monkeypatch.setattr(geometry, "arcpy", _spill_arcpy(rows))
    monkeypatch.setattr(
        geometry.rules,
        "feature_archetype_for_template",
        lambda _template_id: SimpleNamespace(coverage_radius_m=radius),
    )
    geometry.clear_geometry_cache()
    return rules.DocketItem("CASE-9", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)


def test_proposal_spillover_uses_geometry_buffer_not_gp_tools(monkeypatch):
    """Verify spillover buffers in memory and returns sorted touched district ids."""

    proposal = _SpillGeom({"D0003", "D0001"})
    item = _spill_setup(monkeypatch, proposal)

    try:
        assert geometry.proposal_spillover(_paths(), item) == ["D0001", "D0003"]
    finally:
        geometry.clear_geometry_cache()
    assert proposal.radii == [125]


def test_proposal_spillover_keeps_radius_in_meters_for_foot_saves(monkeypatch):
    """Verify a legacy foot-based save still buffers the archetype radius in meters."""

    proposal = _SpillGeom({"D0002"}, meters_per_unit=0.3048)
    item = _spill_setup(monkeypatch, proposal, radius=200)

    try:
        assert geometry.proposal_spillover(_paths(), item) == ["D0002"]
    finally:
        geometry.clear_geometry_cache()
    assert proposal.radii == [200 / 0.3048]


def test_proposal_spillover_without_proposed_row_is_empty(monkeypatch):
    """Verify a missing proposal row yields no spillover instead of an error."""

    _spill_setup(monkeypatch, _SpillGeom({"D0001"}))
    item = rules.DocketItem("CASE-404", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)

    try:
        assert geometry.proposal_spillover(_paths(), item) == []
    finally:
        geometry.clear_geometry_cache()
