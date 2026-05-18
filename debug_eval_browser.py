"""
用 Playwright 抓取评教系统二级页面 API（v2）
===========================================
策略：先 Python 获取 CAS ticket，浏览器直接访问 caslogin?ticket=xxx
让 SPA 自己完成 JWT 存储和初始化
"""

import json
import sys
import os
import re
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neu_auth import NEUAuthClient
from neu_storage import Storage
from playwright.sync_api import sync_playwright

# 1. 登录并获取 CAS service ticket
storage = Storage()
COOKIE_FILE = os.path.join(storage.config.data_dir, "session.pkl")
creds = storage.load_credentials()
if not creds:
    sys.exit(1)

username, password = creds
client = NEUAuthClient(username=username, password=password, cookie_file=COOKIE_FILE)
if not client.ensure_login():
    print("登录失败")
    sys.exit(1)

service_url = "http://zljk.neu.edu.cn/caslogin"
resp = client.get(f"https://pass.neu.edu.cn/tpass/login?service={service_url}",
                  allow_redirects=False, timeout=30)
location = resp.headers.get("Location", "")
match = re.search(r'ticket=([^&]+)', location)
if not match:
    print(f"无法提取 CAS ticket, Location: {location}")
    sys.exit(1)
service_ticket = match.group(1)
print(f"CAS ticket: {service_ticket[:50]}...")

captured_requests = []

def handle_request(request):
    url = request.url
    if "/api/" in url:
        entry = {
            "method": request.method,
            "url": url,
            "headers": dict(request.headers),
            "post_data": request.post_data,
        }
        captured_requests.append(entry)
        print(f"\n[REQ] {request.method} {url[:150]}")
        if request.post_data:
            try:
                parsed = json.loads(request.post_data)
                print(f"  Body: {json.dumps(parsed, ensure_ascii=False, indent=2)[:600]}")
            except:
                print(f"  Body: {request.post_data[:400]}")

def handle_response(response):
    url = response.url
    if "/api/" in url:
        try:
            body = response.json()
            text = json.dumps(body, ensure_ascii=False, indent=2)
            print(f"\n[RES] {response.status} {url[:120]}")
            print(f"  {text[:2500]}")
            for req in captured_requests:
                if req["url"] == url:
                    req["response_status"] = response.status
                    req["response_body"] = body
                    break
        except:
            pass

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.on("request", handle_request)
    page.on("response", handle_response)

    # Step 1: 让浏览器走 CAS 回调（跳过 CAS 登录页）
    print("\n" + "=" * 60)
    print("Step 1: 通过 CAS ticket 直接进入评教系统")
    print("=" * 60)

    caslogin_url = f"http://zljk.neu.edu.cn/caslogin?ticket={service_ticket}"
    page.goto(caslogin_url, wait_until="networkidle", timeout=60000)
    time.sleep(2)

    print(f"最终 URL: {page.url}")

    # 检查是否成功（不应在 CAS 登录页）
    if "pass.neu.edu.cn" in page.url:
        print("ERROR: 仍在 CAS 登录页，ticket 可能已过期")
        browser.close()
        sys.exit(1)

    # 检查 localStorage 中的 JWT
    jwt_in_storage = page.evaluate("""() => {
        const keys = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key.toLowerCase().includes('token') || key.toLowerCase().includes('jwt') || key.toLowerCase().includes('auth')) {
                keys.push({key: key, value: localStorage.getItem(key).substring(0, 100)});
            }
        }
        return keys;
    }""")
    print(f"localStorage 中的 token 相关 key: {jwt_in_storage}")

    # Step 2: 访问一级页面
    print("\n" + "=" * 60)
    print("Step 2: 访问一级页面")
    print("=" * 60)
    page.goto("http://zljk.neu.edu.cn/evaluate/studentEvaluate/student-jdp/index",
              wait_until="networkidle", timeout=60000)
    time.sleep(3)
    print(f"URL: {page.url}")
    text = page.inner_text("body")
    print(f"页面文本: {text[:800]}")

    # Step 3: 点击查看
    print("\n" + "=" * 60)
    print("Step 3: 进入二级页面")
    print("=" * 60)

    clicked = False
    for sel in ['text="查看"', 'a:has-text("查看")', 'span:has-text("查看")', 'button:has-text("查看")']:
        try:
            els = page.query_selector_all(sel)
            vis = [el for el in els if el.is_visible()]
            if vis:
                print(f"用 '{sel}' 找到 {len(vis)} 个元素，点击第一个")
                vis[0].click()
                clicked = True
                time.sleep(3)
                break
        except Exception as e:
            pass

    if not clicked:
        print("未找到查看按钮，直接导航到二级页面")
        page.goto(
            "http://zljk.neu.edu.cn/evaluate/studentEvaluate/student-jdp/two-index"
            "?taskid=42d44c9fb4b5c2ac8ae1778396122877&xnxq=2025-2026-2",
            wait_until="networkidle", timeout=60000)

    time.sleep(3)
    print(f"URL: {page.url}")
    text = page.inner_text("body")
    print(f"页面文本: {text[:2000]}")

    # Step 4: 点击课程评价按钮
    print("\n" + "=" * 60)
    print("Step 4: 点击课程触发详情 API")
    print("=" * 60)

    for sel in ['text="评价"', 'text="去评价"', 'a:has-text("评价")', 'button:has-text("评价")',
                'td a', 'tr:has(td) a']:
        try:
            els = page.query_selector_all(sel)
            vis = [el for el in els if el.is_visible()]
            if vis:
                print(f"用 '{sel}' 找到 {len(vis)} 个可见元素:")
                for i, el in enumerate(vis[:5]):
                    txt = el.inner_text().strip()[:80]
                    href = el.get_attribute("href") or ""
                    print(f"  [{i+1}] text='{txt}' href='{href}'")

                # 点击第一个评价按钮
                vis[0].click()
                time.sleep(3)

                # 检查新 tab
                pages = context.pages
                print(f"  tab 数: {len(pages)}")
                if len(pages) > 1:
                    new_page = pages[-1]
                    new_page.wait_for_load_state("networkidle", timeout=15000)
                    print(f"  新 tab URL: {new_page.url}")
                    new_text = new_page.inner_text("body")
                    print(f"  新 tab 文本: {new_text[:1000]}")
                break
        except Exception as e:
            print(f"  '{sel}' error: {e}")

    time.sleep(3)

    # 汇总
    print("\n" + "=" * 60)
    print(f"共捕获 {len(captured_requests)} 个 API 请求")
    print("=" * 60)

    # 只打印有 post_data 的 POST 请求
    post_reqs = [r for r in captured_requests if r["method"] == "POST" and r.get("post_data")]
    print(f"\nPOST 请求 ({len(post_reqs)} 个):")
    for i, req in enumerate(post_reqs):
        print(f"\n[{i+1}] {req['url']}")
        print(f"    Body: {req['post_data'][:300]}")
        if req.get('response_body'):
            print(f"    Response: {json.dumps(req['response_body'], ensure_ascii=False)[:500]}")

    # 保存
    with open("data/captured_eval_requests.json", "w", encoding="utf-8") as f:
        json.dump(captured_requests, f, ensure_ascii=False, indent=2)
    print("\n已保存到 data/captured_eval_requests.json")

    # 截图
    page.screenshot(path="data/eval_page_final.png", full_page=True)
    print("截图: data/eval_page_final.png")

    time.sleep(3)
    browser.close()
