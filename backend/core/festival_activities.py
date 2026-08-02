"""Four-festival participation scraping and certificate archive support."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup


CXCY_ORIGIN = "https://cxcy.neu.edu.cn"
SECTIONS = {
    "originality": "创意节",
    "popscience": "科普节",
    "technical": "科技节",
    "business": "创业节",
}
MAX_PAGES_PER_SECTION = 50
MAX_ACTIVITIES = 500


class FestivalActivitiesError(RuntimeError):
    pass


@dataclass
class FestivalActivity:
    id: str
    section: str
    name: str
    detail_url: str
    team_name: str = ""
    status: str = ""
    category: str = ""
    type: str = ""
    award: str = ""
    sign_in: str = ""
    sign_out: str = ""
    certificate_available: bool = False
    certificate_url: str = ""
    registration_time: str = ""
    activity_time: str = ""
    start_time: str | None = None
    duration: str = ""
    department: str = ""
    location: str = ""
    notes: str = ""
    description: str = ""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def _label(text: str, *labels: str) -> str:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[：:]\s*(.*?)(?=(?:\s+[\u4e00-\u9fff]{{2,8}}\s*[：:])|$)", text)
        if match:
            return _clean_text(match.group(1))
    return ""


def _short_label(text: str, *labels: str) -> str:
    value = _label(text, *labels)
    return value.split(" ", 1)[0] if value else ""


def _activity_id(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    value = query.get("id", "")
    return value or parsed.path.rstrip("/").rsplit("/", 1)[-1]


def _safe_cache_url(url: str) -> str:
    parsed = urlparse(urljoin(CXCY_ORIGIN, url))
    if parsed.hostname != "cxcy.neu.edu.cn":
        return ""
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"t", "ts", "timestamp", "_"}
    ]
    return urlunparse(("https", parsed.netloc, parsed.path, "", urlencode(query), ""))


def _record_container(link: Any, section: str) -> Any:
    """Return the smallest ancestor that represents exactly one result row."""
    detail_path = f"/{section}/comp/front/comp/info"
    fallback = None
    for parent in link.parents:
        name = getattr(parent, "name", None)
        if name not in {"div", "tr", "li", "article"}:
            continue
        detail_links = 0
        for candidate in parent.find_all("a", href=True):
            href = urljoin(
                f"{CXCY_ORIGIN}/{section}/comp/ucenter/main/index",
                str(candidate.get("href") or ""),
            )
            if urlparse(href).path == detail_path and _activity_id(href):
                detail_links += 1
        if detail_links > 1:
            break
        if detail_links != 1:
            continue
        classes = set(parent.get("class") or [])
        if name in {"tr", "li", "article"} or "list_item" in classes:
            return parent
        if name == "div" and fallback is None:
            fallback = parent
    # Never climb to a page-wide wrapper merely because it happens to contain
    # one result. A local fallback may omit a certificate, but cannot associate
    # another widget's certificate with this activity.
    return fallback or link


def _certificate_url(raw_url: str, section: str) -> str:
    """Accept only the static image directory assigned to this section."""
    if not raw_url:
        return ""
    parsed_input = urlparse(raw_url)
    if parsed_input.scheme and parsed_input.scheme != "https":
        return ""
    absolute = urljoin(CXCY_ORIGIN, raw_url)
    parsed = urlparse(absolute)
    try:
        port = parsed.port
    except ValueError:
        return ""
    decoded_path = unquote(parsed.path)
    if (
        unquote(decoded_path) != decoded_path
        or "\\" in decoded_path
        or any(part in {".", ".."} for part in decoded_path.split("/"))
        or any(ord(char) < 32 for char in decoded_path)
    ):
        return ""
    legacy_path = decoded_path.startswith(
        f"/static/uploads/res/{section}cert/"
    )
    generic_path = bool(re.fullmatch(
        r"/static/uploads/res/certificate/[A-Za-z0-9_-]{1,128}/[^/]+\.(?:png|jpe?g|webp)",
        decoded_path,
        flags=re.IGNORECASE,
    ))
    if (
        parsed.hostname != "cxcy.neu.edu.cn"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not (legacy_path or generic_path)
        or not re.search(r"\.(?:png|jpe?g|webp)$", decoded_path, re.IGNORECASE)
        or any(
            key.lower() not in {"t", "ts", "timestamp", "_"}
            or not re.fullmatch(r"\d{1,20}", value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        )
    ):
        return ""
    return _safe_cache_url(absolute)


def _find_certificate_url(container: Any, section: str) -> str:
    if container is None:
        return ""
    for node in container.find_all(True):
        for attribute in ("href", "src", "data-url", "data-src"):
            result = _certificate_url(str(node.get(attribute) or ""), section)
            if result:
                return result
        onclick = str(node.get("onclick") or "")
        for candidate in re.findall(
            r"[\"']([^\"']*/static/uploads/res/(?:[a-z]+cert|certificate)/[^\"']+)[\"']",
            onclick,
            flags=re.IGNORECASE,
        ):
            result = _certificate_url(candidate, section)
            if result:
                return result
    return ""


def _looks_like_login(response: Any) -> bool:
    url = str(getattr(response, "url", "") or "")
    text = str(getattr(response, "text", "") or "")[:5000]
    return "pass.neu.edu.cn/tpass/login" in url or (
        "/ucenter/index/login" in url and "bloginurl" in url
    ) or ("统一身份认证" in text and "password" in text.lower())


def parse_participation_page(html: str, section: str) -> tuple[list[FestivalActivity], list[str]]:
    """Parse a user-center page using links and visible labels, not CSS hashes."""
    soup = BeautifulSoup(html, "lxml")
    activities: list[FestivalActivity] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = urljoin(
            f"{CXCY_ORIGIN}/{section}/comp/ucenter/main/index",
            str(link.get("href") or ""),
        )
        parsed_href = urlparse(href)
        if (
            parsed_href.hostname != "cxcy.neu.edu.cn"
            or parsed_href.path != f"/{section}/comp/front/comp/info"
            or not _activity_id(href)
        ):
            continue
        detail_url = _safe_cache_url(href)
        activity_id = _activity_id(detail_url)
        if not detail_url or activity_id in seen:
            continue
        seen.add(activity_id)
        container = _record_container(link, section)
        text = _clean_text(container.get_text(" ", strip=True) if container else link.text)
        cert_url = _find_certificate_url(container, section)
        link_text = _clean_text(link.get("title") or link.get_text(" ", strip=True))
        name = _label(text, "活动名称", "名称")
        if not name:
            name = _clean_text(re.split(r"类别\s*[：:]", text, maxsplit=1)[0])
            if link_text and name == link_text:
                name = ""
        activities.append(FestivalActivity(
            id=activity_id,
            section=SECTIONS[section],
            name=name or f"活动 {activity_id}",
            detail_url=detail_url,
            team_name=_label(text, "队伍名称", "团队名称", "队伍名") or link_text,
            status=_label(text, "活动状态", "状态"),
            category=_label(text, "类别"),
            type=_label(text, "类型"),
            award=_label(text, "获奖名次", "获奖", "奖项"),
            sign_in=_short_label(text, "签到情况", "签到状态", "签到"),
            sign_out=_short_label(text, "签退情况", "签退状态", "签退"),
            certificate_available=bool(cert_url),
            certificate_url=cert_url,
        ))
    pages: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = urljoin(f"{CXCY_ORIGIN}/{section}/comp/ucenter/main/index", link["href"])
        parsed = urlparse(href)
        if parsed.hostname == "cxcy.neu.edu.cn" and parsed.path.startswith(f"/{section}/comp/ucenter/"):
            query = dict(parse_qsl(parsed.query))
            if any(key.lower() in {"page", "p"} for key in query):
                pages.add(parsed.path + ("?" + parsed.query if parsed.query else ""))
    return activities, sorted(pages)


def parse_activity_detail(html: str, activity: FestivalActivity) -> FestivalActivity:
    soup = BeautifulSoup(html, "lxml")
    page_text = _clean_text(soup.get_text(" ", strip=True))
    # The shared site header also contains generic ``.title`` elements (for
    # example “最新公告”).  Resolve selectors in explicit priority order so a
    # page-level heading can never replace the activity title.
    title = (
        soup.select_one(".body_box .body_part1 > .title")
        or soup.select_one(".body_part1 > .title")
        or soup.select_one("h1")
    )
    activity.name = _clean_text(title.get_text(" ", strip=True)) if title else activity.name
    activity.category = _label(page_text, "类别") or activity.category
    activity.type = _label(page_text, "类型") or activity.type
    activity.registration_time = _label(page_text, "报名时间")
    activity.activity_time = _label(page_text, "活动时间")
    activity.department = _label(page_text, "所属部门")
    activity.location = _label(page_text, "活动地点")
    time_match = re.search(r"活动时间\s*[：:]\s*(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", page_text)
    if time_match:
        raw = time_match.group(1)
        try:
            parsed = datetime.fromisoformat(raw)
            activity.start_time = parsed.isoformat(timespec="minutes" if " " in raw else "seconds")
        except ValueError:
            activity.start_time = None
    duration_match = re.search(r"活动时间.*?[（(]\s*([^（）()]*)\s*小时\s*[）)]", page_text)
    if duration_match:
        activity.duration = _clean_text(duration_match.group(1))
    sections = soup.select(".body_part2")
    for block in sections:
        heading = _clean_text((block.select_one(".p_title") or block).get_text(" ", strip=True))
        content = block.select_one(".content")
        if content:
            for unsafe in content.select("script, style, noscript"):
                unsafe.decompose()
        value = _clean_text(content.get_text("\n", strip=True)) if content else ""
        if "注意事项" in heading:
            activity.notes = value
        elif "活动简介" in heading:
            activity.description = value
    return activity


def fetch_festival_activities(auth: Any) -> dict[str, Any]:
    activities: dict[str, FestivalActivity] = {}
    warnings: list[str] = []
    for section in SECTIONS:
        pending = [f"/{section}/comp/ucenter/main/index"]
        visited: set[str] = set()
        while pending and len(visited) < MAX_PAGES_PER_SECTION:
            path = pending.pop(0)
            if path in visited:
                continue
            visited.add(path)
            response = auth.request_service("cxcy", "GET", path)
            response.raise_for_status()
            if _looks_like_login(response):
                raise FestivalActivitiesError("四节活动登录会话已过期")
            rows, pages = parse_participation_page(response.text, section)
            for row in rows:
                activities[f"{section}:{row.id}"] = row
            pending.extend(page for page in pages if page not in visited)
            if len(activities) > MAX_ACTIVITIES:
                raise FestivalActivitiesError("四节活动数量超过安全限制")
        if pending:
            raise FestivalActivitiesError(f"{SECTIONS[section]}分页超过安全限制")
    for key, activity in list(activities.items()):
        try:
            parsed = urlparse(activity.detail_url)
            response = auth.request_service("cxcy", "GET", parsed.path + ("?" + parsed.query if parsed.query else ""))
            response.raise_for_status()
            activities[key] = parse_activity_detail(response.text, activity)
            if activities[key].start_time is None:
                warnings.append(f"{activity.section}「{activity.name}」缺少活动时间")
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                warnings.append(f"{activity.section}「{activity.name}」详情不存在")
                continue
            raise
    return {"activities": [asdict(item) for item in activities.values()], "warnings": warnings}
