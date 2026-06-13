"""Tests for pure Permit Office cache primitives."""

from __future__ import annotations

from copy import deepcopy

from toolbox.permit_office import cache_keys, dirty, futures
from toolbox import arcpy_permit_office_rules as rules


def _district(cell_id: str, **kwargs):
    profile = rules.DistrictProfile(
        cell_id,
        kwargs.pop("name", cell_id),
        kwargs.pop("population", 1000),
        kwargs.pop("activity", 50),
        kwargs.pop("friction", 20),
        kwargs.pop("trust", 35),
        kwargs.pop("exposure", 25),
        kwargs.pop("services", 50),
        kwargs.pop("district_type", "mercantile"),
        **kwargs,
    )
    return rules.normalize_profile(profile)


def test_state_fingerprint_is_stable_for_equal_gameplay_state():
    state = rules.CityState(turn=2, ap=1, money=35, stakeholder_heat={"vendors": 2})
    districts = {"D0000": _district("D0000"), "D0001": _district("D0001", services=42)}
    docket = [
        rules.DocketItem("CASE-2", "street_vendor_compact", "Street Vendor Compact", "POINT", 2, target_cell_ids=["D0001"]),
        rules.DocketItem("CASE-1", "procession_route", "Procession Route", "LINE", 2, target_cell_ids=["D0000"]),
    ]
    features = [rules.FeatureInstance("F-1", "street_vendor_compact", target_cell_ids=["D0000"], status="active")]
    projects = {"P-1": rules.ProjectRecord("P-1", "chain", "step", target_cell_ids=["D0001"])}

    first = cache_keys.state_fingerprint(state, districts, docket, features, projects)
    second = cache_keys.state_fingerprint(deepcopy(state), deepcopy(districts), list(reversed(deepcopy(docket))), deepcopy(features), deepcopy(projects))

    assert first == second
    assert first.rules_hash and first.display_hash and first.week_hash


def test_state_fingerprint_separates_rules_display_and_week_changes():
    state = rules.CityState(turn=2, ap=1, money=35)
    districts = {"D0000": _district("D0000")}
    docket = [rules.DocketItem("CASE-1", "procession_route", "Procession Route", "LINE", 2, target_cell_ids=["D0000"])]
    base = cache_keys.state_fingerprint(state, districts, docket)

    display_changed = deepcopy(districts)
    display_changed["D0000"].display_state = "incident"
    display_hash = cache_keys.state_fingerprint(state, display_changed, docket)

    rules_changed = deepcopy(state)
    rules_changed.ap = 0
    rules_hash = cache_keys.state_fingerprint(rules_changed, districts, docket)

    week_changed = deepcopy(docket)
    week_changed[0].status = "denied"
    week_hash = cache_keys.state_fingerprint(state, districts, week_changed)

    assert display_hash.display_hash != base.display_hash
    assert display_hash.rules_hash == base.rules_hash
    assert rules_hash.rules_hash != base.rules_hash
    assert week_hash.week_hash != base.week_hash


def test_generation_tokens_match_by_lifetime():
    generation = cache_keys.GenerationToken("game-1", 3, 7, 2)

    assert generation.matches(cache_keys.GenerationToken("game-1", 3, 7, 2), cache_keys.CacheLifetime.COMMAND)
    assert generation.matches(cache_keys.GenerationToken("game-1", 3, 99, 2), cache_keys.CacheLifetime.DOCKET)
    assert generation.matches(cache_keys.GenerationToken("game-1", 99, 99, 99), cache_keys.CacheLifetime.GAME)
    assert not generation.matches(cache_keys.GenerationToken("game-2", 3, 7, 2), cache_keys.CacheLifetime.GAME)


def test_state_fingerprint_sanitizes_live_object_mapping_pairs():
    """Verify cache hashing tolerates malformed live-state mapping data."""

    feature = rules.FeatureInstance("F-1", "street_vendor_compact", target_cell_ids=["D0000"], status="active")
    state = rules.CityState(turn=2)
    state.pending_followups = [(feature, {"pressure": 1})]
    districts = {"D0000": _district("D0000")}

    first = cache_keys.state_fingerprint(state, districts, [], [feature], game_id="game-1")
    second = cache_keys.state_fingerprint(state, districts, [], [feature], game_id="game-1")

    assert first.rules_hash == second.rules_hash


def test_dirty_bitsets_round_trip_cell_ids_and_layer_names():
    index = dirty.DistrictBitIndex.from_cell_ids(["D0002", "D0000", "D0001"])
    bits = index.to_bits(["D0001", "D0000", "missing", "D0000"])

    assert index.from_bits(bits) == ("D0000", "D0001")
    assert dirty.layer_names_from_bits(dirty.layer_bits_from_names(["points", "districts", "unknown"])) == ("districts", "points")


def test_future_snapshot_is_isolated_from_authoritative_objects():
    state = rules.CityState(ap=2, money=60)
    districts = {"D0000": _district("D0000")}
    item = rules.DocketItem("CASE-1", "street_vendor_compact", "Street Vendor Compact", "POINT", 1, target_cell_ids=["D0000"])

    snapshot = futures.FutureStateSnapshot.capture(state, districts, [item], [], {})
    state.ap = 0
    districts["D0000"].activity = 99
    item.status = "denied"

    assert snapshot.state.ap == 2
    assert snapshot.districts["D0000"].activity != 99
    assert snapshot.docket[0].status == "open"
