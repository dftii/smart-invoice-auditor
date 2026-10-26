import { lazy, Suspense, useCallback, useState } from 'react'
import { api } from './services/api'
import { useAsync } from './hooks/useAsync'
import { ToastProvider } from './components/ui/Toast'
import { Header } from './components/layout/Header'
import { ApiOfflineBanner } from './components/layout/ApiOfflineBanner'
import { KpiCards } from './components/dashboard/KpiCards'
import { AnalyticsRow } from './components/dashboard/AnalyticsRow'
import { InvoiceTable, type PresetRequest } from './components/invoices/InvoiceTable'
import type { StatusPreset } from './components/invoices/filters'
import { UploadZone } from './components/upload/UploadZone'
import { useHealth } from './hooks/useHealth'
import { DEFAULT_MAX_UPLOAD_MB } from './lib/config'
import { useAiKey } from './demo/aiKey'

const IS_DEMO = import.meta.env.VITE_DEMO === '1'
// In-browser demo only (build-time constant: normal builds drop it): use Claude with the visitor's own API key.
const AiKeyPanel = IS_DEMO ? lazy(() => import('./demo/AiKeyPanel')) : null

// The inspector is only needed once an invoice is opened.
const AuditInspector = lazy(() =>
  import('./components/inspector/AuditInspector').then((module) => ({ default: module.AuditInspector })),
)

function Dashboard() {
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<number | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [presetRequest, setPresetRequest] = useState<PresetRequest | null>(null)

  const bump = useCallback(() => setRefreshKey((key) => key + 1), [])
  // Header dot and banner follow the latest health result; data is reloaded when the API comes back.
  const health = useHealth(bump)
  const stats = useAsync(api.stats, refreshKey)

  const showPreset = useCallback((preset: StatusPreset) => {
    setPresetRequest((current) => ({ id: (current?.id ?? 0) + 1, preset }))
    document.getElementById('facturas')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  const demoAiKey = useAiKey()
  // The demo backend always reports DEMO; with the visitor's key its uploads go through Claude.
  const mode = IS_DEMO && demoAiKey ? 'AI' : (health.data?.extraction_mode ?? stats.data?.extraction_mode ?? null)
  const offline = health.status === 'offline'
  // Before the first successful check there is nothing to show; afterwards an outage only adds a banner.
  const blocked = offline && !health.everOnline

  // After deleting an invoice its row disappears: move focus to the table heading instead of losing it.
  const focusInvoices = useCallback(() => {
    window.setTimeout(() => document.getElementById('invoices-title')?.focus(), 0)
  }, [])

  return (
    <div className="min-h-screen">
      <Header health={health.status} mode={mode} />

      <main className="mx-auto flex max-w-[1400px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        {offline && <ApiOfflineBanner blocking={blocked} onRetry={health.recheck} retrying={health.checking} />}
        {!blocked && (
          <>
            <KpiCards stats={stats.data} error={stats.error} onRetry={stats.reload} onFilter={showPreset} />

            <section aria-labelledby="upload-title" className="space-y-3">
              <div>
                <h2 id="upload-title" className="text-base font-semibold text-slate-900">
                  Subir facturas
                </h2>
                <p className="text-sm text-slate-500">
                  Extracción automática de los datos y auditoría fiscal al instante.
                </p>
              </div>
              {AiKeyPanel && (
                <Suspense fallback={null}>
                  <AiKeyPanel />
                </Suspense>
              )}
              <UploadZone
                extractionMode={mode}
                maxUploadMb={health.data?.max_upload_size_mb ?? DEFAULT_MAX_UPLOAD_MB}
                onUploaded={bump}
                onMaybeCreated={bump}
                onInspect={setSelectedInvoiceId}
              />
            </section>

            <AnalyticsRow stats={stats.data} onFilter={showPreset} />

            <InvoiceTable
              selectedId={selectedInvoiceId}
              refreshKey={refreshKey}
              presetRequest={presetRequest}
              onSelect={setSelectedInvoiceId}
            />
          </>
        )}
      </main>

      {selectedInvoiceId !== null && (
        <Suspense fallback={null}>
          <AuditInspector
            invoiceId={selectedInvoiceId}
            onClose={() => setSelectedInvoiceId(null)}
            onChanged={bump}
            onDeleted={focusInvoices}
          />
        </Suspense>
      )}
    </div>
  )
}

export default function App() {
  return (
    <ToastProvider>
      <Dashboard />
    </ToastProvider>
  )
}
