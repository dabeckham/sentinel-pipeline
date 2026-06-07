import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../api.js'

const STATUS_COLORS = {
  completed:         'bg-green-900/50 text-green-300 border-green-700',
  oc_processing:     'bg-blue-900/50 text-blue-300 border-blue-700',
  md_processing:     'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  md_complete:       'bg-cyan-900/50 text-cyan-300 border-cyan-700',
  queued:            'bg-slate-700/50 text-slate-300 border-slate-600',
  motion_processing: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  failed:            'bg-red-900/50 text-red-300 border-red-700',
  dead_letter:       'bg-red-900/50 text-red-300 border-red-700',
}

const ACTIVE_STATUSES = new Set(['queued', 'md_processing', 'md_complete', 'motion_processing', 'oc_processing'])
const DONE_STATUSES   = new Set(['completed', 'failed', 'dead_letter'])

// ── Stage timeline data ───────────────────────────────────────────────────────
function buildTimeline(job) {
  const stages = []
  const ms = (a, b) => a && b ? Math.max(0, Math.round((new Date(b) - new Date(a)) / 100) / 10) : null

  stages.push({ label: 'Queued',        at: job.created_at,      dur: ms(job.created_at, job.md_started_at),   color: 'text-slate-400' })
  if (job.md_started_at)
    stages.push({ label: 'MD Processing', at: job.md_started_at,   dur: ms(job.md_started_at, job.md_completed_at), color: 'text-yellow-400' })
  if (job.md_completed_at)
    stages.push({ label: 'MD Complete',   at: job.md_completed_at, dur: ms(job.md_completed_at, job.oc_started_at),  color: 'text-cyan-400' })
  if (job.oc_started_at)
    stages.push({ label: 'OC Processing', at: job.oc_started_at,   dur: ms(job.oc_started_at, job.completed_at),    color: 'text-blue-400' })
  if (job.completed_at)
    stages.push({ label: job.status === 'failed' ? 'Failed' : 'Completed', at: job.completed_at, dur: null, color: job.status === 'failed' ? 'text-red-400' : 'text-green-400' })

  const totalMs = job.created_at && job.completed_at
    ? Math.round((new Date(job.completed_at) - new Date(job.created_at)) / 100) / 10
    : null

  return { stages, totalMs }
}

function fmtSecs(s) {
  if (s === null || s === undefined) return '…'
  if (s < 60)  return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${(s % 60).toFixed(0)}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

function fmtAt(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// ── Status badge with hover timeline popover ──────────────────────────────────
function StatusBadge({ status, pulse, job }) {
  const [show, setShow] = useState(false)
  const ref = useRef(null)
  const cls = STATUS_COLORS[status] || 'bg-slate-700 text-slate-300 border-slate-600'
  const { stages, totalMs } = buildTimeline(job)

  return (
    <span
      ref={ref}
      className="relative inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded border cursor-default select-none"
      style={{ zIndex: show ? 50 : 'auto' }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <span className={`inline-flex items-center gap-1.5 ${cls} px-2 py-0.5 rounded border`}>
        {pulse && <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />}
        {status}
      </span>

      {show && stages.length > 0 && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-slate-900 border border-slate-600 rounded-xl shadow-2xl p-3 min-w-[260px] pointer-events-none">
          <p className="text-slate-400 text-xs font-semibold mb-2 uppercase tracking-wide">Job #{job.id} Timeline</p>
          <div className="space-y-1">
            {stages.map((s, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className={`font-medium w-28 shrink-0 ${s.color}`}>{s.label}</span>
                <span className="text-slate-500 font-mono">{fmtAt(s.at)}</span>
                {s.dur !== null && (
                  <span className="ml-auto text-slate-400 font-mono tabular-nums shrink-0">
                    [{fmtSecs(s.dur)}]
                  </span>
                )}
              </div>
            ))}
          </div>
          {totalMs !== null && (
            <div className="mt-2 pt-2 border-t border-slate-700 flex justify-between text-xs">
              <span className="text-slate-500">Total</span>
              <span className="text-white font-mono tabular-nums">{fmtSecs(totalMs)}</span>
            </div>
          )}
          {stages.length === 1 && (
            <p className="text-slate-600 text-xs mt-2 italic">Stage timestamps recorded on new jobs only</p>
          )}
        </div>
      )}
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
    const onMove = ev => { if (dragging.current) { onDrag(ev.clientX - startX.current); startX.current = ev.clientX } }
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
const DEFAULT_WIDTHS = { ID: 60, Camera: 130, File: 240, Status: 160, 'In status': 110, Created: 170, Completed: 170, Tracks: 70, Actions: 100 }

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
            <option value="md_complete">MD Complete</option>
            <option value="oc_processing">OC Processing</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          <button onClick={load}
            className="bg-slate-700 hover:bg-slate-600 text-white text-sm px-3 py-1.5 rounded-md transition-colors">
            Refresh
          </button>
          <button onClick={() => setColWidths(DEFAULT_WIDTHS)}
            className="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-400 text-xs px-2.5 py-1.5 rounded-md transition-colors"
            title="Reset column widths">
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
                  className={`border-b border-slate-700/50 hover:bg-slate-700/20 transition-colors ${active ? 'bg-slate-700/10' : ''}`}
                >
                  <td className="px-4 py-3 text-slate-400 font-mono">{job.id}</td>
                  <td className="px-4 py-3 text-slate-300 truncate" title={job.camera_name}>{job.camera_name ?? '—'}</td>
                  <td className="px-4 py-3 text-slate-200 truncate" title={job.file_path}>{basename(job.file_path)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} pulse={active} job={job} />
                  </td>
                  <td className="px-4 py-3">
                    {!done && since ? <ElapsedTimer sinceMs={since} /> : null}
                  </td>
                  <td className="px-4 py-3 text-slate-400 truncate">{fmt(job.created_at)}</td>
                  <td className="px-4 py-3 text-slate-400 truncate">{fmt(job.completed_at)}</td>
                  <td className="px-4 py-3 text-slate-400 tabular-nums">{job.track_count ?? '—'}</td>
                  <td className="px-4 py-3">
                    {active && <KillButton jobId={job.id} onKilled={load} />}
                    {job.status === 'failed' && job.error_message && (
                      <span className="text-xs text-red-400 truncate block" title={job.error_message}>
                        {job.error_message.length > 22 ? job.error_message.slice(0, 22) + '…' : job.error_message}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Status legend */}
      <div className="mt-3 flex flex-wrap gap-2 items-center">
        <span className="text-slate-600 text-xs">Status guide:</span>
        {[
          ['queued',        'Waiting for MD worker'],
          ['md_processing', 'MD actively detecting motion'],
          ['md_complete',   'MD done — frames queued for OC'],
          ['oc_processing', 'OC classifying objects'],
          ['completed',     'Done'],
          ['failed',        'Error or killed'],
        ].map(([s, tip]) => (
          <span key={s} title={tip}
            className={`text-xs px-2 py-0.5 rounded border cursor-help ${STATUS_COLORS[s] ?? 'bg-slate-700 text-slate-300 border-slate-600'}`}>
            {s}
          </span>
        ))}
        <span className="text-slate-600 text-xs ml-2">· hover status for timeline</span>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-md disabled:opacity-40 hover:bg-slate-700 transition-colors">
            ← Prev
          </button>
          <span className="text-slate-400 text-sm">Page {page} / {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-md disabled:opacity-40 hover:bg-slate-700 transition-colors">
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
