import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../api.js'

const STATUS_COLORS = {
  completed:         'bg-green-900/50 text-green-300 border-green-700',
  oc_processing:     'bg-blue-900/50 text-blue-300 border-blue-700',
  md_processing:     'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  queued:            'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  motion_processing: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  failed:            'bg-red-900/50 text-red-300 border-red-700',
  dead_letter:       'bg-red-900/50 text-red-300 border-red-700',
}

const ACTIVE_STATUSES = new Set(['queued', 'md_processing', 'motion_processing', 'oc_processing'])
const DONE_STATUSES   = new Set(['completed', 'failed', 'dead_letter'])

function StatusBadge({ status, pulse }) {
  const cls = STATUS_COLORS[status] || 'bg-slate-700 text-slate-300 border-slate-600'
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded border ${cls}`}>
      {pulse && <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />}
      {status}
    </span>
  )
}

function ElapsedTimer({ sinceMs }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const secs  = Math.floor((now - sinceMs) / 1000)
  const h     = Math.floor(secs / 3600)
  const m     = Math.floor((secs % 3600) / 60)
  const s     = secs % 60
  const label = h > 0
    ? `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`
    : m > 0
      ? `${m}m ${String(s).padStart(2, '0')}s`
      : `${s}s`
  return <span className="text-slate-500 text-xs tabular-nums">{label}</span>
}

function fmt(dt) {
  if (!dt) return '—'
  return new Date(dt).toLocaleString()
}

function WsIndicator({ connected }) {
  return (
    <span className={`flex items-center gap-1.5 text-xs ${connected ? 'text-green-400' : 'text-slate-500'}`}>
      <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400 animate-pulse' : 'bg-slate-600'}`} />
      {connected ? 'Live' : 'Polling'}
    </span>
  )
}

const PAGE_SIZE = 25

export default function Jobs() {
  const [data, setData]                   = useState(null)
  const [page, setPage]                   = useState(1)
  const [statusFilter, setStatusFilter]   = useState('')
  const [loading, setLoading]             = useState(true)
  const [wsConnected, setWsConnected]     = useState(false)
  // job_id → timestamp (ms) of last status change
  const [statusSince, setStatusSince]     = useState({})
  const wsRef   = useRef(null)
  const pollRef = useRef(null)

  // ── Data fetching ────────────────────────────────────────────────────────────
  const load = useCallback(async (p = page, s = statusFilter) => {
    setLoading(true)
    try {
      const res = await api.jobs(p, PAGE_SIZE, s)
      setData(res)
      // Seed statusSince for newly loaded jobs: use created_at as baseline
      setStatusSince((prev) => {
        const next = { ...prev }
        for (const job of res.items) {
          if (!(job.id in next)) {
            next[job.id] = new Date(job.created_at).getTime()
          }
        }
        return next
      })
    } finally {
      setLoading(false)
    }
  }, [page, statusFilter])

  // ── Patch a single job row in state (from WS event) ──────────────────────────
  const patchJob = useCallback((event) => {
    setData((prev) => {
      if (!prev) return prev
      const idx = prev.items.findIndex((j) => j.id === event.job_id)
      if (idx === -1) {
        load()
        return prev
      }
      const items = [...prev.items]
      const changed = items[idx].status !== event.status
      items[idx] = {
        ...items[idx],
        status: event.status,
        ...(event.completed_at ? { completed_at: event.completed_at } : {}),
        ...(event.file_path    ? { filename: event.file_path }        : {}),
      }
      if (changed) {
        setStatusSince((prev2) => ({ ...prev2, [event.job_id]: Date.now() }))
      }
      return { ...prev, items }
    })
  }, [load])

  // ── WebSocket connection ──────────────────────────────────────────────────────
  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const token = localStorage.getItem('sentinel_token')
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/jobs?token=${token}`)

    ws.onopen = () => {
      setWsConnected(true)
      clearInterval(pollRef.current)
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'job_update') patchJob(msg)
        if (msg.type === 'heartbeat') ws.send('ping')
      } catch (_) {}
    }

    ws.onclose = () => {
      setWsConnected(false)
      wsRef.current = null
      startPolling()
      setTimeout(connectWs, 5000)
    }

    ws.onerror = () => ws.close()
    wsRef.current = ws
  }, [patchJob])

  // ── Polling fallback ─────────────────────────────────────────────────────────
  const startPolling = useCallback(() => {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(() => load(), 5000)
  }, [load])

  // ── Mount / unmount ──────────────────────────────────────────────────────────
  useEffect(() => {
    load()
    connectWs()
    const slowPoll = setInterval(() => load(), 10000)
    return () => {
      clearInterval(slowPoll)
      clearInterval(pollRef.current)
      wsRef.current?.close()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load() }, [page, statusFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white">Jobs</h2>
          {data && <p className="text-slate-400 text-sm mt-1">{data.total} total</p>}
        </div>
        <div className="flex items-center gap-3">
          <WsIndicator connected={wsConnected} />
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
            className="bg-slate-800 border border-slate-600 text-slate-300 text-sm rounded-md px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
          >
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="md_processing">MD Processing</option>
            <option value="oc_processing">OC Processing</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          <button
            onClick={() => load()}
            className="bg-slate-700 hover:bg-slate-600 text-white text-sm px-3 py-1.5 rounded-md transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700">
              {['ID', 'File', 'Status', 'In status', 'Created', 'Completed', 'Tracks'].map((h) => (
                <th key={h} className="text-left text-slate-400 font-medium px-4 py-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && !data && (
              <tr>
                <td colSpan={7} className="text-center text-slate-400 py-8">Loading…</td>
              </tr>
            )}
            {!loading && data?.items?.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-slate-400 py-8">No jobs found</td>
              </tr>
            )}
            {data?.items?.map((job) => {
              const active = ACTIVE_STATUSES.has(job.status)
              const done   = DONE_STATUSES.has(job.status)
              const since  = statusSince[job.id]
              return (
                <tr
                  key={job.id}
                  className={`border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors ${active ? 'bg-slate-700/10' : ''}`}
                >
                  <td className="px-4 py-3 text-slate-400 font-mono">{job.id}</td>
                  <td className="px-4 py-3 text-slate-200 max-w-xs truncate" title={job.filename}>
                    {job.filename?.split('/').pop() || job.filename}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} pulse={active} />
                  </td>
                  <td className="px-4 py-3">
                    {!done && since ? <ElapsedTimer sinceMs={since} /> : null}
                  </td>
                  <td className="px-4 py-3 text-slate-400">{fmt(job.created_at)}</td>
                  <td className="px-4 py-3 text-slate-400">{fmt(job.completed_at)}</td>
                  <td className="px-4 py-3 text-slate-400">{job.track_count ?? '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-md disabled:opacity-40 hover:bg-slate-700 transition-colors"
          >
            ← Prev
          </button>
          <span className="text-slate-400 text-sm">Page {page} / {totalPages}</span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-md disabled:opacity-40 hover:bg-slate-700 transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
