import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const build = path.resolve(process.argv[2] || path.join(root, 'frontend', 'build'));
const manifest = JSON.parse(fs.readFileSync(path.join(build, 'asset-manifest.json'), 'utf8'));
const limits = JSON.parse(fs.readFileSync(path.join(root, 'tools', 'frontend_performance_budget.json'), 'utf8'));
const normalized = value => String(value || '').replace(/^\//, '');
const javascriptFiles = [...new Set(Object.values(manifest.files || {})
  .map(normalized)
  .filter(name => /^static\/js\/.*\.js$/.test(name)))].sort();
const initialFiles = [...new Set((manifest.entrypoints || [])
  .map(normalized)
  .filter(name => name.endsWith('.js')))];
const initialSet = new Set(initialFiles);
const asyncFiles = javascriptFiles.filter(name => !initialSet.has(name));

const walk = directory => fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
  const target = path.join(directory, entry.name);
  return entry.isDirectory() ? walk(target) : [target];
});
const failures = [];
const sizesFor = relative => {
  const source = path.join(build, relative);
  const required = [source, `${source}.gz`, `${source}.br`];
  for (const file of required) {
    if (!fs.existsSync(file)) failures.push(`missing ${path.relative(build, file).replaceAll('\\', '/')}`);
  }
  return {
    raw: fs.existsSync(source) ? fs.statSync(source).size : 0,
    gzip: fs.existsSync(`${source}.gz`) ? fs.statSync(`${source}.gz`).size : 0,
    brotli: fs.existsSync(`${source}.br`) ? fs.statSync(`${source}.br`).size : 0,
  };
};
const assets = Object.fromEntries(javascriptFiles.map(file => [file, sizesFor(file)]));
const sum = (files, encoding) => files.reduce((total, file) => total + (assets[file]?.[encoding] || 0), 0);
const largest = (files, encoding) => files.reduce((current, file) => (
  !current || (assets[file]?.[encoding] || 0) > current.size
    ? { file, size: assets[file]?.[encoding] || 0 }
    : current
), null);
const metrics = {
  initial: { raw: sum(initialFiles, 'raw'), gzip: sum(initialFiles, 'gzip'), brotli: sum(initialFiles, 'brotli') },
  largest_async: { gzip: largest(asyncFiles, 'gzip'), brotli: largest(asyncFiles, 'brotli') },
  total: { raw: sum(javascriptFiles, 'raw'), gzip: sum(javascriptFiles, 'gzip'), brotli: sum(javascriptFiles, 'brotli') },
  async_chunks: asyncFiles.length,
};
const compare = (label, actual, limit) => {
  if (actual > limit) failures.push(`${label} ${actual} > ${limit}`);
};
compare('initial gzip', metrics.initial.gzip, limits.initial.gzip);
compare('initial brotli', metrics.initial.brotli, limits.initial.brotli);
compare('largest async gzip', metrics.largest_async.gzip?.size || 0, limits.largest_async.gzip);
compare('largest async brotli', metrics.largest_async.brotli?.size || 0, limits.largest_async.brotli);
compare('total gzip', metrics.total.gzip, limits.total.gzip);
compare('total brotli', metrics.total.brotli, limits.total.brotli);
compare('async chunks', metrics.async_chunks, limits.async_chunks);
const sourceMaps = walk(build).filter(file => file.endsWith('.map'));
if (sourceMaps.length) failures.push(`${sourceMaps.length} runtime source maps remain`);
if (!initialFiles.length) failures.push('asset manifest has no initial JavaScript entrypoint');
if (!asyncFiles.length) failures.push('asset manifest has no asynchronous JavaScript chunks');

process.stdout.write(`${JSON.stringify({ initial_files: initialFiles, async_files: asyncFiles, metrics, limits, source_maps: sourceMaps.length })}\n`);
if (failures.length) throw new Error(`Frontend performance budget failed: ${failures.join(', ')}`);
