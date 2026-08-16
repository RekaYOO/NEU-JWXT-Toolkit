from backend.core.course_selection import (
    WeightCandidate,
    WeightGroupTarget,
    WeightMarketCourse,
    WeightPolicy,
    optimize_grouped_weights,
)


def _market():
    return [
        WeightMarketCourse("A", 30, 35),
        WeightMarketCourse("B", 30, 30),
        WeightMarketCourse("C", 30, 19),
        WeightMarketCourse("D", 80, 58),
    ]


def test_group_optimizer_satisfies_targets_and_returns_one_recommendation():
    result = optimize_grouped_weights(
        policy=WeightPolicy(budget=105, min_bid=5),
        grade_size=126,
        market_courses=_market(),
        groups=[WeightGroupTarget("g1", "A 类", 2), WeightGroupTarget("g2", "B 类", 1)],
        candidates=[
            WeightCandidate("A", "A", 30, 35, 10, ("g1",)),
            WeightCandidate("B", "B", 30, 30, 8, ("g1",)),
            WeightCandidate("C", "C", 30, 19, 7, ("g1",)),
            WeightCandidate("D", "D", 80, 58, 9, ("g2",)),
        ],
    )

    assert result["model_version"].startswith("course-weight-optimizer-d70349b")
    assert all(group["satisfied"] for group in result["groups"])
    selected = [item for item in result["courses"] if item["bid"] > 0]
    assert len(selected) == 3
    assert sum(item["bid"] for item in selected) <= 105
    assert all(set(item["scenario_success_rates"]) == {"conservative", "neutral", "aggressive"} for item in result["courses"])


def test_group_optimizer_respects_hard_conflicts_and_reports_gap():
    result = optimize_grouped_weights(
        policy=WeightPolicy(budget=10, min_bid=5),
        grade_size=100,
        market_courses=_market(),
        groups=[WeightGroupTarget("g", "目标", 2)],
        candidates=[
            WeightCandidate("A", "A", 30, 35, 10, ("g",)),
            WeightCandidate("B", "B", 30, 30, 9, ("g",)),
        ],
        conflicts=[("A", "B")],
    )

    assert result["groups"][0]["missing_count"] == 1
    assert len([item for item in result["courses"] if item["bid"] > 0]) == 1
    assert any("无法满足" in warning for warning in result["warnings"])


def test_selected_course_counts_toward_group_without_spending_remaining_budget():
    result = optimize_grouped_weights(
        policy=WeightPolicy(budget=20, min_bid=5),
        grade_size=100,
        market_courses=_market(),
        groups=[WeightGroupTarget("g", "目标", 2)],
        candidates=[
            WeightCandidate("A", "A", 30, 35, 10, ("g",), already_selected=True),
            WeightCandidate("B", "B", 30, 30, 9, ("g",)),
        ],
    )

    by_id = {item["course_id"]: item for item in result["courses"]}
    assert by_id["A"]["classification"] == "SELECTED"
    assert by_id["A"]["bid"] == 0
    assert by_id["B"]["bid"] == 20
    assert result["groups"][0]["satisfied"] is True


def test_unknown_time_is_warned_but_not_treated_as_safe_conflict_data():
    result = optimize_grouped_weights(
        policy=WeightPolicy(budget=5, min_bid=5),
        grade_size=100,
        market_courses=_market(),
        groups=[WeightGroupTarget("g", "目标", 1)],
        candidates=[WeightCandidate("A", "A", 30, 35, 10, ("g",), time_unknown=True)],
    )
    assert any("时间信息待核验" in warning for warning in result["warnings"])


def test_live_candidate_market_value_replaces_older_round_snapshot():
    result = optimize_grouped_weights(
        policy=WeightPolicy(budget=10, min_bid=5),
        grade_size=100,
        market_courses=[
            WeightMarketCourse("A", 30, 90),
            WeightMarketCourse("BACKGROUND", 30, 10),
        ],
        groups=[WeightGroupTarget("g", "目标", 1)],
        candidates=[WeightCandidate("A", "A", 30, 20, 10, ("g",))],
    )

    assert result["diagnostics"]["total_current_bidders"] == 30
    assert result["courses"][0]["forecast_participants"]["neutral"] < 90
    assert result["courses"][0]["classification"] == "SAFE"
    assert result["courses"][0]["bid"] == 5
    assert "终局预测仅作风险参考" in result["courses"][0]["recommendation_reason"]


def test_live_safe_courses_never_receive_more_than_minimum_weight():
    result = optimize_grouped_weights(
        policy=WeightPolicy(budget=105, min_bid=5),
        grade_size=126,
        market_courses=[
            WeightMarketCourse("SAFE", 104, 68),
            WeightMarketCourse("COMP", 104, 196),
        ],
        groups=[WeightGroupTarget("g", "目标", 2)],
        candidates=[
            WeightCandidate("SAFE", "未满课程", 104, 68, 10, ("g",)),
            WeightCandidate("COMP", "超额课程", 104, 196, 10, ("g",)),
        ],
    )

    by_id = {item["course_id"]: item for item in result["courses"]}
    assert by_id["SAFE"]["classification"] == "SAFE"
    assert by_id["SAFE"]["bid"] == 5
    assert by_id["COMP"]["classification"] == "COMP"
    assert by_id["COMP"]["bid"] == 100
