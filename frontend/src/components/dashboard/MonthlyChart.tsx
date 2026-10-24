import { useMemo, useState } from 'react'
import type { MonthlyTotal } from '../../types/api'
import { formatEur, formatMonth } from '../../lib/format'
import { toNumber } from '../../lib/decimal'
import { useElementWidth } from '../../hooks/useElementWidth'
import { formatCompactEur, lastMonths, niceTicks } from './chartMath'
import { ChartCard } from './ChartCard'

const HEIGHT = 250
const MARGIN = { top: 14, right: 8, bottom: 30, left: 56 }
const TOOLTIP = { width: 150, height: 44 }

const longMonth = new Intl.DateTimeFormat('es-ES', { month: 'long', year: 'numeric' })

function longMonthLabel(month: string): string {
  const [year, number] = month.split('-').map(Number)
  return longMonth.format(new Date(year, number - 1, 1))
}

interface MonthlyChartProps {
  monthly: MonthlyTotal[]
}

export function MonthlyChart({ monthly }: MonthlyChartProps) {
  const { ref, width } = useElementWidth<HTMLDivElement>()
  const [active, setActive] = useState<number | null>(null)

  const months = useMemo(() => lastMonths(monthly), [monthly])
  const values = months.map((item) => toNumber(item.total_amount))
  const hasData = values.some((value) => value > 0)
  const ticks = niceTicks(Math.max(...values))

  const innerWidth = Math.max(1, width - MARGIN.left - MARGIN.right)
  const innerHeight = HEIGHT - MARGIN.top - MARGIN.bottom
  const band = innerWidth / months.length
  const barWidth = Math.min(36, band * 0.62)
  const y = (value: number) => MARGIN.top + innerHeight - (value / ticks.top) * innerHeight
  const labelEvery = band < 38 ? 2 : 1

  const activeItem = active === null ? null : months[active]
  let tooltipX = 0
  let tooltipY = 0
  if (active !== null) {
    const center = MARGIN.left + band * active + band / 2
    tooltipX = Math.min(Math.max(center - TOOLTIP.width / 2, MARGIN.left), width - MARGIN.right - TOOLTIP.width)
    tooltipY = Math.max(0, y(values[active]) - TOOLTIP.height - 8)
  }

  return (
    <ChartCard title="Facturación mensual" description="Total facturado por mes de emisión, últimos 12 meses">
      <div ref={ref} className="relative">
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label="Gráfico de barras de la facturación mensual de los últimos 12 meses"
          className="block overflow-visible"
          onMouseLeave={() => setActive(null)}
        >
          {ticks.values.map((tick) => (
            <g key={tick}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(tick)}
                y2={y(tick)}
                className={tick === 0 ? 'stroke-slate-300' : 'stroke-slate-100'}
              />
              <text x={MARGIN.left - 8} y={y(tick)} textAnchor="end" dominantBaseline="middle" className="fill-slate-500 text-[11px]">
                {formatCompactEur(tick)}
              </text>
            </g>
          ))}

          {months.map((item, index) => {
            const value = values[index]
            const x = MARGIN.left + band * index + (band - barWidth) / 2
            const barHeight = Math.max(value > 0 ? 2 : 0, (value / ticks.top) * innerHeight)
            const isActive = active === index
            return (
              <g
                key={item.month}
                tabIndex={0}
                role="img"
                aria-label={`${longMonthLabel(item.month)}: ${formatEur(item.total_amount)}, ${item.invoice_count} facturas`}
                className="outline-none"
                onMouseEnter={() => setActive(index)}
                onFocus={() => setActive(index)}
                onBlur={() => setActive(null)}
              >
                <title>{`${longMonthLabel(item.month)}: ${formatEur(item.total_amount)} (${item.invoice_count} ${item.invoice_count === 1 ? 'factura' : 'facturas'})`}</title>
                {/* Wide transparent hit area so empty months are hoverable too */}
                <rect x={MARGIN.left + band * index} y={MARGIN.top} width={band} height={innerHeight} fill="transparent" />
                {isActive && (
                  <rect x={MARGIN.left + band * index} y={MARGIN.top} width={band} height={innerHeight} className="fill-slate-900/[0.03]" />
                )}
                <rect
                  x={x}
                  y={MARGIN.top + innerHeight - barHeight}
                  width={barWidth}
                  height={barHeight}
                  rx={3}
                  className={isActive ? 'fill-indigo-500' : value > 0 ? 'fill-indigo-400' : 'fill-slate-200'}
                />
                {index % labelEvery === 0 && (
                  <text
                    x={MARGIN.left + band * index + band / 2}
                    y={HEIGHT - 10}
                    textAnchor="middle"
                    className={isActive ? 'fill-slate-900 text-[11px] font-medium' : 'fill-slate-500 text-[11px]'}
                  >
                    {formatMonth(item.month)}
                  </text>
                )}
              </g>
            )
          })}

          {activeItem && (
            <g transform={`translate(${tooltipX} ${tooltipY})`} pointerEvents="none" aria-hidden>
              <rect width={TOOLTIP.width} height={TOOLTIP.height} rx={8} className="fill-slate-900" />
              <text x={10} y={17} className="fill-slate-300 text-[11px] capitalize">
                {longMonthLabel(activeItem.month)}
              </text>
              <text x={10} y={34} className="fill-white text-[13px] font-semibold">
                {formatEur(activeItem.total_amount)}
              </text>
            </g>
          )}
        </svg>
        {!hasData && (
          <p className="pointer-events-none absolute inset-x-0 top-1/3 text-center text-sm text-slate-500">
            Sin facturas todavía. Sube un archivo o ejecuta <code className="font-mono">python scripts/seed.py</code>.
          </p>
        )}
      </div>
    </ChartCard>
  )
}
