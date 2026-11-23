const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

(async () => {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || path.join(__dirname, '../frontend/build'));
  const screenshots = await fs.mkdtemp(path.join(os.tmpdir(), 'neu-evaluation-qa-'));
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
  let activePage;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.QA_BROWSER || 'msedge' });
    for (const [width, height] of [[320, 800], [375, 812], [812, 375], [1440, 900]]) {
      const page = await browser.newPage({ viewport: { width, height }, serviceWorkers: 'block', reducedMotion: 'reduce' });
      activePage = page;
      let submitted = false;
      let batchMode = false;
      let releaseSubmission;
      let writes = 0;
      let wire;
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => history.replaceState(null, '', '/evaluation'));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const auth = { is_logged_in: true, current_user: '20250001' };
        let data = {};
        if (url.pathname === '/api/access/status') data = { required: false, authenticated: true };
        else if (url.pathname === '/api/client/bootstrap') data = { runtime: { profile: 'development' }, auth };
        else if (url.pathname === '/api/status') data = auth;
        else if (url.pathname === '/api/evaluation/tasks') data = { tasks: [{ task_id: 'task', task_name: '测试评教任务', total_count: 1, pending_count: submitted ? 0 : 1, evaluated_count: submitted ? 1 : 0 }] };
        else if (url.pathname === '/api/evaluation/tasks/task/courses') data = { courses: [{ xspjid: 'record', task_id: 'task', course_name: '测试课程', teacher_name: '测试教师', is_evaluated: submitted }, ...(batchMode ? [{ xspjid: 'second', task_id: 'task', course_name: '第二门课程', teacher_name: '测试教师', is_evaluated: false }] : [])] };
        else if (url.pathname.endsWith('/indicators')) data = { indicators: [
          { zbid: 'first', zbmc: '教学目标', evaltype: 1, sfbt: 1, level_json: [{ value: 2, df: 32, mc: '需改进' }, { value: 5, df: 85, mc: '符合要求' }] },
          { zbid: 'second', zbmc: '教学方法', evaltype: 1, sfdx: 1, sfbt: 1, level_json: [{ value: 2, df: 32, mc: '需改进' }, { value: 5, df: 85, mc: '符合要求' }] },
          { zbid: 'text', zbmc: '评价建议', evaltype: 2, sfbt: 1 },
        ] };
        else if (url.pathname === '/api/evaluation/submit') {
          writes++;
          wire = route.request().postDataJSON();
          if (batchMode && process.env.QA_STOP_BATCH === '1') {
            await new Promise(resolve => { releaseSubmission = resolve; });
            data = { success: true, message: '已确认提交' };
          } else {
            data = { success: false, unknown: true, message: '提交结果尚未确认，请核对学校记录', refetches: ['evaluation-tasks', 'evaluation-courses'] };
          }
        }
        else if (url.pathname.includes('avatar')) return route.fulfill({ status: 204, body: '' });
        await route.fulfill({ json: data });
      });
      await page.goto(origin);
      await page.getByRole('button', { name: '已知晓', exact: true }).click();
      if (width < 768) await page.getByRole('button', { name: /测试评教任务/ }).click();
      else await page.getByRole('button', { name: /查看课程$/ }).click();
      await page.getByRole('button', { name: /评价$/ }).click();
      const dialog = page.getByRole('dialog', { name: '评价课程' });
      await dialog.getByRole('button', { name: '最高分填充' }).click();
      await dialog.getByLabel('评价建议', { exact: true }).fill('保留填写的建议');
      await dialog.getByLabel('评分原因', { exact: true }).fill('保留填写的原因');
      assert.equal(await dialog.getByRole('radio').count(), 2);
      assert.equal(await dialog.getByRole('checkbox').count(), 2);
      await dialog.getByRole('button', { name: '提交评教', exact: true }).click();
      await page.getByRole('button', { name: '确认提交', exact: true }).click();
      await page.locator('.ant-modal-confirm').waitFor({ state: 'hidden' });
      await dialog.getByText('结果待核对', { exact: true }).waitFor();
      assert.equal(writes, 1);
      assert.deepEqual(wire.custom_scores, { first: 2, second: [5] });
      assert.equal(wire.text_results.text, '保留填写的建议');
      assert.equal(wire.reason, '保留填写的原因');
      assert.equal(await dialog.getByLabel('评价建议', { exact: true }).inputValue(), '保留填写的建议');
      assert.equal(await dialog.getByRole('button', { name: '提交评教', exact: true }).isDisabled(), true);
      const bounds = await dialog.boundingBox();
      assert.ok(bounds.width <= width && bounds.x >= 0, `dialog outside viewport: ${width}`);
      await page.screenshot({ path: path.join(screenshots, `${width}x${height}.png`) });
      submitted = true;
      await dialog.getByRole('button', { name: '核对提交结果' }).click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(writes, 1);
      if (width === 1440) {
        submitted = false;
        batchMode = true;
        await page.getByRole('button', { name: /已选中$/ }).click();
        await page.getByRole('button', { name: /全选待评教$/ }).click();
        await page.getByRole('button', { name: /批量提交/ }).click();
        await page.getByRole('button', { name: '确认提交', exact: true }).click();
        await page.locator('.ant-modal-confirm').waitFor({ state: 'hidden' });
        const progress = page.getByRole('dialog', { name: '批量评教进度' });
        if (process.env.QA_STOP_BATCH === '1') {
          await progress.getByRole('button', { name: /^终\s*止$/ }).click();
          assert.equal(typeof releaseSubmission, 'function');
          releaseSubmission();
        }
        await progress.getByRole('button', { name: /^关\s*闭$/, disabled: false }).waitFor();
        assert.equal(writes, 2);
        if (process.env.QA_STOP_BATCH !== '1') {
          assert.equal(await progress.getByText('结果待核对', { exact: true }).count(), 1);
          assert.equal(await progress.locator('.ant-progress-status-exception').count(), 1);
        }
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(JSON.stringify({ passed: true, screenshots }));
  } catch (error) {
    if (activePage && !activePage.isClosed()) {
      await activePage.screenshot({ path: path.join(screenshots, 'failure.png') });
      console.error(await activePage.locator('body').innerText());
      console.error(await activePage.locator('button').evaluateAll(elements => elements.filter(element => element.textContent.includes('提交评教')).map(element => ({ html: element.outerHTML, dialog: element.closest('[role="dialog"]')?.outerHTML.slice(0, 600), hidden: element.closest('[aria-hidden="true"]')?.outerHTML.slice(0, 300) }))));
      console.error({ screenshots });
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
