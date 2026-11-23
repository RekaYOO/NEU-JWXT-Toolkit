const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

(async () => {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-research-qa-'));
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
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    for (const width of [320, 375, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
      let selected = false;
      let favorite = false;
      const favoriteChanges = [];
      const favoriteState = () => ({ favorite_topic_ids: favorite ? ['topic'] : [], favorite_topics: favorite ? [{ topic_id: 'topic', favorite_batch_id: 'batch', title: '材料综合研究课题' }] : [] });
      let verifications = 0;
      const writes = [];
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/research-training'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20990001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/research-training/cache') data = {
          available: true, batch: { batch_id: 'batch', name: '科研训练测试批次', max_topics: 2, rank_limit_percent: 30, minimum_journal_count: 2 },
          eligibility: { available: true, gpa: '3.6', major_rank: '25.93' }, total: 1,
          topics: [{ topic_id: 'topic', title: '材料综合研究课题', advisor_name: '导师', registered_count: selected ? 1 : 0, capacity: 5, is_registered: selected, can_enroll: !selected, can_cancel: selected }],
          confirmed_topics: [], ...favoriteState(),
        };
        else if (url.pathname === '/api/research-training/favorite') {
          const payload = route.request().postDataJSON();
          assert.deepEqual(payload, { batch_id: 'batch', topic_id: 'topic', favorite: !favorite });
          favorite = payload.favorite;
          favoriteChanges.push(favorite);
          data = favoriteState();
        }
        else if (url.pathname === '/api/research-training/enroll' || url.pathname === '/api/research-training/cancel') {
          const action = url.pathname.split('/').pop();
          writes.push(action);
          if (action === 'enroll') assert.deepEqual(route.request().postDataJSON(), { topic_id: 'topic', batch_id: 'batch', phone: '123456', email: 'test@example.invalid', reason: '希望参与研究' });
          data = { success: false, state: 'unknown', message: '学校结果尚未确认，请核验报名记录。' };
        } else if (url.pathname === '/api/research-training/verify') {
          verifications++;
          selected = !selected;
          data = { success: true, state: 'confirmed', message: '官方记录已确认' };
        } else if (url.pathname.startsWith('/api/cache/refresh/')) data = { status: 'fresh' };
        else if (url.pathname.includes('avatar')) return route.fulfill({ status: 204, body: '' });
        return route.fulfill({ json: data });
      });
      await page.goto(origin);
      const addFavorite = page.getByRole('button', { name: '收藏课题', exact: true });
      const removeFavorite = page.getByRole('button', { name: '取消收藏课题', exact: true });
      await addFavorite.click();
      await removeFavorite.waitFor();
      await page.reload();
      await removeFavorite.click();
      await addFavorite.waitFor();
      await page.reload();
      await addFavorite.waitFor();
      assert.deepEqual(favoriteChanges, [true, false]);
      assert.deepEqual(writes, []);
      await page.getByRole('button', { name: /^报\s*名$/ }).click();
      const dialog = page.getByRole('dialog', { name: '科研训练课题报名' });
      await dialog.getByLabel('联系电话').fill('123456');
      await dialog.getByLabel('电子邮箱').fill('test@example.invalid');
      await dialog.getByLabel('申请理由').fill('希望参与研究');
      assert.deepEqual(writes, []);
      await dialog.getByRole('button', { name: '确认报名' }).click();
      const notice = page.locator('.research-operation-notice');
      await notice.waitFor();
      await dialog.waitFor({ state: 'hidden' });
      assert.deepEqual(writes, ['enroll']);
      assert.equal(await page.getByRole('button', { name: /^报\s*名$/ }).isDisabled(), true);
      const verify = notice.getByRole('button', { name: '核验报名结果' });
      await verify.scrollIntoViewIfNeeded();
      const bounds = await verify.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width && bounds.height >= 44);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(screenshots, `research-${width}.png`), fullPage: true });
      await verify.click();
      await notice.waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: '取消报名', exact: true }).click();
      const cancellation = page.getByRole('dialog', { name: '确认取消报名？' });
      assert.deepEqual(writes, ['enroll']);
      await cancellation.getByRole('button', { name: '取消报名', exact: true }).click();
      await notice.waitFor();
      await cancellation.waitFor({ state: 'hidden' });
      await verify.click();
      await notice.waitFor({ state: 'hidden' });
      assert.deepEqual(writes, ['enroll', 'cancel']);
      assert.equal(verifications, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(JSON.stringify({ passed: true, screenshots }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
