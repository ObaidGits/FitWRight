/**
 * Build the extension into dist/ with esbuild.
 *
 * MV3 constraints drive the shape of this script:
 *  - Content scripts cannot be ES modules, so every entry is bundled to a
 *    self-contained IIFE. That is why each one is its own esbuild entry rather
 *    than a shared chunk graph.
 *  - The service worker CAN be a module, but bundling it too keeps one rule for
 *    everything and avoids import-path surprises after packing.
 *  - No remotely hosted code: everything ships in the zip (a Chrome Web Store
 *    hard requirement).
 *
 * Usage: `node build.mjs` (one-shot) | `node build.mjs --watch` (dev)
 */
import { cp, mkdir, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as esbuild from 'esbuild';

const root = dirname(fileURLToPath(import.meta.url));
const outdir = resolve(root, 'dist');
const watch = process.argv.includes('--watch');
const dev = watch || process.env.NODE_ENV === 'development';

/** Each MV3 entry point -> its bundled output name. */
const entries = {
  'service-worker': 'src/background/service-worker.ts',
  content: 'src/content/index.ts',
  bridge: 'src/content/bridge.ts',
  popup: 'src/popup/popup.ts',
  options: 'src/options/options.ts',
};

/** @type {import('esbuild').BuildOptions} */
const options = {
  entryPoints: Object.fromEntries(
    Object.entries(entries).map(([name, file]) => [name, resolve(root, file)]),
  ),
  outdir,
  bundle: true,
  format: 'iife',
  target: 'chrome110',
  platform: 'browser',
  sourcemap: dev ? 'inline' : false,
  minify: !dev,
  legalComments: 'none',
  logLevel: 'info',
  define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
  alias: { '@': resolve(root, 'src') },
};

async function copyStatic() {
  await mkdir(outdir, { recursive: true });
  // public/ holds the manifest, HTML shells, icons and injected CSS - all
  // referenced by the manifest, so they must land at the dist root.
  await cp(resolve(root, 'public'), outdir, { recursive: true });
}

await rm(outdir, { recursive: true, force: true });
await copyStatic();

if (watch) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
  console.log('[fitwright-extension] watching for changes...');
} else {
  await esbuild.build(options);
  console.log(`[fitwright-extension] built -> ${outdir}`);
}
