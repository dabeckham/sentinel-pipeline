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
  const secs  = Math.max(0, Math.floor((now - sinceMs) / 1000))
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

function basename(path) {
  if (!path) return '—'
  // Strip everything up to the last / or \
  return path.split(/[/\\]/).pop() || path
}

// ── Resizable column header ───────────────────────────────────────────────────
function ResizerHandle({ onDrag }) {
  const dragging = useRef(false)
  const startX   = useRef(0)

  const onMouseDown = e => {
    e.preventDefault()
    dragging.current = true
    startX.current = e.clientX
    const onMove = ev => { if (dragging.current) onDrag(ev.clientX - startX.current); startX.current = ev.clientX }
    const onUp   = () => { dragging.current = false; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <span
      onMouseDown={onMouseDown}
      className="absolute right-0 top-0 h-full w-2 cursor-col-resize select-none flex items-center justify-center group"
    >
      <span className="w-px h-4 bg-slate-600 group-hover:bg-brand transition-colors" />
    </span>
  )
}

// ── Kill confirmation button ──────────────────────────────────────────────────
function KillButton({ jobId, onKilled }) {
  const [confirm, setConfirm] = useState(false)
  const [busy,    setBusy]    = useState(false)

  const handleKill = async () => {
    setBusy(true)
    try {
      await api.cancelJob(jobId)
      onKilled()
    } catch (err) {
      alert(`Failed to cancel job: ${err.message}`)
    } finally {
      setBusy(false)
      setConfirm(false)
    }
  }

  if (confirm) {
    return (
      <span className="flex items-center gap-1">
        <button
          onClick={handleKill}
          disabled={busy}
          className="text-xs px-2 py-0.5 bg-red-700 hover:bg-red-600 text-white rounded transition-colors disabled:opacity-50"
        >
          {busy ? '…' : 'Kill'}
        </button>
        <button
          onClick={() => setConfirm(false)}
          className="text-xs px-2 py-0.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors"
        >
          No
        </button>
      </span>
    )
  }

  return (
    <button
      onClick={e => { e.stopPropagation(); setConfirm(true) }}
      title="Cancel this job"
      className="text-xs px-2 py-0.5 border border-red-700/50 text-red-400 hover:bg-red-900/40 rounded transition-colors"
    >
      ✕ Kill
    </button>
  )
}

const PAGE_SIZE = 25

// Default column widths (px)
const DEFAULT_WIDTHS = { ID: 60, Camera: 130, File: 260, Status: 130, 'In status': 110, Created: 170, Completed: 170, Tracks: 70, Actions: 90 }

export default function Jobs() {
  const [data, setData]                 = useState(null)
  const [page, setPage]                 = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading]           = useState(true)
  const [colWidths, setColWidths]       = useState(DEFAULT_WIDTHS)
  const [statusSince, setStatusSince]   = useState({})
  const prevStatusRef = useRef({})
  const pollRef  = useRef(null)
  const loadRef  = useRef(null)
  const dataRef  = useRef(null)

  const load = useCallback(async () => {
    try {
      const res = await api.jobs(page, PAGE_SIZE, statusFilter)
      dataRef.current = res
      setData(res)
      setStatusSince(prev => {
        const next = { ...prev }
        for (const job of res.items) {
          const lastStatus = prevStatusRef.current[job.id]
          if (lastStatus === undefined) {
            if (!(job.id in next)) next[job.id] = new Date(job.created_at).getTime()
          } else if (lastStatus !== job.status) {
            next[job.id] = Date.now()
          }
          prevStatusRef.current[job.id] = job.status
        }
        return next
      })
    } finally {
      setLoading(false)
    }
  }, [page, statusFilter])

  loadRef.current = load

  useEffect(() => {
    loadRef.current()
    const tick = () => {
      const hasActive = dataRef.current?.items?.some(j => ACTIVE_STATUSES.has(j.status))
      pollRef.current = setTimeout(async () => { await loadRef.current(); tick() }, hasActive ? 2000 : 8000)
    }
    tick()
    return () => clearTimeout(pollRef.current)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { loadRef.current() }, [page, statusFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1
  const hasActive  = data?.items?.some(j => ACTIVE_STATUSES.has(j.status))

  const resizeCol = (col, delta) => {
    setColWidths(prev => ({ ...prev, [col]: Math.max(50, (prev[col] ?? 120) + delta) }))
  }

  const cols = ['ID', 'Camera', 'File', 'Status', 'In status', 'Created', 'Completed', 'Tracks', 'Actions']

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white">Jobs</h2>
          {data && (
            <p className="text-slate-400 text-sm mt-1">
              {data.total} total
              {hasActive && <span className="ml-2 text-yellow-400 animate-pulse">● processing</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <select
            value={statusFilter}
            onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
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
            onClick={load}
            className="bg-slate-700 hover:bg-slate-600 text-white text-sm px-3 py-1.5 rounded-md transition-colors"
          >
            Refresh
          </button>
          <button
            onClick={() => setColWidths(DEFAULT_WIDTHS)}
            className="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-400 text-xs px-2.5 py-1.5 rounded-md transition-colors"
            title="Reset column widths"
          >
            Reset cols
          </button>
        </div>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-x-auto">
        <table className="text-sm" style={{ tableLayout: 'fixed', width: `${Object.values(colWidths).reduce((a, b) => a + b, 0)}px`, minWidth: '100%' }}>
          <colgroup>
            {cols.map(c => <col key={c} style={{ width: `${colWidths[c] ?? 120}px` }} />)}
          </colgroup>
          <thead>
            <tr className="border-b border-slate-700">
              {cols.map(h => (
                <th key={h} className="relative text-left text-slate-400 font-medium px-4 py-3 select-none">
                  {h}
                  <ResizerHandle onDrag={delta => resizeCol(h, delta)} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && !data && (
              <tr><td colSpan={cols.length} className="text-center text-slate-400 py-8">Loading…</td></tr>
            )}
            {!loading && data?.items?.length === 0 && (
              <tr><td colSpan={cols.length} className="text-center text-slate-400 py-8">No jobs found</td></tr>
            )}
            {data?.items?.map(job => {
              const active = ACTIVE_STATUSES.has(job.status)
              const done   = DONE_STATUSES.has(job.status)
              const since  = statusSince[job.id]
              return (
                <tr
                  key={job.id}
                  className={`border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors ${active ? 'bg-slate-700/10' : ''}`}
                >
                  <td className="px-4 py-3 text-slate-400 font-mono">{job.id}</td>
                  <td className="px-4 py-3 text-slate-300 truncate" title={job.camera_name}>
                    {job.camera_name ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-200 truncate" title={job.file_path}>
                    {basename(job.file_path)}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} pulse={active} />
                  </td>
                  <td className="px-4 py-3">
                    {!done && since ? <ElapsedTimer sinceMs={since} /> : null}
                  </td>
                  <td className="px-4 py-3 text-slate-400 truncate">{fmt(job.created_at)}</td>
                  <td className="px-4 py-3 text-slate-400 truncate">{fmt(job.completed_at)}</td>
                  <td className="px-4 py-3 text-slate-400 tabular-nums">{job.track_count ?? '—'}</td>
                  <td className="px-4 py-3">
                    {active && (
                      <KillButton jobId={job.id} onKilled={load} />
                    )}
                    {job.status === 'failed' && job.error_message && (
                      <span className="text-xs text-red-400 truncate block" title={job.error_message}>
                        {job.error_message.length > 20 ? job.error_message.slice(0, 20) + '…' : job.error_message}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-md disabled:opacity-40 hover:bg-slate-700 transition-colors"
          >
            ← Prev
          </button>
          <span className="text-slate-400 text-sm">Page {page} / {totalPages}</span>
          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
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
