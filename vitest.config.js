import { defineConfig } from 'vitest/config';

// The widgets live inside the Python package (src/pixel_patrol_deepsea/viewer) and
// are loaded by the viewer at runtime as plain ES modules - they import nothing and
// take everything they need from the ctx the viewer hands them. That is why these
// tests need no viewer checkout: a mocked ctx and a DOM is the whole environment.
export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['tests/viewer/**/*.test.js'],
  },
});
