"""JWXK weight-allocation optimizer.

The mathematical core is adapted from rtb-1005/Course_Weight-Optimizer
(commit d70349b1e8cd5bef2ab73bdcce712614813243e6, MIT).  The upstream
forecast, SAFE/COMP split, exponential proxy and water-filling allocation are
kept, while the search layer is extended for this project's user-defined plan
groups and already-selected courses.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from time import monotonic
from typing import Iterable, Sequence


WEIGHT_MODEL_VERSION = "course-weight-optimizer-d70349b-group-v1"
SCENARIO_MULTIPLIERS = (0.8, 1.0, 1.6)
SCENARIO_NAMES = ("conservative", "neutral", "aggressive")
_EPS = 1e-9
_DELTA = 0.05


class WeightOptimizationError(ValueError):
    """Raised when a weight strategy cannot be produced safely."""


@dataclass(frozen=True)
class WeightPolicy:
    budget: int
    min_bid: int = 5
    bid_step: int = 1
    node_limit: int = 200_000
    time_limit_seconds: float = 1.5


@dataclass(frozen=True)
class WeightMarketCourse:
    course_id: str
    capacity: int
    bidders: int


@dataclass(frozen=True)
class WeightCandidate:
    course_id: str
    name: str
    capacity: int
    bidders: int
    utility: float
    group_ids: tuple[str, ...]
    already_selected: bool = False
    time_unknown: bool = False


@dataclass(frozen=True)
class WeightGroupTarget:
    group_id: str
    name: str
    target_count: int


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_overlap_stats(
    grade_size: int,
    courses: Sequence[WeightMarketCourse],
    policy: WeightPolicy,
) -> tuple[float, float, float]:
    total_bidders = sum(float(course.bidders) for course in courses)
    average_entries = total_bidders / float(grade_size)
    max_entries = policy.budget / float(policy.min_bid)
    return total_bidders, average_entries, max_entries


def predict_final_bidders(
    grade_size: int,
    courses: Sequence[WeightMarketCourse],
    target_entries: float,
) -> dict[str, float]:
    current_total = sum(float(course.bidders) for course in courses)
    target_total = float(grade_size) * float(target_entries)
    addition = max(0.0, target_total - current_total)
    if current_total > 0:
        return {
            course.course_id: min(
                float(grade_size),
                float(course.bidders) + addition * float(course.bidders) / current_total,
            )
            for course in courses
        }
    share = addition / max(1, len(courses))
    return {
        course.course_id: min(float(grade_size), float(course.bidders) + share)
        for course in courses
    }


def compute_alpha(capacity: int, congestion: float, typical_bid: float) -> float:
    del capacity  # Kept in the signature to mirror the upstream model.
    return typical_bid * math.log(max(congestion, 1.0 + _DELTA))


def proxy_probability(bid: float, alpha: float) -> float:
    return 1.0 - math.exp(-bid / (alpha + _EPS))


def waterfill_allocate(
    course_ids: Sequence[str],
    utilities: dict[str, float],
    alphas: dict[str, float],
    budget: float,
    min_bid: float,
) -> dict[str, float]:
    if not course_ids:
        return {}
    if budget + _EPS < min_bid * len(course_ids):
        raise WeightOptimizationError("剩余权重不足以满足最低投权")
    remaining = budget - min_bid * len(course_ids)
    if remaining <= _EPS:
        return {course_id: min_bid for course_id in course_ids}

    adjusted = {course_id: alphas[course_id] + _EPS for course_id in course_ids}

    def total_extra(nu: float) -> float:
        result = 0.0
        for course_id in course_ids:
            utility = max(utilities[course_id], _EPS)
            alpha = adjusted[course_id]
            extra = alpha * math.log(utility / max(nu * alpha, _EPS)) - min_bid
            result += max(0.0, extra)
        return result

    high = max(
        max(utilities[course_id], _EPS)
        / (adjusted[course_id] * math.exp(min_bid / adjusted[course_id]))
        for course_id in course_ids
    ) * 1.0001
    low = high * 1e-12
    for _ in range(80):
        middle = (low + high) / 2.0
        if total_extra(middle) > remaining:
            low = middle
        else:
            high = middle

    bids = {}
    for course_id in course_ids:
        utility = max(utilities[course_id], _EPS)
        alpha = adjusted[course_id]
        extra = alpha * math.log(utility / max(high * alpha, _EPS)) - min_bid
        bids[course_id] = min_bid + max(0.0, extra)
    difference = budget - sum(bids.values())
    if abs(difference) > 1e-6:
        share = difference / len(course_ids)
        bids = {course_id: max(min_bid, bid + share) for course_id, bid in bids.items()}
    return bids


def _integerize_bids(
    raw: dict[str, float],
    *,
    budget: int,
    min_bid: int,
    bid_step: int,
    utilities: dict[str, float],
) -> dict[str, int]:
    result = {
        course_id: max(min_bid, int(math.floor((bid + _EPS) / bid_step)) * bid_step)
        for course_id, bid in raw.items()
    }
    remaining = budget - sum(result.values())
    order = sorted(
        raw,
        key=lambda course_id: (
            -(raw[course_id] - result[course_id]),
            -utilities[course_id],
            course_id,
        ),
    )
    index = 0
    while order and remaining >= bid_step:
        course_id = order[index % len(order)]
        result[course_id] += bid_step
        remaining -= bid_step
        index += 1
    return result


def _conflict_pairs(conflicts: Iterable[tuple[str, str]]) -> set[frozenset[str]]:
    return {frozenset((left, right)) for left, right in conflicts if left != right}


def optimize_grouped_weights(
    *,
    policy: WeightPolicy,
    grade_size: int,
    market_courses: Sequence[WeightMarketCourse],
    candidates: Sequence[WeightCandidate],
    groups: Sequence[WeightGroupTarget],
    conflicts: Iterable[tuple[str, str]] = (),
) -> dict[str, object]:
    """Return one recommendation plus three upstream demand scenarios."""

    if not 0 < grade_size <= 100_000:
        raise WeightOptimizationError("年级人数超出允许范围")
    if not 0 < policy.min_bid <= policy.budget <= 150:
        raise WeightOptimizationError("官方剩余权重或最低投权无效")
    if policy.bid_step <= 0 or policy.min_bid % policy.bid_step:
        raise WeightOptimizationError("官方投权步长无效")
    if not market_courses:
        raise WeightOptimizationError("完整轮次课程数据尚未准备好")
    if not candidates or not groups:
        raise WeightOptimizationError("请先配置包含候选课程的方案组")

    market_by_id = {course.course_id: course for course in market_courses}
    for candidate in candidates:
        # Candidate rows are refreshed immediately before every strategy run.
        # They must replace the possibly older round-wide market snapshot,
        # otherwise the model can visibly show the new QZXKRS value while
        # still calculating forecasts from the archived value.
        market_by_id[candidate.course_id] = WeightMarketCourse(
            candidate.course_id, candidate.capacity, candidate.bidders,
        )
    market = tuple(market_by_id.values())
    group_by_id = {group.group_id: group for group in groups}
    if any(group.target_count <= 0 for group in groups):
        raise WeightOptimizationError("方案组目标门数必须为正数")

    total_bidders, average_entries_raw, max_entries = compute_overlap_stats(
        grade_size, market, policy,
    )
    average_entries = clamp(average_entries_raw, 1.0, max_entries)
    scenario_entries = tuple(
        clamp(average_entries * multiplier, 1.0, max_entries)
        for multiplier in SCENARIO_MULTIPLIERS
    )
    scenario_typical_bids = tuple(policy.budget / max(value, _EPS) for value in scenario_entries)
    forecasts = tuple(
        predict_final_bidders(grade_size, market, value)
        for value in scenario_entries
    )
    market_scope_mismatch = average_entries_raw > max_entries + _EPS

    candidate_by_id = {candidate.course_id: candidate for candidate in candidates}
    if len(candidate_by_id) != len(candidates):
        raise WeightOptimizationError("同一课程只能作为一个投权目标")
    for candidate in candidates:
        if not candidate.group_ids or any(group_id not in group_by_id for group_id in candidate.group_ids):
            raise WeightOptimizationError(f"课程“{candidate.name}”没有有效方案组")
        if not 1 <= candidate.utility <= 10:
            raise WeightOptimizationError(f"课程“{candidate.name}”的意愿评分应为 1–10")
        if candidate.capacity <= 0 or candidate.bidders < 0:
            raise WeightOptimizationError(f"课程“{candidate.name}”缺少有效容量或投权人数")

    # This project runs the strategy repeatedly against live JWXK counts.  The
    # upstream end-of-round forecast remains useful as a risk reference, but it
    # must not consume weight that can protect courses which are already full.
    # Therefore SAFE/COMP is determined by the latest official snapshot:
    # strictly under capacity is SAFE and always receives only min_bid.
    safe_ids: set[str] = set()
    alphas_by_scenario: tuple[dict[str, float], ...] = tuple({} for _ in SCENARIO_NAMES)
    for candidate in candidates:
        predicted = [forecast.get(candidate.course_id, float(candidate.bidders)) for forecast in forecasts]
        if candidate.bidders < candidate.capacity:
            safe_ids.add(candidate.course_id)
        for index, count in enumerate(predicted):
            congestion = count / float(candidate.capacity)
            alphas_by_scenario[index][candidate.course_id] = compute_alpha(
                candidate.capacity, congestion, scenario_typical_bids[index],
            )

    fixed = tuple(candidate for candidate in candidates if candidate.already_selected)
    selectable = sorted(
        (candidate for candidate in candidates if not candidate.already_selected),
        key=lambda item: (-item.utility, item.course_id),
    )
    known_conflicts = _conflict_pairs(conflicts)
    fixed_ids = {candidate.course_id for candidate in fixed}
    fixed_group_counts = {group.group_id: 0 for group in groups}
    for candidate in fixed:
        for group_id in candidate.group_ids:
            fixed_group_counts[group_id] += 1

    started = monotonic()
    visited = 0
    approximate = False
    best: tuple[tuple[float, ...], tuple[WeightCandidate, ...], dict[str, int]] | None = None

    def allocate(selected: Sequence[WeightCandidate]) -> dict[str, int] | None:
        safe = [item.course_id for item in selected if item.course_id in safe_ids]
        competitive = [item.course_id for item in selected if item.course_id not in safe_ids]
        safe_cost = policy.min_bid * len(safe)
        competitive_budget = policy.budget - safe_cost
        if competitive_budget < policy.min_bid * len(competitive):
            return None
        result = {course_id: policy.min_bid for course_id in safe}
        if competitive:
            raw = waterfill_allocate(
                competitive,
                {item.course_id: item.utility for item in selected},
                alphas_by_scenario[1],
                competitive_budget,
                policy.min_bid,
            )
            result.update(_integerize_bids(
                raw,
                budget=competitive_budget,
                min_bid=policy.min_bid,
                bid_step=policy.bid_step,
                utilities={item.course_id: item.utility for item in selected},
            ))
        return result

    def evaluate(selected: tuple[WeightCandidate, ...], counts: dict[str, int]) -> None:
        nonlocal best
        bids = allocate(selected)
        if bids is None:
            return
        covered = sum(min(counts[group.group_id], group.target_count) for group in groups)
        utility = sum(item.utility for item in (*fixed, *selected))
        baseline_proxy = 0.0
        for item in selected:
            if item.course_id in safe_ids:
                probability = 1.0
            else:
                probability = proxy_probability(bids[item.course_id], alphas_by_scenario[1][item.course_id])
            baseline_proxy += item.utility * probability
        score = (float(covered), utility, baseline_proxy, float(-sum(bids.values())))
        if best is None or score > best[0]:
            best = (score, selected, bids)

    def walk(
        index: int,
        selected: tuple[WeightCandidate, ...],
        counts: dict[str, int],
    ) -> None:
        nonlocal visited, approximate
        visited += 1
        if visited > policy.node_limit or monotonic() - started > policy.time_limit_seconds:
            approximate = True
            return
        evaluate(selected, counts)
        if index >= len(selectable) or len(selected) >= policy.budget // policy.min_bid:
            return
        for candidate_index in range(index, len(selectable)):
            candidate = selectable[candidate_index]
            if any(counts[group_id] >= group_by_id[group_id].target_count for group_id in candidate.group_ids):
                continue
            if any(
                frozenset((candidate.course_id, other_id)) in known_conflicts
                for other_id in fixed_ids | {item.course_id for item in selected}
            ):
                continue
            next_counts = dict(counts)
            for group_id in candidate.group_ids:
                next_counts[group_id] += 1
            walk(candidate_index + 1, (*selected, candidate), next_counts)
            if approximate:
                return

    walk(0, (), fixed_group_counts)
    if best is None:
        raise WeightOptimizationError("当前预算和冲突约束下不存在可行投权方案")
    _, chosen, bids = best
    chosen_ids = {item.course_id for item in chosen}
    final_counts = dict(fixed_group_counts)
    for item in chosen:
        for group_id in item.group_ids:
            final_counts[group_id] += 1

    course_results = []
    for candidate in candidates:
        selected = candidate.already_selected or candidate.course_id in chosen_ids
        bid = int(bids.get(candidate.course_id, 0))
        forecast_by_scenario = {
            name: forecasts[index].get(candidate.course_id, float(candidate.bidders))
            for index, name in enumerate(SCENARIO_NAMES)
        }
        forecast_status = "scope_mismatch" if market_scope_mismatch else (
            "flat_current" if len({round(value, 6) for value in forecast_by_scenario.values()}) == 1
            else "available"
        )
        if candidate.already_selected:
            classification = "SELECTED"
            rates = {name: 1.0 for name in SCENARIO_NAMES}
            recommendation_reason = "该课程已经形成选课结果，不参与本轮投权分配"
        elif not selected:
            classification = "OUT"
            rates = {name: 0.0 for name in SCENARIO_NAMES}
            recommendation_reason = "该课程未进入满足方案组目标的本轮推荐组合"
        elif candidate.course_id in safe_ids:
            classification = "SAFE"
            rates = {name: 1.0 for name in SCENARIO_NAMES}
            recommendation_reason = (
                f"当前 {candidate.bidders}/{candidate.capacity} 尚未满，实时策略将其视为 SAFE，"
                f"固定使用官方最低投权 {policy.min_bid} 点；终局预测仅作风险参考"
            )
        else:
            classification = "COMP"
            rates = {
                name: proxy_probability(bid, alphas_by_scenario[index][candidate.course_id])
                for index, name in enumerate(SCENARIO_NAMES)
            }
            aggressive_count = forecast_by_scenario["aggressive"]
            if candidate.bidders < candidate.capacity and aggressive_count > candidate.capacity + _EPS:
                competition_basis = (
                    f"当前 {candidate.bidders}/{candidate.capacity} 尚未满，"
                    f"但激进情景预计 {aggressive_count:.1f}/{candidate.capacity}"
                )
            else:
                competition_basis = (
                    f"当前 {candidate.bidders}/{candidate.capacity}，"
                    f"中性情景预计 {forecast_by_scenario['neutral']:.1f}/{candidate.capacity}"
                )
            recommendation_reason = (
                f"{competition_basis}；water-filling 结合意愿评分分配 {bid} 点"
            )
        blocked_by = [
            other_id for other_id in chosen_ids | fixed_ids
            if frozenset((candidate.course_id, other_id)) in known_conflicts
        ] if not selected else []
        course_results.append({
            "course_id": candidate.course_id,
            "name": candidate.name,
            "bid": bid,
            "utility": candidate.utility,
            "classification": classification,
            "selected": selected,
            "already_selected": candidate.already_selected,
            "scenario_success_rates": rates,
            "forecast_participants": forecast_by_scenario,
            "forecast_status": forecast_status,
            "recommendation_reason": recommendation_reason,
            "blocked_by_conflicts": blocked_by,
            "time_unknown": candidate.time_unknown,
        })

    group_results = [{
        "group_id": group.group_id,
        "name": group.name,
        "target_count": group.target_count,
        "selected_count": final_counts[group.group_id],
        "missing_count": max(0, group.target_count - final_counts[group.group_id]),
        "satisfied": final_counts[group.group_id] >= group.target_count,
    } for group in groups]
    warnings = []
    if average_entries_raw < 1.0 - _EPS:
        warnings.append("全市场已投注次数低于年级人数，终局预测的置信度较低")
    if market_scope_mismatch:
        warnings.append(
            "当前轮次累计投注次数超出年级人数模型的理论范围，三种终局人数情景暂不可区分；"
            "实时 SAFE/COMP 分类和当前人数仍然有效"
        )
    if any(candidate.time_unknown for candidate in candidates):
        warnings.append("部分课程时间信息待核验，模型没有将其视为无冲突")
    if any(not group["satisfied"] for group in group_results):
        warnings.append("当前预算、冲突或候选范围无法满足全部方案组目标")
    if approximate:
        warnings.append("候选组合较多，已返回搜索时限内找到的最佳可行方案")

    return {
        "model_version": WEIGHT_MODEL_VERSION,
        "market_confidence": "uncalibrated_proxy",
        "approximate": approximate,
        "nodes_visited": visited,
        "budget_used": sum(bids.values()),
        "courses": course_results,
        "groups": group_results,
        "warnings": warnings,
        "diagnostics": {
            "grade_size": grade_size,
            "market_course_count": len(market),
            "total_current_bidders": total_bidders,
            "average_entries_raw": average_entries_raw,
            "market_scope_mismatch": market_scope_mismatch,
            "scenario_entries": dict(zip(SCENARIO_NAMES, scenario_entries)),
        },
    }
