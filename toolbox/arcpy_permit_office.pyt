"""Permit Office prototype toolbox.

This ArcGIS Python toolbox entrypoint delegates schema, persistence, geometry,
and dashboard behavior to ``permit_office_arcgis`` helper modules. The gameplay
rules remain pure Python in ``permit_office`` and are exposed through the
``arcpy_permit_office_rules`` compatibility facade.
"""

from __future__ import annotations

import os
import sys
import importlib

import arcpy

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _module_is_from_this_toolbox(module):
    """Return True when an already-imported helper module belongs to this .pyt."""

    module_file = os.path.abspath(getattr(module, "__file__", "") or "")
    toolbox_root = os.path.abspath(_HERE)
    return module_file.startswith(toolbox_root + os.sep)


for _module_name in (
    "permit_office",
    "permit_office.cache_keys",
    "permit_office.dirty",
    "permit_office.futures",
    "permit_office_arcgis",
    "permit_office_arcgis.rules_loader",
    "permit_office_arcgis.schema",
    "permit_office_arcgis.messages",
    "permit_office_arcgis._perf",
    "permit_office_arcgis.store",
    "permit_office_arcgis.symbology_config",
    "permit_office_arcgis.layer_ring",
    "permit_office_arcgis.geometry",
    "permit_office_arcgis.redraw_plan",
    "permit_office_arcgis.desk_model",
    "permit_office_arcgis.desk_view",
    "permit_office_arcgis.dashboard",
):
    _module = sys.modules.get(_module_name)
    if _module is not None:
        if _module_is_from_this_toolbox(_module):
            importlib.reload(_module)
        else:
            sys.modules.pop(_module_name, None)

from permit_office_arcgis import _perf
from permit_office_arcgis.dashboard import (
    DashboardController,
)
from permit_office_arcgis.schema import (
    P_OUTPUT,
    P_PERF,
    P_WORKSPACE,
    DISTRICTS,
    TOOLBOX_ALIAS,
    TOOLBOX_LABEL,
    ensure_schema,
    resolve_workspace,
)


class Toolbox(object):
    """ArcGIS toolbox declaration for the Permit Office prototype."""

    def __init__(self):
        """Register the playable geoprocessing tool with ArcGIS Pro."""

        self.label = TOOLBOX_LABEL
        self.alias = TOOLBOX_ALIAS
        self.tools = [PermitOfficePrototype]


class PermitOfficePrototype(object):
    """ArcGIS geoprocessing tool that hosts the playable permit workflow."""

    def __init__(self):
        """Configure static tool metadata displayed in ArcGIS Pro."""

        self.label = "Permit Office Prototype"
        self.description = "Generated-district permit office dashboard prototype."
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Declare ArcGIS tool parameters for launching the dashboard."""

        p_workspace = arcpy.Parameter(
            displayName="Game Workspace (optional)",
            name="game_workspace",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input",
        )
        p_output = arcpy.Parameter(
            displayName="Output District Layer",
            name="output_district_layer",
            datatype="GPFeatureLayer",
            parameterType="Derived",
            direction="Output",
        )
        p_perf = arcpy.Parameter(
            displayName="Log Refresh Timings",
            name="enable_perf",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        p_perf.value = False
        return [p_workspace, p_output, p_perf]

    def execute(self, parameters, messages):
        """Open the dashboard against the resolved saved-game geodatabase."""

        _perf.set_enabled(bool(parameters[P_PERF].value))
        with _perf.perf_session("startup", messages):
            with _perf.perf_block("workspace"):
                gdb_path = resolve_workspace(parameters[P_WORKSPACE].value, messages)
            with _perf.perf_block("schema"):
                paths = ensure_schema(gdb_path, messages)
            seed = 2026
        DashboardController(
            paths,
            DISTRICTS,
            seed,
            messages,
        ).open()
        arcpy.SetParameterAsText(P_OUTPUT, paths["districts"])
