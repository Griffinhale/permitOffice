"""Season mandates: the goal a player picks from three seeded offers (owner D9).

New Game offers three mandates drawn from the seed. The player picks one as
the season goal; every mandate met when the season closes also counts as an
achievement for that round. Thresholds are first guesses for later tuning.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import random
from typing import Callable, Iterable

from .models import CityState, DistrictProfile, FeatureInstance

GROW_QUARTER_GAIN = 3
PUBLIC_CONFIDENCE_TRUST = 55
CRITICAL_GAP = 40
BALANCED_BOOKS_MONEY = 80
EVEN_HANDED_MAX_TYPE = 8
OFFER_SIZE = 3


@dataclass(frozen=True)
class Mandate:
    """One season goal: a title, a one-line brief, and its check."""

    mandate_id: str
    title: str
    brief: str
    check: Callable[..., tuple[bool, str]]


def _type_counts(districts: dict[str, DistrictProfile]) -> dict[str, int]:
    return dict(Counter(profile.district_type for profile in districts.values()))


def _critical_gaps(districts: dict[str, DistrictProfile]) -> int:
    return sum(1 for profile in districts.values() if max((profile.service_gap or {}).values() or [0]) >= CRITICAL_GAP)


def _label(value: str) -> str:
    return value.replace("_", " ").title()


def _grow_quarter(param, state, districts, features, baseline):
    target = min(len(districts), int(baseline.get("type_counts", {}).get(param, 0)) + GROW_QUARTER_GAIN)
    count = _type_counts(districts).get(param, 0)
    return count >= target, f"{_label(param)} holds {count} of {target} districts"


def _quiet_streets(param, state, districts, features, baseline):
    aggrieved = sum(1 for profile in districts.values() if max((profile.dissatisfaction or {}).values() or [0]) >= 4)
    incidents = sum(1 for profile in districts.values() if (profile.incident_state or "none") != "none")
    return aggrieved == 0 and incidents == 0, f"{aggrieved} district(s) at grievance 4, {incidents} open incident(s)"


def _public_confidence(param, state, districts, features, baseline):
    return state.trust >= PUBLIC_CONFIDENCE_TRUST, f"trust {state.trust} of {PUBLIC_CONFIDENCE_TRUST}"


def _close_the_gaps(param, state, districts, features, baseline):
    target = int(baseline.get("critical_gaps", 0)) // 3
    count = _critical_gaps(districts)
    return count <= target, f"{count} critical service gap(s), target {target} or fewer"


def _balanced_books(param, state, districts, features, baseline):
    failed = sum(1 for feature in features or () if getattr(feature, "status", "") == "failed")
    met = state.money >= BALANCED_BOOKS_MONEY and state.last_net >= 0 and failed == 0
    return met, f"${state.money} of ${BALANCED_BOOKS_MONEY}, net {state.last_net:+d}, {failed} failed feature(s)"


def _even_handed(param, state, districts, features, baseline):
    counts = _type_counts(districts)
    largest = max(counts.values() or [0])
    lost = sorted(name for name in baseline.get("type_counts", {}) if counts.get(name, 0) == 0)
    met = largest <= EVEN_HANDED_MAX_TYPE and not lost
    lost_text = f", lost {', '.join(_label(name) for name in lost)}" if lost else ""
    return met, f"largest type {largest} of {EVEN_HANDED_MAX_TYPE} districts{lost_text}"


MANDATES: dict[str, Mandate] = {
    "grow_quarter": Mandate("grow_quarter", "Grow the {param} quarter", "{param} holds {gain} more districts by the final week.", _grow_quarter),
    "quiet_streets": Mandate("quiet_streets", "Quiet streets", "No group at grievance 4 and no open incident by the final week.", _quiet_streets),
    "public_confidence": Mandate("public_confidence", "Public confidence", f"City trust at {PUBLIC_CONFIDENCE_TRUST} or more by the final week.", _public_confidence),
    "close_the_gaps": Mandate("close_the_gaps", "Close the gaps", "Critical service gaps cut to a third of the starting count.", _close_the_gaps),
    "balanced_books": Mandate("balanced_books", "Balanced books", f"${BALANCED_BOOKS_MONEY} or more, a non-negative net, and no failed features.", _balanced_books),
    "even_handed": Mandate("even_handed", "Even-handed city", f"No district type over {EVEN_HANDED_MAX_TYPE} districts, and every starting type keeps one.", _even_handed),
}
MANDATE_IDS = tuple(MANDATES)


def _split(key: str) -> tuple[Mandate | None, str]:
    base, _, param = str(key or "").partition(":")
    return MANDATES.get(base), param


def mandate_title(key: str) -> str:
    """Return the player-facing title for a mandate key, or '' when unknown."""

    mandate, param = _split(key)
    return mandate.title.format(param=_label(param)) if mandate else ""


def mandate_brief(key: str) -> str:
    """Return the one-line brief for a mandate key, or '' when unknown."""

    mandate, param = _split(key)
    return mandate.brief.format(param=_label(param), gain=GROW_QUARTER_GAIN) if mandate else ""


def offer_mandates(seed: int, districts: dict[str, DistrictProfile]) -> dict[str, object]:
    """Draw the season's three-mandate offer and the baseline its checks compare against."""

    counts = _type_counts(districts)
    rng = random.Random(f"mandate:{seed}")
    largest = max(counts.values() or [0])
    growable = sorted(name for name, count in counts.items() if count < largest)
    pool = [key for key in MANDATE_IDS if key != "grow_quarter" or growable]
    offer = []
    for key in rng.sample(pool, k=min(OFFER_SIZE, len(pool))):
        offer.append(f"grow_quarter:{rng.choice(growable)}" if key == "grow_quarter" else key)
    return {"offer": offer, "chosen": "", "baseline": {"type_counts": dict(sorted(counts.items())), "critical_gaps": _critical_gaps(districts)}}


def choose_mandate(state: CityState, key: str) -> bool:
    """Record the player's season goal; only an offered mandate, and only once."""

    mandate = state.mandate or {}
    if mandate.get("chosen") or key not in (mandate.get("offer") or ()):
        return False
    state.mandate = {**mandate, "chosen": key}
    return True


def mandate_status(
    key: str,
    state: CityState,
    districts: dict[str, DistrictProfile],
    features: Iterable[FeatureInstance] = (),
    baseline: dict[str, object] | None = None,
) -> tuple[bool, str]:
    """Return (met, progress line) for one mandate key against the current city."""

    mandate, param = _split(key)
    if mandate is None:
        return False, ""
    baseline = baseline if baseline is not None else (state.mandate or {}).get("baseline", {})
    return mandate.check(param, state, districts, list(features or ()), baseline or {})


def season_achievements(state: CityState, districts: dict[str, DistrictProfile], features: Iterable[FeatureInstance] = ()) -> list[str]:
    """Return the titles of every mandate the city meets now, offered or not."""

    baseline = (state.mandate or {}).get("baseline", {})
    keys = [key for key in MANDATE_IDS if key != "grow_quarter"]
    keys += [f"grow_quarter:{name}" for name in sorted(baseline.get("type_counts", {}))]
    feature_list = list(features or ())
    return [mandate_title(key) for key in keys if mandate_status(key, state, districts, feature_list, baseline)[0]]


__all__ = [name for name in globals() if not name.startswith("__")]
