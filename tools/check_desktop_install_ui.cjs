const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');
const { chromium } = require(require.resolve('playwright', { paths: [path.resolve(__dirname, '../frontend')] }));

async function main() {
  const release = await fs.realpath(process.argv[2]);
  const manifest = JSON.parse(await fs.readFile(path.join(release, 'go-release-manifest.json')));
  const asset = manifest.assets['windows-amd64'];
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'toolbox-install-ui-'));
  const app = path.join(temp, 'app'), data = path.join(temp, 'data'), exe = path.join(app, 'toolbox.exe');
  const env = { ...process.env, NEU_JWXT_DATA_DIR: data, NEU_JWXT_MODE: 'local-single', HTTPS_PROXY: 'http://127.0.0.1:1', NO_PROXY: 'localhost,127.0.0.1' };
  let child, exited, browser;
  try {
    await fs.cp(path.join(release, asset.name.slice(0, -4)), app, { recursive: true });
    await fs.writeFile(path.join(app, 'VERSION'), '2.2.3');
    execFileSync(exe, ['--init-only'], { env, windowsHide: true });
    browser = await chromium.launch({ channel: 'chrome' });
    let previousSlot;
    for (const round of [1, 2]) {
    const id = (round === 1 ? 'a' : 'b').repeat(32), dir = path.join(data, 'updates', `download-${id}`), zip = path.join(dir, asset.name);
    await fs.mkdir(dir, { recursive: true }); await fs.copyFile(path.join(release, asset.name), zip);
    const job = { job_id: id, state: 'downloaded', path: zip, sha256: asset.sha256, bytes_total: asset.size, bytes_downloaded: asset.size };
    const db = new DatabaseSync(path.join(data, 'toolbox.db'));
    try {
      const insert = db.prepare('INSERT OR REPLACE INTO site_settings(setting_key,value) VALUES(?,?)');
      insert.run('runtime-update-job', Buffer.from(JSON.stringify(job)));
      if (round === 1) insert.run('appearance-default', Buffer.from('sakura'));
    } finally { db.close(); }
    const start = async (...prefix) => {
    const launcher = prefix[0] === 'launch';
    const args = ['--listen=127.0.0.1:0', '--tray=false', '--open-browser=false'];
    child = spawn(launcher ? path.join(app, '启动工具箱.exe') : exe, args, { cwd: app, env, windowsHide: true, stdio: 'ignore' });
    exited = once(child, 'exit');
    let origin;
    for (let attempt = 0; attempt < 100 && !origin; attempt++) {
      try { origin = JSON.parse(await fs.readFile(path.join(data, 'desktop-instance.json'))).url; } catch { await new Promise(resolve => setTimeout(resolve, 100)); }
    }
    assert(origin);
    return origin;
    };
    const origin = await start(...(round === 1 ? [] : ['launch']));
    const updateResponse = await fetch(`${origin}/api/runtime/update`);
    assert.equal(updateResponse.status, 200);
    const update = await updateResponse.json();
    assert.equal(update.capability, 'go-manual');
    assert.equal(update.can_install_on_restart, true);
    assert.equal(update.job.job_id, id);
    assert.equal(update.current_version, round === 1 ? '2.2.3' : manifest.version);
    const page = await browser.newPage();
    const auth = { is_logged_in: true, current_user: 'qa-desktop' };
    await page.route('**/api/client/bootstrap*', route => route.fulfill({ json: { auth, runtime: { profile: 'desktop' } } }));
    await page.route('**/api/status', route => route.fulfill({ json: auth }));
    await page.route(/\/api\/runtime\/update(?:\?.*)?$/, async route => {
      const current = await (await fetch(`${origin}/api/runtime/update`)).json();
      await route.fulfill({ json: { ...current, error: null, latest_version: manifest.version } });
    });
    await page.goto(`${origin}/system-settings`);
    await page.getByRole('button', { name: '检查更新' }).click();
    await page.getByRole('button', { name: '安装更新' }).click();
    await page.getByRole('dialog').getByRole('button', { name: /^取\s*消$/ }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    assert.equal((await (await fetch(`${origin}/api/runtime/update/${id}`)).json()).state, 'downloaded');
    const marker = path.join(app, '.toolbox-active-release');
    if (previousSlot) assert.equal((await fs.readFile(marker, 'utf8')).trim(), previousSlot);
    else await assert.rejects(fs.access(marker), { code: 'ENOENT' });
    await fs.appendFile(zip, Buffer.from([0]));
    await page.getByRole('button', { name: '安装更新' }).click();
    await page.getByRole('button', { name: '确认安装' }).click();
    await page.getByText('安装校验或新版启动检查失败，原启动版本未改变', { exact: true }).waitFor();
    assert.equal((await (await fetch(`${origin}/api/runtime/update/${id}`)).json()).state, 'downloaded');
    if (previousSlot) assert.equal((await fs.readFile(marker, 'utf8')).trim(), previousSlot);
    else await assert.rejects(fs.access(marker), { code: 'ENOENT' });
    await fs.copyFile(path.join(release, asset.name), zip);
    await page.getByRole('button', { name: '安装更新' }).click();
    await page.getByRole('button', { name: '确认安装' }).click();
    await page.getByText(/新版已选定。退出程序后/).waitFor();
    assert.equal((await (await fetch(`${origin}/api/runtime/update/${id}`)).json()).state, 'selected');
    const health = await (await fetch(`${origin}/api/health`)).json();
    assert.equal(health.version, round === 1 ? '2.2.3' : manifest.version);
    const slot = (await fs.readFile(path.join(app, '.toolbox-active-release'), 'utf8')).trim();
    assert.match(slot, /^\.toolbox-update-\d+$/);
    assert.notEqual(slot, previousSlot);
    if (previousSlot) await assert.rejects(fs.access(path.join(app, previousSlot, '.toolbox-active-release')), { code: 'ENOENT' });
    previousSlot = slot;
    assert((await fetch(`${origin}/api/runtime/shutdown`, { method: 'POST', headers: { 'X-NEU-Shutdown-Token': health.shutdown_token } })).ok);
    await exited;
    const launched = await start('launch');
    const active = await (await fetch(`${launched}/api/health`)).json();
    try {
      assert.equal(active.version, manifest.version);
      assert.match(await (await fetch(launched)).text(), /<div id="root"/);
      assert.equal((await (await fetch(`${launched}/api/access/status`)).json()).site_default, 'sakura');
      assert.equal((await (await fetch(`${launched}/api/runtime/update/${id}`)).json()).state, 'selected');
    } finally {
      assert((await fetch(`${launched}/api/runtime/shutdown`, { method: 'POST', headers: { 'X-NEU-Shutdown-Token': active.shutdown_token } })).ok);
      assert.equal((await exited)[0], 0);
    }
    await page.close();
    console.log(`PASS Chrome install round ${round}, launcher HTTP service ${active.version}, sibling slot ${slot}, homepage, preserved theme and selected job, graceful exit`);
    if (round === 2) {
      execFileSync(exe, ['rollback-update'], { cwd: app, env, windowsHide: true });
      await assert.rejects(fs.access(path.join(app, '.toolbox-active-release')), { code: 'ENOENT' });
      const original = await start('launch');
      const health = await (await fetch(`${original}/api/health`)).json();
      try {
        assert.equal(health.version, '2.2.3');
        assert.match(await (await fetch(original)).text(), /<div id="root"/);
        assert.equal((await (await fetch(`${original}/api/access/status`)).json()).site_default, 'sakura');
      } finally {
        assert((await fetch(`${original}/api/runtime/shutdown`, { method: 'POST', headers: { 'X-NEU-Shutdown-Token': health.shutdown_token } })).ok);
        assert.equal((await exited)[0], 0);
      }
      console.log('PASS manual rollback to original launcher service with preserved theme');
    }
    }
  } finally {
    await browser?.close(); if (child && child.exitCode === null) child.kill(); if (exited) await exited;
    await fs.rm(temp, { recursive: true, force: true, maxRetries: 3 });
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
