import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const build = path.resolve(process.argv[2] || path.join(root, 'frontend', 'build'));
const jsDir = path.join(build, 'static', 'js');
const mainFiles = fs.readdirSync(jsDir).filter(name => /^main\.[0-9a-f]+\.js$/.test(name));
if (mainFiles.length !== 1) throw new Error(`Expected one CRA main bundle, found ${mainFiles.length}`);

const main = path.join(jsDir, mainFiles[0]);
const gzipFile = `${main}.gz`;
const brotliFile = `${main}.br`;
for (const required of [gzipFile, brotliFile]) {
  if (!fs.existsSync(required)) throw new Error(`Missing precompressed asset: ${required}`);
}

const walk = directory => fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
  const target = path.join(directory, entry.name);
  return entry.isDirectory() ? walk(target) : [target];
});
const sourceMaps = walk(build).filter(file => file.endsWith('.map'));
const sizes = {
  raw: fs.statSync(main).size,
  gzip: fs.statSync(gzipFile).size,
  brotli: fs.statSync(brotliFile).size,
};
const limits = {
  // Rebased after the shared timetable grid and built-in multi-campus schedule
  // shipped. Keep a bounded ~1.6 KiB headroom above the current CI artifact;
  // future growth still requires an explicit review.
  gzip: 612 * 1024,
  brotli: 500 * 1024,
};
const failures = [];
if (sizes.gzip > limits.gzip) failures.push(`gzip ${sizes.gzip} > ${limits.gzip}`);
if (sizes.brotli > limits.brotli) failures.push(`brotli ${sizes.brotli} > ${limits.brotli}`);
if (sourceMaps.length) failures.push(`${sourceMaps.length} runtime source maps remain`);

process.stdout.write(`${JSON.stringify({ main: path.basename(main), sizes, limits, source_maps: sourceMaps.length })}\n`);
if (failures.length) throw new Error(`Frontend performance budget failed: ${failures.join(', ')}`);
