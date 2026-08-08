import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  // `loadEnv` reads both .env files and the launcher-provided environment without
  // making this TypeScript config depend on Node's global `process` type.
  const env = loadEnv(mode, '.', '');

  return {
    plugins: [react()],
    server: {
      port: Number(env.FRONTEND_PORT ?? 5173),
      // Same-origin in dev, so no CORS preflight on every request and no base URL to
      // configure. `uvicorn app.api:app --reload` serves the other side.
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET ?? 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./vitest.setup.ts'],
    },
  };
});
