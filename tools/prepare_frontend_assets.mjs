import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { brotliCompressSync, constants, gzipSync } from 'node:zlib';

const suffixes = new Set(['.css', '.html', '.js', '.json', '.svg', '.txt', '.webmanifest']);
const minimumSize = 1024;
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const argumentsList = process.argv.slice(2);
const stripFlag = argumentsList.includes('--strip-source-maps');
const buildArgument = argumentsList.find(value => value !== '--strip-source-maps');
const defaultBuildDir = process.env.BUILD_PATH
  ? path.resolve(process.cwd(), process.env.BUILD_PATH)
  : path.join(scriptDir, '..', 'frontend', 'build');
const buildDir = path.resolve(buildArgument || defaultBuildDir);
const keepMaps = process.env.NEU_KEEP_SOURCE_MAPS === '1';
const retrySignal = new Int32Array(new SharedArrayBuffer(4));

if (!fs.existsSync(path.join(buildDir, 'index.html')) || !fs.existsSync(path.join(buildDir, 'static'))) {
  throw new Error(`Incomplete frontend build: ${buildDir}`);
}

const replaceBuildReference = (oldName, newName) => {
  for (const relative of ['index.html', 'asset-manifest.json']) {
    const target = path.join(buildDir, relative);
    if (!fs.existsSync(target)) continue;
    const previous = fs.readFileSync(target, 'utf8');
    const next = previous.split(oldName).join(newName);
    if (next !== previous) fs.writeFileSync(target, next);
  }
};

const retryFileOperation = operation => {
  let lastError;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      return operation();
    } catch (error) {
      lastError = error;
      if (!['EBUSY', 'EPERM', 'EACCES'].includes(error?.code)) throw error;
      Atomics.wait(retrySignal, 0, 0, 75 * (attempt + 1));
    }
  }
  throw lastError;
};

const optimizeMainBundle = async () => {
  const jsDir = path.join(buildDir, 'static', 'js');
  if (!fs.existsSync(jsDir)) return 0;
  const mainFiles = fs.readdirSync(jsDir).filter(name => /^main\.[0-9a-f]+\.js$/.test(name));
  if (mainFiles.length !== 1) {
    throw new Error(`Expected one CRA main bundle, found ${mainFiles.length}`);
  }
  const oldName = mainFiles[0];
  const oldPath = path.join(jsDir, oldName);
  const original = fs.readFileSync(oldPath, 'utf8');
  if (original.startsWith('/*! NEU optimized build;')) return 0;

  const frontendDir = path.resolve(scriptDir, '..', 'frontend');
  const requireFromFrontend = createRequire(path.join(frontendDir, 'package.json'));
  const { minify } = requireFromFrontend('terser');
  const oldMapPath = `${oldPath}.map`;
  const hasMap = fs.existsSync(oldMapPath);
  const code = original
    .replace(/^\/\*![\s\S]*?\*\/\s*/, '')
    .replace(/\s*\/\/# sourceMappingURL=[^\r\n]+\s*$/, '');
  const result = await minify({ [oldName]: code }, {
    compress: { passes: 3 },
    ecma: 5,
    mangle: true,
    format: { comments: false },
    sourceMap: hasMap ? {
      asObject: true,
      content: JSON.parse(fs.readFileSync(oldMapPath, 'utf8')),
    } : undefined,
  });
  if (!result.code) throw new Error(`Terser produced no output for ${oldName}`);

  const hash = createHash('sha256').update(result.code).digest('hex').slice(0, 8);
  const newName = `main.${hash}.js`;
  const newPath = path.join(jsDir, newName);
  const banner = `/*! NEU optimized build; license information: ${newName}.LICENSE.txt */\n`;
  const oldLicense = `${oldPath}.LICENSE.txt`;
  const newLicense = `${newPath}.LICENSE.txt`;
  fs.writeFileSync(newPath, `${banner}${result.code}\n`);
  if (fs.existsSync(oldLicense) && oldLicense !== newLicense) {
    fs.copyFileSync(oldLicense, newLicense);
  }
  if (hasMap) {
    const outputMap = typeof result.map === 'string' ? JSON.parse(result.map) : result.map;
    outputMap.file = newName;
    fs.writeFileSync(`${newPath}.map`, JSON.stringify(outputMap));
  }
  if (newPath !== oldPath) {
    try {
      retryFileOperation(() => fs.unlinkSync(oldPath));
    } catch (error) {
      for (const created of [newPath, newLicense, `${newPath}.map`]) {
        if (fs.existsSync(created)) retryFileOperation(() => fs.unlinkSync(created));
      }
      throw error;
    }
    if (fs.existsSync(oldLicense)) retryFileOperation(() => fs.unlinkSync(oldLicense));
    if (hasMap && fs.existsSync(oldMapPath)) retryFileOperation(() => fs.unlinkSync(oldMapPath));
  }
  replaceBuildReference(oldName, newName);
  return 1;
};

const optimized = await optimizeMainBundle();

const files = [];
const walk = directory => {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(target);
    else if (entry.isFile()) files.push(target);
  }
};
walk(buildDir);

let compressed = 0;
let mapsRemoved = 0;
for (const file of files.sort()) {
  if (file.endsWith('.br') || file.endsWith('.gz')) continue;
  if (file.endsWith('.map') && (stripFlag || !keepMaps)) {
    fs.unlinkSync(file);
    mapsRemoved += 1;
    continue;
  }
  if (!suffixes.has(path.extname(file).toLowerCase()) || fs.statSync(file).size < minimumSize) continue;
  const data = fs.readFileSync(file);
  fs.writeFileSync(`${file}.gz`, gzipSync(data, { level: 9, mtime: 0 }));
  fs.writeFileSync(`${file}.br`, brotliCompressSync(data, {
    params: {
      [constants.BROTLI_PARAM_MODE]: constants.BROTLI_MODE_TEXT,
      [constants.BROTLI_PARAM_QUALITY]: 11,
    },
  }));
  compressed += 1;
}

process.stdout.write(`Prepared frontend assets: ${optimized} optimized, ${compressed} compressed, ${mapsRemoved} source maps removed\n`);
