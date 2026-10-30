// Linting for the card, the demo, the build script and the tests: ESLint's recommended rules, warnings fail CI.
import js from '@eslint/js';
import globals from 'globals';

export default [
  { ignores: ['node_modules/', 'demo/house/', 'test-results/', 'playwright-report/'] },
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser },
    },
  },
  {
    files: ['demo/*.mjs', 'test/**', 'build.mjs', 'playwright.config.js', 'eslint.config.js'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
];
