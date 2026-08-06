from datetime import datetime, timezone
import itertools
from time import perf_counter

import pytest

from backend.core.course_selection import (
    CourseMarket,
    CourseSelectionError,
    MarketSnapshot,
    SelectionPolicy,
    optimize_course_weights,
)
from backend.core.course_selection.model import (
    TieRule,
    _optimize_integer_budget,
    contest_success_rate,
    forecast_participants,
    typical_course_bid,
)


def _market(courses):
    return MarketSnapshot(
        cohort_size=100,
        captured_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        is_complete=True,
        courses=tuple(courses),
    )


def _sample_courses():
    return [
        CourseMarket("A", "高优先级", 28, 42, target_interested=True, target_utility=10),
        CourseMarket("B", "低优先级", 50, 48, target_interested=True, target_utility=5),
        CourseMarket("C", "市场背景课", 80, 20),
    ]


def test_reference_proxy_is_safe_for_under_capacity_course():
    rate = contest_success_rate(
        bid=5,
        typical_bid=45,
        participants=29,
        capacity=30,
        target_already_included=False,
        bid_step=1,
        tie_rule=TieRule.RANDOM,
    )
    assert rate == 1.0


def test_reference_proxy_is_monotone_and_avoids_near_full_saturation():
    bids = (5, 20, 45)
    by_bid = [
        contest_success_rate(
            bid=bid,
            typical_bid=45,
            participants=30,
            capacity=30,
            target_already_included=False,
            bid_step=1,
            tie_rule=TieRule.RANDOM,
        )
        for bid in bids
    ]
    assert by_bid == sorted(by_bid)
    assert by_bid[0] < 0.2
    lower_capacity = contest_success_rate(
        bid=20, typical_bid=45, participants=40, capacity=20,
        target_already_included=False, bid_step=1, tie_rule=TieRule.RANDOM,
    )
    higher_capacity = contest_success_rate(
        bid=20, typical_bid=45, participants=40, capacity=30,
        target_already_included=False, bid_step=1, tie_rule=TieRule.RANDOM,
    )
    more_competitors = contest_success_rate(
        bid=20, typical_bid=45, participants=50, capacity=30,
        target_already_included=False, bid_step=1, tie_rule=TieRule.RANDOM,
    )
    assert higher_capacity >= lower_capacity
    assert more_competitors <= higher_capacity


def test_typical_bid_uses_complete_market_average_entries():
    assert typical_course_bid(SelectionPolicy(), _market(_sample_courses())) == pytest.approx(105 / 1.1)


def test_reference_repository_sample_has_smooth_non_saturated_competition():
    preferences = {
        "COURSE_01": 7,
        "COURSE_02": 10,
        "COURSE_03": 8,
        "COURSE_04": 6,
        "COURSE_06": 6,
    }
    source = [
        ("COURSE_01", 30, 35), ("COURSE_02", 30, 30),
        ("COURSE_03", 30, 19), ("COURSE_04", 30, 31),
        ("COURSE_05", 30, 35), ("COURSE_06", 30, 31),
        ("COURSE_07", 153, 58), ("COURSE_08", 30, 58),
    ]
    market = MarketSnapshot(
        cohort_size=126,
        captured_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        is_complete=True,
        courses=tuple(
            CourseMarket(
                course_id,
                course_id,
                capacity,
                participants,
                target_interested=course_id in preferences,
                target_utility=preferences.get(course_id),
            )
            for course_id, capacity, participants in source
        ),
    )
    result = optimize_course_weights(SelectionPolicy(), market)
    balanced = next(item for item in result["strategies"] if item["name"] == "balanced")
    competitive = [
        course for course in balanced["courses"]
        if course["classification"] == "competitive" and course["bid"] > 0
    ]
    assert balanced["budget_used"] == 105
    assert competitive
    assert any(0.15 < course["scenario_success_rates"]["baseline"] < 0.9 for course in competitive)


def test_forecast_is_coursewise_ordered_and_allows_smoothed_growth():
    market = _market([
        CourseMarket("A", "A", 10, 20, target_interested=True),
        CourseMarket("B", "B", 10, 0),
    ])
    optimistic, baseline, pessimistic = forecast_participants(
        market, (0.8, 1.0, 3.0), 21
    )
    assert all(low <= base <= high for low, base, high in zip(optimistic, baseline, pessimistic))
    assert sum(optimistic) == round(sum(baseline) * 0.8)
    assert pessimistic[1] > 0


def test_forecast_keeps_an_included_target_in_every_scenario():
    market = _market([
        CourseMarket("A", "A", 10, 1, target_included=True, target_interested=True),
        CourseMarket("B", "B", 10, 10),
    ])
    forecasts = forecast_participants(market, (0.8, 1.0, 1.2), 21)
    assert [scenario[0] for scenario in forecasts] == [1, 1, 1]


def test_integer_dp_matches_small_bruteforce():
    policy = SelectionPolicy(budget=10, min_bid=5, bid_step=1)
    courses = [
        CourseMarket("A", "A", 1, 2, target_interested=True, target_utility=2),
        CourseMarket("B", "B", 1, 2, target_interested=True, target_utility=1),
    ]
    bids = tuple(range(5, 11))
    curves = [
        [0.2, 0.3, 0.55, 0.7, 0.8, 0.9],
        [0.25, 0.35, 0.45, 0.55, 0.65, 0.75],
    ]
    allocation = _optimize_integer_budget(courses, bids, curves, curves, policy)
    choices = (0,) + bids
    feasible = [pair for pair in itertools.product(choices, repeat=2) if sum(pair) == 10]
    expected = max(
        feasible,
        key=lambda pair: (
            sum(0 if bid == 0 else curves[index][bids.index(bid)] for index, bid in enumerate(pair)),
            pair,
        ),
    )
    assert allocation == expected


def test_full_solver_returns_three_deterministic_integer_strategies():
    result = optimize_course_weights(SelectionPolicy(), _market(_sample_courses()))
    assert result["market_confidence"] == "uncalibrated_reference_proxy"
    assert [item["name"] for item in result["strategies"]] == ["robust", "balanced", "aggressive"]
    for strategy in result["strategies"]:
        bids = [course["bid"] for course in strategy["courses"]]
        assert sum(bids) == 105
        assert all(bid == 0 or bid >= 5 for bid in bids)
        for course in strategy["courses"]:
            rates = course["scenario_success_rates"]
            assert 0 <= rates["pessimistic"] <= rates["baseline"] <= rates["optimistic"] <= 1
            assert course["success_rate_range"] == {
                "worst_case": min(rates.values()),
                "best_case": max(rates.values()),
            }
    assert result["solution_status"] == "solved"


def test_course_input_order_does_not_change_allocations():
    policy = SelectionPolicy()
    first = optimize_course_weights(policy, _market(_sample_courses()))
    second = optimize_course_weights(policy, _market(list(reversed(_sample_courses()))))
    project = lambda result: [
        (strategy["name"], [(course["course_id"], course["bid"]) for course in strategy["courses"]])
        for strategy in result["strategies"]
    ]
    assert project(first) == project(second)


def test_representative_competitive_market_finishes_within_cpu_budget():
    courses = [
        CourseMarket(
            str(index),
            f"课程{index}",
            1_250,
            2_500,
            target_interested=index == 0,
        )
        for index in range(10)
    ]
    started = perf_counter()
    result = optimize_course_weights(
        SelectionPolicy(budget=150, min_bid=5, bid_step=1),
        MarketSnapshot(
            cohort_size=2_500,
            captured_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            is_complete=True,
            courses=tuple(courses),
        ),
    )
    assert perf_counter() - started < 8.0
    assert result["solution_status"] == "solved"


@pytest.mark.parametrize(
    "policy,market,error",
    [
        (SelectionPolicy(budget=4, min_bid=5), _market(_sample_courses()), "smaller"),
        (SelectionPolicy(), MarketSnapshot(100, datetime.now(timezone.utc), False, tuple(_sample_courses())), "complete"),
        (SelectionPolicy(), _market([CourseMarket("A", "A", 0, 0, target_interested=True)]), "capacity"),
        (SelectionPolicy(), _market([CourseMarket("A", "A", 1, 0)]), "target course"),
        (SelectionPolicy(demand_multipliers=(0.8, 1.0, 1e308)), _market(_sample_courses()), "multipliers"),
    ],
)
def test_invalid_markets_are_rejected(policy, market, error):
    with pytest.raises(CourseSelectionError, match=error):
        optimize_course_weights(policy, market)
