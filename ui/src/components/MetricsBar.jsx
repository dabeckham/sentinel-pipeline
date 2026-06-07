/**
 * MetricsBar — persistent system metrics strip shown at the bottom of the app.
 * Connects to GET /api/metrics/stream (SSE) and updates every ~2 seconds.
 * Shows CPU, RAM, GPU util, GPU VRAM, GPU temp per GPU.
 */
import { useEffect, useRef, useState } from 'react'

function pct(v) { return v != null ? `${v}%` : '—' }
function mb(v)  { return v != null ? `${Math.round(v / 1024 * 10) / 10} GB` : '—' }
function temp(v){ return v != null ? `${v}°C` : '—' }

function Gauge({ label, value, max = 100, color = 'bg-brand', warn = 75, danger = 90, unit = '%' }) {
  const pctVal = max === 100 ? value : (value / max * 100)
  const barColor = pctVal >= danger ? 'bg-red-500' : pctVal >= warn ? 'bg-amber-400' : color
  const textColor = pctVal >= danger ? 'text-red-400' : pctVal >= warn ? 'text-amber-300' : 'text-slate-300'

  return (
    <div className="flex flex-col gap-0.5 min-w-[72px]">
      <div className="flex items-center justify-between gap-1">
        <span className="text-slate-500 text-[10px] uppercase tracking-wide leading-none">{label}</span>
        <span className={`text-[11px] font-mono font-semibold leading-none ${textColor}`}>
          {value != null ? `${value}${unit}` : '—'}
        </span>
      </div>
      <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${barColor}`}
          style={{ width: `${Math.min(100, Math.max(0, pctVal ?? 0))}%` }}
        />
      </div>
    </div>
  )
}

function Divider() {
  return <div className="w-px h-8 bg-slate-700 mx-1 shrink-0" />
}

export default function MetricsBar() {
  const [metrics, setMetrics] = useState(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(false)
  const esRef = useRef(null)
  const retryRef = useRef(null)

  function connect() {
    const token = localStorage.getItem('sentinel_token')
    if (!token) return

    // EventSource doesn't support custom headers — use fetch streaming instead
    const ctrl = new AbortController()
    esRef.current = ctrl

    const doFetch = async () => {
      try {
        const res = await fetch('/api/metrics/stream', {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        })
        if (!res.ok) { setError(true); return }
        setConnected(true); setError(false)

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const lines = buf.split('\n')
          buf = lines.pop()
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try { setMetrics(JSON.parse(line.slice(6))) } catch {}
            }
          }
        }
      } catch (e) {
        if (e.name === 'AbortError') return
        setConnected(false)
        retryRef.current = setTimeout(doFetch, 5000)
      }
    }

    doFetch()
  }

  useEffect(() => {
    connect()
    return () => {
      esRef.current?.abort()
      clearTimeout(retryRef.current)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const sentinel_gpus = metrics?.gpus?.filter(g => g.index === 1) ?? []
  const all_gpus = metrics?.gpus ?? []

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 bg-slate-900/95 backdrop-blur border-t border-slate-700/60 px-4 py-1.5 flex items-center gap-3 overflow-x-auto">

      {/* Connection indicator */}
      <div className="flex items-center gap-1.5 shrink-0">
        <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green-400 animate-pulse' : error ? 'bg-red-500' : 'bg-slate-600'}`} />
        <span className="text-slate-600 text-[10px] uppercase tracking-wide">System</span>
      </div>

      <Divider />

      {/* CPU */}
      <Gauge label="CPU" value={metrics?.cpu_pct} warn={70} danger={90} />

      {/* RAM */}
      <Gauge label="RAM" value={metrics?.ram_pct} warn={80} danger={92} color="bg-violet-500" />
      {metrics && (
        <span className="text-slate-600 text-[10px] font-mono shrink-0">
          {Math.round(metrics.ram_used_mb / 1024 * 10) / 10}/{Math.round(metrics.ram_total_mb / 1024 * 10) / 10} GB
        </span>
      )}

      <Divider />

      {/* GPU stats — show all GPUs with labels */}
      {all_gpus.length === 0 && (
        <span className="text-slate-600 text-[10px]">No GPU data</span>
      )}
      {all_gpus.map(gpu => (
        <div key={gpu.index} className="flex items-center gap-3">
          <span className="text-slate-600 text-[10px] shrink-0 font-mono">
            GPU{gpu.index}{gpu.index === 1 ? ' ●sentinel' : ' ●frigate'}
          </span>
          <Gauge
            label="Util"
            value={gpu.gpu_pct}
            warn={80} danger={95}
            color={gpu.index === 1 ? 'bg-cyan-500' : 'bg-slate-500'}
          />
          <Gauge
            label="VRAM"
            value={gpu.mem_pct}
            warn={85} danger={95}
            color={gpu.index === 1 ? 'bg-cyan-600' : 'bg-slate-600'}
          />
          {gpu.mem_used_mb != null && (
            <span className="text-slate-600 text-[10px] font-mono shrink-0">
              {Math.round(gpu.mem_used_mb / 1024 * 10) / 10}/{Math.round(gpu.mem_total_mb / 1024 * 10) / 10} GB
            </span>
          )}
          <Gauge
            label="Temp"
            value={gpu.temp_c}
            max={100} warn={75} danger={88}
            color="bg-orange-500"
            unit="°"
          />
          {gpu.power_w != null && (
            <span className="text-slate-600 text-[10px] font-mono shrink-0">
              {Math.round(gpu.power_w)}W
            </span>
          )}
        </div>
      ))}

      {all_gpus.length > 0 && (
        <>
          <Divider />
          {/* Disk */}
          <Gauge label="Disk" value={metrics?.disk_pct} warn={80} danger={92} color="bg-emerald-500" />
          {metrics && (
            <span className="text-slate-600 text-[10px] font-mono shrink-0">
              {metrics.disk_used_gb}/{metrics.disk_total_gb} GB
            </span>
          )}
        </>
      )}

      {/* Spacer + timestamp */}
      <div className="ml-auto shrink-0">
        {metrics && (
          <span className="text-slate-700 text-[10px] font-mono">
            {new Date().toLocaleTimeString()}
          </span>
        )}
      </div>
    </div>
  )
}
