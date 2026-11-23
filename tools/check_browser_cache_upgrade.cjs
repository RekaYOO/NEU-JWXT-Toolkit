const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const { chromium } = require(require.resolve('playwright', { paths: [frontend] }));
const { build } = require(require.resolve('esbuild', { paths: [frontend] }));
const source = 'frontend/src/resources/BrowserTimetableStore.js';
const baseline = 'c46d7856aa146e0c9b59c7120ad51b415bd9f1c6';

async function bundle(contents) {
  const result = await build({ stdin: { contents, resolveDir: path.dirname(path.join(root, source)) },
    bundle: true, write: false, globalName: 'cacheAPI', platform: 'browser' });
  return result.outputFiles[0].text;
}

async function main() {
  const old = await bundle(execFileSync('git', ['show', `${baseline}:${source}`], { cwd: root, encoding: 'utf8' }));
  const current = await bundle(await fs.readFile(path.join(root, source), 'utf8'));
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'toolbox-cache-upgrade-'));
  const server = http.createServer((request, response) => response.end('<!doctype html><title>Cache QA</title>'));
  let context;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    for (const legacy of [true, false]) {
      context = await chromium.launchPersistentContext(profile, { channel: 'chrome', headless: true });
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.addScriptTag({ content: legacy ? old : current });
      if (legacy) {
        for (const identity of ['qa-first', 'qa-second']) {
          assert.equal(await page.evaluate(identity => cacheAPI.writeBrowserTimetableCache(identity, {
            current: '2026-2027-1', terms: [{ code: '2026-2027-1' }],
            personal: [{ term_code: '2026-2027-1', courses: [{ course_name: identity }] }],
          }), identity), true);
        }
      } else {
        for (const identity of ['qa-first', 'qa-second']) {
          const saved = await page.evaluate(identity => cacheAPI.readBrowserTimetableCache(identity), identity);
          assert.deepEqual(saved.personal, [{ term_code: '2026-2027-1', courses: [{ course_name: identity }] }]);
        }
        assert.equal(await page.evaluate(() => cacheAPI.clearBrowserTimetableCache('qa-first')), true);
        const recovered = await page.evaluate(() => cacheAPI.readBrowserTimetableCache(cacheAPI.browserTimetableRecoveryIdentity()));
        assert.equal(recovered.personal[0].courses[0].course_name, 'qa-second');
        assert.deepEqual(await page.evaluate(async () => (await cacheAPI.readBrowserTimetableCache('qa-first')).personal), []);
      }
      await context.close(); context = null;
    }
    console.log(`PASS Chrome IndexedDB upgrade from ${baseline}: two accounts, browser restart, isolated clear and recovery`);
  } finally {
    if (context) await context.close();
    server.close();
    await fs.rm(profile, { recursive: true, force: true, maxRetries: 3 });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
