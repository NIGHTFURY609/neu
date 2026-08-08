/// <reference types="vite/client" />

/**
 * `import.meta.env` has no types without this, so any use of it fails `tsc --noEmit` —
 * which `npm run build` runs before vite.
 */
interface ImportMetaEnv {
  /**
   * Overrides the API base. Leave unset in development: `vite.config.ts` proxies `/api`
   * to localhost:8000, which avoids CORS entirely.
   *
   * Do NOT put this in `.env` — vitest loads that file too, and two existing tests
   * assert on literal `/api/...` URLs. Use `.env.production` or `.env.local`.
   */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
