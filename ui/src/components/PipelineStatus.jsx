import { useState, useEffect, useRef, useCallback } from 'react'
import { useWsEvent } from '../WsContext.jsx'
import { api } from '../api.js'

// DLX queue names for right-click purge
const DLX_QUEUE_NAME = {
  dlx_ingest:         'dlx.ingest',
  dlx_motion_results: 'dlx.motion_results',
}

const QUEUE_LABELS = {
  ingest:              'Ingest',
  motion_results:      'Motion',
  oc_results:          'OC Results',
  dlx_ingest:          'DLX Ingest',
  dlx_motion_results:  'DLX Motion',
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtDuration(secs) {
  if (!secs || secs <= 0) return '—'
  if (secs < 60)   return `${Math.round(secs)}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return `${h}h ${m}m`
}

function fmtSinceEpoch(epoch) {
  if (!epoch) return '—'
  return fmtDuration(Math.floor(Date.now() / 1000 - epoch))
}

function fmtNum(n) {
  if (!n) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

// ── Queue depth bar ───────────────────────────────────────────────────────────

function DepthBar({ depth }) {
  if (depth < 0) return <span className="text-slate-600 text-xs">—</span>
  const pct      = Math.min(100, (depth / 200) * 100)
  const color    = depth === 0 ? 'bg-slate-600' : depth < 20 ? 'bg-green-500' : depth < 100 ? 'bg-yellow-500' : 'bg-red-500'
  const textColor = depth === 0 ? 'text-slate-500' : depth < 20 ? 'text-green-400' : depth < 100 ? 'text-yellow-400' : 'text-red-400'
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <div className="flex-1 h-1 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`}
             style={{ width: `${Math.max(pct, depth > 0 ? 4 : 0)}%` }} />
      </div>
      <span className={`text-xs tabular-nums w-7 text-right shrink-0 ${textColor}`}>{depth}</span>
    </div>
  )
}

// ── Queue row with right-click for DLX purge ──────────────────────────────────

function QueueRow({ qkey, label, q, onPurge }) {
  const [menu, setMenu] = useState(null)
  const menuRef = useRef(null)
  const isDlx   = !!DLX_QUEUE_NAME[qkey]

  useEffect(() => {
    if (!menu) return
    const close = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenu(null) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menu])

  return (
    <div
      className={`flex items-center gap-1.5 ${isDlx && q?.depth > 0 ? 'cursor-context-menu' : ''}`}
      onContextMenu={(e) => {
        if (!isDlx || !q || q.depth === 0) return
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY })
      }}
    >
      <span className="text-slate-400 text-xs w-16 shrink-0 truncate">{label}</span>
      <div className="flex-1 min-w-0">
        {q ? <DepthBar depth={q.depth} /> : <span className="text-slate-600 text-xs">—</span>}
      </div>
      {menu && (
        <div ref={menuRef} className="fixed z-[100] bg-slate-800 border border-slate-600 rounded-md shadow-xl py-1 text-xs"
             style={{ top: menu.y, left: menu.x }}>
          <button
            className="w-full text-left px-3 py-1.5 text-red-400 hover:bg-slate-700 whitespace-nowrap"
            onClick={async () => { setMenu(null); await onPurge(DLX_QUEUE_NAME[qkey]) }}
          >
            🗑 Clear DLX ({q.depth} messages)
          </button>
        </div>
      )}
    </div>
  )
}

// ── Stats callout (hover bubble) ──────────────────────────────────────────────

function StatsCallout({ w }) {
  return (
    <div className="absolute left-full top-0 ml-2 z-[200] w-52 bg-slate-900 border border-slate-600 rounded-lg shadow-2xl p-3 text-xs space-y-1.5 pointer-events-none">
      {/* Worker real name */}
      <div className="text-slate-300 font-mono text-[10px] break-all leading-tight border-b border-slate-700 pb-1.5 mb-1.5">
        {w.worker_id}
      </div>

      <div className="flex justify-between">
        <span className="text-slate-500">Online</span>
        <span className="text-slate-300 tabular-nums">{fmtSinceEpoch(w.registered_at)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Jobs</span>
        <span className="text-slate-300 tabular-nums">{w.jobs_processed ?? 0}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Compute</span>
        <span className="text-slate-300 tabular-nums">{fmtDuration(w.total_compute_s)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-slate-500">Frames</span>
        <span className="text-slate-300 tabular-nums">{fmtNum(w.total_frames)}</span>
      </div>
      {(w.fps_avg > 0 || w.fps_high > 0) && (
        <div className="border-t border-slate-700 pt-1.5 mt-1">
          <div className="text-slate-500 mb-1">FPS</div>
          <div className="grid grid-cols-3 gap-1 text-center">
            <div>
              <div className="text-slate-300 tabular-nums font-medium">{w.fps_avg ?? 0}</div>
              <div className="text-slate-600 text-[9px]">avg</div>
            </div>
            <div>
              <div className="text-green-400 tabular-nums font-medium">{w.fps_high ?? 0}</div>
              <div className="text-slate-600 text-[9px]">high</div>
            </div>
            <div>
              <div className="text-yellow-400 tabular-nums font-medium">{w.fps_low ?? 0}</div>
              <div className="text-slate-600 text-[9px]">low</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Worker row ────────────────────────────────────────────────────────────────

function WorkerRow({ w, tick, onSuspend, onResume }) {
  const [menu,    setMenu]    = useState(null)
  const [hovering, setHover]  = useState(false)
  const menuRef = useRef(null)

  // Hide offline workers
  if (w.status === 'offline' || w.status === 'lost') return null

  const isSuspended  = !!w.suspended
  const isProcessing = w.status === 'processing'

  // Label: MD-CPU-1, OC-GPU-2 etc.
  const label = `${(w.type || '?').toUpperCase()}-${(w.device || 'cpu').toUpperCase()}-${(w.index ?? 0) + 1}`

  // Dot + timer color
  let dotClass, tooltipText
  if (isSuspended) {
    dotClass    = 'bg-red-500'
    tooltipText = 'SUSPENDED'
  } else if (isProcessing) {
    dotClass    = 'bg-yellow-400 animate-pulse shadow-[0_0_5px_#facc15]'
    tooltipText = 'BUSY'
  } else {
    dotClass    = 'bg-green-500'
    tooltipText = 'IDLE'
  }

  const timerColor = isSuspended ? 'text-red-400' : isProcessing ? 'text-yellow-400' : 'text-green-400'

  // Timer: show job id while processing, elapsed idle time otherwise
  const timerText = isProcessing
    ? `#${w.job_id}`
    : (w.idle_since ? fmtSinceEpoch(w.idle_since) : '—')

  // Right-click context menu
  useEffect(() => {
    if (!menu) return
    const close = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenu(null) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menu])

  return (
    <div
      className="relative flex items-center gap-1.5 py-0.5 cursor-context-menu select-none"
      onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }) }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* Status dot */}
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotClass}`} />

      {/* Label */}
      <span className="text-xs text-slate-400 w-14 shrink-0 font-mono">{label}</span>

      {/* Timer / job id */}
      <span className={`text-xs tabular-nums ${timerColor}`}>{timerText}</span>

      {/* Suspended badge */}
      {isSuspended && (
        <span className="ml-auto text-[9px] font-semibold text-red-400 bg-red-900/30 px-1 rounded">
          SUSP
        </span>
      )}

      {/* Hover tooltip (status text) */}
      {hovering && (
        <div className="absolute bottom-full left-4 mb-1 px-2 py-0.5 bg-slate-700 text-slate-200 text-[10px] font-semibold rounded shadow-lg whitespace-nowrap z-[150] pointer-events-none">
          {tooltipText}
        </div>
      )}

      {/* Stats callout (hover) */}
      {hovering && <StatsCallout w={w} tick={tick} />}

      {/* Right-click context menu */}
      {menu && (
        <div
          ref={menuRef}
          className="fixed z-[200] bg-slate-800 border border-slate-600 rounded-md shadow-xl py-1 text-xs"
          style={{ top: menu.y, left: menu.x }}
        >
          {isSuspended ? (
            <button
              className="w-full text-left px-3 py-1.5 text-green-400 hover:bg-slate-700 whitespace-nowrap"
              onClick={() => { setMenu(null); onResume(w.worker_id) }}
            >
              ▶ Resume {label}
            </button>
          ) : (
            <button
              className="w-full text-left px-3 py-1.5 text-yellow-400 hover:bg-slate-700 whitespace-nowrap"
              onClick={() => { setMenu(null); onSuspend(w.worker_id) }}
            >
              ⏸ Suspend {label}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function PipelineStatus() {
  const [queues,  setQueues]  = useState(null)
  const [workers, setWorkers] = useState([])
  const [tick,    setTick]    = useState(0)

  // 1-second tick for live timers
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  useWsEvent(msg => {
    if (msg.type !== 'queue_metrics') return
    if (msg.queues)  setQueues(msg.queues)
    if (msg.workers) setWorkers(msg.workers)
  })

  async function handlePurge(queueName) {
    try {
      await api.dlxPurge(queueName)
      setQueues(prev => {
        if (!prev) return prev
        const key = Object.entries(DLX_QUEUE_NAME).find(([, v]) => v === queueName)?.[0]
        if (!key) return prev
        return { ...prev, [key]: { ...prev[key], depth: 0 } }
      })
    } catch (e) {
      console.error('DLX purge failed', e)
    }
  }

  const handleSuspend = useCallback(async (workerId) => {
    try {
      await api.suspendWorker(workerId)
      // Optimistically update local state
      setWorkers(prev => prev.map(w => w.worker_id === workerId ? { ...w, suspended: true } : w))
    } catch (e) {
      console.error('Suspend failed', e)
    }
  }, [])

  const handleResume = useCallback(async (workerId) => {
    try {
      await api.resumeWorker(workerId)
      setWorkers(prev => prev.map(w => w.worker_id === workerId ? { ...w, suspended: false } : w))
    } catch (e) {
      console.error('Resume failed', e)
    }
  }, [])

  const visibleWorkers = workers.filter(w => w.status !== 'offline' && w.status !== 'lost')
  const mdWorkers = visibleWorkers.filter(w => w.type === 'md')
  const ocWorkers = visibleWorkers.filter(w => w.type === 'oc')

  return (
    <div className="px-3 py-3 border-t border-slate-700 space-y-3">

      {/* Queue depths */}
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Queues</p>
        {queues ? (
          <div className="space-y-1">
            {Object.entries(QUEUE_LABELS).map(([key, label]) => (
              <QueueRow key={key} qkey={key} label={label} q={queues[key]} onPurge={handlePurge} />
            ))}
          </div>
        ) : (
          <p className="text-xs text-slate-600">Waiting…</p>
        )}
      </div>

      {/* Workers */}
      {visibleWorkers.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Workers</p>
          <div className="space-y-0.5">
            {mdWorkers.length > 0 && (
              <>
                <p className="text-[10px] text-slate-600 mt-1 mb-0.5 uppercase tracking-wider">MD</p>
                {mdWorkers.map(w => (
                  <WorkerRow key={w.worker_id} w={w} tick={tick}
                             onSuspend={handleSuspend} onResume={handleResume} />
                ))}
              </>
            )}
            {ocWorkers.length > 0 && (
              <>
                <p className="text-[10px] text-slate-600 mt-1.5 mb-0.5 uppercase tracking-wider">OC</p>
                {ocWorkers.map(w => (
                  <WorkerRow key={w.worker_id} w={w} tick={tick}
                             onSuspend={handleSuspend} onResume={handleResume} />
                ))}
              </>
            )}
          </div>
        </div>
      )}

    </div>
  )
}
