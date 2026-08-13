/**
 * Vitest config for the extension.
 *
 * jsdom because everything worth testing here reads the DOM: the login-wall
 * detector, the field classifier, and the adapters. The `@` alias mirrors
 * tsconfig so tests import exactly what the bundle does.
 */
import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    // Chrome APIs do not exist in jsdom; each test stubs only what it needs.
    globals: false,
  },
});
