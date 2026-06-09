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
  paused:            'bg-orange-900/50 text-orange-300 border-orange-700',
}

const ACTIVE_STATUSES = new Set(['queued', 'md_processing', 'md_complete', 'motion_processing', 'oc_processing'])
const DONE_STATUSES   = new Set(['completed', 'failed', 'dead_letter', 'paused'])

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
        <div className="absolute left-0 top-full mt-1 z-50 bg-slate-900 border border-slate-600 rounded-xl shadow-2xl p-3 min-w-[300px] pointer-events-none">
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
          {(job.md_worker_id || job.oc_worker_id) && (
            <div className="mt-2 pt-2 border-t border-slate-700 space-y-0.5">
              {job.md_worker_id && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-slate-600 w-16 shrink-0">MD worker</span>
                  <span className="text-slate-400 font-mono truncate" title={job.md_worker_id}>{job.md_worker_id}</span>
                </div>
              )}
              {job.oc_worker_id && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-slate-600 w-16 shrink-0">OC worker</span>
                  <span className="text-slate-400 font-mono truncate" title={job.oc_worker_id}>{job.oc_worker_id}</span>
                </div>
              )}
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

// ── Single action button with confirm guard ───────────────────────────────────
function ActionButton({ label, title, colorClass, onClick, confirm: needsConfirm = false }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const fire = async e => {
    e.stopPropagation()
    if (needsConfirm && !confirming) { setConfirming(true); return }
    setBusy(true)
    try { await onClick() }
    finally { setBusy(false); setConfirming(false) }
  }

  if (confirming) {
    return (
      <span className="flex items-center gap-1">
        <button onClick={e => { e.stopPropagation(); setConfirming(false) }}
          className="text-xs px-2 py-0.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors">
          No
        </button>
        <button onClick={fire} disabled={busy}
          className="text-xs px-2 py-0.5 bg-red-700 hover:bg-red-600 text-white rounded transition-colors disabled:opacity-50">
          {busy ? '…' : 'Sure?'}
        </button>
      </span>
    )
  }

  return (
    <button onClick={fire} title={title} disabled={busy}
      className={`text-xs px-2 py-0.5 border rounded transition-colors disabled:opacity-50 ${colorClass}`}>
      {busy ? '…' : label}
    </button>
  )
}

// ── Bulk action bar ───────────────────────────────────────────────────────────
function BulkActions({ onRefresh }) {
  const [confirming, setConfirming] = useState(null)  // 'kill' | 'delete' | null
  const [busy, setBusy]             = useState(null)  // same keys

  const run = (key, apiFn, msgFn) => async () => {
    if (confirming !== key) { setConfirming(key); return }
    setBusy(key)
    setConfirming(null)
    try {
      const r = await apiFn()
      onRefresh()
      alert(msgFn(r))
    } catch (err) {
      alert(`Failed: ${err.message}`)
    } finally { setBusy(null) }
  }

  const doPause  = async () => {
    setBusy('pause')
    try { const r = await api.bulkPause();  onRefresh(); alert(`Paused ${r.paused} job(s)`) }
    catch (err) { alert(`Failed: ${err.message}`) }
    finally { setBusy(null) }
  }

  const doResume = async () => {
    setBusy('resume')
    try { const r = await api.bulkResume(); onRefresh(); alert(`Resumed ${r.resumed} job(s)`) }
    catch (err) { alert(`Failed: ${err.message}`) }
    finally { setBusy(null) }
  }

  const doKill   = run('kill',   api.bulkKill,         r => `Killed ${r.killed} job(s)`)
  const doDelete = run('delete', api.bulkDeleteFailed,  r => `Deleted ${r.deleted} job(s)`)

  const disabled = busy !== null

  // Confirm prompt: No first (safe, same position as original button), then Sure?
  const Confirm = ({ label, onConfirm, onCancel }) => (
    <span className="flex items-center gap-1">
      <button onClick={onCancel}
        className="text-xs px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-md transition-colors">
        No
      </button>
      <button onClick={onConfirm} disabled={disabled}
        className="text-xs px-3 py-1.5 bg-red-700 hover:bg-red-600 text-white rounded-md transition-colors disabled:opacity-50">
        {label}
      </button>
    </span>
  )

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-slate-500 text-xs">Bulk:</span>

      {/* Pause All */}
      <button onClick={doPause} disabled={disabled}
        className="text-xs px-3 py-1.5 border border-orange-700/50 text-orange-400 hover:bg-orange-900/30 rounded-md transition-colors disabled:opacity-50"
        title="Pause all queued/pending jobs">
        {busy === 'pause' ? '…' : '⏸ Pause All'}
      </button>

      {/* Resume All */}
      <button onClick={doResume} disabled={disabled}
        className="text-xs px-3 py-1.5 border border-green-700/50 text-green-400 hover:bg-green-900/30 rounded-md transition-colors disabled:opacity-50"
        title="Resume all paused jobs">
        {busy === 'resume' ? '…' : '▶ Resume All'}
      </button>

      {/* Kill All */}
      {confirming === 'kill'
        ? <Confirm label={busy === 'kill' ? '…' : 'Kill All — sure?'} onConfirm={doKill} onCancel={() => setConfirming(null)} />
        : <button onClick={doKill} disabled={disabled}
            className="text-xs px-3 py-1.5 border border-red-700/50 text-red-400 hover:bg-red-900/30 rounded-md transition-colors disabled:opacity-50"
            title="Cancel all active jobs (queued, processing, paused)">
            ✕ Kill All
          </button>
      }

      {/* Delete Failed */}
      {confirming === 'delete'
        ? <Confirm label={busy === 'delete' ? '…' : 'Delete — sure?'} onConfirm={doDelete} onCancel={() => setConfirming(null)} />
        : <button onClick={doDelete} disabled={disabled}
            className="text-xs px-3 py-1.5 border border-slate-600 text-slate-400 hover:bg-slate-700/50 hover:text-slate-300 rounded-md transition-colors disabled:opacity-50"
            title="Permanently delete all failed/duplicate/paused jobs">
            🗑 Delete Failed
          </button>
      }
    </div>
  )
}

// ── Constants ─────────────────────────────────────────────────────────────────
const PAGE_SIZE      = 50
const DEFAULT_WIDTHS = { ID: 60, Camera: 130, File: 240, Status: 160, 'In status': 110, Created: 170, Completed: 170, Tracks: 70, Actions: 170 }

// ── Main component ────────────────────────────────────────────────────────────
export default function Jobs() {
  const [items, setItems]               = useState([])
  const [total, setTotal]               = useState(null)
  const [hasMore, setHasMore]           = useState(true)
  const [nextPage, setNextPage]         = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [initialLoading, setInitialLoading] = useState(true)
  const [loadingMore, setLoadingMore]   = useState(false)
  const [colWidths, setColWidths]       = useState(DEFAULT_WIDTHS)
  const [statusSince, setStatusSince]   = useState({})

  const prevStatusRef   = useRef({})
  const pollRef         = useRef(null)
  const sentinelRef     = useRef(null)
  const loadedCountRef  = useRef(0)
  const filterRef       = useRef(statusFilter)
  const hasMoreRef      = useRef(true)
  const nextPageRef     = useRef(1)
  const loadingMoreRef  = useRef(false)

  filterRef.current    = statusFilter
  hasMoreRef.current   = hasMore
  nextPageRef.current  = nextPage
  loadingMoreRef.current = loadingMore

  const mergeStatusSince = useCallback((newItems) => {
    setStatusSince(prev => {
      const next = { ...prev }
      for (const job of newItems) {
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
  }, [])

  const loadFirst = useCallback(async (filter) => {
    setInitialLoading(true)
    setItems([])
    setHasMore(true)
    setNextPage(1)
    loadedCountRef.current = 0
    prevStatusRef.current = {}
    try {
      const res = await api.jobs(1, PAGE_SIZE, filter)
      setItems(res.items)
      setTotal(res.total)
      const more = res.items.length === PAGE_SIZE && res.total > PAGE_SIZE
      setHasMore(more)
      setNextPage(2)
      loadedCountRef.current = res.items.length
      mergeStatusSince(res.items)
    } finally {
      setInitialLoading(false)
    }
  }, [mergeStatusSince])

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMoreRef.current) return
    setLoadingMore(true)
    try {
      const res = await api.jobs(nextPageRef.current, PAGE_SIZE, filterRef.current)
      setItems(prev => {
        const merged = [...prev, ...res.items]
        loadedCountRef.current = merged.length
        return merged
      })
      setTotal(res.total)
      const more = res.items.length === PAGE_SIZE && nextPageRef.current * PAGE_SIZE < res.total
      setHasMore(more)
      setNextPage(p => p + 1)
      mergeStatusSince(res.items)
    } finally {
      setLoadingMore(false)
    }
  }, [mergeStatusSince])

  const pollRefresh = useCallback(async () => {
    const count  = Math.max(PAGE_SIZE, loadedCountRef.current)
    const filter = filterRef.current
    try {
      const res = await api.jobs(1, count, filter)
      if (filterRef.current !== filter) return
      setItems(res.items)
      setTotal(res.total)
      loadedCountRef.current = res.items.length
      const more = res.total > res.items.length
      setHasMore(more)
      setNextPage(Math.floor(res.items.length / PAGE_SIZE) + 1)
      mergeStatusSince(res.items)
    } catch (_) { /* silent on poll errors */ }
  }, [mergeStatusSince])

  useEffect(() => {
    clearTimeout(pollRef.current)
    loadFirst(statusFilter).then(() => {
      const tick = () => {
        pollRef.current = setTimeout(async () => {
          await pollRefresh()
          tick()
        }, loadedCountRef.current > 0 && items.some?.(j => ACTIVE_STATUSES.has(j.status)) ? 2000 : 6000)
      }
      tick()
    })
    return () => clearTimeout(pollRef.current)
  }, [statusFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  const hasActive = items.some(j => ACTIVE_STATUSES.has(j.status))

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) loadMore() },
      { rootMargin: '200px' }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [loadMore])

  const resizeCol = (col, delta) => {
    setColWidths(prev => ({ ...prev, [col]: Math.max(50, (prev[col] ?? 120) + delta) }))
  }

  const cols = ['ID', 'Camera', 'File', 'Status', 'In status', 'Created', 'Completed', 'Tracks', 'Actions']

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold text-white">Jobs</h2>
          {total !== null && (
            <p className="text-slate-400 text-sm mt-1">
              {items.length < total
                ? <span>{items.length} of {total} loaded</span>
                : <span>{total} total</span>
              }
              {hasActive && <span className="ml-2 text-yellow-400 animate-pulse">● processing</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="bg-slate-800 border border-slate-600 text-slate-300 text-sm rounded-md px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
          >
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="paused">Paused</option>
            <option value="md_processing">MD Processing</option>
            <option value="md_complete">MD Complete</option>
            <option value="oc_processing">OC Processing</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          <button onClick={() => loadFirst(statusFilter)}
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

      {/* Bulk actions bar */}
      <div className="flex items-center gap-4 mb-4 px-1">
        <BulkActions onRefresh={() => loadFirst(statusFilter)} />
      </div>

      {/* Table */}
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
            {initialLoading && (
              <tr><td colSpan={cols.length} className="text-center text-slate-400 py-8">Loading…</td></tr>
            )}
            {!initialLoading && items.length === 0 && (
              <tr><td colSpan={cols.length} className="text-center text-slate-400 py-8">No jobs found</td></tr>
            )}
            {items.map(job => {
              const active  = ACTIVE_STATUSES.has(job.status)
              const done    = DONE_STATUSES.has(job.status)
              const paused  = job.status === 'paused'
              const since   = statusSince[job.id]
              return (
                <tr
                  key={job.id}
                  className={`border-b border-slate-700/50 hover:bg-slate-700/20 transition-colors ${active ? 'bg-slate-700/10' : ''} ${paused ? 'opacity-60' : ''}`}
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
                    <JobActions job={job} onChanged={() => loadFirst(statusFilter)} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {/* Infinite scroll sentinel */}
        <div ref={sentinelRef} className="h-1" />

        {loadingMore && (
          <div className="flex justify-center py-4">
            <span className="text-slate-500 text-sm animate-pulse">Loading more…</span>
          </div>
        )}
        {!hasMore && items.length > 0 && total !== null && items.length >= total && (
          <div className="flex justify-center py-3">
            <span className="text-slate-600 text-xs">All {total} jobs loaded</span>
          </div>
        )}
      </div>

      {/* Status legend */}
      <div className="mt-3 flex flex-wrap gap-2 items-center">
        <span className="text-slate-600 text-xs">Status guide:</span>
        {[
          ['queued',        'Waiting for MD worker'],
          ['paused',        'Held — will not process until resumed'],
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
    </div>
  )
}

// ── Per-job action buttons ────────────────────────────────────────────────────
function JobActions({ job, onChanged }) {
  const status = job.status
  const active = ACTIVE_STATUSES.has(status)

  const wrap = fn => async () => { await fn(); onChanged() }

  return (
    <div className="flex items-center gap-1 flex-wrap">
      {/* Active jobs: pause (if queued) or kill */}
      {status === 'queued' && (
        <ActionButton
          label="⏸"
          title="Pause this job"
          colorClass="border-orange-700/50 text-orange-400 hover:bg-orange-900/40"
          onClick={wrap(() => api.pauseJob(job.id))}
        />
      )}
      {active && (
        <ActionButton
          label="✕ Kill"
          title="Cancel this job"
          colorClass="border-red-700/50 text-red-400 hover:bg-red-900/40"
          confirm
          onClick={wrap(() => api.cancelJob(job.id))}
        />
      )}

      {/* Paused: resume or kill */}
      {status === 'paused' && (
        <>
          <ActionButton
            label="▶ Resume"
            title="Resume this job"
            colorClass="border-green-700/50 text-green-400 hover:bg-green-900/40"
            onClick={wrap(() => api.resumeJob(job.id))}
          />
          <ActionButton
            label="✕"
            title="Kill this job"
            colorClass="border-red-700/50 text-red-400 hover:bg-red-900/40"
            confirm
            onClick={wrap(() => api.cancelJob(job.id))}
          />
        </>
      )}

      {/* Terminal jobs: show error snippet or remove button */}
      {(status === 'failed' || status === 'completed' || status === 'duplicate') && (
        <ActionButton
          label="🗑"
          title="Remove this job permanently"
          colorClass="border-slate-600 text-slate-500 hover:bg-slate-700/50 hover:text-slate-300"
          confirm
          onClick={wrap(() => api.removeJob(job.id))}
        />
      )}
      {status === 'failed' && job.error_message && (
        <span className="text-xs text-red-400 truncate block max-w-[100px]" title={job.error_message}>
          {job.error_message.length > 18 ? job.error_message.slice(0, 18) + '…' : job.error_message}
        </span>
      )}
    </div>
  )
}
