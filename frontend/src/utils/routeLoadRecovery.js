const CHUNK_ERROR_PATTERNS = [
  /ChunkLoadError/i,
  /Loading chunk [^ ]+ failed/i,
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /error loading dynamically imported module/i,
];

const assetPath = value => {
  if (!value) return '';
  try { return new URL(value, window.location.origin).pathname; }
  catch (_error) { return String(value); }
};

export const isChunkLoadError = error => {
  const message = `${error?.name || ''} ${error?.message || error || ''}`;
  return error?.name === 'ChunkLoadError' || CHUNK_ERROR_PATTERNS.some(pattern => pattern.test(message));
};

export const currentMainAsset = (documentRef = document) => {
  const scripts = Array.from(documentRef.querySelectorAll('script[src]'));
  return assetPath(scripts.find(script => /\/static\/js\/main\.[^/]+\.js(?:$|\?)/.test(script.src))?.src);
};

export const latestMainAsset = async (fetchImpl = fetch) => {
  const response = await fetchImpl(`/asset-manifest.json?route-recovery=${Date.now()}`, {
    cache: 'no-store', credentials: 'same-origin',
  });
  if (!response.ok) throw new Error(`Asset manifest unavailable: ${response.status}`);
  const manifest = await response.json();
  return assetPath(manifest?.files?.['main.js']);
};

export const recoverFromStaleBuild = async ({
  error,
  fetchImpl = fetch,
  documentRef = document,
  storage = sessionStorage,
  reload = () => window.location.reload(),
} = {}) => {
  if (!isChunkLoadError(error)) return false;
  let latest = '';
  try { latest = await latestMainAsset(fetchImpl); }
  catch (_error) { return false; }
  const current = currentMainAsset(documentRef);
  if (!latest || !current || latest === current) return false;
  const marker = `neu-route-chunk-reload:${latest}`;
  if (storage.getItem(marker) === '1') return false;
  storage.setItem(marker, '1');
  reload();
  return true;
};
