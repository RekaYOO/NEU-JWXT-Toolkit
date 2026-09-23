"""Read-only system messages from the JWXT message center."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SystemMessage:
    message_id: str
    sent_at: str
    title: str
    content: str
    read: bool
    app_name: str = ""
    url: str = ""
    kind: str = "reminder"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "sent_at": self.sent_at,
            "title": self.title,
            "content": self.content,
            "read": self.read,
            "app_name": self.app_name,
            "url": self.url,
            "kind": self.kind,
        }


class SystemMessageAPI:
    """The list endpoints do not mutate read state."""

    REMINDER_URL = (
        "https://jwxt.neu.edu.cn/jwapp/sys/xxzxapp/modules/xtxx/cxtxxxlb.do"
    )
    AUDIT_URL = (
        "https://jwxt.neu.edu.cn/jwapp/sys/xxzxapp/modules/xtxx/cxshxxlb.do"
    )
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, auth_client):
        self._client = auth_client

    @staticmethod
    def _json_response(response) -> dict[str, Any]:
        # The endpoint occasionally omits/incorrectly declares charset. JSON
        # bytes are UTF-8; decoding raw bytes avoids requests' mojibake text.
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except Exception as error:
            raise RuntimeError("系统消息响应格式异常") from error
        if not isinstance(payload, dict) or payload.get("code") != "0":
            raise RuntimeError("系统消息读取失败")
        return payload

    def _list(self, url: str, action: str) -> list[SystemMessage]:
        response = self._client.post(
            url,
            data={"pageNumber": "1", "pageSize": "100", "querySetting": "[]"},
            headers=self.HEADERS,
            timeout=(5, 20),
        )
        if int(getattr(response, "status_code", 200) or 200) >= 400:
            raise RuntimeError("系统消息读取失败")
        data = self._json_response(response)
        rows = ((data.get("datas") or {}).get(action) or {}).get("rows") or []
        if not isinstance(rows, list):
            raise RuntimeError("系统消息响应结构异常")
        result = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("WID"):
                continue
            result.append(SystemMessage(
                message_id=str(row.get("WID") or ""),
                sent_at=str(row.get("FSSJ") or ""),
                title=str(row.get("XXBT") or "").strip(),
                content=str(row.get("XXNR") or "").strip(),
                read=str(row.get("YDZTDM") or "") == "02",
                app_name=str(row.get("APPNAME") or "").strip(),
                url=str(row.get("YDURL") or row.get("PCURL") or "").strip(),
                kind="audit" if action == "cxshxxlb" else "reminder",
            ))
        return result

    def get_messages(self) -> list[SystemMessage]:
        # Audit messages and reminder messages are separate tabs. A failure in
        # one tab must not hide the successfully readable other tab.
        messages: list[SystemMessage] = []
        errors = []
        succeeded = False
        for url, action in ((self.REMINDER_URL, "cxtxxxlb"), (self.AUDIT_URL, "cxshxxlb")):
            try:
                messages.extend(self._list(url, action))
                succeeded = True
            except Exception as error:
                errors.append(error)
        if not succeeded:
            raise RuntimeError("系统消息读取失败") from errors[0]
        messages.sort(key=lambda item: (item.sent_at, item.message_id), reverse=True)
        return messages
