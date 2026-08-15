from datetime import datetime

import pytest
from fastapi import Response

from backend.core.course_selection import (
    JwxkBatch,
    JwxkError,
    JwxkSessionClient,
    apply_selection_market_semantics,
    course_categories_equivalent,
    group_course_rows,
    normalize_course_category,
    normalize_course_rows,
    normalize_saved_plan_items,
    parse_course_eligibility,
    parse_course_detail_html,
    parse_account_batches,
    parse_public_batches,
    resolve_network_mode,
)
from backend.core.auth import NEUAuthClient
from backend.core.auth.client import NEULoginError, SERVICE_CONFIGS
from backend.core.course_selection.jwxk import JwxkRateLimitError
import backend.core.course_selection.jwxk as jwxk_module
from backend.core.network import WebVPNUrlCodec
from backend.app.routers import course_selection
from backend.app.schemas.course_selection import JwxkSettingsUpdate


class MemoryStorage:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def load_config(self):
        return dict(self.config)

    def save_config(self, config):
        self.config = dict(config)


def _html(rows: str) -> str:
    return f"<script>loginVue.batchList = {rows}; loginVue.creditRatios = [];</script>"


def test_selected_results_degrade_when_one_round_specific_feed_is_unavailable():
    retry_flags = []

    class Client(JwxkSessionClient):
        def _activate_batch(self, _batch_code):
            return {}

        def _post_form(self, path, data=None, **kwargs):
            retry_flags.append(kwargs.get("retry_on_auth"))
            if path == "/xsxk/volunteer/xgxk/select":
                raise NEULoginError("业务系统会话建立失败")
            if path == "/xsxk/elective/select":
                return {"data": [{"JXBID": "CLASS-1", "KCH": "COURSE-1", "KCM": "课程一"}]}
            return {"data": []}

    result = Client(object()).get_selected(batch_code="BATCH-1")

    assert [item["class_id"] for item in result["selected"]] == ["CLASS-1"]
    assert result["volunteered"] == []
    assert result["withdrawal"] == []
    assert retry_flags == [False, False, False, False]


def test_selected_results_preserve_auth_failure_when_every_feed_is_unavailable():
    class Client(JwxkSessionClient):
        def _activate_batch(self, _batch_code):
            return {}

        def _post_form(self, path, data=None, **kwargs):
            assert kwargs.get("retry_on_auth") is False
            raise NEULoginError("业务系统会话建立失败")

    with pytest.raises(NEULoginError, match="业务系统会话建立失败"):
        Client(object()).get_selected(batch_code="BATCH-1")


def test_catalog_recovery_reactivates_batch_before_retrying_course_page():
    events = []

    class Auth:
        def ensure_service_session(self, *_args, **kwargs):
            assert kwargs.get("force_refresh") is True
            events.append("recover")

    class Client(JwxkSessionClient):
        def _request(self, _method, _path, **kwargs):
            assert kwargs.get("retry_on_auth") is False
            events.append("request")
            if events.count("request") == 1:
                raise NEULoginError("expired")
            return type("Response", (), {
                "json": lambda _self: {"code": 200, "data": {"total": 0, "rows": []}},
            })()

        def _activate_batch(self, batch_code):
            assert batch_code == "BATCH-1"
            events.append("activate")
            return {}

    result = Client(Auth())._search_courses_page(
        batch_code="BATCH-1", teaching_class_type="TJKC",
        page_number=1, page_size=20,
    )

    assert result == {"total": 0, "courses": []}
    assert events == ["request", "recover", "activate", "request"]


def test_catalog_rate_limit_enters_shared_cooldown_without_session_recovery():
    calls = []

    class Response:
        headers = {"Content-Type": "application/json"}

        def json(self):
            return {"code": 403, "msg": "请求过快，请登录后再试"}

        def close(self):
            calls.append("close")

    class Auth:
        def request_service(self, *_args, **_kwargs):
            calls.append("request")
            return Response()

        def ensure_service_session(self, *_args, **_kwargs):
            calls.append("recover")

    client = JwxkSessionClient(Auth())
    with pytest.raises(JwxkRateLimitError, match="限制了请求频率"):
        client._search_courses_page(
            batch_code="BATCH-1", teaching_class_type="ALLKC",
            page_number=1, page_size=20, keyword="课程",
        )
    with pytest.raises(JwxkRateLimitError):
        client._search_courses_page(
            batch_code="BATCH-1", teaching_class_type="TJKC",
            page_number=1, page_size=20, keyword="课程",
        )

    assert calls == ["request", "close"]


def test_jwxk_rate_limit_wording_is_not_classified_as_auth_expiry():
    client = NEUAuthClient(restore_session=False)
    response = type("Response", (), {
        "url": "https://jwxk.neu.edu.cn/xsxk/elective/clazz/list",
        "headers": {"Content-Type": "application/json"},
        "text": "",
        "json": lambda _self: {"code": 403, "msg": "请求过快，请登录后再试"},
    })()

    assert client._is_service_auth_required(
        response, SERVICE_CONFIGS["jwxk"], "direct",
        request_path="/xsxk/elective/clazz/list",
    ) is False


def test_catalog_request_pacing_is_shared_across_clients(monkeypatch):
    clock = [100.0]
    sleeps = []
    monkeypatch.setattr(jwxk_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(jwxk_module.time, "sleep", lambda delay: (
        sleeps.append(delay), clock.__setitem__(0, clock[0] + delay)
    ))
    monkeypatch.setattr(jwxk_module.random, "uniform", lambda *_args: 0.0)

    auth = type("Auth", (), {})()
    first = JwxkSessionClient(auth)
    second = JwxkSessionClient(auth)
    first._pace_catalog_request()
    second._pace_catalog_request()

    assert sleeps == [pytest.approx(first._CATALOG_MIN_INTERVAL_SECONDS)]


def test_service_auth_reason_keeps_official_semantics_but_redacts_identity():
    response = type("Response", (), {
        "headers": {"Content-Type": "application/json"},
        "status_code": 200,
        "json": lambda _self: {
            "code": 403,
            "msg": "用户 20241643 登录状态失效 token=abcdefghijklmnopqrstuvwxyz123456",
        },
    })()

    reason = NEUAuthClient._service_auth_response_kind(response)

    assert "code=403" in reason
    assert "登录状态失效" in reason
    assert "20241643" not in reason
    assert "abcdefghijklmnopqrstuvwxyz123456" not in reason


def test_public_batch_parser_distinguishes_weight_and_grab_windows():
    batches = parse_public_batches(_html(r'''[
      {"code":"weight","name":"选修课调整","schoolTerm":"2026-2027-1",
       "schoolTermName":"2026-2027学年秋季学期","beginTime":"2026-08-18 13:00:00",
       "endTime":"2026-08-19 13:00:00","active":"1","typeCode":"04",
       "typeName":"权重","tacticName":"可选可退","clazzTypeList":["TJKC","ALLKC"],
       "needConfirm":"1","confirmInfo":"权重值选课"},
      {"code":"grab","name":"辅修选课","schoolTerm":"2026-2027-1",
       "schoolTermName":"2026-2027学年秋季学期","beginTime":"2026-08-18 13:00:00",
       "endTime":"2026-08-19 13:00:00","active":"1","typeCode":"02",
       "typeName":"抢选","tacticName":"可选可退","clazzTypeList":["FXKC"],
       "needConfirm":"0","confirmInfo":""}
    ]'''), now=datetime(2026, 8, 13, 12, 0, 0))

    assert [item.selection_type for item in batches] == ["权重", "抢选"]
    assert all(item.state == "not_started" and not item.can_enter for item in batches)
    active = parse_public_batches(_html(r'''[
      {"code":"weight","name":"选修课调整","beginTime":"2026-08-18 13:00:00",
       "endTime":"2026-08-19 13:00:00","active":"1","typeName":"权重"}
    ]'''), now=datetime(2026, 8, 18, 14, 0, 0))
    assert active[0].state == "active"
    assert active[0].can_enter is True


def test_public_batch_parser_fails_closed_on_changed_page_contract():
    with pytest.raises(JwxkError):
        parse_public_batches("<html>no batch assignment</html>")


def test_course_detail_parser_only_keeps_student_facing_fields():
    detail = parse_course_detail_html("""
      <table><tr><th>课程名称</th><td>工程测试</td><th>考试类型</th><td>考试</td></tr>
      <tr><th>成绩记载方式</th><td>百分制</td><th>WID</th><td>internal-id</td></tr></table>
      <dl><dt>课程简介</dt><dd>介绍内容</dd></dl>
    """)
    assert detail == {
        "course_name": "工程测试", "exam_type": "考试",
        "score_scale": "百分制", "description": "介绍内容",
    }


def test_course_detail_parser_keeps_general_elective_category_separate():
    detail = parse_course_detail_html("""
      <table><tr><th>课程类别</th><td>通识选修课</td>
      <th>通识选修课类别</th><td>科学素养类</td></tr></table>
    """)

    assert detail == {
        "course_category": "通识选修课",
        "general_elective_category": "科学素养类",
    }


def test_course_category_aliases_share_one_product_taxonomy():
    assert normalize_course_category("通识选修类") == "通识选修"
    assert normalize_course_category("通识选修课") == "通识选修"
    assert course_categories_equivalent("专业方向类", "专业方向课")
    assert course_categories_equivalent("专业基础类", "专业基础课程")
    assert not course_categories_equivalent("专业方向类", "学科基础类")


def test_course_and_teaching_class_details_are_normalized_for_users():
    [course] = normalize_course_rows([{
        "KCH": "COURSE-1", "KCM": "课程", "KCLB": "通识选修课",
        "XGXKLB": "科学素养类", "XGXKLBDM": "01",
        "campusCode": "01", "campusName": "浑南校区",
        "CJJLFS": "100", "CJJLFSMC": "百分制", "KSLX": "01",
        "tcList": [{
            "JXBID": "CLASS-1", "SKJS": "教师甲", "SKJSLB": "教师甲(教授)|T1|",
            "XSXLX": "理论", "TJBJ": "班级一,班级二", "SKSJ": [],
        }],
    }])
    assert course["exam_type"] == "考试"
    assert course["exam_type_code"] == "01"
    assert course["score_scale"] == "百分制"
    assert course["normalized_course_category"] == "通识选修"
    assert course["general_elective_category"] == "科学素养类"
    assert course["general_elective_category_code"] == "01"
    assert course["campus"] == "01"
    assert course["campus_name"] == "浑南校区"
    assert course["teacher_details"] == [{"name": "教师甲", "teacher_id": "T1", "title": "教授"}]
    assert course["target_classes"] == ["班级一", "班级二"]


def test_round_market_semantics_distinguish_selected_and_weight_participants():
    grab = apply_selection_market_semantics([{
        "selected_count": 40, "weight_participant_count": 63,
        "capacity": 50, "full": False,
    }], "02")[0]
    assert grab["market_participant_count"] == 40
    assert grab["market_participant_label"] == "已选人数"

    weight = apply_selection_market_semantics([{
        "selected_count": 50, "weight_participant_count": 63,
        "capacity": 50, "full": True,
    }], "04")[0]
    assert weight["market_participant_count"] == 63
    assert weight["market_participant_label"] == "已投注人数"
    assert weight["full"] is False


def test_course_campus_keeps_request_code_and_user_facing_name_separate():
    [course] = normalize_course_rows([{
        "KCH": "COURSE-1", "KCM": "课程", "campus": "01", "XQ": "浑南校区",
        "tcList": [{"JXBID": "CLASS-1", "SKSJ": [{"XXXQDM": "01"}]}],
    }])

    assert course["campus"] == "01"
    assert course["campus_name"] == "浑南校区"
    assert course["schedules"][0]["campus"] == "01"
    assert course["schedules"][0]["campus_name"] == "浑南校区"


def test_group_course_rows_merges_non_empty_metadata_and_category_aliases():
    groups = group_course_rows([{
        "course_code": "C1", "course_name": "课程", "class_id": "A",
        "course_category": "通识选修类", "course_categories": ["通识选修类"],
        "normalized_course_category": "通识选修", "general_elective_category": "科学素养类",
        "general_elective_category_code": "01", "exam_type": "", "score_scale": "",
    }, {
        "course_code": "C1", "course_name": "课程", "class_id": "B",
        "course_category": "通识选修课", "course_categories": ["通识选修课"],
        "normalized_course_category": "通识选修", "exam_type": "考试", "score_scale": "百分制",
    }])
    assert groups[0]["exam_type"] == "考试"
    assert groups[0]["score_scale"] == "百分制"
    assert groups[0]["course_categories"] == ["通识选修类", "通识选修课"]
    assert groups[0]["normalized_course_category"] == "通识选修"
    assert groups[0]["general_elective_category"] == "科学素养类"
    assert groups[0]["general_elective_category_code"] == "01"


def test_group_course_rows_uses_course_code_as_stable_identity():
    groups = group_course_rows([
        {"course_code": "C1", "course_name": "课程", "credits": "2", "department": "单位甲", "class_id": "A"},
        {"course_code": "C1", "course_name": "课程", "credits": "", "department": "单位乙", "class_id": "B"},
    ])
    assert len(groups) == 1
    assert {item["class_id"] for item in groups[0]["classes"]} == {"A", "B"}


@pytest.mark.parametrize(
    "preference,primary,expected",
    [
        ("follow", "direct", "direct"),
        ("follow", "webvpn", "webvpn"),
        ("direct", "webvpn", "direct"),
        ("webvpn", "direct", "webvpn"),
    ],
)
def test_jwxk_network_mode_can_follow_or_override_primary(preference, primary, expected):
    assert resolve_network_mode(preference, primary) == expected


def test_jwxk_network_mode_rejects_unknown_values():
    with pytest.raises(ValueError):
        resolve_network_mode("automatic", "direct")


def test_status_route_is_no_store_and_preserves_public_batch_contract(monkeypatch):
    storage = MemoryStorage()
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: None)
    monkeypatch.setattr(
        course_selection.JwxkPublicClient,
        "get_batches",
        lambda _self: parse_public_batches(_html(r'''[
          {"code":"weight","name":"选修课调整","beginTime":"2026-08-18 13:00:00",
           "endTime":"2026-08-19 13:00:00","active":"1","typeName":"权重"}
        ]'''), now=datetime(2026, 8, 13, 12, 0, 0)),
    )
    response = Response()
    result = course_selection.get_jwxk_status(response, storage)

    assert response.headers["cache-control"] == "no-store"
    assert result.available is True
    assert result.batches[0].state == "not_started"
    assert result.authenticated is False
    assert result.primary_authenticated is False
    assert result.service_authenticated is False


def test_status_route_reuses_primary_client_under_remote_guard(monkeypatch):
    storage = MemoryStorage()
    events = []

    class Primary:
        active_mode = "direct"
        is_logged_in = True

    primary = Primary()
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: primary)

    class Guard:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *_args):
            events.append("exit")

    monkeypatch.setattr(course_selection, "remote_session_guard", lambda: Guard())
    class SessionClient:
        def __init__(self, auth, *, network_mode):
            assert auth is primary
            events.append(("client", network_mode))

        def get_context(self):
            events.append("context")
            return {"batches": []}

    monkeypatch.setattr(course_selection, "JwxkSessionClient", SessionClient)
    monkeypatch.setattr(course_selection.JwxkPublicClient, "get_batches", lambda _self: [])

    result = course_selection.get_jwxk_status(Response(), storage)

    assert events == ["enter", ("client", "direct"), "context", "exit"]
    assert result.primary_authenticated is True
    assert result.service_authenticated is True
    assert result.authenticated is True


def test_jwxk_service_uses_shared_session_and_does_not_change_primary_mode(monkeypatch):
    client = NEUAuthClient(network_mode="webvpn", restore_session=False)
    client._logged_in = True
    shared_session = client.session
    original_mode = client.active_mode
    calls = []

    def response(url):
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b"ok"
        return item

    def fake_request(method, url, **kwargs):
        calls.append((client.session, method, url, kwargs.get("allow_redirects")))
        return response(url)

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client.request_service(
        "jwxk", "GET", "/xsxk/profile/index.html", network_mode_override="direct"
    )

    assert result.url == "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
    assert calls == [(shared_session, "GET", result.url, False)]
    assert client.active_mode == original_mode == "webvpn"


def test_jwxk_service_maps_business_token_cookie_to_authorization_header(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    client.session.cookies.set("token", "stale-profile-token", domain="jwxk.neu.edu.cn", path="/xsxk/profile")
    client.session.cookies.set("token", "opaque-token", domain="jwxk.neu.edu.cn", path="/xsxk")
    client.session.cookies.set("token", "webvpn-token", domain="webvpn.neu.edu.cn", path="/")
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs.get("headers") or {})
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b'{"code":"200"}'
        item.headers["Content-Type"] = "application/json"
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    client.request_service("jwxk", "POST", "/xsxk/web/now", data={})

    assert captured["Authorization"] == "opaque-token"


def test_jwxk_session_rebuild_does_not_accept_a_stale_token(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    client.session.cookies.set("token", "stale-token", domain="jwxk.neu.edu.cn", path="/xsxk")
    calls = []

    def fake_redirects(_method, _url, **_kwargs):
        calls.append(True)
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
        item._content = b"profile"
        return item

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    monkeypatch.setattr(client, "ensure_login", lambda: True)

    with pytest.raises(NEULoginError, match="无法建立业务系统会话"):
        client.ensure_service_session(
            "jwxk", network_mode_override="direct", force_refresh=True,
        )

    assert len(calls) == 2
    assert client.get_service_token(
        "jwxk", network_mode="direct", request_path="/xsxk/web/now",
    ) is None
    assert client.is_logged_in is True


def test_jwxk_direct_session_can_recover_when_primary_jwxt_route_is_unavailable(monkeypatch):
    client = NEUAuthClient(
        username="student", password="secret", restore_session=False,
    )
    client._logged_in = False
    login_targets = []
    captured = {}

    monkeypatch.setattr(client, "ensure_login", lambda: False)

    def fake_service_login(target):
        login_targets.append(target)
        client._logged_in = True
        client.session.cookies.set(
            "token", "fresh-service-token",
            domain="jwxk.neu.edu.cn", path="/xsxk",
        )
        return True

    def fake_redirects(_method, url, **kwargs):
        captured.update(kwargs.get("headers") or {})
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b'{"code":200,"data":[]}'
        item.headers["Content-Type"] = "application/json"
        return item

    monkeypatch.setattr(client, "_do_login", fake_service_login)
    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)

    response = client.request_service(
        "jwxk", "POST", "/xsxk/volunteer/select", data={},
    )

    assert response.json()["code"] == 200
    assert login_targets == ["https://jwxk.neu.edu.cn/xsxk/auth/cas"]
    assert captured["Authorization"] == "fresh-service-token"


def test_jwxk_json_api_html_response_recovers_service_session_once(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    calls = []
    recovered = []

    def fake_redirects(method, url, **_kwargs):
        calls.append((method, url))
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        if len(calls) == 1:
            item._content = b"<!doctype html><html><body>login</body></html>"
            item.headers["Content-Type"] = "text/html; charset=utf-8"
        else:
            item._content = b'{"code":200,"data":[]}'
            item.headers["Content-Type"] = "application/json"
        return item

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    monkeypatch.setattr(
        client, "ensure_service_session",
        lambda *_args, **_kwargs: recovered.append(True) or True,
    )

    response = client.request_service(
        "jwxk", "POST", "/xsxk/volunteer/select", data={},
    )

    assert response.json()["code"] == 200
    assert len(calls) == 2
    assert recovered == [True]


def test_jwxk_webvpn_override_routes_only_service_request(monkeypatch):
    client = NEUAuthClient(network_mode="direct", restore_session=False)
    client._logged_in = True
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b"ok"
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client.request_service(
        "jwxk", "GET", "/xsxk/profile/index.html", network_mode_override="webvpn"
    )

    assert result.url == WebVPNUrlCodec.convert_url(
        "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
    )
    assert calls == [result.url]
    assert client.active_mode == "direct"


def test_jwxk_webvpn_cas_callback_also_targets_webvpn(monkeypatch):
    client = NEUAuthClient(network_mode="direct", restore_session=False)
    client._logged_in = True
    calls = []
    expected_callback = WebVPNUrlCodec.convert_url(
        "https://jwxk.neu.edu.cn/xsxk/auth/cas"
    )

    def fake_redirects(method, url, **kwargs):
        calls.append(url)
        client.session.cookies.set("token", "opaque", domain="webvpn.neu.edu.cn", path="/")
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = WebVPNUrlCodec.convert_url(
            "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
        )
        item._content = b"ok"
        return item

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    assert client.ensure_service_session("jwxk", network_mode_override="webvpn") is True
    assert len(calls) == 1
    assert "webvpn.neu.edu.cn" in calls[0]
    assert calls[0] == expected_callback
    assert client.active_mode == "direct"


def test_jwxk_cas_http_pass_redirect_is_upgraded_before_request(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    calls = []

    def response(url, status=200, location=""):
        item = __import__("requests").Response()
        item.status_code = status
        item.url = url
        item._content = b"ok"
        if location:
            item.headers["Location"] = location
        return item

    results = iter([
        response(
            "https://jwxk.neu.edu.cn/xsxk/auth/cas", 302,
            "http://pass.neu.edu.cn/tpass/login?service=jwxk",
        ),
        response(
            "https://pass.neu.edu.cn/tpass/login?service=jwxk", 302,
            "https://jwxk.neu.edu.cn/xsxk/auth/cas?ticket=opaque",
        ),
        response(
            "https://jwxk.neu.edu.cn/xsxk/auth/cas?ticket=opaque", 302,
            "/xsxk/profile/index.html",
        ),
        response("https://jwxk.neu.edu.cn/xsxk/profile/index.html"),
    ])

    def fake_request(_method, url, **_kwargs):
        calls.append(url)
        return next(results)

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client._request_service_redirects(
        "GET", "https://jwxk.neu.edu.cn/xsxk/auth/cas",
        service_config=SERVICE_CONFIGS["jwxk"], network_mode="direct",
    )

    assert result.url == "https://jwxk.neu.edu.cn/xsxk/profile/index.html"
    assert calls[1].startswith("https://pass.neu.edu.cn/tpass/login?")
    assert all(not url.startswith("http://") for url in calls)


def test_jwxk_business_401_does_not_invalidate_a_working_session(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    client.session.cookies.set(
        "token", "working-token", domain="jwxk.neu.edu.cn", path="/xsxk",
    )
    recoveries = []

    def fake_request(_method, url, **_kwargs):
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b'{"code":401,"msg":"round feed is unavailable","data":null}'
        item.headers["Content-Type"] = "application/json"
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    monkeypatch.setattr(
        client, "ensure_service_session",
        lambda *_args, **_kwargs: recoveries.append(True) or True,
    )

    response = client.request_service(
        "jwxk", "POST", "/xsxk/volunteer/xgxk/select", data={},
    )

    assert response.json()["code"] == 401
    assert recoveries == []
    assert client.is_logged_in is True


def test_jwxk_token_remains_usable_when_primary_login_flag_is_false(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = False
    client.session.cookies.set(
        "token", "working-token", domain="jwxk.neu.edu.cn", path="/xsxk",
    )
    captured = {}

    def fake_request(_method, url, **kwargs):
        captured.update(kwargs.get("headers") or {})
        item = __import__("requests").Response()
        item.status_code = 200
        item.url = url
        item._content = b'{"code":200,"data":[]}'
        item.headers["Content-Type"] = "application/json"
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    monkeypatch.setattr(
        client, "ensure_login",
        lambda: (_ for _ in ()).throw(AssertionError("primary login must not be probed")),
    )

    response = client.request_service(
        "jwxk", "POST", "/xsxk/volunteer/select", data={},
    )

    assert response.json()["code"] == 200
    assert captured["Authorization"] == "working-token"


def test_account_batches_use_official_time_and_keep_account_eligibility():
    batches = parse_account_batches([{
        "code": "grab",
        "name": "必修课初选",
        "beginTime": "2026-08-14 13:00:00",
        "endTime": "2026-08-15 13:00:00",
        "canSelect": "1",
        "isConfirmed": "1",
        "notRetakeMultiCampus": "1",
        "typeCode": "02",
        "typeName": "抢选",
        "menuList": [{"teachingClassType": "FANKC", "displayName": "培养方案内课"}],
    }], official_now=1786685400000)

    assert batches[0].state == "active"
    assert batches[0].can_enter is True
    assert batches[0].account_selectable is True
    assert batches[0].confirmed is True
    assert batches[0].allow_cross_campus is True
    assert batches[0].menus == ({"code": "FANKC", "name": "培养方案内课"},)


def test_course_selection_refetches_secret_and_performs_explicit_301_confirmation():
    responses = [
        {"code": 200, "msg": "", "data": ["是否可选：可选", "学生：已脱敏"]},
        {"code": 301, "msg": "存在冲突"},
        {"code": 200, "msg": "已进入选课队列"},
    ]
    calls = []

    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="抢选", term_code="2026-2027-1", term_name="秋季",
                begin_time="2026-08-14 13:00:00", end_time="2026-08-15 13:00:00",
                selection_type="抢选", selection_type_code="02", tactic_name="可选可退",
                course_types=("ALLKC",), need_confirm=False, notice="", state="active",
                can_enter=True, account_selectable=True, confirmed=True,
            )]}

        def _search_raw(self, **kwargs):
            assert kwargs["teaching_class_type"] == "ALLKC"
            assert kwargs["keyword"] == "COURSE-1"
            return [{
                "JXBID": "CLASS-1", "KCH": "COURSE-1", "secretVal": "server-secret",
                "teachingClassType": "FANKC",
            }]

        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": []}

        def _request(self, method, path, **kwargs):
            calls.append((path, dict(kwargs["data"])))
            item = __import__("requests").Response()
            item.status_code = 200
            item.url = "https://jwxk.neu.edu.cn" + path
            item.headers["Content-Type"] = "application/json"
            item._content = __import__("json").dumps(responses.pop(0)).encode()
            return item

    result = Client(Auth()).select_course(
        batch_code="batch", teaching_class_type="ALLKC", class_id="CLASS-1",
        course_code="COURSE-1", weight=None, confirm_risk=True,
    )

    assert result["success"] is True
    assert calls[0][0] == "/xsxk/elective/check"
    assert calls[0][1]["secretVal"] == "server-secret"
    assert calls[1][0] == "/xsxk/elective/clazz/add"
    assert calls[1][1]["clazzType"] == "FANKC"
    assert calls[1][1]["secretVal"] == "server-secret"
    assert "isConfirm" not in calls[1][1]
    assert calls[2][1]["isConfirm"] == "1"


def test_course_selection_stops_before_mutation_when_official_check_rejects_class():
    calls = []

    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="抢选", term_code="2026-2027-1", term_name="秋季",
                begin_time="2026-08-14 13:00:00", end_time="2026-08-15 13:00:00",
                selection_type="抢选", selection_type_code="02", tactic_name="可选可退",
                course_types=("ALLKC",), need_confirm=False, notice="", state="active",
                can_enter=True, account_selectable=True, confirmed=True,
            )]}

        def _find_raw_class(self, **_kwargs):
            return {
                "JXBID": "CLASS-1", "KCH": "COURSE-1", "secretVal": "server-secret",
                "teachingClassType": "FANKC",
            }

        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": []}

        def _request(self, method, path, **kwargs):
            calls.append(path)
            item = __import__("requests").Response()
            item.status_code = 200
            item.url = "https://jwxk.neu.edu.cn" + path
            item.headers["Content-Type"] = "application/json"
            item._content = __import__("json").dumps({
                "code": 200,
                "data": ["是否可选：不可选", "轮次中未找到该教学班"],
            }).encode()
            return item

    with pytest.raises(JwxkError, match="轮次中未找到"):
        Client(Auth()).select_course(
            batch_code="batch", teaching_class_type="ALLKC", class_id="CLASS-1",
            course_code="COURSE-1", weight=None, confirm_risk=True,
        )

    assert calls == ["/xsxk/elective/check"]


def test_course_selection_rejects_another_class_with_the_same_selected_course_code():
    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="抢选", term_code="2026-2027-1", term_name="秋季",
                begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
                tactic_name="", course_types=("FANKC",), need_confirm=False, notice="",
                state="active", can_enter=True, account_selectable=True, confirmed=True,
            )]}

        def get_selected(self, **_kwargs):
            return {"selected": [{
                "class_id": "CLASS-OLD", "course_code": "COURSE-1", "course_name": "已选课程",
            }], "volunteered": []}

        def _find_raw_class(self, **_kwargs):
            raise AssertionError("duplicate course must be rejected before class lookup")

    with pytest.raises(JwxkError, match="同课程代码"):
        Client(Auth()).select_course(
            batch_code="batch", teaching_class_type="FANKC", class_id="CLASS-NEW",
            course_code="COURSE-1", weight=None, confirm_risk=True,
        )


def test_course_selection_treats_official_already_selected_race_as_achieved():
    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="抢选", term_code="2026-2027-1", term_name="秋季",
                begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
                tactic_name="", course_types=("FANKC",), need_confirm=False, notice="",
                state="active", can_enter=True, account_selectable=True, confirmed=True,
            )]}

        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": []}

        def _find_raw_class(self, **_kwargs):
            return {"JXBID": "CLASS-1", "KCH": "COURSE-1", "secretVal": "secret"}

        def _check_one_course_eligibility(self, **_kwargs):
            return {"status": "selectable", "reason": ""}

        def _post_mutation(self, *_args, **_kwargs):
            return {
                "success": False, "queued": False, "requires_confirmation": False,
                "code": "500", "message": "该课程已在选课结果中",
            }

    result = Client(Auth()).select_course(
        batch_code="batch", teaching_class_type="FANKC", class_id="CLASS-1",
        course_code="COURSE-1", weight=None, confirm_risk=False,
    )

    assert result["success"] is True
    assert result["queued"] is False
    assert result["code"] == "already_selected"


def test_weight_budget_reads_current_term_weight_from_official_context():
    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def _post_form(self, path, data=None):
            if path == "/xsxk/elective/user":
                return {
                    "code": 200,
                    "data": {"student": {
                        "electiveBatchList": [{
                            "code": "weight-batch", "termCode": "2026-2027-1",
                        }],
                        "termWeightList": [
                            {"termCode": "2025-2026-2", "weight": "80"},
                            {"termCode": "2026-2027-1", "weight": "105"},
                        ],
                    }},
                }
            raise AssertionError(path)

        def get_selected(self, **_kwargs):
            return {"selected": [], "volunteered": [], "withdrawal": []}

    result = Client(Auth()).get_weight_budget(batch_code="weight-batch")

    assert result == {
        "remaining": 105,
        "total": 105,
        "used": 0,
        "minimum": 5,
        "step": 1,
        "source": "official_round_context",
    }


@pytest.mark.parametrize(
    "result_path,expected_source",
    [
        ("/xsxk/elective/select", "yxkcyx"),
        ("/xsxk/volunteer/select", "fakcyx"),
        ("/xsxk/volunteer/xgxk/select", "xgxkyx"),
    ],
)
def test_deselect_uses_the_official_source_for_each_selection_result(
    result_path, expected_source,
):
    calls = []

    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="权重轮次", term_code="2026-2027-1", term_name="秋季",
                begin_time="", end_time="", selection_type="权重", selection_type_code="04",
                tactic_name="可选可退", course_types=("FANKC",), need_confirm=False,
                notice="", state="active", can_enter=True, account_selectable=True,
                confirmed=True,
            )]}

        def _post_form(self, path, data=None):
            if path == result_path:
                return {"data": [{
                    "JXBID": "CLASS-1", "KCH": "COURSE-1", "KCM": "课程",
                    "teachingClassType": "FANKC", "secretVal": "server-secret",
                }]}
            return {"data": []}

        def _post_mutation(self, path, data, *, confirm_risk):
            calls.append((path, data, confirm_risk))
            return {"success": True, "message": "退选成功"}

    result = Client(Auth()).deselect_course(
        batch_code="batch", class_id="CLASS-1", confirm_risk=True,
    )

    assert result["success"] is True
    assert calls == [(
        "/xsxk/elective/neu/clazz/del",
        {
            "clazzType": "FANKC", "clazzId": "CLASS-1",
            "secretVal": "server-secret", "source": expected_source,
        },
        True,
    )]


def test_deselect_prefers_the_known_selection_source():
    calls = []

    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="权重轮次", term_code="2026-2027-1", term_name="秋季",
                begin_time="", end_time="", selection_type="权重", selection_type_code="04",
                tactic_name="可选可退", course_types=("FANKC",), need_confirm=False,
                notice="", state="active", can_enter=True, account_selectable=True,
                confirmed=True,
            )]}

        def _post_form(self, path, data=None):
            calls.append(path)
            if path == "/xsxk/volunteer/select":
                return {"data": [{
                    "JXBID": "CLASS-1", "KCH": "COURSE-1", "teachingClassType": "FANKC",
                    "secretVal": "server-secret",
                }]}
            return {"data": []}

        def _post_mutation(self, path, data, *, confirm_risk):
            return {"success": True, "message": "退选成功"}

    result = Client(Auth()).deselect_course(
        batch_code="batch", class_id="CLASS-1", selection_source="fakcyx", confirm_risk=True,
    )

    assert result["success"] is True
    assert calls[:2] == ["/xsxk/elective/user", "/xsxk/volunteer/select"]


def test_deselect_does_not_submit_when_class_is_absent_from_official_results():
    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="轮次", term_code="2026-2027-1", term_name="秋季",
                begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
                tactic_name="可选可退", course_types=("FANKC",), need_confirm=False,
                notice="", state="active", can_enter=True, account_selectable=True,
                confirmed=True,
            )]}

        def _post_form(self, path, data=None):
            return {"data": []}

        def _post_mutation(self, *_args, **_kwargs):
            raise AssertionError("missing official selection must not trigger mutation")

    with pytest.raises(JwxkError, match="已变化"):
        Client(Auth()).deselect_course(
            batch_code="batch", class_id="CLASS-1", confirm_risk=True,
        )


def test_deselect_does_not_guess_source_when_a_required_result_feed_fails():
    class Auth:
        timeout = 10

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [JwxkBatch(
                code="batch", name="轮次", term_code="2026-2027-1", term_name="秋季",
                begin_time="", end_time="", selection_type="权重", selection_type_code="04",
                tactic_name="可选可退", course_types=("FANKC",), need_confirm=False,
                notice="", state="active", can_enter=True, account_selectable=True,
                confirmed=True,
            )]}

        def _post_form(self, path, data=None):
            if path == "/xsxk/elective/user":
                return {"data": {"student": {}}}
            if path == "/xsxk/elective/select":
                raise JwxkError("读取普通已选结果失败")
            return {"data": [{
                "JXBID": "CLASS-1", "teachingClassType": "FANKC",
                "secretVal": "must-not-be-used",
            }]}

        def _post_mutation(self, *_args, **_kwargs):
            raise AssertionError("ambiguous result source must not trigger mutation")

    with pytest.raises(JwxkError, match="读取普通已选结果失败"):
        Client(Auth()).deselect_course(
            batch_code="batch", class_id="CLASS-1", confirm_risk=True,
        )


def test_jwxk_service_rejects_paths_and_untrusted_redirects(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    with pytest.raises(ValueError, match="not allowed"):
        client.request_service("jwxk", "GET", "/jwapp/private")

    def fake_request(method, url, **kwargs):
        item = __import__("requests").Response()
        item.status_code = 302
        item.url = url
        item.headers["Location"] = "https://evil.example/steal"
        item._content = b""
        return item

    monkeypatch.setattr(client.session, "request", fake_request)
    with pytest.raises(NEULoginError, match="不受信任"):
        client.request_service("jwxk", "GET", "/xsxk/profile/index.html")


def test_jwxk_mutation_request_does_not_replay_after_auth_response(monkeypatch):
    client = NEUAuthClient(restore_session=False)
    client._logged_in = True
    calls = []

    def fake_redirects(method, url, **_kwargs):
        calls.append((method, url))
        item = __import__("requests").Response()
        item.status_code = 302
        item.url = "https://pass.neu.edu.cn/tpass/login"
        item._content = b""
        return item

    monkeypatch.setattr(client, "_request_service_redirects", fake_redirects)
    monkeypatch.setattr(
        client, "ensure_service_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("mutation must not recover and replay")),
    )

    with pytest.raises(NEULoginError):
        client.request_service(
            "jwxk", "POST", "/xsxk/elective/clazz/add",
            retry_on_auth=False, data={"clazzId": "CLASS-1"},
        )

    assert len(calls) == 1


def test_network_setting_update_keeps_unrelated_config(monkeypatch):
    storage = MemoryStorage({"other": {"enabled": True}})
    monkeypatch.setattr(course_selection, "peek_auth_client", lambda: None)
    monkeypatch.setattr(course_selection.JwxkPublicClient, "get_batches", lambda _self: [])

    result = course_selection.update_jwxk_settings(
        JwxkSettingsUpdate(network_mode="webvpn"),
        Response(),
        storage,
    )

    assert storage.config["other"] == {"enabled": True}
    assert storage.config["course_selection"] == {"network_mode": "webvpn"}
    assert result.effective_network_mode == "webvpn"


def test_course_catalog_groups_teaching_classes_and_sorts_selectable_first():
    groups = group_course_rows([
        {"course_code": "IE001", "course_name": "人因工程学", "credits": "2", "department": "工业工程系", "class_id": "full", "teacher": "乙", "capacity": 30, "selected_count": 30, "full": True, "restricted": False, "conflict": False, "eligibility_status": "unavailable"},
        {"course_code": "IE001", "course_name": "人因工程学", "credits": "2", "department": "工业工程系", "class_id": "open", "teacher": "甲", "capacity": 30, "selected_count": 20, "full": False, "restricted": False, "conflict": False, "eligibility_status": "selectable"},
    ], source_tags={"IE001": {"培养方案内课", "全校课程查询"}})

    assert len(groups) == 1
    assert groups[0]["class_count"] == 2
    assert groups[0]["selectable_count"] == 1
    assert groups[0]["eligibility_pending_count"] == 0
    assert groups[0]["classes"][0]["class_id"] == "open"
    assert groups[0]["source_tags"] == ["全校课程查询", "培养方案内课"]


def test_official_eligibility_parser_removes_student_identity_and_keeps_reason():
    result = parse_course_eligibility({
        "code": 200,
        "msg": "",
        "data": [
            "是否可选：不可选",
            "学生：20240000",
            "教学班：A114754",
            "选课轮次：测试轮次",
            "轮次中未找到该教学班",
        ],
    }, class_id="A114754")

    assert result == {
        "class_id": "A114754",
        "status": "unavailable",
        "reason": "轮次中未找到该教学班",
    }
    assert "20240000" not in str(result)


def test_jwxk_schedule_normalization_prefers_week_mask_and_matches_each_location():
    courses = normalize_course_rows([{
        "KCH": "IE001",
        "KCM": "人因工程学",
        "JXBID": "CLASS-1",
        "YPSJDD": (
            "10-12周,14-15周[理论]/星期二/第一节-第二节/张老师/生命B101，"
            "12-14周(双)[理论]/星期三/第一节-第二节/张老师/生命B202"
        ),
        "SKSJ": [{
            "XNXQ": "2026-2027-1", "SKZCMC": "10-12周,14-15周",
            "SKZC": "000000000111011", "SKXQ": 2, "KSJC": 1, "JSJC": 2,
            "SKJS": "张老师",
        }, {
            "XNXQ": "2026-2027-1", "SKZCMC": "12-14周(双)",
            "SKZC": "00000000000101", "SKXQ": 3, "KSJC": 1, "JSJC": 2,
            "SKJS": "张老师",
        }],
    }])

    first, second = courses[0]["schedules"]
    assert first["weeks"] == [10, 11, 12, 14, 15]
    assert first["location"] == "生命B101"
    assert second["weeks"] == [12, 14]
    assert second["location"] == "生命B202"
    assert first["meeting_id"] != second["meeting_id"]
    assert first["recurrence_unknown"] is False
    assert courses[0]["location"] == "生命B101；生命B202"


def test_jwxk_schedule_normalization_marks_mask_text_mismatch():
    course = normalize_course_rows([{
        "KCH": "IE002", "KCM": "课程设计", "JXBID": "CLASS-2",
        "SKSJ": [{
            "SKZCMC": "1-4周", "SKZC": "0101", "SKXQ": 1,
            "KSJC": 3, "JSJC": 4,
        }],
    }])[0]

    assert course["schedules"][0]["weeks"] == [2, 4]
    assert course["schedules"][0]["parse_status"] == "mismatch"


def test_saved_plan_migration_restores_structured_meetings_and_drops_legacy_location():
    migrated = normalize_saved_plan_items([{
        "course_code": "IE003",
        "course_name": "课程设计",
        "class_id": "CLASS-OLD",
        "location": "10-12周/星期二/第三节-第四节/张老师/生命B101",
        "schedules": [{
            "week_mask": "00000000000101",
            "week_text": "1-4周",
            "weekday": 2,
            "start_section": 3,
            "end_section": 4,
            "teacher": "张老师",
            "location": "10-12周/星期二/第三节-第四节/张老师/生命B101",
        }],
    }])

    assert len(migrated) == 1
    item = migrated[0]
    assert item["official_schedule"]
    assert item["location"] == "生命B101"
    assert item["plan_group_id"] == "IE003"
    assert item["plan_group_name"] == "课程设计"
    assert item["plan_group_target_count"] == 1
    meeting = item["schedules"][0]
    assert meeting["weeks"] == [12, 14]
    assert meeting["parse_status"] == "mismatch"
    assert meeting["location"] == "生命B101"
    assert meeting["meeting_id"].startswith("jwxk-mtg-")


def test_catalog_time_slot_matches_classes_covering_the_selected_section():
    batch = JwxkBatch(
        code="batch", name="选课", term_code="2026-2027-1", term_name="秋季",
        begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
        tactic_name="", course_types=("ALLKC",), need_confirm=False, notice="",
        state="active", can_enter=True, menus=({"code": "ALLKC", "name": "全校课程"},),
    )

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [batch]}

        def _activate_batch(self, batch_code):
            assert batch_code == "batch"
            return {}

        def _search_courses_page(self, **kwargs):
            assert kwargs["filters"]["SKXQ"] == "1"
            return {"total": 3, "courses": [
                {"course_code": "A", "course_name": "1-2节", "class_id": "A1", "schedules": [{"weekday": 1, "start_section": 1, "end_section": 2}]},
                {"course_code": "B", "course_name": "1-4节", "class_id": "B1", "schedules": [{"weekday": 1, "start_section": 1, "end_section": 4}]},
                {"course_code": "C", "course_name": "3-4节", "class_id": "C1", "schedules": [{"weekday": 1, "start_section": 3, "end_section": 4}]},
            ]}

    result = Client(type("Auth", (), {})()).search_catalog(
        batch_code="batch", page_number=1, page_size=20,
        scope="ALLKC",
        time_slot={"weekday": 1, "section": 2},
    )

    assert result["total"] == 2
    assert [group["course_code"] for group in result["groups"]] == ["A", "B"]


def test_round_catalog_uses_real_menu_scopes_and_merges_plan_courses():
    batch = JwxkBatch(
        code="batch", name="选课", term_code="2026-2027-1", term_name="秋季",
        begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
        tactic_name="", course_types=("FANKC", "XGKC", "ALLKC"), need_confirm=False,
        notice="", state="active", can_enter=True, menus=(
            {"code": "FANKC", "name": "培养方案内课"},
            {"code": "XGKC", "name": "通识选修课"},
            {"code": "ALLKC", "name": "全校课程查询"},
        ),
    )
    calls = []

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [batch]}

        def _activate_batch(self, _batch_code):
            return {}

        def _search_courses_page(self, **kwargs):
            calls.append(kwargs["teaching_class_type"])
            code = kwargs["teaching_class_type"]
            return {"total": 1, "courses": [{
                "course_code": code, "course_name": code, "class_id": f"{code}-1",
                "schedules": [],
            }]}

    result = Client(type("Auth", (), {})()).search_catalog(
        batch_code="batch", page_number=1, page_size=20, scope="ROUND",
    )

    assert calls == ["FANKC", "XGKC"]
    assert {group["course_code"] for group in result["groups"]} == {"FANKC", "XGKC"}
    assert result["scope_options"][0] == {"code": "ALL", "name": "所有课程"}


def test_all_catalog_excludes_the_all_school_query_scope():
    batch = JwxkBatch(
        code="batch", name="选课", term_code="2026-2027-1", term_name="秋季",
        begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
        tactic_name="", course_types=("FANKC", "ALLKC"), need_confirm=False,
        notice="", state="active", can_enter=True, menus=(
            {"code": "FANKC", "name": "培养方案内课"},
            {"code": "ALLKC", "name": "全校课程查询"},
        ),
    )
    calls = []

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [batch]}

        def _activate_batch(self, _batch_code):
            return {}

        def _search_courses_page(self, **kwargs):
            scope = kwargs["teaching_class_type"]
            calls.append(scope)
            courses = [{
                "course_code": "A", "course_name": "课程A", "class_id": "shared",
                "teacher": "甲", "schedules": [],
            }]
            if scope == "ALLKC":
                courses.append({
                    "course_code": "B", "course_name": "课程B", "class_id": "all-only",
                    "teacher": "乙", "schedules": [], "teaching_class_type": "TJKC",
                })
            return {"total": len(courses), "courses": courses}

    result = Client(type("Auth", (), {})()).search_catalog(
        batch_code="batch", page_number=1, page_size=20, scope="ALL",
    )

    assert calls == ["FANKC"]
    assert {group["course_code"] for group in result["groups"]} == {"A"}
    shared = next(group for group in result["groups"] if group["course_code"] == "A")
    assert shared["classes"][0]["teaching_class_type"] == "FANKC"
    assert shared["source_tags"] == ["培养方案内课"]


def test_scope_category_and_campus_filters_use_codes_without_losing_alias_matches():
    batch = JwxkBatch(
        code="batch", name="选课", term_code="2026-2027-1", term_name="秋季",
        begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
        tactic_name="", course_types=("TJKC",), need_confirm=False,
        notice="", state="active", can_enter=True,
        menus=({"code": "TJKC", "name": "TJKC"},),
    )
    calls = []

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [batch], "current_campus": "01", "current_campus_name": "浑南校区"}

        def _activate_batch(self, _batch_code):
            return {}

        def _search_courses_page(self, **kwargs):
            calls.append(kwargs)
            return {"total": 1, "courses": [{
                "course_code": "A", "course_name": "专业课", "class_id": "A-1",
                "course_category": "专业方向课", "campus": "01",
                "campus_name": "浑南校区", "schedules": [],
            }]}

    result = Client(type("Auth", (), {})()).search_catalog(
        batch_code="batch", page_number=1, page_size=20, scope="TJKC",
        campus="浑南校区", filters={"KCLB": "专业方向类"},
    )

    assert result["total"] == 1
    assert result["groups"][0]["course_code"] == "A"
    assert calls[0]["campus"] == "01"
    assert "KCLB" not in calls[0]["filters"]
    assert result["scope_options"][-1] == {"code": "TJKC", "name": "任务推荐班课程"}


def test_all_school_scope_applies_campus_locally_because_official_ignores_it():
    batch = JwxkBatch(
        code="batch", name="选课", term_code="2026-2027-1", term_name="秋季",
        begin_time="", end_time="", selection_type="抢选", selection_type_code="02",
        tactic_name="", course_types=("ALLKC",), need_confirm=False,
        notice="", state="active", can_enter=True,
        menus=({"code": "ALLKC", "name": "全校课程查询"},),
    )

    class Client(JwxkSessionClient):
        def get_context(self):
            return {"batches": [batch]}

        def _activate_batch(self, _batch_code):
            return {}

        def _search_courses_page(self, **_kwargs):
            return {"total": 2, "courses": [{
                "course_code": "A", "course_name": "浑南课程", "class_id": "A-1",
                "campus": "01", "campus_name": "浑南校区", "schedules": [],
            }, {
                "course_code": "B", "course_name": "南湖课程", "class_id": "B-1",
                "campus": "00", "campus_name": "南湖校区", "schedules": [],
            }]}

    result = Client(type("Auth", (), {})()).search_catalog(
        batch_code="batch", page_number=1, page_size=20, scope="ALLKC",
        campus="浑南校区",
    )

    assert result["total"] == 1
    assert [group["course_code"] for group in result["groups"]] == ["A"]
