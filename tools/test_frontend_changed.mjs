// Local iteration only. CI must continue to run the complete npm test suite.
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontend = path.join(root, 'frontend');

export function selectTests(files, exists = file => existsSync(path.join(root, file))) {
  const related = [];
  for (const file of [...new Set(files)]) {
    // Only clearly non-executable documentation can bypass Jest.
    if (/^docs\/.*\.md$/.test(file) || /^(README|AGENTS)\.md$/.test(file)) continue;
    // Deleted files and shared/runtime/config changes require full regression.
    if (!file.startsWith('frontend/src/') || !exists(file)
        || /^frontend\/src\/(setupTests\.js|index\.[jt]sx?|App\.[jt]sx?|services\/|resources\/)/.test(file)) {
      return { mode: 'full', files: [] };
    }
    related.push(file.slice('frontend/'.length));
  }
  return { mode: related.length ? 'related' : 'none', files: related };
}

export function parseArgs(args) {
  let since;
  let dryRun = false;
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--dry-run') dryRun = true;
    else if (args[i] === '--since' && args[i + 1] && !args[i + 1].startsWith('-')) {
      since = args[++i];
    } else {
      throw new Error('Usage: npm run test:changed -- [--since <git-ref>] [--dry-run]');
    }
  }
  return { since, dryRun };
}

function git(args) {
  return execFileSync('git', args, { cwd: root, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
}

export function main(args) {
  const { since, dryRun } = parseArgs(args);
  let selection;
  try {
    const base = since ? git(['merge-base', 'HEAD', since]).trim() : 'HEAD';
    const files = [
      ...git(['diff', '--name-only', '--no-renames', '-z', base, '--']).split('\0'),
      ...git(['ls-files', '--others', '--exclude-standard', '-z']).split('\0'),
    ].filter(Boolean);
    selection = selectTests(files);
  } catch {
    console.warn('Cannot determine changed files; running the full frontend suite.');
    selection = { mode: 'full', files: [] };
  }
  console.log(JSON.stringify(selection));
  if (selection.mode === 'none') {
    console.log('No executable changes found. This is not a full regression result; use --since <git-ref> for committed changes.');
  }
  if (dryRun || selection.mode === 'none') return 0;
  const script = path.join(frontend, 'node_modules/react-scripts/bin/react-scripts.js');
  const command = [script, 'test', '--watchAll=false'];
  if (selection.mode === 'full') command.push('--maxWorkers=2');
  else command.push('--runInBand', '--findRelatedTests', ...selection.files);
  // Do not pass --passWithNoTests: an uncovered change must be visible.
  const result = spawnSync(process.execPath, command, { cwd: frontend, stdio: 'inherit' });
  if (result.error) console.error(result.error.message);
  return result.status ?? 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
