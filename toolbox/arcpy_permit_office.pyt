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
    "permit_office_arcgis",
    "permit_office_arcgis.rules_loader",
    "permit_office_arcgis.schema",
    "permit_office_arcgis.messages",
    "permit_office_arcgis._perf",
    "permit_office_arcgis.store",
    "permit_office_arcgis.symbology_config",
    "permit_office_arcgis.layer_ring",
    "permit_office_arcgis.map_layers",
    "permit_office_arcgis.proposals",
    "permit_office_arcgis.city_features",
    "permit_office_arcgis.geometry",
    "permit_office_arcgis.redraw_plan",
    "permit_office_arcgis.map_redraw",
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
    DISTRICTS,
    TOOLBOX_ALIAS,
    TOOLBOX_LABEL,
    ensure_schema,
    resolve_game_workspace,
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
        self.description = (
            "Opens the Permit Office main menu: Continue, New Game, Help. "
            "Play in a new map with every layer removed; a basemap makes the "
            "districts go blank for a second at each week close. The game saves "
            "to data\\permit_office.gdb in the project folder."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Declare only the derived district layer; Run needs no input."""

        p_output = arcpy.Parameter(
            displayName="Output District Layer",
            name="output_district_layer",
            datatype="GPFeatureLayer",
            parameterType="Derived",
            direction="Output",
        )
        return [p_output]

    def execute(self, parameters, messages):
        """Open the main menu against the project's saved-game geodatabase.

        PERMIT_OFFICE_WORKSPACE overrides the save for live testing, and
        PERMIT_OFFICE_PERF turns on refresh timings.
        """

        with _perf.perf_session("startup", messages):
            with _perf.perf_block("workspace"):
                gdb_path = resolve_game_workspace(messages)
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
