import { defineConfig, loadEnv, transformWithEsbuild, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

function compatibleManifest(): Plugin {
  return {
    name: 'toolbox-asset-manifest',
    generateBundle(_options, bundle) {
      const files: Record<string, string> = {};
      const entrypoints = new Set<string>();
      const collect = (fileName: string) => {
        if (entrypoints.has(fileName)) return;
        entrypoints.add(fileName);
        const entry = bundle[fileName];
        if (entry?.type === 'chunk') entry.imports.forEach(collect);
      };
      for (const [fileName, output] of Object.entries(bundle)) {
        if (fileName.endsWith('.map')) continue;
        files[fileName] = `/${fileName}`;
        if (output.type === 'chunk' && output.isEntry) {
          files['main.js'] = `/${fileName}`;
          collect(fileName);
        }
      }
      this.emitFile({
        type: 'asset',
        fileName: 'asset-manifest.json',
        source: JSON.stringify({ buildTool: 'vite', files, entrypoints: [...entrypoints] }),
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), 'REACT_APP_');
  return {
    plugins: [
      {
        name: 'existing-javascript-jsx',
        enforce: 'pre',
        async transform(source, identifier) {
          if (!/\/src\/.*\.js$/.test(identifier.replaceAll('\\', '/'))) return;
          return transformWithEsbuild(source, identifier, { loader: 'jsx', jsx: 'automatic' });
        },
      },
      react(),
      compatibleManifest(),
    ],
    define: {
      'process.env.REACT_APP_API_URL': JSON.stringify(''),
    },
    server: { host: 'localhost', port: 3000, proxy: { '/api': environment.REACT_APP_API_URL || 'http://127.0.0.1:8000' } },
    optimizeDeps: { esbuildOptions: { loader: { '.js': 'jsx' } } },
    build: {
      outDir: process.env.BUILD_PATH || 'build',
      sourcemap: process.env.NEU_KEEP_SOURCE_MAPS === '1' ? 'hidden' : false,
      minify: 'terser',
      terserOptions: { compress: { passes: 3 }, format: { comments: /@license|@preserve/ } },
      rollupOptions: {
        output: {
          hashCharacters: 'hex',
          experimentalMinChunkSize: 12000,
          entryFileNames: 'static/js/main.[hash].js',
          chunkFileNames: 'static/js/[name].[hash].chunk.js',
          assetFileNames: 'static/[ext]/[name].[hash].[ext]',
        },
      },
    },
  };
});
