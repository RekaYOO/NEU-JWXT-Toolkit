const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

async function main() {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || 'frontend/build');
  const calendar = execFileSync(process.env.QA_GO || 'go', ['run', './tools/fixtures/exam_calendar'], {
    cwd: path.resolve(__dirname, '..'), encoding: 'utf8',
  });
  const browser = await chromium.launch({ channel: process.env.QA_BROWSER || 'chrome' });
  try {
    for (const width of [375, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const auth = { is_logged_in: true, current_user: 'fixture' };
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://localhost') return route.abort();
        if (!url.pathname.startsWith('/api/')) {
          const file = path.resolve(build, path.extname(url.pathname) ? `.${url.pathname}` : 'index.html');
          if (!file.startsWith(build + path.sep)) return route.abort();
          const contentType = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' }[path.extname(file)];
          return route.fulfill({ path: file, contentType }).catch(() => route.abort());
        }
        const term = url.searchParams.get('term_code');
        if (url.pathname === '/api/exams/export-ics') {
          assert.equal(term, 'normal');
          return route.fulfill({ contentType: 'text/calendar', body: calendar });
        }
        if (url.pathname === '/api/exams/terms') return route.fulfill({ json: { current: 'normal', terms: ['normal', 'empty', 'failed'].map(key => ({ item_code: key, item_name: key })) } });
        if (url.pathname === '/api/exams') {
          if (term === 'failed') return route.fulfill({ status: 503, json: { detail: 'fixture failure' } });
          const exams = term === 'normal' ? [{ course_name: '考试回归课程', task_id: 'one', exam_status: 0 }] : [];
          return route.fulfill({ json: { exams, total: exams.length, upcoming: exams.length } });
        }
        const bootstrap = { '/api/client/bootstrap': { runtime: { profile: 'development' }, auth }, '/api/access/status': { required: false, authenticated: true }, '/api/status': auth };
        return route.fulfill({ json: bootstrap[url.pathname] || {} });
      });
      await page.addInitScript(() => history.replaceState(null, '', '/exams'));
      await page.goto('http://localhost');
      const notice = page.getByRole('button', { name: '已知晓', exact: true });
      await notice.or(page.locator('.exam-header-actions')).first().waitFor();
      if (await notice.isVisible()) await notice.click();
      const course = page.getByText('考试回归课程', { exact: true });
      const downloadButton = page.getByRole('button', { name: /导出 ICS/ });
      await course.waitFor();
      for (const term of ['empty', 'normal', 'failed', 'normal']) {
        await page.locator('.exam-header-actions .ant-select-selector').click();
        await page.locator('.ant-select-item-option-content').getByText(term, { exact: true }).or(page.getByText(term, { exact: true }).and(page.locator('.ant-select-item-option-content'))).first().click();
        if (term === 'normal') await course.waitFor();
        else {
          await page.locator(term === 'failed' ? '.exam-page .ant-alert-error' : '.exam-page .ant-empty').waitFor();
          assert.equal(await course.count(), 0);
          assert.equal(await downloadButton.isDisabled(), true);
          if (term === 'failed') assert.equal(await page.locator('.exam-page .ant-empty').count(), 0);
        }
      }
      const downloadPromise = page.waitForEvent('download');
      await downloadButton.click();
      const download = await downloadPromise;
      assert.equal(download.suggestedFilename(), 'exams_normal.ics');
      assert.equal(await fs.readFile(await download.path(), 'utf8'), calendar);
      if (process.env.QA_CALENDAR_OUTPUT) await download.saveAs(process.env.QA_CALENDAR_OUTPUT);
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`Exam navigation and calendar download passed at ${width}px`);
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
