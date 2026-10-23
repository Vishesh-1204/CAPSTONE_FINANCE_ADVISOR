# tests/test_sip_planner.py

import pytest

from app.services.investing.sip_planner import future_value_sip, recommend_sip, required_sip


def test_zero_return_future_value_is_just_the_deposits():
    assert future_value_sip(1000, 0.0, 5) == pytest.approx(60000)


def test_positive_return_beats_deposits_and_more_time_beats_less():
    assert future_value_sip(1000, 0.10, 5) > 60000
    assert future_value_sip(1000, 0.10, 10) > future_value_sip(1000, 0.10, 5)


def test_step_up_increases_the_corpus():
    assert future_value_sip(1000, 0.10, 10, annual_step_up=0.10) > future_value_sip(1000, 0.10, 10)


def test_required_sip_round_trips_with_future_value():
    sip = required_sip(1_000_000, 0.10, 8)
    assert future_value_sip(sip, 0.10, 8) == pytest.approx(1_000_000, rel=1e-6)


def test_recommendation_uses_safety_factor_and_reserves_emergency_topup():
    rec = recommend_sip(surplus_before_investing_low=20000, existing_sip=5000, emergency_gap=60000,
                        months_to_fill_emergency=12, safety_factor=0.8)
    assert rec.emergency_topup == 5000
    assert rec.capacity_total == pytest.approx(11000)          # 20000*0.8 - 5000
    assert rec.change_vs_existing == pytest.approx(6000)


def test_existing_sip_above_safe_level_gives_a_negative_change():
    rec = recommend_sip(surplus_before_investing_low=8000, existing_sip=10000, emergency_gap=0)
    assert rec.change_vs_existing < 0


def test_target_below_capacity_wins():
    rec = recommend_sip(30000, 0, 0, target_total=5000)
    assert rec.recommended_total == 5000 and rec.limited_by == "target"


def test_no_surplus_means_no_sip():
    assert recommend_sip(0, 0, 120000).recommended_total == 0


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        recommend_sip(1000, 0, 0, safety_factor=0)


def test_months_to_reach_matches_future_value():
    from app.services.investing.sip_planner import months_to_reach
    months = months_to_reach(500_000, 5000, 0.10)
    assert future_value_sip(5000, 0.10, months / 12) >= 500_000 > future_value_sip(5000, 0.10, (months - 1) / 12)


def test_bigger_sip_reaches_goal_sooner_and_zero_sip_never():
    from app.services.investing.sip_planner import months_to_reach
    assert months_to_reach(500_000, 7000, 0.10) < months_to_reach(500_000, 5000, 0.10)
    assert months_to_reach(500_000, 0, 0.10) is None
