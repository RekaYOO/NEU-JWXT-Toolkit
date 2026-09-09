/* Run against a production build with an installed Playwright module.
 * All API responses are synthetic; the school services are never contacted.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '../frontend/build');
const output = process.env.CXCY_UI_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'cxcy-ui-'));
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  const file = path.resolve(root, `.${pathname}`);
  const safe = file.startsWith(root + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile();
  const target = safe ? file : path.join(root, 'index.html');
  const type = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)];
  response.setHeader('Content-Type', type || 'application/octet-stream');
  fs.createReadStream(target).pipe(response);
});

async function settleModals(page) {
  await page.waitForFunction(() => [...document.querySelectorAll('.ant-modal')]
    .filter(element => element.getClientRects().length)
    .every(element => {
      const style = getComputedStyle(element);
      return style.animationName === 'none' && style.opacity === '1' && style.transform === 'none';
    }));
}

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}),
  });
  try {
    for (const [kind, width, height] of [
      ['web', 320, 800], ['web', 375, 812], ['client', 430, 932],
      ['local', 768, 1024], ['web', 1440, 1000],
    ]) {
      const context = await browser.newContext({ viewport: { width, height }, locale: 'zh-CN' });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let route = 'follow';
      let authenticated = false;
      let smsAttempts = 0;
      const testPendingSMS = process.env.CXCY_UI_SMS_PENDING === '1';
      const testJwxk = process.env.JWXK_UI === '1';
      let releaseJwxkProbe;
      const jwxkProbeGate = new Promise(resolve => { releaseJwxkProbe = resolve; });
      const targetService = testJwxk ? 'jwxk' : 'cxcy';
      const calls = [];
      const api = async (rawPath, method, body) => {
        const url = new URL(rawPath, origin);
        const endpoint = url.pathname;
        calls.push(endpoint);
        let data = {};
        if (endpoint === '/api/access/status') data = { required: false, authenticated: true };
        else if (endpoint === '/api/client/bootstrap') data = {
          auth: { is_logged_in: true, current_user: '20250001', network_mode: 'webvpn' },
          runtime: { profile: kind === 'local' ? 'mobile' : 'server' },
        };
        else if (endpoint === '/api/client/updates') data = { cache: { events: [], cursor: 0 }, pending_auth: { required: false } };
        else if (endpoint === '/api/auth/pending') data = { required: false };
        else if (endpoint === '/api/course-selection/jwxk/catalog/archives') data = { archives: [] };
        else if (endpoint === '/api/course-selection/jwxk/status'
          || endpoint === '/api/course-selection/jwxk/settings') {
          if (method === 'PUT') route = JSON.parse(body).network_mode;
          const localOnly = url.searchParams.get('probe') === 'false';
          if (!localOnly) await jwxkProbeGate;
          data = {
            network_mode: route, effective_network_mode: route === 'direct' ? 'direct' : 'webvpn',
            primary_authenticated: true, current_user: '20250001', service_authenticated: false,
            service_auth_state: localOnly ? 'checking' : authenticated ? 'not_in_selection_round' : 'login_required',
            message: localOnly ? '正在核验选课系统会话' : authenticated
              ? '当前账号不在学校开放的选课轮次中'
              : '选课系统的 WebVPN 登录已失效，请使用账号密码或微信扫码恢复。',
            batches: [],
          };
        }
        else if (endpoint === '/api/export/festival-activities/settings') route = JSON.parse(body).network_mode;
        else if (endpoint === '/api/webvpn/password/start') {
          assert.equal(JSON.parse(body).target_service, targetService);
          authenticated = !testPendingSMS;
          data = testPendingSMS
            ? { success: true, status: 'sms_required', flow_id: 'fixture-sms', target_service: targetService }
            : { success: true, status: 'authenticated', target_service: targetService,
              ...(testJwxk ? { service_auth_state: 'not_in_selection_round' } : {}) };
        } else if (endpoint === '/api/webvpn/sms/verify') {
          smsAttempts += 1;
          assert.equal(JSON.parse(body).code, smsAttempts === 1 ? '123456' : '');
          authenticated = smsAttempts === 2;
          data = authenticated
            ? { success: true, status: 'authenticated', target_service: 'cxcy' }
            : { success: false, status: 'session_pending', sms_verified: true,
              flow_id: 'fixture-sms', target_service: 'cxcy', message: '短信已通过，等待建立会话' };
        } else if (endpoint === '/api/export/festival-activities/cache') data = {
          available: true, source: 'cache', activities: [{
            id: '1', section: '科普节', name: '模拟活动', start_time: '2026-09-09T08:00:00',
          }], cache: { revision: 'fixture', is_stale: false },
        };
        if (endpoint.endsWith('/festival-activities/status') || endpoint.endsWith('/festival-activities/settings')) {
          data = { network_mode: route, effective_network_mode: route === 'direct' ? 'direct' : 'webvpn',
            primary_authenticated: true, current_user: '20250001', service_authenticated: authenticated,
            service_auth_state: authenticated ? 'authenticated' : 'login_required',
            message: authenticated ? '创院系统会话有效' : '创院系统的 WebVPN 登录已失效，请使用账号密码或微信扫码恢复。' };
        }
        return { status: 200, headers: { 'content-type': 'application/json' }, body: JSON.stringify(data) };
      };
      await page.route('**/api/**', async handler => {
        const request = handler.request();
        await handler.fulfill(await api(request.url(), request.method(), request.postData()));
      });
      await page.route(/https:\/\/(?!127\.0\.0\.1)/, handler => handler.abort());
      if (kind !== 'web') {
        await page.exposeFunction('__fixtureRequest', raw => {
          const request = JSON.parse(raw);
          return api(request.path, request.method, request.body);
        });
        await page.addInitScript(shellKind => {
          let sequence = 0;
          window.NeuNative = {
            getShellInfo: () => JSON.stringify({ kind: shellKind }),
            cancel() {},
            request(raw) {
              const id = `fixture-${++sequence}`;
              window.__fixtureRequest(raw).then(result => window.__neuNativeDeliver(id, result));
              return id;
            },
          };
        }, kind);
      }
      if (testJwxk) {
        await page.goto(`${origin}/course-selection`);
        const routeControl = page.locator('.service-route-control');
        await routeControl.getByText('当前有效线路：WebVPN（正在核验）', { exact: true }).waitFor();
        assert.equal(await routeControl.getByRole('radio', { name: '跟随教务', exact: true }).isChecked(), true);
        await page.screenshot({ path: path.join(output, `${kind}-${width}-jwxk-checking.png`), fullPage: true });
        releaseJwxkProbe();
        const notice = page.locator('.course-selection-auth-alert');
        await notice.getByRole('button', { name: '账号密码恢复' }).waitFor();
        assert.equal(await notice.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
        const countProbes = () => calls.filter(p => p === '/api/course-selection/jwxk/status').length;
        const beforeRecovery = countProbes();
        await notice.getByRole('button', { name: '账号密码恢复' }).click();
        const login = page.getByRole('dialog', { name: '登录 WebVPN', exact: true });
        await login.getByPlaceholder('密码', { exact: true }).fill('fixture-only');
        await login.getByRole('button', { name: '恢复 WebVPN 登录', exact: true }).click();
        await login.waitFor({ state: 'hidden' });
        await notice.getByText('当前账号不在学校开放的选课轮次中', { exact: false }).waitFor();
        assert.equal(countProbes(), beforeRecovery, 'authentication result must not trigger another probe');
        assert.equal(await notice.getByRole('button', { name: '账号密码恢复' }).count(), 0);
        await routeControl.getByText('直连', { exact: true }).click();
        await routeControl.getByText('当前有效线路：直连', { exact: true }).waitFor();
        await routeControl.getByText('跟随教务', { exact: true }).click();
        await routeControl.getByText('当前有效线路：WebVPN', { exact: true }).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
        await page.screenshot({ path: path.join(output, `${kind}-${width}-jwxk-result.png`), fullPage: true, animations: 'disabled' });
        assert.deepEqual(errors, []);
        assert.equal(calls.includes('/api/webvpn/sms/send'), false);
        console.log(JSON.stringify({ kind, width, height, jwxk: true, errors: errors.length, passed: true }));
        await context.close();
        continue;
      }
      await page.goto(`${origin}/export/festival-activities`);
      const dialog = page.getByRole('dialog', { name: '选择四节活动时间与读取方式' });
      await dialog.waitFor();
      await dialog.getByText('创院系统的 WebVPN 登录已失效', { exact: false }).waitFor();
      await settleModals(page);
      const notice = dialog.locator('.service-auth-notice');
      assert.equal(await notice.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
      await page.screenshot({ path: path.join(output, `${kind}-${width}-config.png`), fullPage: true });
      const confirmBounds = await dialog.getByRole('button', { name: '确认并读取活动' }).boundingBox();
      assert.ok(confirmBounds.y >= 0 && confirmBounds.y + confirmBounds.height <= height);
      await dialog.getByRole('button', { name: '账号密码恢复' }).click();
      const login = page.getByRole('dialog', { name: '登录 WebVPN', exact: true });
      await login.waitFor();
      await login.getByPlaceholder('密码', { exact: true }).fill('fixture-only');
      await settleModals(page);
      assert.ok(await login.evaluate(element => {
        const rect = element.getBoundingClientRect();
        return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
      }), 'login modal must be above the configuration modal');
      await page.screenshot({ path: path.join(output, `${kind}-${width}-login.png`), fullPage: true });
      await login.getByRole('button', { name: '恢复 WebVPN 登录', exact: true }).click();
      await login.waitFor({ state: 'hidden' });
      if (testPendingSMS) {
        const sms = page.getByRole('dialog', { name: '短信二次认证', exact: true });
        await sms.getByRole('textbox', { name: '短信验证码', exact: true }).fill('123456');
        await sms.getByRole('button', { name: '验证并登录', exact: true }).click();
        const pending = page.getByRole('dialog', { name: '建立教务会话', exact: true });
        await pending.waitFor();
        assert.equal(await pending.getByRole('textbox').count(), 0);
        await settleModals(page);
        await page.screenshot({ path: path.join(output, `${kind}-${width}-session-pending.png`), fullPage: true });
        await pending.getByRole('button', { name: '继续建立会话', exact: true }).click();
        await pending.waitFor({ state: 'hidden' });
        assert.equal(smsAttempts, 2);
        assert.equal(calls.includes('/api/webvpn/sms/send'), false);
      }
      await dialog.getByRole('button', { name: '确认并读取活动' }).click();
      await dialog.waitFor({ state: 'hidden' });
      await page.getByText('模拟活动', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      assert.equal(calls.includes('/api/export/festival-activities/certificates/archive'), false);
      await page.screenshot({ path: path.join(output, `${kind}-${width}-activities.png`), fullPage: true, animations: 'disabled' });
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ kind, width, height, errors: errors.length, passed: true }));
      await context.close();
    }
    console.log(`Screenshots: ${output}`);
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(() => server.close());
