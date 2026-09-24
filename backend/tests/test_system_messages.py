import json

import pytest

from backend.core.academic.system_messages import SystemMessageAPI
from backend.core.cache.resources import canonicalize_system_messages, diff_system_messages


class Response:
    status_code = 200

    def __init__(self, payload):
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Client:
    def post(self, url, **_kwargs):
        action = "cxshxxlb" if "cxshxxlb" in url else "cxtxxxlb"
        return Response({
            "code": "0",
            "datas": {action: {"rows": [{
                "WID": action,
                "FSSJ": "2026-09-23 10:00:00",
                "XXBT": "课程变更通知",
                "XXNR": "课表地点发生变化",
                "YDZTDM": "01",
                "APPNAME": "系统消息",
                "PCURL": "/sys/homeapp/home/index.html",
            }]}},
        })


class ReadClient(Client):
    def __init__(self):
        self.read_calls = []

    def post(self, url, **kwargs):
        if "updateReadAndGetMessage" in url:
            self.read_calls.append((url, kwargs.get("data")))
            return Response({"code": "0", "datas": {}})
        return super().post(url, **kwargs)


def test_system_message_read_only_parser_and_diff():
    messages = SystemMessageAPI(Client()).get_messages()
    assert len(messages) == 2
    assert messages[0].read is False
    payload = canonicalize_system_messages({"messages": [item.to_dict() for item in messages]})
    assert payload["messages"][0]["title"] == "课程变更通知"
    diff = diff_system_messages({"messages": []}, payload)
    assert diff["counts"]["added"] == 2


def test_mark_read_uses_official_single_message_endpoints():
    client = ReadClient()
    api = SystemMessageAPI(client)

    api.mark_read("reminder-1", "reminder")
    api.mark_read("audit-1", "audit")

    assert client.read_calls == [
        (
            SystemMessageAPI.REMINDER_READ_URL,
            {"WID": "reminder-1"},
        ),
        (
            SystemMessageAPI.AUDIT_READ_URL,
            {"WID": "audit-1"},
        ),
    ]


def test_mark_read_rejects_invalid_id_and_official_failure():
    client = ReadClient()
    api = SystemMessageAPI(client)

    with pytest.raises(ValueError):
        api.mark_read("")

    class FailedReadClient(ReadClient):
        def post(self, url, **kwargs):
            if "updateReadAndGetMessage" in url:
                return Response({"code": "1", "msg": "拒绝"})
            return super().post(url, **kwargs)

    with pytest.raises(RuntimeError, match="被拒绝"):
        SystemMessageAPI(FailedReadClient()).mark_read("message-1")


class ReminderOnlyClient(Client):
    def post(self, url, **kwargs):
        if "cxshxxlb" in url:
            raise RuntimeError("审核标签暂不可用")
        return super().post(url, **kwargs)


def test_one_message_tab_failure_keeps_the_other_tab():
    messages = SystemMessageAPI(ReminderOnlyClient()).get_messages()
    assert len(messages) == 1
    assert messages[0].kind == "reminder"


class EmptyReminderClient(ReminderOnlyClient):
    def post(self, url, **kwargs):
        if "cxtxxxlb" in url:
            return Response({"code": "0", "datas": {"cxtxxxlb": {"rows": []}}})
        return super().post(url, **kwargs)


def test_successful_empty_tab_is_not_treated_as_total_failure():
    assert SystemMessageAPI(EmptyReminderClient()).get_messages() == []


def test_marked_messages_are_reconciled_in_local_cache(monkeypatch, tmp_path):
    from backend.app import cache_support
    from backend.core.cache import CacheKey, CacheStore

    store = CacheStore(tmp_path / "cache.db")
    spec = cache_support._cache_registry.get("system-messages")
    payload = {
        "messages": [
            {
                "id": "message-1",
                "kind": "reminder",
                "title": "课程变更",
                "content": "地点调整",
                "read": False,
            },
            {
                "id": "message-2",
                "kind": "reminder",
                "title": "其他通知",
                "content": "保留未读",
                "read": False,
            },
        ]
    }
    canonical = spec.canonicalize(payload)
    store.commit_success(
        key=CacheKey("student", "system-messages"),
        schema_version=spec.schema_version,
        revision_algorithm_version=spec.revision_algorithm_version,
        payload_type=spec.payload_type,
        payload=canonical,
        revision=cache_support._revision(canonical, "system-messages"),
        dependency_revisions={},
        changes={"seed": True},
        reason="test",
    )
    monkeypatch.setattr(
        cache_support,
        "read_cache",
        lambda _account, _resource: (store.get(CacheKey("student", "system-messages")), False),
    )
    monkeypatch.setattr(cache_support, "_cache_store", store)
    monkeypatch.setattr(cache_support, "auth_generation_is_current", lambda *_args: True)

    assert cache_support.mark_system_messages_read_cache(
        "student",
        [{"message_id": "message-1", "kind": "reminder"}],
    ) is True
    result = {
        item["id"]: item["read"]
        for item in store.get(CacheKey("student", "system-messages")).payload["messages"]
    }
    assert result["message-1"] is True
    assert result["message-2"] is False


def test_refresh_cannot_regress_an_acknowledged_message():
    from backend.core.cache.system_message_merge import preserve_system_message_read_state

    previous = {
        "messages": [
            {"id": "message-1", "kind": "reminder", "read": True},
            {"id": "message-2", "kind": "audit", "read": False},
        ]
    }
    current = {
        "messages": [
            {"id": "message-1", "kind": "reminder", "read": False},
            {"id": "message-2", "kind": "audit", "read": False},
        ]
    }

    merged = preserve_system_message_read_state(previous, current)

    assert merged["messages"][0]["read"] is True
    assert merged["messages"][1]["read"] is False
