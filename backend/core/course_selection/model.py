"""Reference-inspired, stateless course-weight allocation model.

Only aggregate participation is available before the new selection API is
integrated.  The model therefore exposes a smooth contest proxy, not a claim
about real admission probabilities or a recovered Nash equilibrium.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Sequence


MODEL_VERSION = "course-selection-contest-v1"
_SCORE_EPSILON = 1e-10


class CourseSelectionError(ValueError):
    """Raised when a normalized market cannot be solved safely."""


class TieRule(str, Enum):
    RANDOM = "random"
    CONSERVATIVE = "conservative"


@dataclass(frozen=True)
class SelectionPolicy:
    budget: int = 105
    min_bid: int = 5
    bid_step: int = 1
    tie_rule: TieRule = TieRule.RANDOM
    max_selected_courses: int | None = None
    demand_multipliers: tuple[float, float, float] = (0.8, 1.0, 1.2)


@dataclass(frozen=True)
class CourseMarket:
    course_id: str
    name: str
    capacity: int
    current_participants: int
    target_included: bool = False
    target_interested: bool = False
    target_utility: float | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    cohort_size: int
    captured_at: datetime
    is_complete: bool
    courses: tuple[CourseMarket, ...]


def _legal_bids(policy: SelectionPolicy) -> tuple[int, ...]:
    return tuple(range(policy.min_bid, policy.budget + 1, policy.bid_step))


def _validate(policy: SelectionPolicy, market: MarketSnapshot) -> None:
    if not market.is_complete:
        raise CourseSelectionError("course market snapshot must be complete")
    if not 0 < market.cohort_size <= 100_000:
        raise CourseSelectionError("cohort_size is outside the model limit")
    if market.captured_at.tzinfo is None or market.captured_at.utcoffset() is None:
        raise CourseSelectionError("captured_at must include a timezone")
    if not market.courses:
        raise CourseSelectionError("course market must not be empty")
    if len(market.courses) > 100:
        raise CourseSelectionError("course market exceeds the model limit")
    if not 0 < policy.budget <= 150 or policy.min_bid <= 0 or policy.bid_step <= 0:
        raise CourseSelectionError("budget, min_bid and bid_step are outside the model limit")
    if policy.min_bid > policy.budget:
        raise CourseSelectionError("budget is smaller than min_bid")
    if policy.budget % policy.bid_step or policy.min_bid % policy.bid_step:
        raise CourseSelectionError("budget and min_bid must align to bid_step")
    if len(policy.demand_multipliers) != 3:
        raise CourseSelectionError("exactly three demand multipliers are required")
    low, base, high = policy.demand_multipliers
    if not all(math.isfinite(value) for value in (low, base, high)):
        raise CourseSelectionError("demand multipliers must be finite")
    if not 0.25 <= low < base < high <= 3.0 or abs(base - 1.0) > 1e-9:
        raise CourseSelectionError("demand multipliers must be ordered around a 1.0 base")

    ids: set[str] = set()
    interested = 0
    for course in market.courses:
        if not course.course_id or course.course_id in ids:
            raise CourseSelectionError("course_id values must be non-empty and unique")
        ids.add(course.course_id)
        if len(course.course_id) > 64 or len(course.name) > 200:
            raise CourseSelectionError(f"course text exceeds the model limit: {course.course_id}")
        if not 0 < course.capacity <= 100_000:
            raise CourseSelectionError(f"capacity must be positive: {course.course_id}")
        if not 0 <= course.current_participants <= market.cohort_size:
            raise CourseSelectionError(f"participant count is invalid: {course.course_id}")
        if course.target_included and course.current_participants == 0:
            raise CourseSelectionError(f"target cannot be included in an empty course: {course.course_id}")
        if course.target_utility is not None and (
            not math.isfinite(course.target_utility)
            or course.target_utility <= 0
            or course.target_utility > 100
        ):
            raise CourseSelectionError(f"target utility must be finite and positive: {course.course_id}")
        if course.target_interested:
            interested += 1
    if interested == 0:
        raise CourseSelectionError("at least one target course is required")
    if sum(course.current_participants for course in market.courses) > (
        market.cohort_size * (policy.budget // policy.min_bid)
    ):
        raise CourseSelectionError("aggregate participation exceeds the feasible entry budget")
    max_selected = policy.max_selected_courses or policy.budget // policy.min_bid
    if max_selected <= 0:
        raise CourseSelectionError("max_selected_courses must be positive")
    legal_bid_count = len(_legal_bids(policy))
    if len(market.courses) * legal_bid_count > 3_000:
        raise CourseSelectionError("market and bid-grid combination is too large")
    max_selected = min(interested, max_selected)
    if interested > 30 or interested * policy.budget * legal_bid_count * max_selected > 8_000_000:
        raise CourseSelectionError("target-course optimization workload is too large")


def _distribute_additions(
    base: Sequence[int],
    additions: int,
    weights: Sequence[float],
    limits: Sequence[int],
) -> list[int]:
    result = list(base)
    remaining = additions
    while remaining > 0:
        available = [index for index, value in enumerate(result) if value < limits[index]]
        if not available:
            break
        total_weight = sum(weights[index] for index in available)
        quotas = {index: remaining * weights[index] / total_weight for index in available}
        allocated = 0
        for index in available:
            amount = min(limits[index] - result[index], int(math.floor(quotas[index])))
            result[index] += amount
            allocated += amount
        remaining -= allocated
        if remaining <= 0:
            break
        ranked = sorted(
            available,
            key=lambda index: (-(quotas[index] - math.floor(quotas[index])), index),
        )
        progressed = False
        for index in ranked:
            if remaining <= 0:
                break
            if result[index] < limits[index]:
                result[index] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return result


def forecast_participants(
    market: MarketSnapshot,
    multipliers: Sequence[float],
    max_entries_per_student: int | None = None,
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Build monotone low/base/high demand from aggregate participation."""

    target_offsets = [1 if course.target_included else 0 for course in market.courses]
    current = [
        course.current_participants - target_offsets[index]
        for index, course in enumerate(market.courses)
    ]
    total = sum(current)
    weights = [value + 0.5 for value in current]
    low_factor, _, high_factor = multipliers

    low_target = max(0, min(total, int(round(total * low_factor))))
    if total:
        low = [int(math.floor(value * low_target / total)) for value in current]
        low = _distribute_additions(low, low_target - sum(low), weights, current)
    else:
        low = [0] * len(current)

    opponent_limits = [market.cohort_size - offset for offset in target_offsets]
    high_target = min(
        sum(opponent_limits),
        market.cohort_size * (max_entries_per_student or len(current)),
        max(total, int(round(total * high_factor))),
    )
    high = _distribute_additions(current, high_target - total, weights, opponent_limits)
    return tuple(
        tuple(value + target_offsets[index] for index, value in enumerate(counts))
        for counts in (low, current, high)
    )


def typical_course_bid(policy: SelectionPolicy, market: MarketSnapshot) -> float:
    """Return the reference model's market-wide budget-per-entry scale."""

    total_entries = sum(course.current_participants for course in market.courses)
    average_entries = min(
        policy.budget / policy.min_bid,
        max(1.0, total_entries / market.cohort_size),
    )
    return policy.budget / average_entries


def contest_success_rate(
    *,
    bid: int,
    typical_bid: float,
    participants: int,
    capacity: int,
    target_already_included: bool,
    bid_step: int,
    tie_rule: TieRule,
) -> float:
    """Smooth, monotone improvement of the reference exponential proxy.

    The original proxy used ``log(max(rho, 1.05))``.  Near rho=1 that makes
    the scale almost zero and reports nearly 100% success for tiny bids.  This
    variant normalizes ``log1p(rho)`` so a just-full course has a cutoff scale
    equal to the market's typical bid.
    """

    if bid <= 0:
        return 0.0
    target_addition = 0 if target_already_included else 1
    final_participants = participants + target_addition
    if final_participants <= capacity:
        return 1.0
    congestion = final_participants / capacity
    competition_scale = typical_bid * math.log1p(congestion) / math.log(2.0)
    tie_credit = 0.5 * bid_step if tie_rule == TieRule.RANDOM else 0.0
    effective_bid = bid + tie_credit
    return min(1.0, max(0.0, -math.expm1(-effective_bid / competition_scale)))


def _optimize_integer_budget(
    courses: Sequence[CourseMarket],
    bids: Sequence[int],
    objective_curves: Sequence[Sequence[float]],
    secondary_curves: Sequence[Sequence[float]],
    policy: SelectionPolicy,
) -> tuple[int, ...]:
    max_selected = min(
        len(courses),
        policy.max_selected_courses or policy.budget // policy.min_bid,
    )
    states: dict[tuple[int, int], tuple[float, float, tuple[int, ...]]] = {
        (0, 0): (0.0, 0.0, ())
    }
    choices = (0,) + tuple(bids)
    for course_index in range(len(courses)):
        next_states: dict[tuple[int, int], tuple[float, float, tuple[int, ...]]] = {}
        primary = objective_curves[course_index]
        secondary = secondary_curves[course_index]
        lookup = {0: (0.0, 0.0)}
        lookup.update({bid: (primary[index], secondary[index]) for index, bid in enumerate(bids)})
        for (spent, selected), (score, secondary_score, allocation) in states.items():
            for bid in choices:
                new_spent = spent + bid
                new_selected = selected + (1 if bid else 0)
                if new_spent > policy.budget or new_selected > max_selected:
                    continue
                gain, secondary_gain = lookup[bid]
                candidate = (score + gain, secondary_score + secondary_gain, allocation + (bid,))
                key = (new_spent, new_selected)
                existing = next_states.get(key)
                if existing is None or candidate[0] > existing[0] + _SCORE_EPSILON or (
                    abs(candidate[0] - existing[0]) <= _SCORE_EPSILON
                    and (
                        candidate[1] > existing[1] + _SCORE_EPSILON
                        or (
                            abs(candidate[1] - existing[1]) <= _SCORE_EPSILON
                            and candidate[2] > existing[2]
                        )
                    )
                ):
                    next_states[key] = candidate
        states = next_states

    finalists = [value for (spent, _), value in states.items() if spent == policy.budget]
    if not finalists:
        raise CourseSelectionError("budget cannot be allocated under the selected constraints")
    best = finalists[0]
    for candidate in finalists[1:]:
        if candidate[0] > best[0] + _SCORE_EPSILON or (
            abs(candidate[0] - best[0]) <= _SCORE_EPSILON
            and (
                candidate[1] > best[1] + _SCORE_EPSILON
                or (
                    abs(candidate[1] - best[1]) <= _SCORE_EPSILON
                    and candidate[2] > best[2]
                )
            )
        ):
            best = candidate
    return best[2]


def optimize_course_weights(policy: SelectionPolicy, market: MarketSnapshot) -> dict[str, object]:
    """Return three deterministic, full-budget integer strategies."""

    _validate(policy, market)
    scenario_names = ("optimistic", "baseline", "pessimistic")
    forecasts = forecast_participants(
        market,
        policy.demand_multipliers,
        policy.budget // policy.min_bid,
    )
    course_scale = typical_course_bid(policy, market)
    diagnostics = []
    for name, counts in zip(scenario_names, forecasts):
        total_entries = sum(counts)
        diagnostics.append({
            "scenario": name,
            "total_entries": total_entries,
            "average_entries": total_entries / market.cohort_size,
            "typical_bid": course_scale,
        })

    courses_all = tuple(sorted(market.courses, key=lambda course: course.course_id))
    forecast_by_id = [
        {course.course_id: counts[index] for index, course in enumerate(market.courses)}
        for counts in forecasts
    ]
    desired = [course for course in courses_all if course.target_interested]
    utilities = {course.course_id: course.target_utility or 1.0 for course in desired}
    desired.sort(key=lambda course: (-utilities[course.course_id], course.course_id))
    bids = _legal_bids(policy)

    probability_curves: dict[str, list[list[float]]] = {}
    for course in desired:
        probability_curves[course.course_id] = [
            [
                contest_success_rate(
                    bid=bid,
                    typical_bid=course_scale,
                    participants=forecast_by_id[scenario_index][course.course_id],
                    capacity=course.capacity,
                    target_already_included=course.target_included,
                    bid_step=policy.bid_step,
                    tie_rule=policy.tie_rule,
                )
                for bid in bids
            ]
            for scenario_index in range(3)
        ]

    balanced_curves: list[list[float]] = []
    objectives: dict[str, list[list[float]]] = {"robust": [], "balanced": [], "aggressive": []}
    for course in desired:
        utility = utilities[course.course_id]
        optimistic, baseline, pessimistic = probability_curves[course.course_id]
        robust = [utility * value for value in pessimistic]
        balanced = [
            utility * (0.25 * optimistic[index] + 0.5 * baseline[index] + 0.25 * pessimistic[index])
            for index in range(len(bids))
        ]
        aggressive = [utility * value for value in optimistic]
        objectives["robust"].append(robust)
        objectives["balanced"].append(balanced)
        objectives["aggressive"].append(aggressive)
        balanced_curves.append(balanced)

    strategies = []
    for strategy_name in ("robust", "balanced", "aggressive"):
        allocation = _optimize_integer_budget(
            desired,
            bids,
            objectives[strategy_name],
            balanced_curves,
            policy,
        )
        course_results = []
        scenario_totals = [0.0, 0.0, 0.0]
        objective_total = 0.0
        for index, (course, bid) in enumerate(zip(desired, allocation)):
            utility = utilities[course.course_id]
            curves = probability_curves[course.course_id]
            if bid:
                bid_index = bids.index(bid)
                rates = [curve[bid_index] for curve in curves]
                objective_value = objectives[strategy_name][index][bid_index]
                previous_value = 0.0 if bid_index == 0 else objectives[strategy_name][index][bid_index - 1]
                marginal = max(0.0, objective_value - previous_value)
                useful_bid = bid
                while useful_bid > policy.min_bid:
                    candidate_index = bids.index(useful_bid - policy.bid_step)
                    if abs(objectives[strategy_name][index][candidate_index] - objective_value) > _SCORE_EPSILON:
                        break
                    useful_bid -= policy.bid_step
                redundant = bid - useful_bid
            else:
                rates = [0.0, 0.0, 0.0]
                objective_value = 0.0
                marginal = 0.0
                redundant = 0
            for scenario_index, rate in enumerate(rates):
                scenario_totals[scenario_index] += utility * rate
            objective_total += objective_value
            pessimistic_count = forecast_by_id[2][course.course_id]
            target_addition = 0 if course.target_included else 1
            uncontested = pessimistic_count + target_addition <= course.capacity
            classification = "omitted" if bid == 0 else ("uncontested" if uncontested else "competitive")
            rate_map = dict(zip(scenario_names, rates))
            course_results.append({
                "course_id": course.course_id,
                "name": course.name,
                "bid": bid,
                "classification": classification,
                "utility": utility,
                "scenario_success_rates": rate_map,
                "success_rate_range": {
                    "worst_case": min(rates),
                    "best_case": max(rates),
                },
                "expected_utility": utility * (
                    0.25 * rate_map["optimistic"]
                    + 0.5 * rate_map["baseline"]
                    + 0.25 * rate_map["pessimistic"]
                ),
                "marginal_gain": marginal,
                "redundant_bid": redundant,
                "forecast_participants": {
                    name: forecast_by_id[scenario_index][course.course_id]
                    for scenario_index, name in enumerate(scenario_names)
                },
            })
        strategies.append({
            "name": strategy_name,
            "budget_used": sum(allocation),
            "objective_value": objective_total,
            "scenario_expected_utilities": dict(zip(scenario_names, scenario_totals)),
            "courses": sorted(course_results, key=lambda item: item["course_id"]),
        })

    return {
        "model_version": MODEL_VERSION,
        "market_confidence": "uncalibrated_reference_proxy",
        "solution_status": "solved",
        "warnings": [
            "模型内中选率基于聚合人数和参考指数竞争函数，未经真实录取结果校准。"
        ],
        "assumptions": [
            "reference_inspired_exponential_contest",
            "fixed_market_typical_bid_across_scenarios",
            "aggregate_participation_only",
            "complete_course_market",
            "model_success_rate_not_real_probability",
        ],
        "scenario_multipliers": dict(zip(scenario_names, policy.demand_multipliers)),
        "diagnostics": diagnostics,
        "strategies": strategies,
    }
