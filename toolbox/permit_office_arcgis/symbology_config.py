"""Map presentation constants for Permit Office ArcGIS layers."""

import os

# Styled layer files exported from Pro (rulings D2, D7). Every Permit Office
# layer and ring slot is added from its file; there is no code styling.
LAYER_FILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "layers")
LAYER_FILE_NAMES = {
    "districts": "districts.lyrx",
    "district_prosperity": "prosperity.lyrx",
    "district_identity": "identity.lyrx",
    "points": "points.lyrx",
    "lines": "lines.lyrx",
    "zones": "zones.lyrx",
}


def layer_file_path(layer_key):
    """Return the shipped .lyrx path for a layer key, or None when absent."""

    name = LAYER_FILE_NAMES.get(layer_key)
    if not name:
        return None
    path = os.path.join(LAYER_FILE_DIR, name)
    return path if os.path.isfile(path) else None

DISPLAY_STATE_SYMBOLS = {
    "stable": ([226, 232, 222, 100], "Stable"),
    "daily_pressure": ([205, 132, 78, 100], "Daily Pressure"),
    "incident": ([206, 54, 52, 100], "Civic Incident"),
    "grievance": ([150, 72, 90, 100], "Local Grievance"),
    "service_gap": ([64, 126, 180, 100], "Service Gap"),
    "hazard": ([196, 90, 74, 100], "Hazard Pressure"),
    "housing_pressure": ([177, 126, 88, 100], "Housing Pressure"),
    "economic_growth": ([106, 162, 114, 100], "Economic Growth"),
    "high_activity": ([106, 162, 114, 100], "Prosperous"),
    "high_friction": ([216, 159, 84, 100], "Restless"),
    "high_trust": ([140, 126, 188, 100], "Cultured"),
    "high_exposure": ([196, 90, 74, 100], "At Exposure"),
    "strained": ([205, 132, 78, 100], "Strained"),
    "aggrieved": ([150, 72, 90, 100], "Aggrieved"),
    "proposed": ([45, 196, 199, 100], "Proposed"),
    "active": ([52, 150, 100, 100], "Active"),
    "denied": ([112, 112, 112, 100], "Denied"),
    "deferred": ([158, 135, 82, 100], "Deferred"),
    "failed": ([170, 66, 66, 100], "Failed"),
    "settled": ([68, 140, 156, 100], "Settled"),
    "enforced": ([100, 88, 158, 100], "Enforced"),
    "responded": ([70, 128, 178, 100], "Responded"),
    "maintained": ([74, 150, 122, 100], "Maintained"),
    "maintenance_due": ([218, 138, 62, 100], "Maintenance Due"),
    "degraded": ([155, 92, 74, 100], "Degraded"),
    "road": ([58, 62, 60, 100], "Road"),
    "utility": ([54, 126, 185, 100], "Utility"),
    "park": ([82, 150, 88, 100], "Park"),
    "housing": ([177, 126, 88, 100], "Housing"),
    "commerce": ([188, 146, 58, 100], "Commerce"),
    "civic": ([82, 120, 172, 100], "Civic"),
    "industry": ([124, 118, 110, 100], "Industry"),
    "campus": ([130, 112, 174, 100], "Campus"),
    "temporary": ([216, 145, 68, 100], "Temporary"),
    "overlay": ([63, 168, 159, 100], "Overlay"),
    "case": ([199, 82, 96, 100], "Case"),
    "context": ([146, 139, 128, 100], "Context"),
    "special_interest": ([214, 168, 64, 100], "Special Interest"),
}

DISTRICT_TYPE_SYMBOLS = {
    "residential": ([222, 190, 160, 100], "Residential"),
    "mercantile": ([225, 194, 124, 100], "Mercantile"),
    "industrial": ([174, 166, 154, 100], "Industrial"),
    "civic": ([151, 178, 210, 100], "Civic"),
    "academic": ([176, 160, 211, 100], "Academic"),
    "natural": ([153, 194, 148, 100], "Natural"),
}

IDENTITY_STATE_SYMBOLS = {
    "stable": ([226, 232, 222, 0], "Stable Identity"),
    "vulnerable": ([210, 132, 78, 100], "Vulnerable"),
    "contested": ([188, 74, 70, 100], "Contested Buyout"),
    "converted": ([92, 150, 105, 100], "Recently Converted"),
    "overextended": ([150, 72, 90, 100], "Overextended"),
}

# Prosperity overlay: transparent fills (alpha 0) so the land-use-type fill stays
# visible beneath; the shipped prosperity.lyrx carries the graduated outline.
PROSPERITY_BAND_SYMBOLS = {
    "thriving": ([106, 162, 114, 0], "Thriving"),
    "stable": ([170, 186, 170, 0], "Stable"),
    "strained": ([216, 159, 84, 0], "Strained"),
    "failing": ([196, 90, 74, 0], "Failing"),
}

SYMBOLS_BY_FIELD = {
    "display_state": DISPLAY_STATE_SYMBOLS,
    "district_type": DISTRICT_TYPE_SYMBOLS,
    "identity_state": IDENTITY_STATE_SYMBOLS,
    "prosperity_band": PROSPERITY_BAND_SYMBOLS,
}

RENDER_FIELD_BY_LAYER_KEY = {
    "districts": "district_type",
    "points": "display_state",
    "lines": "display_state",
    "zones": "display_state",
    # Overlay layers reuse the PermitDistricts feature class with a different
    # render field so type, prosperity, and identity each get a visual channel.
    "district_prosperity": "prosperity_band",
    "district_identity": "identity_state",
}
