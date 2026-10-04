"""Player-started initiatives: one a week, on top of the docket (owner D6, D7).

Earmark backs one district type for a few weeks. Its districts outbid rivals
in buyouts, its kind of case comes up more often in the weekly draw, and the
stakeholders of the biggest rival types take offence. Civic action and Market
push work on one or two map-selected districts: the first eases the worst-off
group and lifts trust, the second lifts activity but raises prices and can
draw speculators. All numbers are first guesses for later tuning.
"""

from __future__ import annotations

from collections import Counter
import random

from .helpers import _blocked, _top_dissatisfaction, adjust_stakeholder_pressure, normalize_profile
from .models import DISTRICT_TYPES, CityState, DecisionResult, DistrictProfile
from .type_pressure import adjust_type_ledger, read_type_ledger, write_type_ledger

EARMARK_COST = 10
EARMARK_AP = 1
EARMARK_WEEKS = 3
# Buyout bidder score bonus, and the draw weight multiplier for templates that
# suit an earmarked district type.
EARMARK_BID_BONUS = 50
EARMARK_DRAW_MULTIPLIER = 3
# The stakeholder who speaks for each district type when a rival type is backed.
TYPE_STAKEHOLDERS = {
    "residential": "homeowners",
    "mercantile": "vendors",
    "industrial": "workers",
    "civic": "civil_servants",
    "academic": "arts_council",
    "natural": "conservation_trust",
}
CIVIC_ACTION_COST = 12
CIVIC_ACTION_TRUST = 4
MARKET_PUSH_COST = 8
MARKET_PUSH_ACTIVITY = 6
MARKET_PUSH_AFFORDABILITY = 5
MARKET_PUSH_SPECULATION_CHANCE = 0.35
INITIATIVE_AP = 1
MAX_INITIATIVE_TARGETS = 2
INITIATIVE_KINDS = ("earmark", "civic_action", "market_push")


def active_earmarks(state: CityState) -> dict[str, int]:
    """Return {district type: turn the earmark ends} for earmarks still running."""

    earmarks = (state.initiatives or {}).get("earmarks") or {}
    return {str(dtype): int(until) for dtype, until in earmarks.items() if int(until) > int(state.turn)}


def initiative_used_this_week(state: CityState) -> bool:
    """Return whether this week's initiative slot is already spent."""

    return int((state.initiatives or {}).get("week", 0) or 0) == int(state.turn)


def start_initiative(
    state: CityState,
    districts: dict[str, DistrictProfile],
    kind: str,
    target,
    seed: int = 2026,
) -> DecisionResult:
    """Start one player initiative this week, or return why it cannot start.

    ``target`` is a district type for Earmark and a list of one or two district
    ids for Civic action and Market push.
    """

    if initiative_used_this_week(state):
        return _blocked(kind, "", "The office already filed one initiative this week.")
    if kind == "earmark":
        return _earmark(state, districts, target)
    if kind in ("civic_action", "market_push"):
        return _district_initiative(state, districts, kind, list(target or ()), seed)
    return _blocked(kind, "", f"Unknown initiative {kind!r}.")


def _district_initiative(state, districts, kind, targets, seed) -> DecisionResult:
    label = "Civic action" if kind == "civic_action" else "Market push"
    cost = CIVIC_ACTION_COST if kind == "civic_action" else MARKET_PUSH_COST
    targets = [cid for cid in dict.fromkeys(targets) if cid in districts]
    if not 1 <= len(targets) <= MAX_INITIATIVE_TARGETS:
        return _blocked(kind, "", f"{label} needs one or two selected districts.")
    if state.ap < INITIATIVE_AP:
        return _blocked(kind, "", f"{label} requires {INITIATIVE_AP} AP.")
    if state.money < cost:
        return _blocked(kind, "", f"{label} requires ${cost}.")
    state.ap -= INITIATIVE_AP
    state.money -= cost
    state.initiatives = {**(state.initiatives or {}), "week": int(state.turn)}
    deltas: dict[str, dict[str, int]] = {}
    notes = []
    for cid in targets:
        profile = districts[cid]
        before = {"activity": profile.activity, "trust": profile.trust, "buyout_pressure": profile.buyout_pressure}
        if kind == "civic_action":
            group, band = _top_dissatisfaction(profile)
            if band > 0:
                profile.dissatisfaction[group] = band - 1
                notes.append(f"{profile.name} {group.replace('_', ' ')} grievance eased")
            profile.trust = min(100, profile.trust + CIVIC_ACTION_TRUST)
        else:
            profile.activity = min(100, profile.activity + MARKET_PUSH_ACTIVITY)
            profile.affordability = max(0, profile.affordability - MARKET_PUSH_AFFORDABILITY)
            if random.Random(f"market:{seed}:{state.turn}:{cid}").random() < MARKET_PUSH_SPECULATION_CHANCE:
                profile.buyout_pressure = min(100, profile.buyout_pressure + 1)
                notes.append(f"speculators circle {profile.name}")
        normalize_profile(profile)
        delta = {key: getattr(profile, key) - value for key, value in before.items() if getattr(profile, key) != value}
        if delta:
            deltas[cid] = delta
    if kind == "civic_action":
        state.trust = min(100, state.trust + 1)
    else:
        state.activity = min(100, state.activity + 1)
    names = ", ".join(districts[cid].name for cid in targets)
    note_text = f" {'; '.join(notes).capitalize()}." if notes else ""
    report = f"{label} funded in {names} (${cost}).{note_text}"
    return DecisionResult(True, kind, "", report, affected_cell_ids=targets, district_deltas=deltas, command_status="applied")


def _earmark(state: CityState, districts: dict[str, DistrictProfile], dtype: str) -> DecisionResult:
    if dtype not in DISTRICT_TYPES:
        return _blocked("earmark", "", f"Earmark needs a district type, not {dtype!r}.")
    if state.ap < EARMARK_AP:
        return _blocked("earmark", "", f"Earmark requires {EARMARK_AP} AP.")
    if state.money < EARMARK_COST:
        return _blocked("earmark", "", f"Earmark requires ${EARMARK_COST}.")
    state.ap -= EARMARK_AP
    state.money -= EARMARK_COST
    initiatives = dict(state.initiatives or {})
    earmarks = dict(active_earmarks(state))
    earmarks[dtype] = int(state.turn) + EARMARK_WEEKS
    initiatives.update(week=int(state.turn), earmarks=earmarks)
    state.initiatives = initiatives
    ledger = read_type_ledger(state, districts)
    adjust_type_ledger(ledger, dtype, capital_delta=10, appetite_delta=2)
    write_type_ledger(state, ledger)
    counts = Counter(profile.district_type for profile in districts.values() if profile.district_type != dtype)
    rivals = [name for name, _count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))[:2]]
    for rival in rivals:
        adjust_stakeholder_pressure(state, TYPE_STAKEHOLDERS[rival], 1)
    label = dtype.replace("_", " ")
    rival_text = f" {' and '.join(TYPE_STAKEHOLDERS[r].replace('_', ' ') for r in rivals)} filed objections." if rivals else ""
    report = f"Earmarked the {label} quarter for {EARMARK_WEEKS} weeks (${EARMARK_COST}).{rival_text}"
    return DecisionResult(True, "earmark", "", report, command_status="applied")


def expire_initiatives(state: CityState) -> None:
    """Drop earmarks that have run their course; called after the turn advances."""

    initiatives = dict(state.initiatives or {})
    if "earmarks" in initiatives:
        initiatives["earmarks"] = active_earmarks(state)
        state.initiatives = initiatives


__all__ = [name for name in globals() if not name.startswith("__")]
