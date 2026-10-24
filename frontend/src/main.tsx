import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Static hosting (GitHub Pages): the backend runs in the browser. `VITE_DEMO` is a build-time constant, so a normal
// build drops this branch and the demo code (worker, boot screen) never reaches the bundle.
const DemoGate = import.meta.env.VITE_DEMO === '1' ? lazy(() => import('./demo/DemoGate')) : null

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {DemoGate ? (
      <Suspense fallback={null}>
        <DemoGate>
          <App />
        </DemoGate>
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
)
