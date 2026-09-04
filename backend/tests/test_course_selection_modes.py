import pytest

from backend.core.course_selection.jwxk import JwxkSessionClient
from backend.core.course_selection.modes import (
    classify_selection_record,
    current_selection_records,
    is_real_teaching_class_type,
    selection_mode,
)


def test_round_modes_keep_result_feeds_and_task_types_separate():
    assert selection_mode("02")["current_result_feeds"] == ("selected",)
    assert selection_mode("02")["task_types"] == ("selection", "vacancy_swap")
    assert selection_mode("04")["current_result_feeds"] == ("volunteered",)
    assert selection_mode("04")["task_types"] == ("weight_strategy",)


def test_current_grab_results_only_use_matching_selected_rows():
    official = {
        "selected": [
            {
                "class_id": "current",
                "record_batch_code": "batch",
                "record_term_code": "term",
                "selected_count": 3,
                "capacity": 30,
            },
            {
                "class_id": "other-batch",
                "record_batch_code": "old",
                "record_term_code": "term",
                "selected_count": 3,
                "capacity": 30,
            },
            {
                "class_id": "zero-zero",
                "record_batch_code": "batch",
                "record_term_code": "term",
                "selected_count": 0,
                "capacity": 0,
            },
        ],
        "volunteered": [{
            "class_id": "weighted",
            "record_batch_code": "batch",
            "weight_participant_count": 5,
            "capacity": 30,
        }],
    }

    rows = current_selection_records(
        official,
        selection_type_code="02",
        batch_code="batch",
        term_code="term",
    )

    assert [row["class_id"] for row in rows] == ["current"]


@pytest.mark.parametrize(
    "selection_type_code,record_type,participant_field",
    [
        ("02", "selected", "selected_count"),
        ("04", "volunteered", "weight_participant_count"),
    ],
)
def test_zero_zero_is_never_operable(selection_type_code, record_type, participant_field):
    classified = classify_selection_record(
        {
            participant_field: 0,
            "capacity": 0,
            "selection_record_type": record_type,
        },
        selection_type_code=selection_type_code,
        batch_code="batch",
        record_type=record_type,
    )

    assert classified["current_batch_record"] is False
    assert classified["operation_allowed"] is False


@pytest.mark.parametrize("value", ["", "ALL", "ROUND", "ALLKC"])
def test_catalog_scopes_are_not_mutation_types(value):
    assert is_real_teaching_class_type(value) is False


@pytest.mark.parametrize(
    "code,message,expected",
    [
        ("409", "课程已满", "FULL"),
        ("409", "当前不可选", "UNAVAILABLE"),
        ("409", "名额已被抢", "RACE_LOST"),
        ("401", "请重新登录", "AUTH_REQUIRED"),
        ("429", "请求过快", "RATE_LIMITED"),
        ("500", "系统处理失败", "UNKNOWN"),
    ],
)
def test_mutation_failure_classification_is_conservative(code, message, expected):
    assert JwxkSessionClient._classify_mutation_failure(
        code, message, success=False,
    ) == expected
