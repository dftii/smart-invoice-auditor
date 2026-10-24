import { useCallback, useState } from 'react'
import { api } from './services/api'
import { useAsync } from './hooks/useAsync'
import { ToastProvider } from './components/ui/Toast'
import { Header, type HealthState } from './components/layout/Header'
import { ApiOfflineBanner } from './components/layout/ApiOfflineBanner'
import { KpiCards } from './components/dashboard/KpiCards'
import { AnalyticsRow } from './components/dashboard/AnalyticsRow'
import { InvoiceTable, type PresetRequest } from './components/invoices/InvoiceTable'
import type { StatusPreset } from './components/invoices/filters'
import { UploadZone } from './components/upload/UploadZone'
import { AuditInspector } from './components/inspector/AuditInspector'

function Dashboard() {
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<number | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [presetRequest, setPresetRequest] = useState<PresetRequest | null>(null)

  const health = useAsync(api.health, refreshKey)
  const stats = useAsync(api.stats, refreshKey)

  const bump = useCallback(() => setRefreshKey((key) => key + 1), [])

  const showPreset = useCallback((preset: StatusPreset) => {
    setPresetRequest((current) => ({ id: (current?.id ?? 0) + 1, preset }))
    document.getElementById('facturas')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  const healthState: HealthState = health.data ? 'online' : health.error ? 'offline' : 'checking'
  const mode = health.data?.extraction_mode ?? stats.data?.extraction_mode ?? null

  const retry = () => {
    health.reload()
    bump()
  }

  return (
    <div className="min-h-screen">
      <Header health={healthState} mode={mode} />

      <main className="mx-auto flex max-w-[1400px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        {healthState === 'offline' ? (
          <ApiOfflineBanner onRetry={retry} retrying={health.loading} />
        ) : (
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
              <UploadZone extractionMode={mode} onUploaded={bump} onInspect={setSelectedInvoiceId} />
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

      <AuditInspector invoiceId={selectedInvoiceId} onClose={() => setSelectedInvoiceId(null)} onChanged={bump} />
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
