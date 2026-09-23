import json

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


def test_system_message_read_only_parser_and_diff():
    messages = SystemMessageAPI(Client()).get_messages()
    assert len(messages) == 2
    assert messages[0].read is False
    payload = canonicalize_system_messages({"messages": [item.to_dict() for item in messages]})
    assert payload["messages"][0]["title"] == "课程变更通知"
    diff = diff_system_messages({"messages": []}, payload)
    assert diff["counts"]["added"] == 2


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
