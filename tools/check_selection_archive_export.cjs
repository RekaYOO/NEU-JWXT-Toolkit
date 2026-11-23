const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

async function main() {
  const build = path.resolve(process.env.QA_FRONTEND_BUILD || 'frontend/build');
  const archiveId = 'a'.repeat(32);
  const courses = Array.from({ length: 500 }, (_, index) => ({
    course_name: index === 0 ? '课程甲' : `课程乙${index}`,
    class_id: `class${index}`,
    course_code: `code${index}`,
    department: '历史轮次模拟教学单位',
  }));
  const archive = { archive_id: archiveId, batch_name: '验收轮次', courses };
  const browser = await chromium.launch({ channel: process.env.QA_BROWSER || 'chrome' });
  try {
    for (const width of [375, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
      const page = await context.newPage();
      await page.addInitScript(url => history.replaceState(null, '', url), `/course-selection/archive/${archiveId}`);
      let deletions = 0;
      let failRead = true;
      const archiveList = '/api/course-selection/jwxk/catalog/archives';
      await page.context().route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://localhost') return route.abort();
        if (!url.pathname.startsWith('/api/')) {
          const file = path.resolve(build, path.extname(url.pathname) ? `.${url.pathname}` : 'index.html');
          if (!file.startsWith(build + path.sep)) return route.abort();
          const contentType = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' }[path.extname(file)];
          return route.fulfill({ path: file, contentType }).catch(() => route.abort());
        }
        const auth = { is_logged_in: true, current_user: 'fixture' };
        if (url.pathname === archiveList) return route.fulfill({ json: { archives: deletions ? [] : [archive] } });
        if (url.pathname === `${archiveList}/${archiveId}`) {
          if (route.request().method() === 'DELETE') {
            deletions += 1;
            return route.fulfill({ json: { success: true } });
          }
          if (deletions) return route.fulfill({ status: 404, json: { detail: '课程备份不存在或已经删除' } });
          if (failRead) {
            failRead = false;
            return route.fulfill({ status: 503, json: { detail: '归档暂时无法读取' } });
          }
        }
        const responses = { '/api/client/bootstrap': { auth, runtime: { profile: 'development' } }, '/api/status': auth, '/api/access/status': { required: false, authenticated: true }, [`/api/course-selection/jwxk/catalog/archives/${archiveId}`]: archive };
        return route.fulfill({ json: responses[url.pathname] || {} });
      });
      await page.goto('http://localhost');
      await page.getByText('归档暂时无法读取', { exact: true }).waitFor();
      assert.equal(await page.getByText('课程备份不存在或已经删除', { exact: true }).count(), 0);
      const initialNotice = page.getByRole('button', { name: '已知晓', exact: true });
      if (await initialNotice.isVisible()) await initialNotice.click();
      await page.getByRole('button', { name: /重\s*试/ }).click();
      const notice = page.getByRole('button', { name: '已知晓', exact: true });
      const search = page.getByPlaceholder('输入课程名称、代码、教师或单位');
      await notice.or(search).first().waitFor();
      if (await notice.isVisible()) await notice.click();
      await search.fill('课程甲');
      await search.press('Enter');
      await page.getByText(/当前筛出 1 个/).waitFor();
      const pending = page.waitForEvent('download');
      await page.getByRole('button', { name: '导出完整轮次 JSON' }).click();
      const download = await pending;
      assert.equal(download.suggestedFilename(), `选课历史_${archiveId}.json`);
      assert.deepEqual(JSON.parse(await fs.readFile(await download.path(), 'utf8')), archive);
      await page.evaluate(() => {
        window.archiveChunks = [];
        window.NeuNative = { saveFile(name, type, value) {
          if (value === '@begin') return 'fixture-token';
          if (name === 'fixture-token' && value.startsWith('@chunk:')) {
            window.archiveChunks.push(value.slice(7));
            return 'ok';
          }
          if (value === '@native:fixture-token') window.archiveSave = { name, type };
        } };
      });
      await page.getByRole('button', { name: '导出完整轮次 JSON' }).click();
      await page.waitForFunction(() => Boolean(window.archiveSave));
      const native = await page.evaluate(() => ({ ...window.archiveSave, chunks: window.archiveChunks }));
      assert.equal(native.name, download.suggestedFilename());
      assert.equal(native.type, 'application/json');
      assert.ok(native.chunks.length > 1);
      assert.deepEqual(JSON.parse(Buffer.concat(native.chunks.map(chunk => Buffer.from(chunk, 'base64'))).toString('utf8')), archive);
      console.log(`PASS complete archive after filtering at ${width}px`);
      const listPage = await page.context().newPage();
      await listPage.goto('http://localhost/course-selection');
      await listPage.locator('.course-selection-archive-card').getByRole('button', { name: /删\s*除/ }).click();
      await listPage.getByRole('button', { name: '删除备份' }).click();
      await listPage.locator('.course-selection-archive-card').waitFor({ state: 'hidden' });
      assert.equal(deletions, 1);
      await page.reload();
      await page.getByText('课程备份不存在或已经删除', { exact: true }).first().waitFor();
      assert.equal(await page.getByRole('button', { name: '导出完整轮次 JSON' }).count(), 0);
      await listPage.close();
      await context.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
