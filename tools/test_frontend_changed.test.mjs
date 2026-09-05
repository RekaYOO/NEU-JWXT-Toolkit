import assert from 'node:assert/strict';
import test from 'node:test';
import { parseArgs, selectTests } from './test_frontend_changed.mjs';

test('source, style and new test files use dependency selection without duplicates', () => {
  const files = [
    'frontend/src/pages/TimetablePage.js',
    'frontend/src/pages/TimetablePage.css',
    'frontend/src/pages/TimetablePage.test.js',
    'frontend/src/pages/TimetablePage.js',
  ];
  assert.deepEqual(selectTests(files, () => true), {
    mode: 'related', files: [...new Set(files)].map(file => file.slice(9)),
  });
});

test('shared, configuration, backend and unknown changes never silently skip regression', () => {
  for (const file of [
    'frontend/package-lock.json', 'frontend/package.json', 'frontend/.env.test',
    'frontend/src/setupTests.js', 'frontend/src/App.js', 'frontend/src/index.js',
    'frontend/src/services/api.js', 'frontend/src/resources/ResourceStore.js',
    'backend/app/routers/auth.py', '.github/workflows/ci.yml',
    'tools/test_frontend_changed.mjs', 'frontend/public/index.html',
  ]) {
    assert.equal(selectTests([file], () => true).mode, 'full', file);
  }
});

test('deleted or renamed source requires full regression', () => {
  assert.equal(selectTests(['frontend/src/old.js', 'frontend/src/new.js'],
    file => file.endsWith('/new.js')).mode, 'full');
});

test('documentation is skipped but does not hide mixed executable changes', () => {
  assert.equal(selectTests(['README.md', 'docs/开发/前端开发.md']).mode, 'none');
  assert.equal(selectTests([], () => true).mode, 'none');
  assert.equal(selectTests(['README.md', 'frontend/package.json'], () => true).mode, 'full');
});

test('accepts explicit comparison base and preview without accepting unknown arguments', () => {
  assert.deepEqual(parseArgs(['--since', 'origin/main', '--dry-run']),
    { since: 'origin/main', dryRun: true });
  for (const args of [['--since'], ['--since', '--dry-run'], ['--passWithNoTests']]) {
    assert.throws(() => parseArgs(args), /Usage:/);
  }
});
