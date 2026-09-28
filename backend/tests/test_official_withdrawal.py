"""Sanitized contract tests; no request in this module contacts the school."""

import json

import pytest
from fastapi import HTTPException, Response

from backend.app.routers import course_selection
from backend.app.schemas.course_selection import OfficialWithdrawalRequest, OfficialWithdrawalResponse
from backend.core.academic.withdrawal import OfficialWithdrawalAPI, OfficialWithdrawalError


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def json(self):
        return json.loads(self.content)


def row(wid, *, source="01", start="2099-09-01 08:00:00", end="2099-09-08 08:00:00"):
    return {
        "WID": wid,
        "KCXH": "SERIAL-1",
        "KCH": "COURSE-1",
        "KCM": "匿名课程",
        "JXBID": "CLASS-1",
        "XKLY": source,
        "XKLY_DISPLAY": "自选" if source == "01" else "非自选",
        "KSSJ": start,
        "DSXSKSSJ": end,
        "XF": 2,
        "XS": 32,
        "QZZ": "5",
    }


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "cxdqxkxnxq" in url:
            return FakeResponse({"code": "0", "datas": {"cxdqxkxnxq": {
                "rows": [{"DM": "2099-2100-1", "MC": "测试学期"}],
            }}})
        if "cxxqkcbl" in url:
            phase = kwargs["params"]["PX"]
            return FakeResponse({"code": "0", "datas": {"cxxqkcbl": {
                "rows": [{"KCBL": 0.5 if phase == "1" else 1, "KCBLMC": "50%" if phase == "1" else "100%"}],
            }}})
        page = int(kwargs["params"]["pageNumber"])
        subset = self.rows[(page - 1) * 100:page * 100]
        return FakeResponse({"code": "0", "datas": {"cxxsktrw": {
            "rows": subset, "totalSize": len(self.rows),
        }}})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"code": "0", "msg": "操作成功"})


def test_official_current_term_list_and_penalty_preview():
    client = FakeClient([row("WID-1"), row("WID-2", source="02")])
    api = OfficialWithdrawalAPI(client)
    assert api.get_current_term() == {"code": "2099-2100-1", "name": "测试学期"}
    courses, rules = api.list_courses("2099-2100-1")
    assert len(courses) == 2
    assert courses[0].can_withdraw is True
    assert courses[0].penalty_phase == "1"
    assert courses[0].penalty_weight == 3
    assert courses[1].can_withdraw is False
    assert courses[1].unavailable_reason == "非自选课程不允许退课"
    assert rules["2"]["label"] == "100%"
    OfficialWithdrawalResponse.model_validate({
        "term_code": "2099-2100-1", "courses": [item.to_dict() for item in courses],
    })


def test_official_list_paginates_before_showing_complete_result():
    client = FakeClient([row(f"WID-{index}") for index in range(101)])
    courses, _ = OfficialWithdrawalAPI(client).list_courses("2099-2100-1")
    assert len(courses) == 101
    pages = [call[2]["params"]["pageNumber"] for call in client.calls if "cxxsktrw" in call[1]]
    assert pages == ["1", "2"]


def test_official_list_disables_expired_and_unparseable_windows():
    client = FakeClient([
        row("WID-1", start="2020-01-01 08:00:00", end="2020-01-08 08:00:00"),
        row("WID-2", start="invalid"),
    ])
    courses, _ = OfficialWithdrawalAPI(client).list_courses("2099-2100-1")
    assert [item.can_withdraw for item in courses] == [False, False]
    assert [item.unavailable_reason for item in courses] == ["已超过允许退课时间", "退课时间暂不可确认"]


def test_official_withdraw_builds_single_wid_query_without_auth_replay():
    client = FakeClient([])
    result = OfficialWithdrawalAPI(client).withdraw("2099-2100-1", "WID-1")
    method, url, options = client.calls[-1]
    assert result["message"] == "操作成功"
    assert method == "POST" and url.endswith("/tkglController/toTk.do")
    assert options["retry_on_auth"] is False
    assert options["retry_on_transport"] is False
    assert options["data"]["XNXQDM"] == "2099-2100-1"
    assert json.loads(options["data"]["querySetting"]) == [{
        "name": "WID", "value": "WID-1", "linkOpt": "and", "builder": "m_value_equal",
    }]


def test_router_rejects_nonself_without_remote_write(monkeypatch):
    client = FakeClient([row("WID-1", source="02")])
    monkeypatch.setattr(course_selection, "OfficialWithdrawalAPI", lambda _auth: OfficialWithdrawalAPI(client))
    request = OfficialWithdrawalRequest(term_code="2099-2100-1", wid="WID-1", confirmed=True)
    with pytest.raises(HTTPException) as caught:
        course_selection.deselect_official_course(request, Response(), auth=object())
    assert caught.value.status_code == 409
    assert not any(method == "POST" for method, _url, _kwargs in client.calls)


def test_router_rejects_stale_term_without_remote_write(monkeypatch):
    client = FakeClient([row("WID-1")])
    monkeypatch.setattr(course_selection, "OfficialWithdrawalAPI", lambda _auth: OfficialWithdrawalAPI(client))
    request = OfficialWithdrawalRequest(term_code="2098-2099-2", wid="WID-1", confirmed=True)
    with pytest.raises(HTTPException) as caught:
        course_selection.deselect_official_course(request, Response(), auth=object())
    assert caught.value.status_code == 409
    assert not any(method == "POST" for method, _url, _kwargs in client.calls)


def test_malformed_official_list_is_error_not_empty_result():
    class MalformedClient(FakeClient):
        def get(self, url, **kwargs):
            if "cxxsktrw" in url:
                return FakeResponse({"code": "0", "datas": {"cxxsktrw": {}}})
            return super().get(url, **kwargs)

    with pytest.raises(OfficialWithdrawalError, match="结构异常"):
        OfficialWithdrawalAPI(MalformedClient([])).list_courses("2099-2100-1")
