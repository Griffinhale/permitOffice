"""Player-facing derived rules for Permit Office.

The simulation keeps detailed internal ledgers. This module translates those
ledgers into the compact public model shown in reports and the desk.
"""

from __future__ import annotations

from copy import copy, deepcopy
from typing import Iterable

from .helpers import _top_dissatisfaction, district_label
from .models import (
    DISSATISFACTION_AGGRIEVED_THRESHOLD,
    DistrictProfile,
    FeatureInstance,
    ThreatTrack,
)


THREAT_TRACK_LABELS = ("Public Anger", "Legal Exposure", "Service Failure", "Speculation Pressure")


def derive_threat_tracks(
    state,
    districts: dict[str, DistrictProfile] | Iterable[DistrictProfile] | None = None,
    active_features: Iterable[FeatureInstance] | None = None,
    docket=None,
) -> tuple[ThreatTrack, ...]:
    """Translate hidden pressure systems into four public threat tracks."""

    profiles = _profiles(districts)
    features = list(active_features or ())
    buckets: dict[str, list[tuple[int, str]]] = {label: [] for label in THREAT_TRACK_LABELS}

    friction = int(getattr(state, "friction", 0) or 0)
    if friction >= 60:
        buckets["Public Anger"].append((friction, f"city friction {friction}"))
    for stakeholder, heat_value in sorted((getattr(state, "stakeholder_heat", {}) or {}).items()):
        heat = int(heat_value or 0)
        if heat > 0:
            buckets["Public Anger"].append((heat * 10, f"{_display(stakeholder)} heat {heat}"))

    exposure = int(getattr(state, "exposure", 0) or 0)
    if exposure >= 60:
        buckets["Legal Exposure"].append((exposure, f"city exposure {exposure}"))

    activity = int(getattr(state, "activity", 0) or 0)
    if activity >= 70:
        buckets["Speculation Pressure"].append((activity, f"activity spike {activity}"))

    for profile in profiles:
        _add_profile_reasons(buckets, profile)

    maintenance = [
        feature
        for feature in features
        if getattr(feature, "status", "") in ("maintenance_due", "degraded", "failed")
        or _feature_condition(feature) < 35
    ]
    if maintenance:
        lowest = min(_feature_condition(feature) for feature in maintenance)
        buckets["Service Failure"].append((100 - lowest, f"maintenance due {len(maintenance)}"))

    tracks = []
    for label in THREAT_TRACK_LABELS:
        entries = sorted(buckets[label], key=lambda row: (-row[0], row[1]))
        score = min(100, entries[0][0]) if entries else 0
        tone = "bad" if score >= 60 else "watch" if score > 0 else "neutral"
        tracks.append(ThreatTrack(label, score, tone, tuple(text for _score, text in entries[:3])))
    return tuple(tracks)


def derive_district_tags(profile: DistrictProfile) -> tuple[str, ...]:
    """Return compact map/report tags for hidden district causes."""

    tags = []
    if max((int(gap or 0) for gap in (profile.service_gap or {}).values()), default=0) >= 40 or int(profile.services or 0) <= 20:
        tags.append("Service Desert")
    if int(profile.activity or 0) >= 68 or max((int(band or 0) for band in (profile.displacement or {}).values()), default=0) >= 2:
        tags.append("Speculation Front")
    if int(profile.friction or 0) >= 60 or _top_dissatisfaction(profile)[1] >= DISSATISFACTION_AGGRIEVED_THRESHOLD or profile.incident_state != "none":
        tags.append("Anger Cluster")
    if int(profile.exposure or 0) >= 70 or max((int(band or 0) for band in (profile.hazards or {}).values()), default=0) >= 3:
        tags.append("Unsafe Corridor")
    if getattr(profile, "identity_state", "stable") in ("vulnerable", "contested") or int(getattr(profile, "buyout_pressure", 0) or 0) >= 3:
        tags.append("Contested Edge")
    if not tags and int(profile.trust or 0) >= 50 and int(profile.friction or 0) <= 20 and int(profile.exposure or 0) <= 25:
        tags.append("Stable Anchor")
    return tuple(tags[:3])


def threat_report_sentence(state, districts=None, active_features=None, docket=None) -> str:
    """Format the weekly lead-threat finding for the week report."""

    return f" {_lead_threat_text(derive_threat_tracks(state, districts, active_features, docket))}"


def district_tag_report_sentence(districts) -> str:
    """Format notable map-facing district tags for weekly reports."""

    notes = []
    for profile in sorted(_profiles(districts), key=lambda candidate: candidate.cell_id):
        tags = derive_district_tags(profile)
        if tags and tags != ("Stable Anchor",):
            notes.append(f"{district_label(profile)}: {', '.join(tags)}")
    if not notes:
        return ""
    return f" District tags: {'; '.join(notes[:3])}."


def _add_profile_reasons(buckets: dict[str, list[tuple[int, str]]], profile: DistrictProfile) -> None:
    label = district_label(profile)
    if profile.incident_state != "none":
        buckets["Public Anger"].append((80, f"{label} {profile.incident_state}"))
    group, band = _top_dissatisfaction(profile)
    if band >= DISSATISFACTION_AGGRIEVED_THRESHOLD:
        buckets["Public Anger"].append((band * 10, f"{_display(group)} grievance {band}"))
    for hazard, band_value in (profile.hazards or {}).items():
        band = int(band_value or 0)
        if band >= 2:
            buckets["Legal Exposure"].append((band * 20, f"{_display(hazard)} band {band}"))
    for service, gap_value in (profile.service_gap or {}).items():
        gap = int(gap_value or 0)
        if gap >= 25:
            buckets["Service Failure"].append((gap, f"{_display(service)} gap {gap}"))
    for group_name, band_value in (profile.displacement or {}).items():
        band = int(band_value or 0)
        if band >= 2:
            buckets["Speculation Pressure"].append((band * 20, f"{_display(group_name)} displacement {band}"))
    buyout = int(getattr(profile, "buyout_pressure", 0) or 0)
    if buyout > 0 or getattr(profile, "identity_state", "stable") in ("vulnerable", "contested"):
        buckets["Speculation Pressure"].append((max(10, buyout * 10), f"buyout pressure {buyout}"))


def _lead_threat_text(threats: tuple[ThreatTrack, ...]) -> str:
    active = [track for track in threats if track.score > 0]
    if not active:
        return "Threat tracks quiet."
    lead = sorted(active, key=lambda track: (-track.score, THREAT_TRACK_LABELS.index(track.label)))[0]
    reasons = "; ".join(lead.reasons[:2]) if lead.reasons else "pressure noted"
    return f"Lead threat: {lead.label} ({reasons})."


def _profiles(districts) -> list[DistrictProfile]:
    return list(districts.values() if isinstance(districts, dict) else districts or ())


def _display(value) -> str:
    return str(value or "none").replace("_", " ")


def _feature_condition(feature) -> int:
    value = getattr(feature, "condition", 100)
    return 100 if value in (None, "") else int(value)


def snapshot_city_state(state):
    """Return a shallow copy suitable for before/after standing comparisons."""

    clone = copy(state)
    clone.stakeholder_heat = dict(getattr(state, "stakeholder_heat", {}) or {})
    clone.stakeholder_memory = dict(getattr(state, "stakeholder_memory", {}) or {})
    clone.pending_followups = dict(getattr(state, "pending_followups", {}) or {})
    clone.daily_pressure = dict(getattr(state, "daily_pressure", {}) or {})
    clone.mandate = deepcopy(getattr(state, "mandate", {}) or {})
    clone.initiatives = deepcopy(getattr(state, "initiatives", {}) or {})
    clone.type_ledger = {
        key: dict(value)
        for key, value in (getattr(state, "type_ledger", {}) or {}).items()
    }
    return clone


__all__ = [name for name in globals() if not name.startswith("__")]
