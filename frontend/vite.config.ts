import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

/** `/` or `/sub/path/` (leading and trailing slash), from VITE_BASE_PATH. */
function normalizeBase(value: string | undefined): string {
  const trimmed = (value ?? '').trim().replace(/^\/+|\/+$/g, '')
  return trimmed ? `/${trimmed}/` : '/'
}

// The dev server proxies the API so the browser talks to a single origin (no CORS needed).
export default defineConfig(({ mode }) => {
  // `vite build --mode demo` = static GitHub Pages build where the backend runs in the browser (see src/demo/).
  if (mode === 'demo') {
    process.env.VITE_DEMO ??= '1'
    process.env.VITE_BASE_PATH ??= '/smart-invoice-auditor/'
  }
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:8000'
  // Sub-path hosting (e.g. GitHub Pages under /smart-invoice-auditor/): every asset URL is built from this base.
  const base = normalizeBase(env.VITE_BASE_PATH || process.env.VITE_BASE_PATH)
  const backendPaths = ['api', 'health', 'docs', 'redoc', 'openapi.json']
  const proxy = Object.fromEntries(
    backendPaths.map((path) => [
      `${base}${path}`,
      {
        target: apiTarget,
        changeOrigin: true,
        // The backend lives at the root of its own origin: drop the app's base path.
        rewrite: (url: string) => (base === '/' ? url : `/${url.slice(base.length)}`),
      },
    ]),
  )
  return {
    base,
    plugins: [react(), tailwindcss()],
    // The backend worker is an ES module (it loads Pyodide with a dynamic import()).
    worker: { format: 'es' },
    server: { port: 5173, proxy },
    preview: { port: 4173, proxy },
    test: {
      include: ['src/**/*.test.ts'],
      environment: 'node',
    },
  }
})
