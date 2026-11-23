const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));
const playwrightRoot = path.dirname(require.resolve('playwright-core/package.json', { paths: [path.resolve(__dirname, '../frontend')] }));
const { PNG } = require(path.join(playwrightRoot, 'lib/utilsBundle.js'));

async function focusContrast(page, button) {
  const { x, y, width } = await button.boundingBox();
  const image = PNG.sync.read(await page.screenshot());
  const column = Math.floor(x + width / 2);
  const top = Math.floor(y);
  assert.ok(top >= 10 && top < image.height && column >= 0 && column < image.width);
  const luminance = row => {
    const offset = (row * image.width + column) * 4;
    const linear = Array.from(image.data.subarray(offset, offset + 3), value => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  };
  const background = luminance(top - 9);
  return Math.max(...Array.from({ length: 6 }, (_, index) => {
    const ring = luminance(top - index - 1);
    return (Math.max(background, ring) + 0.05) / (Math.min(background, ring) + 0.05);
  }));
}

async function textContrast(locator) {
  const bounds = await locator.evaluate(element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const rect = range.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  });
  const image = PNG.sync.read(await locator.page().screenshot({ clip: bounds }));
  let lightest = 0;
  let darkest = 1;
  for (let offset = 0; offset < image.data.length; offset += 4) {
    const channels = Array.from(image.data.subarray(offset, offset + 3), byte => {
      const value = byte / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    const luminance = channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    lightest = Math.max(lightest, luminance);
    darkest = Math.min(darkest, luminance);
  }
  return (lightest + 0.05) / (darkest + 0.05);
}

(async () => {
  const bundle = process.argv[2] && await fs.realpath(process.argv[2]);
  const build = bundle ? path.join(bundle, 'frontend/build') : path.resolve(__dirname, '../frontend/build');
  const themeFile = bundle ? path.join(bundle, 'themes/sakura.json') : path.resolve(__dirname, '../frontend/src/theme/templates/sakura.json');
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = new URL(request.url, 'http://localhost').pathname;
      let file = path.resolve(build, `.${pathname}`);
      if (file !== build && !file.startsWith(build + path.sep)) return response.writeHead(403).end();
      if (!path.extname(pathname)) file = path.join(build, 'index.html');
      response.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream');
      response.end(await fs.readFile(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-appearance-qa-'));
  let browser; let activePage;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.NEU_QA_BROWSER || 'chrome' });
    for (const width of [320, 375, 430, 768, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block', acceptDownloads: true });
      const page = await context.newPage(); activePage = page;
      page.setDefaultTimeout(10000);
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      let saved = { revision: 0, selected: '', templates: [], site_default: 'blue', effective: 'blue' };
      await page.addInitScript(() => history.replaceState(null, '', '/system-settings?tab=appearance'));
      await page.route('**/*', async route => {
        const request = route.request(); const url = new URL(request.url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20990001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/appearance') {
          if (request.method() === 'PUT') { const input = request.postDataJSON(); assert.equal(input.revision, saved.revision); saved = { ...saved, ...input, revision: saved.revision + 1, effective: input.selected || 'blue' }; }
          data = saved;
        } else if (url.pathname.includes('avatar')) return route.fulfill({ status: 204, body: '' });
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      const primary = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--color-brand').trim());
      const activateByKeyboard = async (name, key) => {
        const button = page.getByRole('button', { name, exact: true });
        await button.waitFor();
        for (let step = 0; step < 180; step++) {
          await page.keyboard.press('Tab');
          if (await button.evaluate(element => element === document.activeElement)) {
            assert.ok(await button.evaluate(element => element.matches(':focus-visible')));
            const ratio = await focusContrast(page, button);
            assert.ok(ratio >= 3, `${name} at ${width}px: focus contrast ${ratio.toFixed(2)}:1`);
            await page.keyboard.press(key);
            return;
          }
        }
        assert.fail(`Keyboard cannot reach ${name} at ${width}px`);
      };
      await activateByKeyboard('预览 樱空', 'Enter');
      assert.equal(await primary(), '#a83264');
      await activateByKeyboard('取消预览', 'Space');
      assert.equal(await primary(), '#2563eb');
      assert.equal(saved.revision, 0);
      await page.getByRole('button', { name: '应用 青庭', exact: true }).click();
      await page.getByText('主题已应用并保存到当前账号', { exact: true }).waitFor();
      assert.equal(saved.selected, 'garden');
      assert.equal(await primary(), '#0f766e');
      assert.equal(await page.evaluate(() => document.documentElement.dataset.themeMotion), 'reduced');
      await page.locator('input[type=file]').setInputFiles(themeFile);
      await page.getByText('文件已校验，请预览后保存为自己的模板', { exact: true }).waitFor();
      await page.getByRole('button', { name: '预览编辑结果', exact: true }).click();
      await page.getByText('正在预览，尚未保存', { exact: true }).waitFor();
      assert.equal(await primary(), '#a83264');
      assert.equal(saved.selected, 'garden');
      assert.equal(saved.templates.length, 0);
      await page.getByLabel('模板名称', { exact: true }).fill('测试个人主题');
      await page.getByRole('button', { name: '保存并应用', exact: true }).click();
      await page.getByText('自定义模板已保存并应用', { exact: true }).waitFor();
      assert.equal(saved.templates.length, 1);
      await page.reload();
      await page.getByText('测试个人主题', { exact: true }).waitFor();
      assert.equal(await primary(), '#a83264');
      const template = page.locator('.appearance-template').filter({ hasText: '测试个人主题' });
      const downloaded = page.waitForEvent('download').catch(error => error);
      await template.getByRole('button', { name: /^导\s*出$/ }).click();
      const download = await downloaded;
      if (download instanceof Error) throw download;
      const exported = JSON.parse(await fs.readFile(await download.path(), 'utf8'));
      assert.equal(exported.name, '测试个人主题');
      await page.locator('input[type=file]').setInputFiles({ name: 'unsafe.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ ...exported, css: 'url(https://example.com)' })) });
      await page.getByText(/主题文件格式无效/).waitFor();
      assert.equal(saved.templates.length, 1);
      await page.screenshot({ path: path.join(screenshots, `appearance-${width}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await template.getByRole('button', { name: /^删\s*除$/ }).click();
      await page.getByText('模板已删除；若为当前模板，已恢复站点默认', { exact: true }).waitFor();
      assert.equal(saved.templates.length, 0);
      assert.equal(saved.selected, '');
      assert.deepEqual(errors, []);
      await context.close();
    }
    for (const width of [320, 1440]) {
      for (const [theme, primaryColor] of [['sakura', '#a83264'], ['blue', '#2563eb'], ['garden', '#0f766e']]) {
        for (const surface of ['access', 'school', 'admin', 'credits']) {
          const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
          const page = await context.newPage(); activePage = page; page.setDefaultTimeout(10000);
          const errors = []; page.on('pageerror', error => errors.push(error.message));
          let privateReads = 0;
          let finishSave;
          const savePending = new Promise(resolve => { finishSave = resolve; });
          const auth = surface === 'credits' ? { is_logged_in: true, current_user: 'credit-fixture' } : { is_logged_in: false };
          await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== origin) return route.abort();
            if (!url.pathname.startsWith('/api/')) return route.continue();
            let data = {};
            if (url.pathname === '/api/access/status') data = { required: surface === 'access', configured: true, authenticated: surface !== 'access', site_default: theme };
            else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
            else if (url.pathname === '/api/status') data = auth;
            else if (url.pathname === '/api/admin/status') data = { configured: true, authenticated: false, deployment_mode: 'remote-multi' };
            else if (url.pathname === '/api/offline/status') data = { available: false };
            else if (url.pathname === '/api/appearance') {
              privateReads += 1;
              if (route.request().method() === 'PUT') await savePending;
              data = { revision: 0, selected: theme, effective: theme, site_default: theme, templates: [] };
            } else if (url.pathname === '/api/academic-report/cache') {
              data = { categories: [], credit_summary: { total_required: 40, total_passed: 10, total_selected: 2, total_remaining: 30, completion_rate: 25 } };
            }
            return route.fulfill({ json: data });
          });
          if (surface === 'credits') {
            await page.goto(`${origin}/academic-report`);
            await page.getByRole('button', { name: /学分统计/ }).click();
            const percent = page.locator('.progress-text .percent');
            await percent.waitFor();
            assert.equal(await percent.innerText(), '25%');
            const rgb = `rgb(${primaryColor.slice(1).match(/../g).map(value => parseInt(value, 16)).join(', ')})`;
            assert.equal(await percent.evaluate(node => getComputedStyle(node).color), rgb);
            assert.equal(await page.locator('.credit-progress .ant-progress-circle-path').first().evaluate(node => getComputedStyle(node).stroke), rgb);
            assert.equal(await page.locator('.credit-progress-bar .ant-progress-bg').evaluate(node => getComputedStyle(node).backgroundColor), rgb);
            await page.getByRole('button', { name: width === 320 ? '打开导航' : '展开导航' }).click();
            const currentItem = page.locator('.main-nav:visible .ant-menu-item-selected .ant-menu-title-content');
            await currentItem.waitFor();
            assert.match(await currentItem.innerText(), /培养计划/);
            const navContrast = await textContrast(currentItem);
            assert.ok(navContrast >= 4.5, `${theme} ${width}px navigation: ${navContrast.toFixed(2)}:1`);
            await page.goto(`${origin}/system-settings?tab=appearance`);
            await page.getByRole('button', { name: '恢复站点默认' }).click();
            const disabled = page.getByRole('button', { name: '创建自定义模板', disabled: true });
            try {
              await disabled.waitFor();
              const ratio = await textContrast(disabled.locator('span').last());
              assert.ok(ratio >= 4.5, `${theme} ${width}px disabled text: ${ratio.toFixed(2)}:1`);
            } finally { finishSave(); }
            assert.deepEqual(errors, []);
            await context.close();
            continue;
          }
          await page.goto(`${origin}${surface === 'admin' ? '/admin' : '/'}`);
          await page.locator(surface === 'access' ? '.access-login-page' : surface === 'school' ? '.login-page' : '.admin-login').waitFor();
          await page.waitForFunction(color => getComputedStyle(document.documentElement).getPropertyValue('--color-brand').trim() === color, primaryColor);
          if (surface === 'access') {
            await page.locator('button[type=submit]').click();
            const validation = page.getByText('请输入访问密码', { exact: true });
            await validation.waitFor();
            for (const text of [page.locator('.access-login-card h2'), page.locator('.access-login-card .ant-typography-secondary'), validation]) {
              const ratio = await textContrast(text);
              assert.ok(ratio >= 4.5, `${theme} ${width}px ${await text.innerText()}: ${ratio.toFixed(2)}:1`);
            }
          }
          assert.equal(privateReads, 0);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
          assert.deepEqual(errors, []);
          await page.screenshot({ path: path.join(screenshots, `${surface}-${theme}-${width}.png`), fullPage: true });
          await context.close();
        }
      }
    }
    console.log(JSON.stringify({ passed: true, widths: [320, 375, 430, 768, 1440], publicThemes: 18, creditCharts: 6, screenshots }));
  } catch (error) { if (activePage && !activePage.isClosed()) console.error(await activePage.locator('body').innerText()); throw error; }
  finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
