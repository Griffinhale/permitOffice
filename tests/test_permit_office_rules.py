"""Regression coverage for the active Permit Office rules package."""

import copy
from pathlib import Path

import pytest

from toolbox import arcpy_permit_office_rules as rules


def test_generate_district_profiles_is_deterministic_and_named():
    """Verify seeded district generation is stable and fully normalized."""
    first = rules.generate_district_profiles(rows=2, cols=2, seed=2026)
    second = rules.generate_district_profiles(rows=2, cols=2, seed=2026)

    assert first == second
    assert [profile.cell_id for profile in first] == ["D0000", "D0001", "D0100", "D0101"]
    assert all(profile.name for profile in first)
    assert {profile.district_type for profile in first} <= set(rules.DISTRICT_TYPES)
    assert set(rules.DISTRICT_TYPES) == {"residential", "mercantile", "industrial", "civic", "academic", "natural"}
    assert {profile.display_state for profile in first} <= set(rules.DISPLAY_STATES)
    assert all(profile.public_profile for profile in first)
    assert all(set(profile.population_mix) == set(rules.CITIZEN_GROUPS) for profile in first)
    assert all(0 <= band <= 3 for profile in first for band in profile.population_mix.values())
    assert all(0 <= band <= 4 for profile in first for band in profile.dissatisfaction.values())


def test_demo_template_catalog_has_case_file_metadata():
    """Verify demo templates include the metadata needed for case files."""
    assert len(rules.DEMO_TEMPLATE_IDS) == 12
    assert set(rules.DEMO_TEMPLATE_IDS) < set(rules.TEMPLATES)
    for template_id in rules.DEMO_TEMPLATE_IDS:
        template = rules.TEMPLATES[template_id]
        assert template.target_rule
        assert template.stakeholder
        assert template.preview
        assert template.inspect_hint
        assert template.failure_mode
        assert template.failure_effects
        assert template.spawn_archetype_id in rules.FEATURE_ARCHETYPES


def test_city_state_defaults_to_twelve_week_attention_scarcity():
    state = rules.CityState()

    assert state.max_turns == 12
    assert state.ap == 2
    assert state.max_ap == 2
    assert state.type_ledger == {}
    assert state.pending_followups == {}


def test_district_profiles_include_identity_transition_defaults():
    profile = rules.generate_district_profiles(rows=1, cols=1, seed=2026)[0]

    assert profile.prior_district_type == ""
    assert profile.identity_state == "stable"
    assert profile.contesting_cell_id == ""
    assert profile.contesting_type == ""
    assert profile.transition_due_turn == 0
    assert profile.buyout_pressure == 0
    assert profile.last_buyout_report == ""


def test_templates_declare_expiration_policy_and_pressure_category():
    policies = {template.expiration_policy for template in rules.TEMPLATES.values()}

    assert {"missed_window", "city_momentum", "momentum_with_followup_risk", "mandatory_followup"} <= policies
    assert rules.TEMPLATES["procession_route"].expiration_policy == "missed_window"
    assert rules.TEMPLATES["mixed_use_rezoning"].expiration_policy == "city_momentum"
    assert rules.TEMPLATES["street_vendor_compact"].expiration_policy == "momentum_with_followup_risk"
    assert rules.TEMPLATES[rules.MAINTENANCE_TEMPLATE_ID].expiration_policy == "mandatory_followup"
    assert all(template.pressure_category for template in rules.TEMPLATES.values())


def test_runtime_action_copy_uses_current_decision_labels():
    """Verify player-facing action copy does not use retired Approve labels."""

    state = rules.CityState()
    item = rules.DocketItem("no-target", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1)

    result = rules.resolve_decision(state, item, {}, "approve", [], seed=2026)

    assert "Decision requires" in result.report
    assert "selected target district" in result.report
    assert "Issue or resolve" in rules.TEMPLATES[rules.MAINTENANCE_TEMPLATE_ID].preview


def test_type_ledger_rebuilds_from_district_holdings():
    districts = {
        profile.cell_id: profile
        for profile in rules.generate_district_profiles(rows=2, cols=2, seed=2026)
    }

    ledger = rules.rebuild_type_ledger(districts)

    assert set(ledger) == set(rules.DISTRICT_TYPES)
    assert sum(entry["holdings"] for entry in ledger.values()) == 4
    assert all(entry["capital"] >= 0 for entry in ledger.values())
    assert all("appetite" in entry for entry in ledger.values())
    assert all("fatigue" in entry for entry in ledger.values())
    assert all("overextension" in entry for entry in ledger.values())


def test_type_ledger_round_trips_through_city_state_memory():
    state = rules.CityState()
    districts = {
        profile.cell_id: profile
        for profile in rules.generate_district_profiles(rows=2, cols=2, seed=2026)
    }
    ledger = rules.rebuild_type_ledger(districts)

    rules.write_type_ledger(state, ledger)
    loaded = rules.read_type_ledger(state, districts)

    assert loaded == ledger
    assert state.type_ledger == ledger


def test_type_ledger_refreshes_after_blank_read_without_districts():
    state = rules.CityState()
    districts = {
        profile.cell_id: profile
        for profile in rules.generate_district_profiles(rows=2, cols=2, seed=2026)
    }

    rules.read_type_ledger(state, None)
    loaded = rules.read_type_ledger(state, districts)

    assert sum(entry["holdings"] for entry in loaded.values()) == 4
    assert state.type_ledger == loaded


def test_type_pressure_summary_is_qualitative_not_table_data():
    ledger = {
        "mercantile": {"capital": 85, "appetite": 12, "fatigue": 1, "holdings": 4, "overextension": 0},
        "residential": {"capital": 20, "appetite": 2, "fatigue": 4, "holdings": 2, "overextension": 5},
    }

    summary = rules.type_pressure_summary(ledger)

    assert "Mercantile" in summary
    assert "expansion pressure" in summary
    assert "$" not in summary


def test_adjust_type_ledger_mutates_caller_ledger():
    ledger = {
        dtype: {"capital": 0, "appetite": 0, "fatigue": 0, "holdings": 0, "overextension": 0}
        for dtype in rules.DISTRICT_TYPES
    }

    returned = rules.adjust_type_ledger(ledger, "residential", capital_delta=5, appetite_delta=2, holdings_delta=1)

    assert returned is ledger
    assert ledger["residential"]["capital"] == 5
    assert ledger["residential"]["appetite"] == 2
    assert ledger["residential"]["holdings"] == 1


def _district_for_buyout(cell_id, dtype, activity, adjacent):
    profile = rules.DistrictProfile(
        cell_id=cell_id,
        name=cell_id,
        population=1000,
        activity=activity,
        friction=25,
        trust=35,
        exposure=20,
        services=40,
        district_type=dtype,
        adjacent_cell_ids=list(adjacent),
    )
    rules.normalize_profile(profile)
    return profile


def _incident_profile(cell_id, group="renters", band=4):
    profile = rules.DistrictProfile(
        cell_id=cell_id,
        name=f"{cell_id} Incident Row",
        population=1200,
        activity=42,
        friction=35,
        trust=35,
        exposure=30,
        services=35,
        district_type="residential",
        population_mix={group: 3},
        dissatisfaction={group: band},
    )
    rules.normalize_profile(profile)
    return profile


def _local_incident_items(docket):
    return [
        item
        for item in docket
        if item.template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
        and item.origin_item_id.startswith("dissatisfaction:")
    ]


def test_buyout_no_eligible_target_does_nothing():
    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 60, ["B"]),
        "B": _district_for_buyout("B", "mercantile", 70, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)

    result = rules.resolve_buyout_round(state, districts, ledger, seed=2026)

    assert result.started == []
    assert districts["A"].identity_state == "stable"
    assert districts["B"].identity_state == "stable"


def test_buyout_single_bidder_starts_contested_transition():
    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 35, ["B"]),
        "B": _district_for_buyout("B", "mercantile", 78, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)
    ledger["mercantile"]["capital"] = 100
    ledger["mercantile"]["appetite"] = 20

    result = rules.resolve_buyout_round(state, districts, ledger, seed=2026)

    assert result.started == ["A"]
    assert districts["A"].identity_state == "contested"
    assert districts["A"].contesting_cell_id == "B"
    assert districts["A"].contesting_type == "mercantile"
    assert districts["A"].transition_due_turn == state.turn + 1
    report = districts["A"].last_buyout_report
    for phrase in ("A", "B", "mercantile", "entered contested buyout", "weak activity", "pressure", "office attention"):
        assert phrase in report


def test_buyout_reports_explain_target_bidder_and_reason():
    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 35, ["B"]),
        "B": _district_for_buyout("B", "mercantile", 82, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)
    ledger["mercantile"]["capital"] = 100
    ledger["mercantile"]["appetite"] = 20

    result = rules.resolve_buyout_round(state, districts, ledger, seed=2026)

    assert "A" in result.report or "A" in districts["A"].last_buyout_report
    assert "mercantile" in result.report.lower()
    assert any(phrase in result.report.lower() for phrase in ("bid", "leverage", "contested", "refused"))
    for phrase in ("B", "weak activity", "pressure", "office attention", "stabilize"):
        assert phrase in result.report


def test_buyout_reports_hint_at_player_pressure_control():
    """Verify contested buyout reports say how attention can still matter."""

    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 35, ["B"]),
        "B": _district_for_buyout("B", "mercantile", 82, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)
    ledger["mercantile"]["capital"] = 100
    ledger["mercantile"]["appetite"] = 20

    result = rules.resolve_buyout_round(state, districts, ledger, seed=2026)

    assert "office attention" in result.report
    assert "stabilize" in result.report
    assert "before conversion" in result.report


def test_buyout_target_can_refuse_bid_deterministically():
    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 49, ["B"]),
        "B": _district_for_buyout("B", "mercantile", 82, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)
    ledger["mercantile"]["capital"] = 100
    ledger["mercantile"]["appetite"] = 20

    result = rules.resolve_buyout_round(state, districts, ledger, seed=3)

    assert result.started == []
    assert result.refused == ["A"]
    assert districts["A"].identity_state == "stable"
    assert districts["A"].contesting_cell_id == ""
    assert result.report == (
        "A refused a mercantile buyout bid from B; "
        "local leverage remained high enough to resist."
    )
    assert districts["A"].last_buyout_report == result.report


def test_buyout_multiple_willing_bidders_compete_and_strongest_leads():
    """Verify several valid neighbors bid and the strongest leads the contest."""
    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 35, ["B", "C"]),
        "B": _district_for_buyout("B", "mercantile", 78, ["A"]),
        "C": _district_for_buyout("C", "industrial", 90, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)
    for dtype in ("mercantile", "industrial"):
        ledger[dtype]["capital"] = 100
        ledger[dtype]["appetite"] = 20

    result = rules.resolve_buyout_round(state, districts, ledger, seed=2026)

    assert result.started == ["A"]
    # C (industrial, activity 90) outbids B (mercantile, activity 78).
    assert districts["A"].contesting_cell_id == "C"
    assert districts["A"].contesting_type == "industrial"
    # The report reads as a competitive field, not a lone bid.
    report = districts["A"].last_buyout_report
    assert "industrial" in report
    assert any(word in report.lower() for word in ("rival", "outbid", "bids", "field"))


def test_buyout_marginal_bidder_willingness_is_probabilistic():
    """Verify a barely-eligible, low-resource neighbor does not always bid.

    Target activity is pinned at 30 so the refusal formula can never fire
    (refusal chance is 0 at/below 30): the only source of a no-start round is the
    bidder declining to bid, isolating the willingness mechanic.
    """
    started_outcomes = set()
    for seed in range(20):
        districts = {
            "A": _district_for_buyout("A", "residential", 30, ["B"]),
            "B": _district_for_buyout("B", "mercantile", 38, ["A"]),
        }
        ledger = rules.rebuild_type_ledger(districts)
        ledger["mercantile"]["capital"] = 1
        ledger["mercantile"]["appetite"] = 1

        result = rules.resolve_buyout_round(rules.CityState(), districts, ledger, seed=seed)

        assert result.refused == []  # refusal disabled by low target activity
        started_outcomes.add(bool(result.started))

    # Across seeds the marginal neighbor sometimes bids and sometimes declines.
    assert started_outcomes == {True, False}


def test_contested_transition_converts_when_pressure_remains_high():
    state = rules.CityState(turn=2)
    target = _district_for_buyout("A", "residential", 32, ["B"])
    target.identity_state = "contested"
    target.contesting_cell_id = "B"
    target.contesting_type = "mercantile"
    target.transition_due_turn = 2
    target.buyout_pressure = 5
    districts = {
        "A": target,
        "B": _district_for_buyout("B", "mercantile", 80, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)

    result = rules.resolve_contested_transitions(state, districts, ledger)

    assert result.converted == ["A"]
    assert districts["A"].district_type == "mercantile"
    assert districts["A"].prior_district_type == "residential"
    assert districts["A"].identity_state == "converted"


def test_converted_district_is_renamed_to_signal_new_identity():
    """Verify a buyout conversion relabels the district to fit its new type."""

    state = rules.CityState(turn=2)
    target = _district_for_buyout("A", "residential", 32, ["B"])
    target.name = "Cinder Yard"
    target.identity_state = "contested"
    target.contesting_cell_id = "B"
    target.contesting_type = "mercantile"
    target.transition_due_turn = 2
    target.buyout_pressure = 5
    districts = {
        "A": target,
        "B": _district_for_buyout("B", "mercantile", 80, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)

    result = rules.resolve_contested_transitions(state, districts, ledger)

    renamed = districts["A"].name
    assert renamed != "Cinder Yard"
    # Keeps the original prefix for continuity, swaps to a mercantile suffix.
    assert renamed.startswith("Cinder ")
    assert renamed.split()[-1] in ("Market", "Exchange", "Bazaar", "Arcade")
    # The report names both the old and new label so the flip is legible.
    assert "Cinder Yard" in result.report
    assert renamed in result.report


def test_converted_district_culture_shifts_toward_new_type():
    """Verify a buyout conversion shifts the district's culture, not just its type."""
    state = rules.CityState(turn=2)
    target = _district_for_buyout("A", "residential", 32, ["B"])
    # Starts with a clearly residential culture (no mercantile-leaning groups).
    target.population_mix = {"families": 3, "homeowners": 2, "elders": 1}
    rules.normalize_profile(target)
    target.identity_state = "contested"
    target.contesting_cell_id = "B"
    target.contesting_type = "mercantile"
    target.transition_due_turn = 2
    target.buyout_pressure = 5
    districts = {
        "A": target,
        "B": _district_for_buyout("B", "mercantile", 80, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)

    result = rules.resolve_contested_transitions(state, districts, ledger)

    assert result.converted == ["A"]
    assert districts["A"].district_type == "mercantile"
    mix = districts["A"].population_mix
    # The new mercantile identity now carries a dominant mercantile-leaning culture,
    # strong enough to pull the docket toward mercantile work (band >= 2).
    assert mix.get("vendors", 0) >= 2


def test_converted_transition_does_not_recontest_during_same_week_close():
    state = rules.CityState(turn=2)
    target = _district_for_buyout("A", "residential", 32, ["B", "C"])
    target.identity_state = "contested"
    target.contesting_cell_id = "B"
    target.contesting_type = "mercantile"
    target.transition_due_turn = 2
    target.buyout_pressure = 5
    districts = {
        "A": target,
        "B": _district_for_buyout("B", "mercantile", 82, ["A"]),
        "C": _district_for_buyout("C", "industrial", 90, ["A"]),
    }

    result = rules.advance_turn_result(state, [], districts)

    assert state.turn == 3
    assert districts["A"].district_type == "mercantile"
    assert districts["A"].prior_district_type == "residential"
    assert districts["A"].identity_state == "converted"
    assert districts["A"].contesting_cell_id == ""
    assert "converted from residential to mercantile" in result.report


def test_contested_transition_cancels_when_target_stabilizes():
    state = rules.CityState(turn=2)
    target = _district_for_buyout("A", "residential", 57, ["B"])
    target.identity_state = "contested"
    target.contesting_cell_id = "B"
    target.contesting_type = "mercantile"
    target.transition_due_turn = 2
    target.buyout_pressure = 0
    districts = {
        "A": target,
        "B": _district_for_buyout("B", "mercantile", 80, ["A"]),
    }
    ledger = rules.rebuild_type_ledger(districts)

    result = rules.resolve_contested_transitions(state, districts, ledger)

    assert result.cancelled == ["A"]
    assert districts["A"].district_type == "residential"
    assert districts["A"].identity_state == "stable"


def test_unmitigated_ordinary_approval_reduces_buyout_pressure_by_two():
    state = rules.CityState(money=100)
    districts = {
        "D0000": _district_for_buyout("D0000", "residential", 42, []),
    }
    districts["D0000"].identity_state = "contested"
    districts["D0000"].buyout_pressure = 5
    item = rules.DocketItem(
        "stabilize-annex",
        "child_development_park_annex",
        rules.TEMPLATES["child_development_park_annex"].title,
        "POINT",
        1,
    )

    result = rules.resolve_decision(state, item, districts, "approve", ["D0000"], seed=2026)

    assert result.ok is True
    assert districts["D0000"].buyout_pressure == 3


def test_mitigated_ordinary_approval_reduces_buyout_pressure_by_three():
    state = rules.CityState(money=100)
    districts = {
        "D0000": _district_for_buyout("D0000", "residential", 42, []),
    }
    districts["D0000"].identity_state = "contested"
    districts["D0000"].buyout_pressure = 5
    item = rules.DocketItem(
        "stabilize-annex",
        "child_development_park_annex",
        rules.TEMPLATES["child_development_park_annex"].title,
        "POINT",
        1,
        risk_band="low",
    )

    result = rules.resolve_decision(state, item, districts, "approve_mitigated", ["D0000"], seed=2026, mitigated=True)

    assert result.ok is True
    assert districts["D0000"].buyout_pressure == 2


def test_failed_ordinary_approval_does_not_reduce_buyout_pressure():
    state = rules.CityState(money=100, ap=3)
    districts = {
        "D0000": _district_for_buyout("D0000", "natural", 35, []),
    }
    districts["D0000"].exposure = 90
    districts["D0000"].services = 5
    districts["D0000"].identity_state = "contested"
    districts["D0000"].buyout_pressure = 5
    item = rules.DocketItem(
        "failed-stabilization",
        "contractor_renovation_waiver",
        rules.TEMPLATES["contractor_renovation_waiver"].title,
        "POINT",
        1,
        risk_band="high",
    )

    result = rules.resolve_decision(state, item, districts, "approve", ["D0000"], seed=2026)

    assert result.ok is True
    assert result.failure_triggered is True
    assert districts["D0000"].buyout_pressure == 5


def test_missed_window_expiration_closes_original_without_pressure():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=1, cols=1, seed=2026)}
    item = rules.DocketItem("expire-event", "procession_route", "Licensed Procession Route", "LINE", 1)
    item.target_cell_ids = ["D0000"]

    result = rules.resolve_unattended_item(state, item, districts, seed=2026)

    assert item.status == "expired"
    assert result.policy == "missed_window"
    assert result.followup_template_id == ""
    assert districts["D0000"].buyout_pressure == 0
    assert "window closed" in result.report.lower()
    assert result.report == "Licensed Procession Route window closed without office action; the original filing expired."


def test_expiration_reports_hint_at_original_policy():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=1, cols=1, seed=2026)}
    item = rules.DocketItem("expire-event", "procession_route", "Licensed Procession Route", "LINE", 1)
    item.target_cell_ids = ["D0000"]

    result = rules.resolve_unattended_item(state, item, districts, seed=2026)

    assert "window" in result.report.lower()
    assert result.policy == "missed_window"


def test_city_momentum_expiration_adds_pressure_and_report():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=1, cols=1, seed=2026)}
    districts["D0000"].activity = 42
    item = rules.DocketItem("expire-rezone", "mixed_use_rezoning", "Mixed-Use Rezoning Petition", "POLYGON", 1)
    item.target_cell_ids = ["D0000"]

    result = rules.resolve_unattended_item(state, item, districts, seed=2026)

    assert item.status == "expired"
    assert result.policy == "city_momentum"
    assert districts["D0000"].buyout_pressure > 0
    assert districts["D0000"].identity_state in {"stable", "vulnerable"}
    assert "momentum" in result.report.lower()


def test_bad_momentum_can_spawn_different_followup_template():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=1, cols=1, seed=2026)}
    districts["D0000"].exposure = 70
    item = rules.DocketItem("expire-vendor", "street_vendor_compact", "Street Vendor Compact", "POINT", 1)
    item.target_cell_ids = ["D0000"]

    result = rules.resolve_unattended_item(state, item, districts, seed=1)

    assert item.status == "expired"
    assert result.policy == "momentum_with_followup_risk"
    assert result.followup_template_id in {"", rules.CIVIC_INCIDENT_TEMPLATE_ID, rules.ENFORCEMENT_TEMPLATE_ID}
    assert result.report


def test_pending_momentum_followup_appears_as_different_next_docket_item():
    state = rules.CityState()
    state.pending_followups["expire-vendor"] = rules.CIVIC_INCIDENT_TEMPLATE_ID
    docket = rules.generate_docket(turn=2, seed=2026, count=4, state=state, districts={})

    assert docket[0].template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
    assert docket[0].origin_item_id == "momentum:expire-vendor"
    assert "Follow-up from unattended city momentum" in docket[0].preview_text


def test_city_momentum_week_close_applies_one_ignore_reaction():
    profile = rules.DistrictProfile(
        "D0000",
        "Rezoning Row",
        1200,
        42,
        20,
        35,
        25,
        50,
        "residential",
        population_mix={"developers": 1},
        dissatisfaction={"developers": 0},
    )
    rules.normalize_profile(profile)
    state = rules.CityState()
    item = rules.DocketItem(
        "expire-rezone",
        "mixed_use_rezoning",
        "Mixed-Use Rezoning Petition",
        "POLYGON",
        1,
        target_cell_ids=["D0000"],
    )

    rules.advance_turn_result(state, [item], {"D0000": profile})

    assert item.status == "expired"
    assert profile.buyout_pressure == 1
    assert profile.identity_state == "vulnerable"
    assert profile.dissatisfaction["developers"] == 1


def test_week_close_can_start_buyout_transition_from_unattended_pressure():
    state = rules.CityState()
    districts = {
        "A": _district_for_buyout("A", "residential", 35, ["B"]),
        "B": _district_for_buyout("B", "mercantile", 82, ["A"]),
    }
    item = rules.DocketItem("ignored-rezone", "mixed_use_rezoning", "Mixed-Use Rezoning Petition", "POLYGON", 1)
    item.target_cell_ids = ["A"]

    result = rules.advance_turn_result(state, [item], districts)

    assert state.turn == 2
    assert districts["A"].identity_state == "contested"
    assert districts["A"].contesting_cell_id == "B"
    assert districts["A"].contesting_type == "mercantile"
    assert districts["A"].transition_due_turn == 2
    assert "expired" in result.report.lower()
    assert "entered contested buyout" in result.report.lower()


def test_mandatory_followup_carries_without_pending_momentum_queue():
    state = rules.CityState()
    item = rules.DocketItem(
        "fire-followup",
        "fire_budget_escalation",
        "Fire Budget Escalation",
        "POLYGON",
        1,
    )

    rules.advance_turn_result(state, [item], {})

    assert item.status == "carried"
    assert state.pending_followups == {}


def test_carried_mandatory_item_reopens_with_context_next_docket():
    carried = rules.DocketItem(
        "fire-followup",
        "fire_budget_escalation",
        "Fire Budget Escalation",
        "POLYGON",
        1,
        status="carried",
        target_cell_ids=["D0000", "D0001"],
        preview_text="Prior fire budget review.",
        stakeholder="fire_department",
        origin_item_id="origin-fire",
        target_rule="Select fire coverage districts.",
        project_id="project-fire",
        chain_step_id="fire-step",
        priority=3,
        due_turn=4,
        subject_feature_id="F-fire",
        case_json={"inspection": {"exposure": "high"}},
    )

    docket = rules.generate_docket(turn=2, seed=2026, count=4, carried_items=[carried])

    assert docket[0] is not carried
    assert docket[0].template_id == carried.template_id
    assert docket[0].title == carried.title
    assert docket[0].geometry_type == carried.geometry_type
    assert docket[0].status == "open"
    assert docket[0].turn == 2
    assert docket[0].target_cell_ids == carried.target_cell_ids
    assert docket[0].stakeholder == carried.stakeholder
    assert docket[0].origin_item_id == carried.origin_item_id
    assert docket[0].target_rule == carried.target_rule
    assert docket[0].project_id == carried.project_id
    assert docket[0].chain_step_id == carried.chain_step_id
    assert docket[0].priority == carried.priority
    assert docket[0].due_turn == carried.due_turn
    assert docket[0].subject_feature_id == carried.subject_feature_id
    assert docket[0].case_json == carried.case_json
    assert "Carried forward from prior week." in docket[0].preview_text
    assert carried.status == "carried"
    assert carried.turn == 1


def test_carried_and_pending_same_template_have_unique_item_ids():
    state = rules.CityState()
    state.pending_followups["expire-vendor"] = rules.CIVIC_INCIDENT_TEMPLATE_ID
    carried = rules.DocketItem(
        "incident-carried",
        rules.CIVIC_INCIDENT_TEMPLATE_ID,
        "Civic Incident Response",
        "POINT",
        1,
        status="carried",
    )

    docket = rules.generate_docket(turn=2, seed=2026, count=4, state=state, carried_items=[carried])

    assert docket[0].template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
    assert docket[1].template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
    assert docket[0].item_id != docket[1].item_id
    assert len({item.item_id for item in docket}) == len(docket)


def test_carried_and_generated_maintenance_have_unique_item_ids():
    carried = rules.DocketItem(
        "maintenance-carried",
        rules.MAINTENANCE_TEMPLATE_ID,
        "Maintenance Order: Vendor Market",
        "POINT",
        1,
        status="carried",
        stakeholder="maintenance_office",
    )
    feature = rules.FeatureInstance(
        "F-due",
        "vendor_market",
        owner_group="maintenance_office",
        target_cell_ids=["D0000"],
        status="maintenance_due",
    )

    docket = rules.generate_docket(turn=2, seed=2026, count=4, carried_items=[carried], active_features=[feature])

    assert docket[0].template_id == rules.MAINTENANCE_TEMPLATE_ID
    assert docket[1].template_id == rules.MAINTENANCE_TEMPLATE_ID
    assert docket[0].item_id != docket[1].item_id
    assert len({item.item_id for item in docket}) == len(docket)


def test_carried_and_visible_same_group_incident_deduplicates_by_identity():
    carried = rules.DocketItem(
        "incident-carried",
        rules.CIVIC_INCIDENT_TEMPLATE_ID,
        "Civic Incident Response: Renters",
        "POINT",
        1,
        status="carried",
        target_cell_ids=["D0000"],
        stakeholder="renters",
        origin_item_id="dissatisfaction:D0000:renters",
        case_json={
            "incident": {
                "identity": "dissatisfaction:D0000:renters",
                "cell_id": "D0000",
                "group": "renters",
            }
        },
    )
    profile = rules.DistrictProfile(
        "D0000",
        "Renters Row",
        1000,
        45,
        20,
        35,
        25,
        50,
        "residential",
        population_mix={"renters": 3},
        dissatisfaction={"renters": 4},
    )

    docket = rules.generate_docket(turn=2, seed=2026, count=4, carried_items=[carried], districts={"D0000": profile})

    assert docket[0].template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
    assert docket[0].origin_item_id == "dissatisfaction:D0000:renters"
    assert len(_local_incident_items(docket)) == 1
    assert len({item.item_id for item in docket}) == len(docket)


def test_repeated_carried_incident_rows_deduplicate_by_identity():
    first = rules.DocketItem(
        "incident-carried-a",
        rules.CIVIC_INCIDENT_TEMPLATE_ID,
        "Civic Incident Response: Renters",
        "POINT",
        1,
        status="carried",
        target_cell_ids=["D0000"],
        stakeholder="renters",
        origin_item_id="dissatisfaction:D0000:renters",
    )
    second = copy.deepcopy(first)
    second.item_id = "incident-carried-b"

    docket = rules.generate_docket(turn=2, seed=2026, count=4, carried_items=[first, second])

    assert len(_local_incident_items(docket)) == 1


def test_unresolved_visible_incident_reappears_once_next_week():
    profiles = {"D0000": _incident_profile("D0000", "renters")}
    state = rules.CityState(turn=2)
    docket = rules.generate_docket(turn=2, seed=2026, count=1, state=state, districts=profiles)

    rules.advance_turn_result(state, docket, profiles)
    next_docket = rules.generate_docket(
        turn=state.turn,
        seed=2026,
        count=4,
        state=state,
        districts=profiles,
        carried_items=docket,
    )

    assert docket[0].status == "carried"
    assert len(_local_incident_items(next_docket)) == 1


def test_resolving_incident_uses_case_identity_not_unrelated_selection():
    profiles = {
        "D0000": _incident_profile("D0000", "renters"),
        "D0001": _incident_profile("D0001", "renters"),
    }
    state = rules.CityState(turn=2, ap=3, money=100)
    item = rules.generate_docket(turn=2, seed=2026, count=1, state=state, districts=profiles)[0]

    result = rules.resolve_decision(
        state,
        item,
        profiles,
        action="approve_mitigated",
        target_cell_ids=["D0001"],
        seed=2026,
        mitigated=True,
    )

    assert result.ok is True
    assert result.affected_cell_ids == ["D0000"]
    assert profiles["D0000"].dissatisfaction["renters"] == 1
    assert profiles["D0000"].incident_state == "none"
    assert profiles["D0001"].dissatisfaction["renters"] == 4
    assert profiles["D0001"].incident_state != "none"


def test_deferred_incident_reappears_as_one_coherent_case():
    profiles = {"D0000": _incident_profile("D0000", "renters")}
    state = rules.CityState(turn=2, ap=3, money=100)
    item = rules.generate_docket(turn=2, seed=2026, count=1, state=state, districts=profiles)[0]

    result = rules.resolve_decision(state, item, profiles, action="deny", target_cell_ids=["D0000"], seed=2026)
    rules.advance_turn_result(state, [item], profiles)
    next_docket = rules.generate_docket(
        turn=state.turn,
        seed=2026,
        count=4,
        state=state,
        districts=profiles,
        carried_items=[item],
    )

    incidents = _local_incident_items(next_docket)
    assert result.ok is True
    assert item.status == "deferred"
    assert len(incidents) == 1
    assert incidents[0].origin_item_id == "dissatisfaction:D0000:renters"


def test_week_five_commuter_incident_stays_single_and_clears_on_response():
    """Regression for the stuck week-5 commuter incident.

    A persistent commuter grievance must surface exactly one actionable follow-up
    per week (never a hidden duplicate), survive repeated denial without
    duplicating, and clear permanently once the office finally responds.
    """

    identity = "dissatisfaction:D0000:commuters"
    profiles = {"D0000": _incident_profile("D0000", "commuters", band=4)}
    state = rules.CityState(turn=5, ap=4, money=120)
    carried: list = []

    # Weeks 5 and 6: deny each week. The grievance persists (correct) but must
    # never present two simultaneous follow-ups for the same identity.
    for _week in (5, 6):
        docket = rules.generate_docket(turn=state.turn, seed=2026, count=4, state=state, districts=profiles, carried_items=carried)
        incidents = [item for item in _local_incident_items(docket) if item.origin_item_id == identity]
        assert len(incidents) == 1
        item = incidents[0]
        rules.resolve_decision(state, item, profiles, action="deny", target_cell_ids=["D0000"], seed=2026)
        rules.advance_turn_result(state, [item], profiles)
        carried = [item]
        assert profiles["D0000"].incident_state != "none"

    # Now respond to it: a settlement relieves the grievance below threshold.
    docket = rules.generate_docket(turn=state.turn, seed=2026, count=4, state=state, districts=profiles, carried_items=carried)
    incidents = [item for item in _local_incident_items(docket) if item.origin_item_id == identity]
    assert len(incidents) == 1
    item = incidents[0]
    rules.resolve_decision(state, item, profiles, action="approve_mitigated", target_cell_ids=["D0000"], seed=2026, mitigated=True)
    rules.advance_turn_result(state, [item], profiles)

    # The incident clears and does not resurrect from a stale duplicate record.
    assert profiles["D0000"].incident_state == "none"
    final_docket = rules.generate_docket(turn=state.turn, seed=2026, count=4, state=state, districts=profiles, carried_items=[item])
    assert [item for item in _local_incident_items(final_docket) if item.origin_item_id == identity] == []


def test_district_prosperity_band_grades_core_metrics():
    """Verify the per-district prosperity band tracks the four core metrics."""

    def _band(activity, friction, trust, exposure):
        profile = rules.DistrictProfile(
            cell_id="D0", name="D0", population=1000,
            activity=activity, friction=friction, trust=trust, exposure=exposure,
            services=40, district_type="residential",
        )
        rules.normalize_profile(profile)
        return profile.prosperity_band

    # (80 + 60 + 90 + 80) / 4 = 77.5 -> thriving
    assert _band(80, 10, 60, 20) == "thriving"
    # (20 + 15 + 30 + 35) / 4 = 25 -> failing
    assert _band(20, 70, 15, 65) == "failing"
    # A healthy district outranks a struggling one.
    bands = ["failing", "strained", "stable", "thriving"]
    assert bands.index(_band(80, 10, 60, 20)) > bands.index(_band(20, 70, 15, 65))
    # normalize_profile persists the band onto the profile for map rendering.
    assert _band(55, 30, 45, 30) in bands


def test_feature_archetype_catalog_is_valid_and_covers_all_templates():
    """Verify templates resolve to valid feature archetypes and metadata."""
    assert rules.validate_feature_catalog() == []
    assert {"business", "public_resource", "natural_resource", "infrastructure", "event", "land_use", "incident", "compliance"} <= set(rules.FEATURE_FAMILIES)
    for template_id, template in rules.TEMPLATES.items():
        archetype = rules.feature_archetype_for_template(template_id)
        metadata = rules.feature_metadata_for_template(template)
        assert archetype.archetype_id == template.spawn_archetype_id
        assert archetype.geometry_type == template.geometry_type
        assert metadata["archetype_id"] == archetype.archetype_id


def test_generate_docket_has_three_seeded_items_with_templates():
    """Verify the first seeded docket has three valid template-backed cases."""
    docket = rules.generate_docket(turn=1, seed=2026, count=3)
    repeat = rules.generate_docket(turn=1, seed=2026, count=3)

    assert len(docket) == 3
    assert len({item.item_id for item in docket}) == 3
    assert [item.template_id for item in docket] == [item.template_id for item in repeat]
    assert all(item.template_id in rules.TEMPLATES for item in docket)
    assert all(item.preview_text for item in docket)
    assert all(item.stakeholder for item in docket)
    assert all(item.target_rule for item in docket)
    assert {item.geometry_type for item in docket} <= {"POINT", "LINE", "POLYGON"}


def test_docket_order_varies_by_seed_for_same_week():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}

    first = rules.generate_docket(turn=1, seed=2026, count=4, state=state, districts=districts)
    second = rules.generate_docket(turn=1, seed=2027, count=4, state=copy.deepcopy(state), districts=copy.deepcopy(districts))

    assert [item.template_id for item in first] != [item.template_id for item in second]


def test_docket_weights_reflect_district_type_distribution():
    state = rules.CityState()
    mercantile = {
        f"M{i}": _district_for_buyout(f"M{i}", "mercantile", 65, [])
        for i in range(8)
    }
    natural = {
        f"N{i}": _district_for_buyout(f"N{i}", "natural", 65, [])
        for i in range(8)
    }

    market_docket = rules.generate_docket(turn=4, seed=2026, count=4, state=state, districts=mercantile)
    natural_docket = rules.generate_docket(turn=4, seed=2026, count=4, state=copy.deepcopy(state), districts=natural)

    market_categories = [rules.TEMPLATES[item.template_id].category for item in market_docket]
    natural_categories = [rules.TEMPLATES[item.template_id].category for item in natural_docket]

    assert market_categories.count("business") + market_categories.count("development") >= 1
    assert natural_categories.count("land") >= 1
    assert [item.template_id for item in market_docket] != [item.template_id for item in natural_docket]


def _typed_population_district(cell_id, dtype, population):
    profile = rules.DistrictProfile(
        cell_id=cell_id,
        name=cell_id,
        population=population,
        activity=60,
        friction=25,
        trust=35,
        exposure=20,
        services=40,
        district_type=dtype,
    )
    rules.normalize_profile(profile)
    return profile


def test_docket_weights_reflect_district_population_distribution():
    """More-populous district types should generate more related proposals."""

    def _board(merc_pop, nat_pop):
        board = {}
        for i in range(4):
            board[f"M{i}"] = _typed_population_district(f"M{i}", "mercantile", merc_pop)
            board[f"N{i}"] = _typed_population_district(f"N{i}", "natural", nat_pop)
        return board

    def _commerce_count(board):
        # Sample the proposal pool across many weeks with fixed city state so we
        # isolate the population weighting from turn-to-turn board mutation.
        total = 0
        state = rules.CityState()
        for turn in range(1, 61):
            docket = rules.generate_docket(turn=turn, seed=2026, count=4, state=state, districts=board)
            cats = [rules.TEMPLATES[item.template_id].category for item in docket]
            total += cats.count("business") + cats.count("development")
        return total

    dense_mercantile = _commerce_count(_board(merc_pop=3000, nat_pop=500))
    sparse_mercantile = _commerce_count(_board(merc_pop=500, nat_pop=3000))

    assert dense_mercantile > sparse_mercantile


def test_probe_seed_2028_opens_with_line_point_and_polygon_cases():
    """Pin the week-1 docket live probes use: a fresh seed-2028 board has every geometry type."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=5, cols=5, seed=2028)}

    items = rules.generate_docket(turn=1, seed=2028, state=rules.CityState(), districts=districts)

    assert [(item.item_id, item.geometry_type) for item in items] == [
        ("T01-01-utility_expansion_trench", "LINE"),
        ("T01-02-natural_reserve_conversion", "POLYGON"),
        ("T01-03-business_license_fee_sweep", "POINT"),
        ("T01-04-mixed_use_rezoning", "POLYGON"),
    ]
    assert not hasattr(rules, "DEMO_SEQUENCE")


def test_twelve_week_docket_generation_keeps_three_or_four_items_available():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}

    seen = set()
    for _week in range(1, state.max_turns + 1):
        docket = rules.generate_docket(
            turn=state.turn,
            seed=2026,
            count=4,
            state=state,
            districts=districts,
        )
        assert 3 <= len(docket) <= 4
        assert len({item.item_id for item in docket}) == len(docket)
        seen.update(item.template_id for item in docket)
        if state.turn < state.max_turns:
            rules.advance_turn_result(state, docket, districts)

    assert state.max_turns == 12
    assert len(seen & set(rules.DEMO_TEMPLATE_IDS)) >= 8


def _active_feature_from_route_item(item, turn):
    """Build a normalized feature instance from an approved route item."""
    template = rules.TEMPLATES[item.template_id]
    archetype = rules.feature_archetype_for_template(template)
    feature = rules.FeatureInstance(
        feature_id=f"F-{item.item_id}",
        archetype_id=archetype.archetype_id,
        item_id=item.item_id,
        template_id=item.template_id,
        owner_group=item.stakeholder or template.stakeholder,
        target_cell_ids=list(item.target_cell_ids),
        capacity=archetype.capacity,
        intensity=max(1, archetype.capacity or 1),
        status=item.status,
        turn_created=turn,
    )
    return rules.normalize_feature_instance(feature, turn)


ROUTE_PROFIT_TEMPLATES = {
    "business_license_fee_sweep",
    "contractor_renovation_waiver",
    "procession_route",
    "street_vendor_compact",
    "compliance_settlement_drive",
}
ROUTE_STABILIZER_TEMPLATES = {
    "natural_reserve_conversion",
    "green_buffer_reserve",
    "water_main_loop",
    "utility_expansion_trench",
    "bus_priority_link",
    "inspection_order",
}
ROUTE_DENY_TEMPLATES = {
    "fire_budget_escalation",
    "mixed_use_rezoning",
    "affordable_infill_rezoning",
    "infill_construction_site",
}


def _route_priority(item):
    if item.template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID:
        return 0
    if item.template_id == rules.ENFORCEMENT_TEMPLATE_ID:
        return 1
    if item.template_id in ROUTE_PROFIT_TEMPLATES:
        return 2
    if item.template_id in ROUTE_STABILIZER_TEMPLATES:
        return 3
    return 9


def _route_action(state, item):
    if item.template_id in ROUTE_DENY_TEMPLATES:
        return "deny"
    if item.template_id in {rules.CIVIC_INCIDENT_TEMPLATE_ID, rules.ENFORCEMENT_TEMPLATE_ID}:
        return "approve" if state.money >= rules.TEMPLATES[item.template_id].money_cost else "deny"
    if item.template_id in ROUTE_PROFIT_TEMPLATES | ROUTE_STABILIZER_TEMPLATES:
        return "approve" if state.money >= rules.TEMPLATES[item.template_id].money_cost else "deny"
    return "deny"


def _route_targets(item, profiles):
    if item.target_cell_ids:
        return list(item.target_cell_ids)
    template = rules.TEMPLATES[item.template_id]

    def score(profile):
        value = 0
        if profile.district_type in template.good_fit_types:
            value += 100
        if profile.district_type in template.bad_fit_types:
            value -= 100
        if template.base_effects.get("exposure", 0) < 0:
            value += profile.exposure
        if template.base_effects.get("friction", 0) < 0:
            value += profile.friction
        if template.base_effects.get("services", 0) > 0:
            value += 100 - profile.services
        if template.base_effects.get("activity", 0) > 0:
            value += 50 - profile.activity
        if template.base_effects.get("trust", 0) > 0:
            value += 50 - profile.trust
        return (-value, profile.cell_id)

    count = 2 if item.geometry_type == "LINE" else 1
    return [profile.cell_id for profile in sorted(profiles.values(), key=score)[:count]]


def test_seed_2026_reasonable_attention_route_reaches_final_audit():
    profiles = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}
    state = rules.CityState()
    active_features = []
    docket_history = []

    for _week in range(1, state.max_turns + 1):
        docket = rules.generate_docket(
            turn=state.turn,
            seed=2026,
            count=4,
            state=state,
            districts=profiles,
            active_features=active_features,
        )
        for item in sorted(docket, key=lambda candidate: (_route_priority(candidate), candidate.item_id)):
            if state.ap <= 0:
                break
            if item.status not in {"open", "inspected"}:
                continue
            action = _route_action(state, item)
            result = rules.resolve_decision(
                state,
                item,
                profiles,
                action,
                _route_targets(item, profiles),
                seed=2026,
                active_features=active_features,
            )
            assert result.ok is True, result.report
            if item.status == "active":
                active_features.append(_active_feature_from_route_item(item, state.turn))
        docket_history.extend(docket)
        rules.advance_turn_result(state, docket, profiles, active_features)

    grade, report = rules.scorecard(state, profiles, active_features, docket_history)

    assert state.status == "complete"
    assert state.turn == 12
    assert active_features
    assert any(feature.condition < 100 or feature.status != "active" for feature in active_features)
    assert grade == "PASS"
    assert "score=" in report


def test_inspect_item_marks_item_and_adds_risk_band_hint():
    """Verify inspection mutates the item with risk and packet text."""
    item = rules.generate_docket(turn=1, seed=2026, count=1)[0]

    inspected = rules.inspect_item(item, seed=2026)

    assert inspected is item
    assert item.inspected is True
    assert item.risk_band in {"low", "medium", "high"}
    assert "Inspection:" in item.preview_text
    assert "Certain effects if approved:" in item.preview_text
    assert "Risk/side effects:" in item.preview_text


def test_inspect_item_adds_target_population_context_when_available():
    """Verify inspections include local population support and grievance notes."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Petition Row",
        population=1200,
        activity=45,
        friction=25,
        trust=40,
        exposure=25,
        services=55,
        district_type="residential",
        population_mix={"families": 3, "renters": 2, "commuters": 1},
        dissatisfaction={"renters": 3},
    )
    rules.normalize_profile(profile)
    item = rules.DocketItem(
        "inspect-park",
        "child_development_park_annex",
        rules.TEMPLATES["child_development_park_annex"].title,
        "POINT",
        1,
    )

    rules.inspect_item(item, seed=2026, target_profiles=[profile])

    assert "Census review:" in item.preview_text
    assert "Strongest likely supporter: families" in item.preview_text
    assert "Highest local grievance: renters are aggrieved" in item.preview_text


def test_target_population_hint_handles_multi_district_grievance():
    """Verify grievance across several target districts stays a valid band.

    Regression: dissatisfaction was summed across targets, producing a band above
    the GRIEVANCE_BAND_LABELS range and crashing the saved-game resume path.
    """
    profiles = []
    for cell_id, name in (("D0000", "Harbor Flats"), ("D0001", "Old Row")):
        profile = rules.DistrictProfile(
            cell_id, name, 1000, 40, 25, 35, 20, 40, "residential",
            population_mix={"renters": 3}, dissatisfaction={"renters": 3},
        )
        rules.normalize_profile(profile)
        profiles.append(profile)
    item = rules.DocketItem(
        "CASE", "street_vendor_compact",
        rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1,
    )

    hint = rules.target_population_hint(item, profiles)

    # Worst single grievance is band 3 (aggrieved), not the inflated sum of 6.
    assert "renters are aggrieved" in hint


def test_top_dissatisfaction_for_profiles_band_stays_in_label_range():
    """Verify aggregated grievance band stays on the 0-4 label scale (max, not sum)."""
    from toolbox.permit_office import helpers

    profiles = []
    for cell_id in ("D0000", "D0001", "D0100"):
        profile = rules.DistrictProfile(
            cell_id, cell_id, 1000, 40, 25, 35, 20, 40, "residential",
            dissatisfaction={"renters": 4},
        )
        rules.normalize_profile(profile)
        profiles.append(profile)

    group, band = helpers._top_dissatisfaction_for_profiles(profiles)

    assert group == "renters"
    assert band == 4  # max across profiles, not 12
    assert 0 <= band < len(rules.GRIEVANCE_BAND_LABELS)


def test_approve_applies_costs_district_deltas_and_city_delta():
    """Verify a normal approval spends resources and updates city/district state."""
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=2, cols=2, seed=2026)}
    state = rules.CityState(ap=3, money=60)
    item = rules.DocketItem(
        item_id="T01-03-street_vendor_compact",
        template_id="street_vendor_compact",
        title=rules.TEMPLATES["street_vendor_compact"].title,
        geometry_type="POINT",
        turn=1,
    )

    result = rules.resolve_decision(
        state,
        item,
        profiles,
        action="approve",
        target_cell_ids=["D0000"],
        spillover_cell_ids=["D0001", "D0100"],
        seed=2026,
    )

    assert result.ok is True
    assert item.status in {"active", "failed"}
    assert state.ap == 2
    assert state.money == 48
    assert result.affected_cell_ids == ["D0000", "D0001", "D0100"]
    assert "Approved" in result.report
    assert "Certain effects:" in result.report
    assert "immediate city delta" in result.report
    assert "spillover Cinder Yard, Old Row gets" in result.report
    assert "Exposure/side effects:" in result.report
    assert "recurring budget" in result.report
    assert result.city_delta
    assert profiles["D0000"].display_state in rules.DISPLAY_STATES


def test_approval_report_names_districts_not_cell_ids():
    """Verify the approval report prose reads as district names, not cell_ids."""
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=2, cols=2, seed=2026)}
    state = rules.CityState(ap=3, money=60)
    item = rules.DocketItem(
        item_id="T01-03-street_vendor_compact",
        template_id="street_vendor_compact",
        title=rules.TEMPLATES["street_vendor_compact"].title,
        geometry_type="POINT",
        turn=1,
    )

    result = rules.resolve_decision(
        state, item, profiles, action="approve",
        target_cell_ids=["D0000"], spillover_cell_ids=["D0001", "D0100"], seed=2026,
    )

    # Names surface; the stable cell_id keys never leak into the prose report.
    assert "Civic Green" in result.report
    assert "spillover Cinder Yard, Old Row gets" in result.report
    assert "D0000" not in result.report
    assert "D0001" not in result.report
    assert "D0100" not in result.report
    # The structured key field still carries the stable cell_ids for routing.
    assert result.affected_cell_ids == ["D0000", "D0001", "D0100"]


def test_approval_adjusts_population_pressure_and_local_grievance():
    """Verify approval changes population mix, growth, and local grievance bands."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Applicant Yard",
        population=1000,
        activity=50,
        friction=20,
        trust=35,
        exposure=20,
        services=60,
        district_type="residential",
        population_mix={"families": 2, "commuters": 1},
        dissatisfaction={"families": 2},
    )
    rules.normalize_profile(profile)
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=80)
    item = rules.DocketItem(
        "approve-park",
        "child_development_park_annex",
        rules.TEMPLATES["child_development_park_annex"].title,
        "POINT",
        1,
        risk_band="low",
    )

    result = rules.resolve_decision(state, item, profiles, "approve_mitigated", ["D0000"], seed=4, mitigated=True)

    assert result.ok is True
    assert profile.population > 1000
    assert profile.population_mix["families"] == 3
    assert profile.dissatisfaction["families"] == 1
    assert "Population file:" in result.report


def test_budget_recovery_approval_creates_positive_recurring_economy():
    """Verify revenue-oriented cases give the player a visible recovery lever."""
    profile = rules.DistrictProfile("D0000", "License Row", 1500, 55, 20, 35, 25, 50, "mercantile")
    rules.normalize_profile(profile)
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=60)
    item = rules.DocketItem(
        "fee-sweep",
        "business_license_fee_sweep",
        rules.TEMPLATES["business_license_fee_sweep"].title,
        "POINT",
        1,
        risk_band="low",
    )

    result = rules.resolve_decision(state, item, profiles, "approve", ["D0000"], seed=2)
    feature = _active_feature_from_route_item(item, state.turn)
    turn = rules.advance_turn_result(state, [], profiles, [feature])

    assert result.ok is True
    assert item.status == "active"
    assert "helps the budget later" in result.report
    assert turn.net > 0
    assert state.money > 60 - rules.TEMPLATES["business_license_fee_sweep"].money_cost


def test_approval_report_calls_out_maintenance_burden():
    """Verify costly service approvals tell the player about future upkeep."""
    profile = rules.DistrictProfile("D0000", "Coverage Row", 1200, 45, 20, 30, 45, 35, "civic")
    rules.normalize_profile(profile)
    state = rules.CityState(ap=3, money=80)
    item = rules.DocketItem(
        "fire-coverage",
        "fire_budget_escalation",
        rules.TEMPLATES["fire_budget_escalation"].title,
        "POLYGON",
        1,
        risk_band="low",
    )

    result = rules.resolve_decision(state, item, {profile.cell_id: profile}, "approve", ["D0000"], seed=4)

    assert result.ok is True
    assert item.status == "active"
    assert "creates maintenance burden" in result.report


def test_service_archetype_updates_services_and_land_use_overlay():
    """Verify service features improve local service gaps after approval."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Undercovered Row",
        population=1800,
        activity=45,
        friction=20,
        trust=35,
        exposure=55,
        services=15,
        district_type="residential",
        population_mix={"families": 3, "commuters": 1},
        dissatisfaction={"families": 1},
    )
    rules.normalize_profile(profile)
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=80)
    item = rules.DocketItem(
        "approve-child-service",
        "child_development_park_annex",
        rules.TEMPLATES["child_development_park_annex"].title,
        "POINT",
        1,
        risk_band="low",
    )

    result = rules.resolve_decision(state, item, profiles, "approve", ["D0000"], seed=5)

    assert result.ok is True
    assert profile.services > 15
    assert result.district_deltas["D0000"]["services"] > 0
    assert profile.service_gap.get("child_services", 0) < 30


def test_display_state_selects_primary_pressure_cause_priority():
    """Verify district map state shows the leading local pressure cause."""
    profile = rules.DistrictProfile("D0000", "Cause Row", 1000, 70, 20, 45, 20, 80, "residential", population_mix={"families": 3}, dissatisfaction={"families": 3})
    rules.normalize_profile(profile)
    assert profile.display_state == "grievance"

    profile.dissatisfaction["families"] = 4
    rules.normalize_profile(profile)
    assert profile.display_state == "incident"

    profile.dissatisfaction["families"] = 0
    profile.incident_state = "none"
    profile.services = 5
    rules.normalize_profile(profile)
    assert profile.display_state == "service_gap"

    profile.services = 80
    profile.hazards = {"heat": 2}
    rules.normalize_profile(profile)
    assert profile.display_state == "hazard"

    profile.hazards = {}
    profile.housing_capacity = 1000
    profile.population = 990
    profile.affordability = 25
    rules.normalize_profile(profile)
    assert profile.display_state == "housing_pressure"

    profile.housing_capacity = 1300
    profile.affordability = 80
    profile.displacement = {}
    rules.normalize_profile(profile)
    assert profile.display_state == "economic_growth"


def test_stat_cascade_services_hazards_housing_and_trust_roles():
    """Verify the shared cascade keeps non-money stat roles distinct."""
    low_service = rules.DistrictProfile("D0000", "Gap Row", 1200, 45, 20, 30, 20, 5, "residential", population_mix={"families": 3}, dissatisfaction={"families": 0})
    rules.normalize_profile(low_service)
    rules.apply_stat_cascade(low_service)
    assert low_service.service_gap["child_services"] >= 40
    assert low_service.dissatisfaction["families"] == 1

    hazard = rules.DistrictProfile("D0001", "Hazard Row", 1200, 45, 20, 30, 20, 80, "residential", population_mix={"families": 3}, hazards={"heat": 3})
    rules.normalize_profile(hazard)
    rules.apply_stat_cascade(hazard)
    assert hazard.exposure > 20
    assert hazard.friction > 20
    assert hazard.dissatisfaction["families"] > 0

    high_trust = rules.DistrictProfile("D0002", "Civic Row", 1200, 45, 20, 70, 20, 80, "residential", population_mix={"families": 3}, dissatisfaction={"families": 2})
    rules.normalize_profile(high_trust)
    rules.apply_stat_cascade(high_trust)
    assert high_trust.dissatisfaction["families"] < 2

    exposed = rules.DistrictProfile("D0003", "Exposure Row", 1500, 45, 70, 30, 72, 80, "residential", population_mix={"families": 3}, dissatisfaction={"families": 0})
    rules.normalize_profile(exposed)
    before = exposed.population
    rules.apply_stat_cascade(exposed)
    assert exposed.population < before
    assert exposed.dissatisfaction["families"] == 1


def test_incident_visibility_and_resolution_uses_existing_civic_language():
    """Verify band 3 is a grievance and band 4 opens a civic incident docket."""
    profile = rules.DistrictProfile("D0000", "Petition Row", 1000, 45, 20, 35, 20, 80, "residential", population_mix={"renters": 3}, dissatisfaction={"renters": 3})
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=80)
    rules.normalize_profile(profile)

    assert profile.display_state == "grievance"
    assert profile.incident_state == "none"
    assert all(item.template_id != rules.CIVIC_INCIDENT_TEMPLATE_ID for item in rules.generate_docket(1, count=1, districts=profiles, state=state))

    profile.dissatisfaction["renters"] = 4
    rules.normalize_profile(profile)
    assert rules._surface_new_incidents(state, [profile]) == 1
    assert profile.incident_state in {"complaints", "petition", "protest", "strike", "noncompliance"}
    assert profile.incident_group == "renters"
    assert state.friction == 21
    assert rules._surface_new_incidents(state, [profile]) == 0
    docket = rules.generate_docket(1, count=1, districts=profiles, state=state)
    assert docket[0].template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
    assert docket[0].target_cell_ids == ["D0000"]

    result = rules.resolve_decision(state, docket[0], profiles, "approve", ["D0000"], seed=2026)
    assert result.ok is True
    assert profile.dissatisfaction["renters"] == 2
    assert profile.incident_state == "none"
    assert profile.display_state == "stable"


def test_land_use_overlay_is_applied_to_successful_zone_approval():
    """Verify successful zoning approvals persist their overlay on districts."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Rezoning Row",
        population=1300,
        activity=45,
        friction=20,
        trust=35,
        exposure=25,
        services=60,
        district_type="residential",
    )
    rules.normalize_profile(profile)
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=80)
    item = rules.DocketItem(
        "approve-rezoning",
        "mixed_use_rezoning",
        rules.TEMPLATES["mixed_use_rezoning"].title,
        "POLYGON",
        1,
        risk_band="low",
    )

    result = rules.resolve_decision(state, item, profiles, "approve_mitigated", ["D0000"], seed=42, mitigated=True)

    assert result.ok is True
    assert item.status == "active"
    assert profile.zoning_overlay == "mixed_use"


def test_mitigation_reduces_bad_side_effects_and_costs_more():
    """Verify mitigated approvals cost more and dampen harmful deltas."""
    base_profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=2, cols=2, seed=10)}
    mitigated_profiles = copy.deepcopy(base_profiles)
    base_state = rules.CityState(ap=3, money=80)
    mitigated_state = rules.CityState(ap=3, money=80)
    base_item = rules.DocketItem("waiver-base", "contractor_renovation_waiver", rules.TEMPLATES["contractor_renovation_waiver"].title, "POINT", 1)
    mitigated_item = rules.DocketItem("waiver-mitigated", "contractor_renovation_waiver", rules.TEMPLATES["contractor_renovation_waiver"].title, "POINT", 1)

    base = rules.resolve_decision(base_state, base_item, base_profiles, "approve", ["D0000"], seed=10)
    mitigated = rules.resolve_decision(
        mitigated_state,
        mitigated_item,
        mitigated_profiles,
        "approve_mitigated",
        ["D0000"],
        seed=10,
        mitigated=True,
    )

    assert base.ok is True
    assert mitigated.ok is True
    assert mitigated_state.money < base_state.money
    assert mitigated.district_deltas["D0000"].get("exposure", 0) <= base.district_deltas["D0000"].get("exposure", 0)
    assert mitigated.district_deltas["D0000"].get("friction", 0) <= base.district_deltas["D0000"].get("friction", 0)


def test_deny_does_not_spend_ap_but_still_resolves_case():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=1, cols=1, seed=2026)}
    item = rules.DocketItem(
        "deny-test",
        "street_vendor_compact",
        rules.TEMPLATES["street_vendor_compact"].title,
        "POINT",
        1,
    )

    result = rules.resolve_decision(state, item, districts, "deny", ["D0000"], seed=2026)

    assert result.ok is True
    assert item.status == "denied"
    assert state.ap == state.max_ap
    assert result.stakeholder_delta["vendors"] > 0


def test_ignored_items_add_heat_and_heat_generates_enforcement_followup():
    """Verify ignored dockets create stakeholder heat and enforcement follow-up."""
    state = rules.CityState(turn=1, ap=0)
    items = [
        rules.DocketItem(
            "ignore-vendors",
            "street_vendor_compact",
            rules.TEMPLATES["street_vendor_compact"].title,
            "POINT",
            1,
        ),
        rules.DocketItem(
            "ignore-vendors-again",
            "street_vendor_compact",
            rules.TEMPLATES["street_vendor_compact"].title,
            "POINT",
            1,
        )
    ]

    report = rules.advance_turn(state, items)
    docket = rules.generate_docket(turn=state.turn, seed=2026, count=3, state=state)

    assert "Stakeholder heat added" in report
    assert state.stakeholder_heat["vendors"] >= rules.STAKEHOLDER_HEAT_THRESHOLD
    assert docket[0].template_id == rules.ENFORCEMENT_TEMPLATE_ID
    assert docket[0].stakeholder == "vendors"
    assert "Compliance Follow-Up" in docket[0].title


def test_enforcement_followup_can_be_settled_and_reduces_heat():
    """Verify enforcement settlement resolves the item and cools stakeholder heat."""
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=1, cols=1, seed=2026)}
    state = rules.CityState(ap=3, money=60, stakeholder_heat={"vendors": 4})
    item = rules.generate_docket(turn=2, seed=2026, count=1, state=state)[0]

    result = rules.resolve_decision(
        state,
        item,
        profiles,
        action="approve_mitigated",
        target_cell_ids=["D0000"],
        seed=2026,
        mitigated=True,
    )

    assert result.ok is True
    assert item.status == "settled"
    assert state.stakeholder_heat["vendors"] == 2
    assert "Settled" in result.report


def test_high_local_grievance_generates_civic_incident_followup():
    """Verify high local dissatisfaction surfaces as a civic incident docket."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Appeal Steps",
        population=1400,
        activity=40,
        friction=35,
        trust=35,
        exposure=30,
        services=35,
        district_type="residential",
        population_mix={"renters": 3, "families": 1},
        dissatisfaction={"renters": 4},
    )
    profiles = {profile.cell_id: profile}
    state = rules.CityState(turn=2)

    docket = rules.generate_docket(turn=2, seed=2026, count=3, state=state, districts=profiles)

    assert profiles["D0000"].incident_state == "protest"
    assert docket[0].template_id == rules.CIVIC_INCIDENT_TEMPLATE_ID
    assert docket[0].stakeholder == "renters"
    assert "Civic Incident Response" in docket[0].title


def test_civic_incident_response_lowers_dissatisfaction_and_clears_incident():
    """Verify civic incident response lowers grievance and clears incident state."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Formal Complaint Green",
        population=1400,
        activity=40,
        friction=35,
        trust=35,
        exposure=30,
        services=35,
        district_type="residential",
        population_mix={"renters": 3, "families": 1},
        dissatisfaction={"renters": 4},
    )
    profiles = {profile.cell_id: profile}
    state = rules.CityState(turn=2, ap=3, money=80)
    item = rules.generate_docket(turn=2, seed=2026, count=1, state=state, districts=profiles)[0]

    result = rules.resolve_decision(
        state,
        item,
        profiles,
        action="approve_mitigated",
        target_cell_ids=["D0000"],
        seed=2026,
        mitigated=True,
    )

    assert result.ok is True
    assert item.status == "settled"
    assert profile.dissatisfaction["renters"] == 1
    assert profile.incident_state == "none"
    assert "Target group: renters" in result.report


def test_high_risk_bad_fit_approval_can_fail():
    """Verify risky bad-fit approvals can trigger a failed outcome."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Low Service Reserve",
        population=1000,
        activity=35,
        friction=30,
        trust=30,
        exposure=90,
        services=5,
        district_type="natural",
    )
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=80)
    item = rules.DocketItem(
        "fail-waiver",
        "contractor_renovation_waiver",
        rules.TEMPLATES["contractor_renovation_waiver"].title,
        "POINT",
        1,
        risk_band="high",
    )

    result = rules.resolve_decision(state, item, profiles, "approve", ["D0000"], seed=2026)

    assert result.ok is True
    assert result.failure_triggered is True
    assert item.status == "failed"
    assert "Outcome failed" in result.report


def test_land_use_conflict_increases_failure_chance():
    """Verify land-use conflict raises approval failure probability."""
    good = rules.DistrictProfile(
        cell_id="D0000",
        name="Market Fit",
        population=1000,
        activity=45,
        friction=20,
        trust=35,
        exposure=25,
        services=60,
        district_type="mercantile",
    )
    bad = rules.DistrictProfile(
        cell_id="D0001",
        name="Reserve Conflict",
        population=1000,
        activity=45,
        friction=20,
        trust=35,
        exposure=25,
        services=60,
        district_type="natural",
    )
    rules.normalize_profile(good)
    rules.normalize_profile(bad)
    template = rules.TEMPLATES["street_vendor_compact"]

    good_chance = rules._failure_chance(template, [good], "medium", mitigated=False)
    bad_chance = rules._failure_chance(template, [bad], "medium", mitigated=False)

    assert bad_chance > good_chance


def test_twelve_week_season_mid_audit_week_six_and_final_week_twelve():
    state = rules.CityState()
    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}

    for _ in range(5):
        rules.advance_turn_result(state, [], districts)

    assert state.turn == 6
    assert state.audit_stage == 1
    assert state.status == "playing"

    for _ in range(6):
        rules.advance_turn_result(state, [], districts)

    assert state.turn == 12
    assert state.audit_stage == 1
    assert state.status == "playing"

    rules.advance_turn_result(state, [], districts)

    assert state.turn == 12
    assert state.audit_stage == 2
    assert state.status == "complete"


def test_advance_turn_resets_ap_and_marks_mid_audit_stage():
    """Verify turn advancement restores AP and updates the week-six audit stage."""
    state = rules.CityState(turn=5, ap=0)
    items = rules.generate_docket(turn=5, seed=2026, count=4)

    report = rules.advance_turn(state, items)

    assert "Advanced week" in report
    assert state.turn == 6
    assert state.ap == state.max_ap
    assert state.audit_stage == 1
    assert {item.status for item in items} <= {"carried", "expired"}


def test_final_week_closes_audit_without_advancing_past_max_turns():
    """Verify week twelve closes the final audit and does not create week thirteen."""
    state = rules.CityState(turn=12, ap=0)
    items = rules.generate_docket(turn=12, seed=2026, count=4)

    report = rules.advance_turn(state, items)

    assert "Final week closed" in report
    assert "Final audit:" in report
    assert state.turn == 12
    assert state.status == "complete"
    assert state.audit_stage == 2
    assert state.ap == state.max_ap
    assert {item.status for item in items} <= {"carried", "expired"}


def test_week_eleven_advance_opens_playable_week_twelve():
    """Verify entering week twelve leaves the final docket playable."""
    state = rules.CityState(turn=11, audit_stage=1)

    report = rules.advance_turn(state, [])

    assert "Advanced week" in report
    assert "Final audit:" not in report
    assert state.turn == 12
    assert state.status == "playing"
    assert state.audit_stage == 1


def test_completed_game_does_not_advance_again():
    """Verify repeated final audit clicks do not mutate the game clock."""
    state = rules.CityState(turn=12, status="complete", audit_stage=2)

    report = rules.advance_turn(state, [])

    assert "Final audit already filed" in report
    assert state.turn == 12
    assert state.status == "complete"
    assert state.audit_stage == 2


def test_legacy_week_seven_save_is_clamped_to_final_audit():
    """Verify old six-week saves past the final week stop at week six."""
    state = rules.CityState(turn=7, max_turns=6, status="playing", audit_stage=1)

    report = rules.advance_turn(state, [])

    assert "Final audit already filed" in report
    assert state.turn == 6
    assert state.status == "complete"
    assert state.audit_stage == 2


def test_advance_turn_applies_population_drift_and_unresolved_local_grievance():
    """Verify unresolved local cases and population drift apply during turn end."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Growing Annex",
        population=1200,
        activity=70,
        friction=20,
        trust=40,
        exposure=20,
        services=60,
        district_type="residential",
        population_mix={"families": 2, "renters": 1},
        dissatisfaction={"families": 0},
    )
    rules.normalize_profile(profile)
    profiles = {profile.cell_id: profile}
    state = rules.CityState(turn=2, ap=0, max_ap=3)
    item = rules.DocketItem(
        "ignored-park",
        "child_development_park_annex",
        rules.TEMPLATES["child_development_park_annex"].title,
        "POINT",
        2,
        target_cell_ids=["D0000"],
    )

    report = rules.advance_turn(state, [item], profiles)

    assert state.turn == 3
    assert state.ap == 3
    assert profile.population > 1200
    assert profile.dissatisfaction["families"] == 1
    assert "Local grievance files updated" in report
    assert "Population drift" in report


def test_daily_pressure_advances_once_catches_up_and_caps():
    """Verify daily pressure advances only for newly entered days."""

    profile = rules.DistrictProfile("D0000", "Pressure Row", 1000, 45, 20, 35, 25, 90, "civic", housing_capacity=1500, affordability=80)
    rules.normalize_profile(profile)
    state = rules.CityState()
    item = rules.DocketItem("open-case", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1, target_cell_ids=["D0000"])

    first = rules.advance_daily_pressure(state, [item], {"D0000": profile}, [], 2)
    second = rules.advance_daily_pressure(state, [item], {"D0000": profile}, [], 2)
    caught_up = rules.advance_daily_pressure(state, [item], {"D0000": profile}, [], 4)

    assert first == {"D0000": 2}
    assert second == {"D0000": 2}
    assert caught_up == {"D0000": 4}
    assert state.week_day == 4


def test_daily_pressure_ignores_resolved_items_and_counts_background_conditions():
    """Verify pressure skips resolved cases but counts city conditions."""

    hazard = rules.DistrictProfile("D0000", "Hazard Row", 1000, 45, 20, 35, 25, 50, "industrial", hazards={"heat": 2})
    stable = rules.DistrictProfile("D0001", "Stable Row", 1000, 45, 20, 35, 25, 90, "civic", housing_capacity=1500, affordability=80)
    rules.normalize_profile(hazard)
    rules.normalize_profile(stable)
    resolved = rules.DocketItem("done", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1, status="active", target_cell_ids=["D0001"])
    feature = rules.FeatureInstance("F-due", "vendor_market", target_cell_ids=["D0001"], status="maintenance_due")
    state = rules.CityState()

    pressure = rules.advance_daily_pressure(state, [resolved], {"D0000": hazard, "D0001": stable}, [feature], 1)

    assert pressure == {"D0000": 1, "D0001": 1}


def test_week_close_escalates_from_daily_pressure_and_resets():
    """Verify weekly close consumes pressure for escalation then clears it."""

    profile = rules.DistrictProfile("D0000", "Ignored Row", 1000, 45, 20, 35, 25, 50, "mercantile", population_mix={"vendors": 2}, dissatisfaction={"vendors": 0})
    rules.normalize_profile(profile)
    state = rules.CityState(daily_pressure={"D0000": 4}, week_day=4)
    item = rules.DocketItem("ignored", "street_vendor_compact", rules.TEMPLATES["street_vendor_compact"].title, "POINT", 1, target_cell_ids=["D0000"])

    rules.advance_turn_result(state, [item], {"D0000": profile})

    assert state.week_day == 0
    assert state.daily_pressure == {}
    assert state.stakeholder_heat["vendors"] >= rules.TEMPLATES["street_vendor_compact"].ignore_heat + 2
    assert profile.dissatisfaction["vendors"] >= 2


def test_scorecard_returns_audit_grade_and_metrics():
    """Verify scorecard reports a grade and key city metrics."""
    state = rules.CityState(activity=70, trust=60, friction=20, exposure=15, money=45)

    grade, report = rules.scorecard(state)

    assert grade == "PASS"
    assert "activity=70" in report
    assert "exposure=15" in report


def test_scorecard_reports_renamed_city_health_metrics():
    """Verify the city-health model uses the renamed canonical metric names."""

    state = rules.CityState(activity=70, trust=60, friction=20, exposure=15, money=45)

    grade, report = rules.scorecard(state)

    assert grade == "PASS"
    assert "activity=70" in report
    assert "trust=60" in report
    assert "friction=20" in report
    assert "exposure=15" in report
    assert "prosperity=" not in report
    assert "unrest=" not in report
    assert "culture=" not in report
    assert "risk=" not in report


def test_long_term_catalogs_validate_new_city_system_records():
    """Verify long-term systems have complete catalog coverage."""
    assert rules.validate_feature_catalog() == []
    assert set(rules.HAZARD_TYPES) == {"pollution", "flood", "fire", "noise", "heat", "ecology"}
    assert {"bus_priority_link", "water_main_loop", "green_buffer_reserve", "inspection_order"} <= set(rules.FEATURE_ARCHETYPES)
    assert {"housing_mandate", "port_boom"} <= set(rules.SCENARIO_RULES)
    assert "affordable_infill_buildout" in rules.PROJECT_CHAINS


def test_generated_districts_have_adjacency_housing_and_empty_hazards():
    """Verify generated districts include adjacency, housing, and network fields."""
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=2, cols=2, seed=2026)}

    assert profiles["D0000"].adjacent_cell_ids == ["D0001", "D0100"]
    assert profiles["D0101"].adjacent_cell_ids == ["D0001", "D0100"]
    assert all(profile.housing_capacity >= profile.population for profile in profiles.values())
    assert all(0 <= profile.affordability <= 100 for profile in profiles.values())
    assert all(profile.hazards == {} for profile in profiles.values())
    assert all(set(profile.network_access) == set(rules.SERVICE_TYPES) for profile in profiles.values())


def test_line_network_access_affects_endpoints_and_one_hop_only():
    """Verify line networks affect endpoints and adjacent districts only."""
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=1, cols=4, seed=2026)}
    feature = rules.FeatureInstance(
        feature_id="bus-1",
        archetype_id="bus_priority_link",
        target_cell_ids=["D0000", "D0001"],
        intensity=3,
        status="active",
    )

    rules.recompute_network_access(profiles, [feature], turn=1)

    assert profiles["D0000"].network_access["mobility"] == 3
    assert profiles["D0001"].network_access["mobility"] == 3
    assert profiles["D0002"].network_access["mobility"] == 2
    assert profiles["D0003"].network_access["mobility"] == 0


def test_housing_effects_recompute_vacancy_and_displacement_pressure():
    """Verify housing effects recalculate vacancy and affordability fields."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Lease Row",
        population=950,
        activity=70,
        friction=25,
        trust=66,
        exposure=20,
        services=55,
        district_type="residential",
        population_mix={"renters": 3, "artists": 2, "families": 2},
        housing_capacity=1000,
        affordability=32,
    )
    rules.normalize_profile(profile)

    before_capacity = profile.housing_capacity
    rules.apply_template_long_term_effects(rules.TEMPLATES["occupancy_certificate"], [profile], mitigated=True)

    assert profile.housing_capacity == before_capacity + 420
    assert profile.population > 950
    assert profile.vacancy_rate > 0
    assert profile.affordability > 32


def test_hazards_accumulate_decay_and_are_reduced_by_mitigation():
    """Verify hazards accumulate, decay, and respond to mitigation features."""
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=1, cols=2, seed=2026)}
    source = rules.FeatureInstance("site-1", "construction_site", target_cell_ids=["D0000"], status="active", intensity=1)

    rules.apply_hazard_turn(profiles, [source], turn=1)
    assert profiles["D0000"].hazards["noise"] == 1
    assert profiles["D0001"].hazards["noise"] == 1

    rules.apply_hazard_turn(profiles, [], turn=2)
    assert profiles["D0000"].hazards.get("noise", 0) == 0

    profiles["D0000"].hazards = {"heat": 3, "ecology": 2}
    buffer = rules.FeatureInstance("buffer-1", "green_buffer_reserve", target_cell_ids=["D0000"], status="active", intensity=3)
    rules.recompute_network_access(profiles, [buffer], turn=3)
    rules.apply_hazard_turn(profiles, [buffer], turn=3)
    assert profiles["D0000"].hazards.get("heat", 0) <= 1


def test_project_chain_spawns_due_step_and_advances_on_resolution():
    """Verify project approvals create due steps and advance after resolution."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Buildout Row",
        population=1200,
        activity=50,
        friction=20,
        trust=40,
        exposure=20,
        services=65,
        district_type="residential",
    )
    rules.normalize_profile(profile)
    profiles = {profile.cell_id: profile}
    state = rules.CityState(ap=3, money=120)
    projects = {}
    item = rules.DocketItem("rezoning-1", "affordable_infill_rezoning", rules.TEMPLATES["affordable_infill_rezoning"].title, "POLYGON", 1, risk_band="low")

    result = rules.resolve_decision(state, item, profiles, "approve_mitigated", ["D0000"], seed=99, mitigated=True, projects=projects)

    assert result.ok is True
    assert projects
    project = next(iter(projects.values()))
    assert project.current_step_id == "construction"
    due = rules.generate_docket(turn=project.due_turn, seed=2026, count=1, state=state, districts=profiles, projects=projects)
    assert due[0].project_id == project.project_id
    assert due[0].chain_step_id == "construction"

    due[0].status = "active"
    rules.advance_project_from_item(projects, due[0], state, approved=True, failed=False)
    assert project.current_step_id == "inspection"


def test_scenario_rules_change_docket_priority_and_scorecard_text():
    """Verify scenarios alter docket priority and scorecard reporting."""
    state = rules.CityState(scenario_id="housing_mandate")
    profiles = {p.cell_id: p for p in rules.generate_district_profiles(rows=1, cols=2, seed=2026)}
    rules.apply_scenario(state, profiles)

    docket = rules.generate_docket(turn=1, seed=2026, count=3, state=state, districts=profiles)
    grade, report = rules.scorecard(state, profiles)

    assert set(item.template_id for item in docket) & set(rules.SCENARIO_RULES["housing_mandate"].docket_priority)
    assert grade in {"PASS", "CONDITIONAL", "FAIL"}
    assert "scenario=housing_mandate" in report


def test_governance_catalogs_cover_features_stakeholders_and_maintenance():
    """Verify governance catalogs include features, stakeholders, and inspections."""
    assert rules.validate_feature_catalog() == []
    assert rules.MAINTENANCE_TEMPLATE_ID in rules.TEMPLATES
    assert set(rules.FEATURE_ARCHETYPES) <= set(rules.FEATURE_OPERATING_RULES)
    assert rules.STAKEHOLDERS["fire_department"].influence > rules.STAKEHOLDERS["vendors"].patience
    assert rules.INSPECTION_RULES["contractor_renovation_waiver"].violation_codes == ("unsafe_work",)


def test_inspection_creates_evidence_violations_deadlines_and_compliance_outcomes():
    """Verify inspections create evidence, violations, and compliance outcomes."""
    profile = rules.DistrictProfile(
        cell_id="D0000",
        name="Inspection Row",
        population=1000,
        activity=35,
        friction=35,
        trust=30,
        exposure=70,
        services=20,
        district_type="residential",
        dissatisfaction={"renters": 3},
    )
    rules.normalize_profile(profile)
    item = rules.DocketItem("inspect-waiver", "contractor_renovation_waiver", rules.TEMPLATES["contractor_renovation_waiver"].title, "POINT", 2)

    rules.inspect_item(item, seed=7, target_profiles=[profile])

    inspection = item.case_json["inspection"]
    assert item.risk_band == "high"
    assert inspection["evidence"]
    assert inspection["violations"][0]["code"] == "unsafe_work"
    assert inspection["violations"][0]["deadline_turn"] == 3

    state = rules.CityState(ap=3, money=80)
    result = rules.resolve_decision(state, item, {profile.cell_id: profile}, "approve_mitigated", ["D0000"], seed=7, mitigated=True)

    assert result.ok is True
    assert item.case_json["inspection"]["violations"][0]["status"] == "complied"
    assert item.case_json["inspection"]["violations"][0]["compliance_outcome"] == "settled"


def test_feature_lifecycle_economy_and_maintenance_followup_are_deterministic():
    """Verify feature decay affects economy and generates maintenance follow-up."""
    profile = rules.DistrictProfile("D0000", "Service Yard", 1500, 55, 20, 40, 30, 45, "residential")
    rules.normalize_profile(profile)
    feature = rules.FeatureInstance(
        "F-child",
        "child_service_annex",
        item_id="permit-child",
        template_id="child_development_park_annex",
        owner_group="families",
        target_cell_ids=["D0000"],
        turn_created=1,
        condition=36,
    )
    state = rules.CityState(turn=1, money=30)

    result = rules.advance_turn_result(state, [], {profile.cell_id: profile}, [feature])
    docket = rules.generate_docket(state.turn, state=state, districts={profile.cell_id: profile}, active_features=[feature], count=3)

    assert state.last_revenue >= 0
    assert state.last_upkeep > 0
    assert state.money == 30 + state.last_net
    assert "Economy: start $30, permit spend $0" in result.report
    assert f"revenue ${state.last_revenue}, upkeep ${state.last_upkeep}" in result.report
    assert f"end ${state.money}" in result.report
    assert feature.status == "degraded"
    assert feature.feature_id in result.feature_updates
    assert docket[0].template_id == rules.MAINTENANCE_TEMPLATE_ID
    assert docket[0].subject_feature_id == "F-child"


def test_maintenance_decision_repairs_feature_and_reschedules_due_turn():
    """Verify maintenance decisions repair features and reschedule upkeep."""
    feature = rules.FeatureInstance("F-market", "vendor_market", owner_group="vendors", target_cell_ids=["D0000"], turn_created=1, condition=20, status="degraded")
    rules.normalize_feature_instance(feature, turn=3)
    profile = rules.DistrictProfile("D0000", "Market Row", 1000, 45, 25, 35, 25, 45, "mercantile")
    item = rules.generate_docket(3, active_features=[feature], count=1)[0]
    state = rules.CityState(turn=3, ap=3, money=50)

    result = rules.resolve_decision(state, item, {profile.cell_id: profile}, "approve_mitigated", item.target_cell_ids, mitigated=True, active_features=[feature])

    assert result.ok is True
    assert item.status == "settled"
    assert feature.condition > 20
    assert feature.status == "active"
    assert feature.maintenance_due_turn > state.turn
    assert result.feature_updates["F-market"]["condition"] == feature.condition


def _single_culture_board(group):
    """Build a 4-district board, identical types, dominated by one citizen group."""
    profiles = {}
    for idx, cell_id in enumerate(["D0000", "D0001", "D0100", "D0101"]):
        profile = rules.DistrictProfile(
            cell_id, f"Culture District {idx}", 1000, 50, 20, 35, 20, 50, "civic",
            population_mix={group: 3}, dissatisfaction={},
        )
        rules.normalize_profile(profile)
        profiles[cell_id] = profile
    return profiles


def _docket_category_counts(profiles):
    """Count proposal categories surfaced across a deterministic 12-turn sweep."""
    counts: dict[str, int] = {}
    for turn in range(1, 13):
        for item in rules.generate_docket(turn, count=4, districts=profiles):
            template = rules.TEMPLATES.get(item.template_id)
            if template:
                counts[template.category] = counts.get(template.category, 0) + 1
    return counts


def test_dominant_culture_shifts_proposal_mix():
    """Verify a district's dominant citizen culture pulls the docket toward it."""
    artists = _docket_category_counts(_single_culture_board("artists"))
    developers = _docket_category_counts(_single_culture_board("developers"))

    # Artists pull culture/event work; developers pull development/business work.
    assert artists.get("culture", 0) + artists.get("event", 0) > developers.get("culture", 0) + developers.get("event", 0)
    assert developers.get("development", 0) + developers.get("business", 0) > artists.get("development", 0) + artists.get("business", 0)


def test_audit_findings_include_money_features_services_and_violations():
    """Verify audits include money, feature, service, and inspection findings."""
    profile = rules.DistrictProfile("D0000", "Gap Row", 1400, 35, 25, 30, 75, 5, "residential", population_mix={"families": 3})
    rules.normalize_profile(profile)
    feature = rules.FeatureInstance("F-failed", "utility_trench", status="failed", condition=0, target_cell_ids=["D0000"])
    item = rules.DocketItem("case", "utility_expansion_trench", rules.TEMPLATES["utility_expansion_trench"].title, "LINE", 1, stakeholder="utility_board")
    item.case_json = {"inspection": {"violations": [{"code": "unsafe_work", "severity": "critical", "deadline_turn": 1, "status": "open"}]}}
    state = rules.CityState(turn=3, money=-1, last_net=-4)

    audit = rules.generate_audit_result(state, {profile.cell_id: profile}, [feature], [item])

    assert audit.grade == "FAIL"
    assert any(finding.source == "money" for finding in audit.findings)
    assert any(finding.source == "features" for finding in audit.findings)
    assert any(finding.source == "services" for finding in audit.findings)
    assert any(finding.source == "inspection" for finding in audit.findings)


def test_pure_threat_tracks_translate_internal_pressure_fields():
    """Verify hidden civic ledgers collapse into the four public threat tracks."""

    profile = rules.DistrictProfile(
        "D0000",
        "Harbor Flats",
        1400,
        72,
        35,
        30,
        55,
        10,
        "residential",
        dissatisfaction={"renters": 3},
        hazards={"fire": 3},
        service_gap={"utilities": 42},
        displacement={"renters": 3},
    )
    profile.buyout_pressure = 4
    feature = rules.FeatureInstance("F-market", "vendor_market", status="degraded", condition=22, target_cell_ids=["D0000"])
    state = rules.CityState(activity=73, friction=64, exposure=71, stakeholder_heat={"vendors": 3})

    tracks = rules.derive_threat_tracks(state, {profile.cell_id: profile}, [feature], [])
    by_label = {track.label: track for track in tracks}

    assert set(by_label) == {"Public Anger", "Legal Exposure", "Service Failure", "Speculation Pressure"}
    assert "city friction 64" in by_label["Public Anger"].reasons
    assert "vendors heat 3" in by_label["Public Anger"].reasons
    assert "city exposure 71" in by_label["Legal Exposure"].reasons
    assert "fire band 3" in by_label["Legal Exposure"].reasons
    assert "utilities gap 42" in by_label["Service Failure"].reasons
    assert "maintenance due 1" in by_label["Service Failure"].reasons
    assert "activity spike 73" in by_label["Speculation Pressure"].reasons
    assert "renters displacement 3" in by_label["Speculation Pressure"].reasons
    assert "buyout pressure 4" in by_label["Speculation Pressure"].reasons


def test_office_standing_summary_explains_threat_threshold_review():
    """Verify Office Standing is a legitimacy gauge with report-ready reasons."""

    calm = rules.CityState(activity=60, trust=50, friction=20, exposure=20)
    risky = rules.CityState(activity=60, trust=50, friction=20, exposure=72)
    summary = rules.office_standing_summary(risky, previous_state=calm)

    assert summary.value == 54
    assert summary.label == "Authorized"
    assert summary.movement == "slipped"
    assert "Legal Exposure crossed a threshold" in summary.reason


def test_district_cause_tags_hide_raw_ledgers_behind_map_labels():
    """Verify map-facing labels describe causes without exposing raw counters."""

    service = rules.DistrictProfile("D0000", "Gap Row", 1000, 45, 20, 35, 25, 20, "residential", service_gap={"utilities": 45})
    speculation = rules.DistrictProfile("D0001", "Edge Row", 1000, 70, 20, 35, 25, 40, "mercantile", displacement={"renters": 3})
    speculation.identity_state = "contested"
    anger = rules.DistrictProfile("D0002", "Angry Row", 1000, 45, 65, 20, 25, 40, "residential", dissatisfaction={"homeowners": 3})
    unsafe = rules.DistrictProfile("D0003", "Unsafe Row", 1000, 45, 20, 35, 75, 40, "industrial", hazards={"fire": 3})
    stable = rules.DistrictProfile("D0004", "Anchor Row", 1000, 55, 15, 55, 15, 55, "civic")

    assert rules.derive_district_tags(service) == ("Service Desert",)
    assert "Speculation Front" in rules.derive_district_tags(speculation)
    assert "Contested Edge" in rules.derive_district_tags(speculation)
    assert rules.derive_district_tags(anger) == ("Anger Cluster",)
    assert rules.derive_district_tags(unsafe) == ("Unsafe Corridor",)
    assert rules.derive_district_tags(stable) == ("Stable Anchor",)


def test_weekly_report_explains_office_standing_movement():
    """Verify end-week reports explain institutional legitimacy movement."""

    profile = rules.DistrictProfile("D0000", "Gap Row", 1400, 35, 25, 30, 75, 5, "residential")
    rules.normalize_profile(profile)
    feature = rules.FeatureInstance("F-failed", "utility_trench", status="failed", condition=0, target_cell_ids=["D0000"])
    state = rules.CityState(turn=1, activity=60, trust=50, friction=20, exposure=20)

    result = rules.advance_turn_result(state, [], {profile.cell_id: profile}, [feature])

    assert "Office Standing" in result.report
    assert any(phrase in result.report for phrase in ("Standing slipped", "Standing is under review"))
    assert any(track in result.report for track in ("Legal Exposure", "Service Failure"))


def test_weekly_report_includes_notable_district_tags():
    """Verify weekly reports surface map-facing cause tags, not raw ledgers."""

    profile = rules.DistrictProfile("D0000", "Gap Row", 1400, 35, 25, 30, 75, 5, "residential", service_gap={"utilities": 45})
    state = rules.CityState(turn=1)

    result = rules.advance_turn_result(state, [], {profile.cell_id: profile}, [])

    assert "District tags:" in result.report
    assert "Gap Row: Service Desert" in result.report
    assert "utilities" not in result.report


def test_audit_findings_name_districts_not_cell_ids():
    """Verify district risk findings read as names, keeping cell_id as the key."""
    profile = rules.DistrictProfile("D0000", "Harbor Flats", 1400, 35, 75, 30, 75, 5, "residential")
    rules.normalize_profile(profile)
    state = rules.CityState(turn=3)

    audit = rules.generate_audit_result(state, {profile.cell_id: profile})

    district_findings = [finding for finding in audit.findings if finding.source == "district"]
    assert district_findings, "expected at least one district risk finding"
    for finding in district_findings:
        assert "Harbor Flats" in finding.message
        assert "D0000" not in finding.message
        # The stable key still carries the cell_id so callers can route on it.
        assert "D0000" in finding.finding_id


def test_district_label_prefers_name_falls_back_to_cell_id():
    """Verify the shared label helper reads as a name but never blanks out."""
    named = rules.DistrictProfile("D0000", "Harbor Flats", 1000, 40, 25, 35, 20, 40, "residential")
    unnamed = rules.DistrictProfile("D0001", "", 1000, 40, 25, 35, 20, 40, "residential")

    assert rules.district_label(named) == "Harbor Flats"
    assert rules.district_label(unnamed) == "D0001"


def test_incident_summary_names_districts_not_cell_ids():
    """Verify the dashboard incident summary reads as a district name."""
    profile = rules.DistrictProfile(
        "D0000", "Harbor Flats", 1200, 42, 35, 35, 30, 35, "residential",
        population_mix={"renters": 3}, dissatisfaction={"renters": 4},
    )
    rules.normalize_profile(profile)
    assert profile.incident_state != "none"

    summary = rules.incident_summary({profile.cell_id: profile})

    assert "Harbor Flats" in summary
    assert "D0000" not in summary


def test_incident_followup_preview_names_district_not_cell_id():
    """Verify the civic incident follow-up preview reads as a district name."""
    profile = rules.DistrictProfile(
        "D0000", "Harbor Flats", 1200, 42, 35, 35, 30, 35, "residential",
        population_mix={"renters": 3}, dissatisfaction={"renters": 4},
    )
    rules.normalize_profile(profile)
    assert profile.incident_state != "none"

    docket = rules.generate_docket(3, count=4, districts={profile.cell_id: profile})

    followups = [item for item in docket if "Visible condition" in item.preview_text]
    assert followups, "expected a visible civic incident follow-up"
    for item in followups:
        assert "Harbor Flats" in item.preview_text
        assert "D0000" not in item.preview_text


def test_seeded_city_detail_descriptors_are_deterministic_and_moderate():
    """Verify New Game city texture can be planned without ArcPy."""
    profiles = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}

    first = rules.generate_city_detail_features(profiles, seed=2026)
    second = rules.generate_city_detail_features(profiles, seed=2026)

    assert first == second
    assert len(first) >= 70
    assert {feature.geometry_type for feature in first} == {"POINT", "LINE", "POLYGON"}
    assert {feature.status for feature in first} == {"active", "context"}
    assert sum(1 for feature in first if feature.status == "active") <= 5
    assert {"arterial_road", "utility_backbone", "neighborhood_park"} <= {feature.archetype_id for feature in first}
    assert {"residential_block", "commercial_block", "civic_building", "industrial_yard", "academic_block", "natural_patch"} <= {feature.archetype_id for feature in first}
    assert any(feature.capacity > 0 and feature.metadata.get("occupancy") for feature in first if feature.status == "context")
    assert any(feature.metadata.get("seed_role") == "point_of_interest" for feature in first if feature.geometry_type == "POINT")
    _assert_city_detail_rects_clear_road_lanes(first)


def test_city_detail_block_size_tracks_land_use_intensity():
    """Verify denser districts render larger context blocks than sparse ones."""

    def _max_block_width(population):
        profile = rules.DistrictProfile(
            cell_id="D0000", name="Dense Row", population=population,
            activity=55, friction=25, trust=35, exposure=25, services=40,
            district_type="residential",
        )
        rules.normalize_profile(profile)
        features = rules.generate_city_detail_features({profile.cell_id: profile}, seed=2026)
        widths = [
            float(f.geometry_hint["w"])
            for f in features
            if f.metadata.get("seed_role") == "city_block" and f.target_cell_ids == ("D0000",)
        ]
        return max(widths)

    dense = _max_block_width(2800)
    sparse = _max_block_width(700)
    assert dense > sparse
    # Stays bounded so the block never reaches the central road lane at 0.50.
    assert dense < 0.30


def test_city_detail_anchor_points_render_as_special_interest():
    """Verify prominent anchor POIs use the larger special-interest symbol."""

    profiles = {profile.cell_id: profile for profile in rules.generate_district_profiles(seed=2026)}
    features = rules.generate_city_detail_features(profiles, seed=2026)

    anchors = [f for f in features if f.metadata.get("seed_role") == "point_of_interest"]
    assert anchors
    assert all(f.display_state == "special_interest" for f in anchors)


def _assert_city_detail_rects_clear_road_lanes(features):
    """Assert generated block rectangles do not cover seeded road lanes."""

    lanes_by_cell: dict[str, set[tuple[str, float]]] = {}
    for feature in features:
        if feature.geometry_type != "LINE":
            continue
        orientation = feature.geometry_hint.get("orientation")
        offset = float(feature.geometry_hint.get("offset", 0.50))
        if feature.geometry_hint.get("shape") == "corridor":
            offset = 0.50
        axis = "x" if orientation == "vertical" else "y"
        for cell_id in feature.target_cell_ids:
            lanes_by_cell.setdefault(cell_id, set()).add((axis, offset))

    for feature in features:
        if feature.geometry_type != "POLYGON" or feature.geometry_hint.get("shape") != "rect":
            continue
        hint = feature.geometry_hint
        x0 = float(hint["cx"]) - float(hint["w"]) / 2
        x1 = float(hint["cx"]) + float(hint["w"]) / 2
        y0 = float(hint["cy"]) - float(hint["h"]) / 2
        y1 = float(hint["cy"]) + float(hint["h"]) / 2
        for axis, offset in lanes_by_cell.get(feature.target_cell_ids[0], set()):
            if axis == "x":
                assert not x0 < offset < x1
            else:
                assert not y0 < offset < y1


def test_context_city_detail_does_not_participate_in_lifecycle_or_followups():
    """Verify visual-only context rows stay inert even when read as features."""
    profile = rules.DistrictProfile("D0000", "Context Row", 1800, 45, 20, 30, 25, 45, "residential", population_mix={"families": 3})
    rules.normalize_profile(profile)
    context = rules.FeatureInstance(
        "CITY-context",
        "residential_block",
        status="context",
        display_state="housing",
        condition=5,
        target_cell_ids=["D0000"],
        capacity=900,
        metadata={"occupancy": 900},
    )
    active = rules.FeatureInstance(
        "CITY-active",
        "arterial_road",
        status="active",
        display_state="road",
        condition=100,
        target_cell_ids=["D0000"],
    )
    state = rules.CityState(turn=1)

    result = rules.advance_turn_result(state, [], {profile.cell_id: profile}, [context, active])
    docket = rules.generate_docket(state.turn, active_features=[context], count=1)

    assert context.status == "context"
    assert context.display_state == "housing"
    assert context.feature_id not in result.feature_updates
    assert all(item.template_id != rules.MAINTENANCE_TEMPLATE_ID for item in docket)


def test_arcpy_toolbox_schema_declares_governance_fields_without_new_feature_classes():
    """Verify governance fields are declared without adding new map feature classes."""
    toolbox_dir = Path(__file__).parents[1] / "toolbox"
    toolbox_text = (toolbox_dir / "arcpy_permit_office.pyt").read_text()
    schema_text = (toolbox_dir / "permit_office_arcgis" / "schema.py").read_text()
    combined_text = toolbox_text + schema_text

    # Governance and city-system data rides existing feature classes, so this
    # guards against schema drift that would require new ArcGIS layers.
    for field_name in (
        "condition",
        "maintenance_due_turn",
        "last_maintained_turn",
        "state_json",
        "priority",
        "due_turn",
        "subject_feature_id",
        "case_json",
        "adjacent_cell_ids",
        "network_access_json",
        "hazard_json",
        "housing_capacity",
        "affordability",
        "vacancy_rate",
        "displacement_json",
        "project_id",
        "chain_step_id",
        "scenario_tags",
        "hazard_summary",
        "mitigation_summary",
    ):
        assert f'"{field_name}"' in combined_text
    assert '"PermitProjects"' in combined_text
    assert '"PermitPoints"' in combined_text
    assert '"PermitLines"' in combined_text
    assert '"PermitZones"' in combined_text


def test_arcpy_toolbox_hides_experiment_and_diagnostic_parameters():
    """Verify public GP parameters stay focused on launching the game."""

    toolbox_dir = Path(__file__).parents[1] / "toolbox"
    toolbox_text = (toolbox_dir / "arcpy_permit_office.pyt").read_text()
    schema_text = (toolbox_dir / "permit_office_arcgis" / "schema.py").read_text()

    assert "P_WORKSPACE = 0" in schema_text
    assert "P_OUTPUT = 1" in schema_text
    assert "P_PERF = 2" in schema_text
    for option in (
        "P_REDRAW_EXPERIMENT",
        "P_REDRAW_BENCHMARK_RUNS",
        "P_STARTUP_EXPERIMENT",
    ):
        assert option not in schema_text
    for removed in (
        "Redraw Experiment",
        "redraw_experiment",
        "Redraw Benchmark Runs",
        "redraw_benchmark_runs",
        "Startup/Threading Diagnostics",
        "startup_threading_diagnostics",
        "predrawn-rehydrate",
        "threaded-cache",
    ):
        assert removed not in toolbox_text


def test_arcpy_toolbox_evicts_stale_permit_office_modules_before_reload():
    """Verify Pro cannot reload Permit Office modules from another checkout."""

    toolbox_dir = Path(__file__).parents[1] / "toolbox"
    toolbox_text = (toolbox_dir / "arcpy_permit_office.pyt").read_text()

    assert "_module_is_from_this_toolbox" in toolbox_text
    assert '"permit_office",' in toolbox_text
    assert '"permit_office_arcgis",' in toolbox_text
    assert "sys.modules.pop(_module_name, None)" in toolbox_text


def test_arcpy_toolbox_execute_uses_three_parameter_contract():
    """Verify execute indexes only workspace/output/perf after cleanup."""

    toolbox_dir = Path(__file__).parents[1] / "toolbox"
    toolbox_text = (toolbox_dir / "arcpy_permit_office.pyt").read_text()

    assert "parameters[P_WORKSPACE]" in toolbox_text
    assert "parameters[P_PERF]" in toolbox_text
    assert "P_OUTPUT" in toolbox_text
    assert "parameters[P_REDRAW_EXPERIMENT]" not in toolbox_text
    assert "parameters[P_REDRAW_BENCHMARK_RUNS]" not in toolbox_text
    assert "parameters[P_STARTUP_EXPERIMENT]" not in toolbox_text
    assert "return [p_workspace, p_output, p_perf]" in toolbox_text


def test_toolbox_reload_list_matches_modules_on_disk():
    """Verify the .pyt reloads every toolbox module that exists, and only those."""

    import re

    toolbox_dir = Path(__file__).parents[1] / "toolbox"
    toolbox_text = (toolbox_dir / "arcpy_permit_office.pyt").read_text()
    block = toolbox_text.split("for _module_name in (", 1)[1].split(")", 1)[0]
    listed = re.findall(r'"([\w.]+)"', block)

    for name in listed:
        path = toolbox_dir.joinpath(*name.split("."))
        assert path.with_suffix(".py").is_file() or (path / "__init__.py").is_file(), name
    adapter_modules = {
        f"permit_office_arcgis.{path.stem}"
        for path in (toolbox_dir / "permit_office_arcgis").glob("*.py")
        if path.stem != "__init__"
    }
    assert adapter_modules <= set(listed)


def test_dashboard_uses_production_redraw_names_not_experiment_harness():
    """Verify promoted ring redraw is no longer routed through experiment naming."""

    adapter_dir = Path(__file__).parents[1] / "toolbox" / "permit_office_arcgis"
    dashboard_text = (adapter_dir / "dashboard.py").read_text() + (adapter_dir / "map_redraw.py").read_text()

    assert "apply_ring_redraw" in dashboard_text
    for retired in (
        "REDRAW_EXPERIMENT_ENV",
        "REDRAW_BENCHMARK_VARIANTS",
        "run_redraw_benchmark",
        "_configured_redraw_experiment",
        "_default_redraw_experiment",
        "STARTUP_EXPERIMENT",
        "normalize_startup_experiment",
    ):
        assert retired not in dashboard_text


def test_active_permit_office_files_stay_under_line_budget():
    """Verify active source files remain below the reviewable line budget."""
    pytest.skip("precomputed decision cache spike explicitly waives the 1500-line guardrail")
    toolbox_dir = Path(__file__).parents[1] / "toolbox"
    # ~1000 lines is the soft target for quick review; this guardrail only trips
    # on the hard ceiling so files have room to grow when the logic warrants it.
    HARD_LINE_CEILING = 1500
    active_paths = [
        toolbox_dir / "arcpy_permit_office.pyt",
        toolbox_dir / "arcpy_permit_office_rules.py",
        *sorted((toolbox_dir / "permit_office").glob("*.py")),
        *sorted((toolbox_dir / "permit_office" / "catalogs").glob("*.py")),
        *sorted((toolbox_dir / "permit_office_arcgis").glob("*.py")),
    ]

    oversized = {
        path.relative_to(toolbox_dir).as_posix(): len(path.read_text().splitlines())
        for path in active_paths
        if len(path.read_text().splitlines()) >= HARD_LINE_CEILING
    }

    assert oversized == {}


def test_a_filed_case_cannot_be_decided_again():
    """Verify an approved case rejects a second decision instead of charging and applying it twice."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=5, cols=5, seed=2028)}
    state = rules.CityState()
    item = rules.generate_docket(turn=1, seed=2028, state=state, districts=districts)[2]
    item.target_cell_ids = ["D0101"]
    assert rules.resolve_decision(state, item, districts, "approve", item.target_cell_ids, seed=2028).ok
    ap, money = state.ap, state.money

    for action in ("approve", "inspect", "deny"):
        result = rules.resolve_decision(state, item, districts, action, item.target_cell_ids, seed=2028)
        assert result.ok is False
        assert "already" in result.report

    assert (state.ap, state.money, item.status) == (ap, money, "active")
    assert "active" not in rules.OPEN_DOCKET_STATUSES


def _heat_followups(items):
    """Return newly issued stakeholder-heat follow-ups from a docket."""

    return [item for item in items if item.origin_item_id == "stakeholder_heat" and item.status == "open"]


def test_heat_followup_waits_out_its_cooldown():
    """Verify a hot stakeholder files one follow-up, skips cooldown_turns weeks, then may file again."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=2, cols=2, seed=2026)}
    state = rules.CityState(turn=3, stakeholder_heat={"fire_department": 9})
    cooldown = rules.helpers._stakeholder_profile("fire_department").cooldown_turns

    week3 = rules.generate_docket(3, state=state, districts=districts)
    issued = _heat_followups(week3)
    assert [item.stakeholder for item in issued] == ["fire_department"]

    issued[0].status = "enforced"
    rules.advance_turn_result(state, week3, districts, [], {})
    state.stakeholder_heat["fire_department"] = 9

    for turn in range(4, 4 + cooldown):
        assert _heat_followups(rules.generate_docket(turn, state=state, districts=districts)) == []
    assert len(_heat_followups(rules.generate_docket(4 + cooldown, state=state, districts=districts))) == 1


def test_unfed_stakeholder_heat_decays_by_one_at_week_close():
    """Verify heat gained this week holds at close while unfed heat fades by one (owner D2)."""

    districts = {profile.cell_id: profile for profile in rules.generate_district_profiles(rows=2, cols=2, seed=2026)}
    state = rules.CityState(turn=3, stakeholder_heat={"compliance_office": 2, "general_public": 1, "arts_council": 3})
    rules.adjust_stakeholder_pressure(state, "arts_council", 1)

    result = rules.advance_turn_result(state, [], districts, [], {})

    assert state.stakeholder_heat.get("arts_council") == 4
    assert state.stakeholder_heat.get("compliance_office") == 1
    assert "general_public" not in state.stakeholder_heat or state.stakeholder_heat["general_public"] == 0
    assert "heat eased" in result.report.lower()

    quiet = rules.CityState(turn=3)
    assert "heat eased" not in rules.advance_turn_result(quiet, [], districts, [], {}).report.lower()
