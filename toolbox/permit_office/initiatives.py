"""Player-started initiatives: one a week, on top of the docket (owner D6, D7).

Earmark backs one district type for a few weeks. Its districts outbid rivals
in buyouts, its kind of case comes up more often in the weekly draw, and the
stakeholders of the biggest rival types take offence.
"""

from __future__ import annotations

from collections import Counter

from .helpers import _blocked, adjust_stakeholder_pressure
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
INITIATIVE_KINDS = ("earmark",)


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
    target: str,
) -> DecisionResult:
    """Start one player initiative this week, or return why it cannot start."""

    if initiative_used_this_week(state):
        return _blocked(kind, "", "The office already filed one initiative this week.")
    if kind == "earmark":
        return _earmark(state, districts, target)
    return _blocked(kind, "", f"Unknown initiative {kind!r}.")


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
