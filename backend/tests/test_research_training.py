import json

import pytest

from backend.core.academic.research_training import (
    ResearchTrainingAPI,
    ResearchTrainingError,
)


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeResearchClient:
    def __init__(self, *, eligibility=True):
        self.eligibility = eligibility
        self.calls = []

    def post(self, url, data, headers):
        self.calls.append((url, data))
        if url.endswith("/cxdqxnxqpc.do"):
            return FakeResponse({
                "code": "0",
                "datas": {
                    "cxdqxnxqpc": {
                        "WID": "batch-1",
                        "PCMC": "测试批次",
                        "XNXQDM": "2025-2026-2",
                        "XNXQDM_DISPLAY": "2025-2026学年春季学期",
                        "XSKXKTS": 1,
                        "ZYPM": 50,
                        "SFYXYBJGCJ": "0",
                        "SFYXYBJGCJ_DISPLAY": "否",
                        "ZBCS": 4,
                    }
                },
            })
        if url.endswith("/ktbmcxlb.do"):
            return FakeResponse({
                "code": "0",
                "datas": {
                    "ktbmcxlb": {
                        "rows": [{
                            "WID": "topic-1",
                            "PCWID": "batch-1",
                            "YJTM": "测试课题",
                            "KYXMMC": "测试项目",
                            "SSZY_DISPLAY": "示例专业",
                            "SSYX_DISPLAY": "示例学院",
                            "DSXM": "示例导师",
                            "ZSXSS": 2,
                            "YBMRS": 1,
                            "YQRRS": 0,
                            "BMWID": None,
                            "BMZT": None,
                        }],
                        "totalSize": 1,
                        "pageNumber": 1,
                        "pageSize": 20,
                    }
                },
            })
        if url.endswith("/cxxsjdjzypm.do"):
            rows = [{"PJJD": "3.5", "ZYPM": "12"}] if self.eligibility else []
            return FakeResponse({
                "code": "0",
                "datas": {"cxxsjdjzypm": {"rows": rows}},
            })
        if url.endswith("/ktbm/save.do"):
            return FakeResponse({"code": "0", "msg": "操作成功"})
        if url.endswith("/ktbm/qxbm.do"):
            return FakeResponse({"code": "0", "msg": "操作成功"})
        raise AssertionError(f"unexpected URL: {url}")


def test_research_training_queries_batch_topics_and_eligibility():
    client = FakeResearchClient()
    api = ResearchTrainingAPI(client)

    batch = api.get_current_batch()
    eligibility = api.get_eligibility(batch.batch_id)
    result = api.get_topics(
        batch.batch_id,
        keyword="测试",
        project_name="项目",
        advisor_name="导师",
    )

    assert batch.batch_id == "batch-1"
    assert batch.max_topics == 1
    assert eligibility.available is True
    assert result["total"] == 1
    assert result["topics"][0]["topic_id"] == "topic-1"
    assert result["topics"][0]["can_enroll"] is True

    topic_call = next(call for call in client.calls if call[0].endswith("/ktbmcxlb.do"))
    rules = json.loads(topic_call[1]["querySetting"])
    assert [(rule["name"], rule["builder"]) for rule in rules] == [
        ("PCWID", "equal"),
        ("YJTM", "include"),
        ("KYXMMC", "include"),
        ("DSXM", "include"),
    ]


def test_enrollment_stops_with_visible_error_when_official_rank_data_is_empty():
    client = FakeResearchClient(eligibility=False)
    api = ResearchTrainingAPI(client)

    eligibility = api.get_eligibility("batch-1")
    assert eligibility.available is False
    assert "官方页面" in eligibility.reason

    with pytest.raises(ResearchTrainingError, match="资格数据"):
        api.enroll(
            "topic-1",
            batch_id="batch-1",
            phone="13800000000",
            email="student@example.com",
            reason="希望参加",
        )
    assert not any(url.endswith("/ktbm/save.do") for url, _ in client.calls)


def test_enrollment_and_cancellation_use_official_payloads():
    client = FakeResearchClient()
    api = ResearchTrainingAPI(client)

    enrolled = api.enroll(
        "topic-1",
        batch_id="batch-1",
        phone="13800000000",
        email="student@example.com",
        reason="希望参加",
    )
    cancelled = api.cancel_enrollment("topic-1")

    assert enrolled["success"] is True
    assert cancelled["success"] is True

    save_call = next(call for call in client.calls if call[0].endswith("/ktbm/save.do"))
    assert save_call[1]["KTWID"] == "topic-1"
    form = json.loads(save_call[1]["FORMJSON"])
    assert form == {
        "LXDH": "13800000000",
        "DZYX": "student@example.com",
        "PJJD": "3.5",
        "PM": "12.00",
        "SQLY": "希望参加",
    }

    cancel_call = next(call for call in client.calls if call[0].endswith("/ktbm/qxbm.do"))
    rules = json.loads(cancel_call[1]["querySetting"])
    assert rules[0]["name"] == "WID"
    assert rules[0]["value"] == "topic-1"
