"""District buyout transition rules for hidden type pressure."""

from __future__ import annotations
from .initiatives import EARMARK_BID_BONUS, active_earmarks

from dataclasses import dataclass, field
import random
from typing import Mapping

from .catalogs import ARCHETYPE_BASE_MIX
from .helpers import district_label, normalize_profile
from .models import DISTRICT_TYPES, CityState, DistrictProfile
from .type_pressure import adjust_type_ledger


# Type-flavored name suffixes so a converted district's name signals its new
# identity (keeping the original prefix for continuity, e.g. "Cinder Yard" ->
# civic -> "Cinder Hall"). This makes buyouts legible on the map/dashboard
# without the player inspecting raw district_type columns.
_TYPE_NAME_SUFFIXES: dict[str, tuple[str, ...]] = {
    "residential": ("Quarter", "Terrace", "Commons", "Gardens"),
    "mercantile": ("Market", "Exchange", "Bazaar", "Arcade"),
    "industrial": ("Works", "Foundry", "Forge", "Yard"),
    "civic": ("Hall", "Plaza", "Court", "Square"),
    "academic": ("Campus", "College", "Quad", "Library"),
    "natural": ("Green", "Reserve", "Meadow", "Park"),
}


def _rename_for_type(profile: DistrictProfile, new_type: str) -> None:
    """Relabel a converted district with a name suffix that fits its new type."""

    suffixes = _TYPE_NAME_SUFFIXES.get(new_type)
    if not suffixes:
        return
    parts = district_label(profile).split()
    prefix = parts[0] if parts else profile.cell_id
    rng = random.Random(f"rename:{profile.cell_id}:{new_type}")
    profile.name = f"{prefix} {rng.choice(suffixes)}"


def _shift_culture_for_type(profile: DistrictProfile, new_type: str) -> None:
    """Blend a converted district's census toward its new type's archetype mix.

    The new type's two leading archetype groups rise into the dominant band so the
    district reads -- and generates proposals -- as its new identity, while the
    prior strongest group decays. This is the culture half of the type/culture/
    resource shift a successful buyout brings.
    """

    base = ARCHETYPE_BASE_MIX.get(new_type)
    if not base:
        return
    mix = dict(profile.population_mix or {})
    if mix:
        strongest = max(mix, key=lambda group: (mix.get(group, 0), group))
        if mix.get(strongest, 0) > 0:
            mix[strongest] = mix[strongest] - 1
    leading = sorted(base, key=lambda group: (-base[group], group))[:2]
    for group in leading:
        mix[group] = min(3, max(mix.get(group, 0) + 1, 2))
    profile.population_mix = mix


@dataclass
class BuyoutRoundResult:
    """Result of one buyout bidding round."""

    started: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    report: str = ""


@dataclass
class TransitionResult:
    """Result of resolving due contested district transitions."""

    converted: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    report: str = ""


def resolve_buyout_round(
    state: CityState,
    districts: dict[str, DistrictProfile],
    ledger: dict[str, dict[str, int]],
    seed: int = 2026,
) -> BuyoutRoundResult:
    """Start deterministic contested transitions for weak adjacent districts."""

    if not districts:
        return BuyoutRoundResult()

    rng = random.Random(f"buyout:{seed}:{state.turn}")
    earmarks = active_earmarks(state)
    started: list[str] = []
    refused: list[str] = []
    reports: list[str] = []
    for cell_id in sorted(districts):
        target = districts[cell_id]
        if not _eligible_target(cell_id, target, districts):
            continue
        bidders = _eligible_bidders(target, districts, ledger, rng, earmarks)
        if not bidders:
            continue
        # Each resourced neighbor independently decides whether to file a bid this
        # round, so a field of eligible bidders can shrink to one, several, or
        # none. The strongest willing bidder (bidders stay score-sorted) leads.
        willing = [
            bidder
            for bidder in bidders
            if bidder.district_type in earmarks or _bidder_is_willing(bidder, target, ledger, seed, state.turn)
        ]
        if not willing:
            continue
        bidder = willing[0]
        if _target_refuses_buyout(target, rng):
            refused.append(cell_id)
            target.last_buyout_report = (
                f"{target.name} refused a {bidder.district_type} buyout bid from {bidder.name}; "
                f"local leverage remained high enough to resist."
            )
            reports.append(target.last_buyout_report)
            adjust_type_ledger(ledger, bidder.district_type, appetite_delta=-1, fatigue_delta=1)
            continue
        _start_contested_transition(state, target, bidder, ledger, bid_count=len(willing))
        started.append(cell_id)
        reports.append(target.last_buyout_report)

    return BuyoutRoundResult(started, refused, " ".join(reports))


def resolve_contested_transitions(
    state: CityState,
    districts: dict[str, DistrictProfile],
    ledger: dict[str, dict[str, int]],
) -> TransitionResult:
    """Resolve contested buyouts whose due turn has arrived."""

    if not districts:
        return TransitionResult()

    converted: list[str] = []
    cancelled: list[str] = []
    reports: list[str] = []
    for cell_id in sorted(districts):
        profile = districts[cell_id]
        if profile.identity_state != "contested":
            continue
        if int(profile.transition_due_turn or 0) > state.turn:
            continue
        if profile.activity >= 50 and profile.buyout_pressure <= 1:
            _cancel_transition(profile)
            cancelled.append(cell_id)
            reports.append(f"{profile.name} stabilized and cancelled its contested buyout.")
            continue
        if profile.activity < 50 or profile.buyout_pressure > 1:
            new_type = _contesting_type(profile, districts)
            if not new_type:
                _cancel_transition(profile)
                cancelled.append(cell_id)
                reports.append(f"{profile.name} cancelled a contested buyout with no active sponsor.")
                continue
            old_type = profile.district_type
            old_name = profile.name
            _convert_transition(profile, new_type, ledger)
            converted.append(cell_id)
            reports.append(
                f"{old_name} converted from {old_type} to {new_type} after contested buyout pressure held "
                f"and is now {profile.name}."
            )

    return TransitionResult(converted, cancelled, " ".join(reports))


def reduce_buyout_pressure(profile: DistrictProfile, amount: int = 2) -> DistrictProfile:
    """Reduce hidden buyout pressure on a district after successful attention."""

    current = max(0, min(100, int(profile.buyout_pressure or 0)))
    if current == 0:
        return profile
    try:
        reduction = int(amount or 0)
    except (TypeError, ValueError):
        reduction = 0
    profile.buyout_pressure = max(0, current - max(0, reduction))
    if profile.identity_state == "vulnerable" and profile.buyout_pressure == 0:
        profile.identity_state = "stable"
    normalize_profile(profile)
    return profile


def _eligible_target(
    cell_id: str,
    target: DistrictProfile,
    districts: Mapping[str, DistrictProfile],
) -> bool:
    """Return whether a district can be targeted by a buyout bid."""

    if target.activity >= 50:
        return False
    if target.identity_state in {"contested", "converted"}:
        return False
    if target.incident_state != "none":
        return False
    return any(adjacent_id in districts and adjacent_id != cell_id for adjacent_id in target.adjacent_cell_ids)


def _eligible_bidders(
    target: DistrictProfile,
    districts: Mapping[str, DistrictProfile],
    ledger: Mapping[str, Mapping[str, int]],
    rng: random.Random,
    earmarks: Mapping[str, int] | None = None,
) -> list[DistrictProfile]:
    """Return adjacent bidders ordered by hidden pressure strength; earmarked types lead."""

    bidders: list[DistrictProfile] = []
    for adjacent_id in sorted(target.adjacent_cell_ids):
        bidder = districts.get(adjacent_id)
        if bidder is None:
            continue
        if bidder.identity_state == "contested":
            continue
        if bidder.district_type == target.district_type:
            continue
        if bidder.activity < target.activity + 8:
            continue
        entry = ledger.get(bidder.district_type, {})
        if int(entry.get("capital", 0) or 0) <= 0 or int(entry.get("appetite", 0) or 0) <= 0:
            continue
        bidders.append(bidder)

    rng.shuffle(bidders)
    bonus = {dtype: EARMARK_BID_BONUS for dtype in (earmarks or {})}
    return sorted(bidders, key=lambda bidder: _bidder_score(bidder, ledger) + bonus.get(bidder.district_type, 0), reverse=True)


def _bidder_is_willing(
    bidder: DistrictProfile,
    target: DistrictProfile,
    ledger: Mapping[str, Mapping[str, int]],
    seed: int,
    turn: int,
) -> bool:
    """Return whether a resourced neighbor chooses to file a bid this round.

    Willingness rises with the bidder's prosperity advantage over the target and
    with its type's capital/appetite, and falls with fatigue/overextension, so a
    flush, eager type bids reliably while a stretched or marginal one often
    abstains. Uses a side RNG stream keyed by the pairing so the willingness draw
    never disturbs the shared draw order that bidder shuffling and target refusal
    depend on.
    """

    entry = ledger.get(bidder.district_type, {})
    capital = int(entry.get("capital", 0) or 0)
    appetite = int(entry.get("appetite", 0) or 0)
    fatigue = int(entry.get("fatigue", 0) or 0)
    overextension = int(entry.get("overextension", 0) or 0)
    advantage = max(0, int(bidder.activity or 0) - int(target.activity or 0))
    chance = (
        0.25
        + advantage * 0.01
        + capital * 0.01
        + appetite * 0.05
        - fatigue * 0.05
        - overextension * 0.08
    )
    chance = max(0.05, min(1.0, chance))
    rng = random.Random(f"willing:{seed}:{turn}:{bidder.cell_id}:{target.cell_id}")
    return rng.random() < chance


def _target_refuses_buyout(target: DistrictProfile, rng: random.Random) -> bool:
    """Return whether target leverage blocks the current buyout bid."""

    activity = max(0, min(100, int(target.activity or 0)))
    pressure = max(0, min(100, int(target.buyout_pressure or 0)))
    refusal_chance = max(0.0, min(0.65, (activity - 30) * 0.025 - pressure * 0.06))
    return rng.random() < refusal_chance


def _bidder_score(
    bidder: DistrictProfile,
    ledger: Mapping[str, Mapping[str, int]],
) -> int:
    """Score one bidder using visible activity and hidden type pressure."""

    entry = ledger.get(bidder.district_type, {})
    return (
        bidder.activity
        + int(entry.get("capital", 0) or 0)
        + int(entry.get("appetite", 0) or 0) * 3
        - int(entry.get("fatigue", 0) or 0) * 2
        - int(entry.get("overextension", 0) or 0) * 3
    )


def _start_contested_transition(
    state: CityState,
    target: DistrictProfile,
    bidder: DistrictProfile,
    ledger: dict[str, dict[str, int]],
    bid_count: int = 1,
) -> None:
    """Mutate target and ledger for a newly filed contested transition.

    A contested field of more than one willing bidder costs the winner extra
    capital and overextension: outbidding rivals is the upside-with-risk lever
    that can stretch an aggressive type thin across the map.
    """

    rivals = max(0, int(bid_count) - 1)
    competition_clause = ""
    if rivals:
        plural = "s" if rivals != 1 else ""
        competition_clause = f" {bidder.name} outbid {rivals} rival bid{plural} to lead the contest."
    target.identity_state = "contested"
    target.contesting_cell_id = bidder.cell_id
    target.contesting_type = bidder.district_type
    target.transition_due_turn = state.turn + 1
    target.last_buyout_report = (
        f"{target.name} entered contested buyout from {bidder.name}; "
        f"{bidder.district_type} bid cleared local leverage after weak activity and pressure;"
        f"{competition_clause} "
        "office attention can still stabilize the district before conversion."
    )
    adjust_type_ledger(
        ledger,
        bidder.district_type,
        capital_delta=-10 - 3 * rivals,
        appetite_delta=-3,
        fatigue_delta=2,
        overextension_delta=1 + rivals,
    )
    adjust_type_ledger(ledger, target.district_type, fatigue_delta=1)
    normalize_profile(target)


def _contesting_type(profile: DistrictProfile, districts: Mapping[str, DistrictProfile]) -> str:
    """Return the active contesting type for a due transition, if valid."""

    if profile.contesting_type in DISTRICT_TYPES and profile.contesting_type != profile.district_type:
        return profile.contesting_type
    bidder = districts.get(profile.contesting_cell_id)
    if bidder and bidder.district_type in DISTRICT_TYPES and bidder.district_type != profile.district_type:
        return bidder.district_type
    return ""


def _convert_transition(
    profile: DistrictProfile,
    new_type: str,
    ledger: dict[str, dict[str, int]],
) -> None:
    """Convert a contested district to its bidder type and update pressure."""

    old_type = profile.district_type
    old_name = profile.name
    profile.prior_district_type = old_type
    profile.district_type = new_type
    profile.land_use = ""
    profile.identity_state = "converted"
    profile.contesting_cell_id = ""
    profile.contesting_type = ""
    profile.transition_due_turn = 0
    profile.buyout_pressure = max(0, int(profile.buyout_pressure or 0) - 2)
    profile.activity = max(0, min(100, int(profile.activity or 0) + 4))
    profile.friction = max(0, min(100, int(profile.friction or 0) + 4))
    _shift_culture_for_type(profile, new_type)
    _rename_for_type(profile, new_type)
    profile.last_buyout_report = f"{old_name} converted from {old_type} to {new_type} and is now {profile.name}."
    adjust_type_ledger(ledger, old_type, holdings_delta=-1, fatigue_delta=1)
    adjust_type_ledger(
        ledger,
        new_type,
        capital_delta=-5,
        holdings_delta=1,
        fatigue_delta=2,
        overextension_delta=1,
    )
    normalize_profile(profile)


def _cancel_transition(profile: DistrictProfile) -> None:
    """Clear contested transition fields after successful stabilization."""

    profile.identity_state = "stable"
    profile.contesting_cell_id = ""
    profile.contesting_type = ""
    profile.transition_due_turn = 0
    profile.buyout_pressure = max(0, min(1, int(profile.buyout_pressure or 0)))
    profile.last_buyout_report = f"{profile.name} cancelled a contested buyout."
    normalize_profile(profile)


__all__ = [name for name in globals() if not name.startswith("__")]
