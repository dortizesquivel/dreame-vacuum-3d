import { build, context } from 'esbuild';
import { readFileSync, statSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const outfile = '../custom_components/dreame_vacuum_3d/frontend/dreame-vacuum-3d-card.js';

const options = {
  entryPoints: ['src/dreame-vacuum-3d-card.js'],
  bundle: true,
  format: 'esm',
  target: 'es2021',
  minify: true,
  outfile,
  // No version in the bundle: a release bumps manifest.json only, and CI checks the committed bundle against the source.
  banner: { js: '/*! dreame-vacuum-3d-card | MIT | bundles three.js (MIT) */' },
  legalComments: 'none',
};

if (process.argv.includes('--watch')) {
  const ctx = await context({ ...options, minify: false, sourcemap: 'inline' });
  await ctx.watch();
  console.log(`watching src/ → ${outfile}`);
} else {
  await build(options);
  const kb = (n) => `${(n / 1024).toFixed(0)} KB`;
  console.log(`${outfile}: ${kb(statSync(outfile).size)} (${kb(gzipSync(readFileSync(outfile)).length)} gzip)`);
}
