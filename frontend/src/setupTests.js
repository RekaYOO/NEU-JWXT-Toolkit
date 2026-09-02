// Ant Design's responsive components subscribe to matchMedia.  JSDOM does
// not provide it, so keep the test environment equivalent to a browser
// without coupling individual page tests to Ant Design internals.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
