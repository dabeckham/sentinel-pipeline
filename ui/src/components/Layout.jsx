import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import { wsUrl, api } from '../api.js'
import MetricsBar from './MetricsBar.jsx'
import PipelineStatus from './PipelineStatus.jsx'
import { WsContext } from '../WsContext.jsx'

const NAV = [
  { to: '/dashboard', label: 'Dashboard', icon: '📊' },
  { to: '/jobs', label: 'Jobs', icon: '🎬' },
  { to: '/tracks', label: 'Tracks', icon: '🎯' },
  { to: '/users', label: 'Users', icon: '👥' },
]

export default function Layout() {
  const navigate = useNavigate()
  const [wsStatus, setWsStatus]           = useState('connecting')
  const [toast, setToast]                 = useState(null)
  const [pipelineAlert, setPipelineAlert] = useState(null)  // {diagnosis, details} | null
  const [watcherPaused, setWatcherPaused] = useState(false)
  const [watcherToggling, setWatcherToggling] = useState(false)
  const [showScrollTop, setShowScrollTop] = useState(false)
  const wsRef       = useRef(null)
  const mainRef     = useRef(null)
  const wsHandlers  = useRef(new Set())   // shared WS event bus for child pages

  // Scroll-to-top: native listener on the <main> scroll container
  useEffect(() => {
    const el = mainRef.current
    if (!el) return
    const onScroll = () => setShowScrollTop(el.scrollTop > 200)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // Restore alert banner + watcher state on page load (survives browser refresh)
  useEffect(() => {
    api.pipelineStatus().then(s => {
      setWatcherPaused(s.watcher_paused)
      if (s.watcher_paused && s.diagnosis) {
        setPipelineAlert({ diagnosis: s.diagnosis, details: {} })
      }
    }).catch(() => {})
  }, [])

  useEffect(() => {
    let timer
    let ws

    function connect() {
      ws = new WebSocket(wsUrl())
      wsRef.current = ws

      ws.onopen = () => setWsStatus('connected')
      ws.onclose = () => {
        setWsStatus('disconnected')
        timer = setTimeout(connect, 5000)
      }
      ws.onerror = () => setWsStatus('error')
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          // Broadcast to all subscribed pages
          wsHandlers.current.forEach(h => { try { h(msg) } catch (_) {} })
          if (msg.type === 'job_update' && (msg.status === 'completed' || msg.status === 'failed' || msg.status === 'dead_letter')) {
            const icon = msg.status === 'completed' ? '✅' : '❌'
            setToast(`${icon} Job #${msg.job_id} ${msg.status}`)
            setTimeout(() => setToast(null), 4000)
          } else if (msg.type === 'pipeline_alert') {
            setPipelineAlert({ diagnosis: msg.diagnosis, details: msg })
            setWatcherPaused(true)
          } else if (msg.type === 'pipeline_recovery') {
            setPipelineAlert(null)
            setWatcherPaused(false)
            setToast(`✅ Pipeline recovered — ingest resumed`)
            setTimeout(() => setToast(null), 6000)
          }
        } catch (_) {}
      }

      // keep-alive ping every 25s
      const pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping')
      }, 25000)

      ws.onclose = () => {
        clearInterval(pingInterval)
        setWsStatus('disconnected')
        timer = setTimeout(connect, 5000)
      }
    }

    connect()
    return () => {
      clearTimeout(timer)
      ws?.close()
    }
  }, [])

  function logout() {
    localStorage.removeItem('sentinel_token')
    navigate('/login')
  }

  const wsDot = {
    connected: 'bg-green-400',
    connecting: 'bg-yellow-400 animate-pulse',
    disconnected: 'bg-red-400',
    error: 'bg-red-600',
  }[wsStatus]

  return (
    <WsContext.Provider value={wsHandlers}>
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <nav className="w-56 bg-slate-800 flex flex-col border-r border-slate-700 shrink-0">
        <div className="px-4 py-5 border-b border-slate-700">
          <span className="text-brand font-bold text-lg tracking-wide">📡 Sentinel</span>
          <div className="flex items-center gap-1.5 mt-1">
            <span className={`w-2 h-2 rounded-full ${wsDot}`} />
            <span className="text-xs text-slate-400 capitalize">{wsStatus}</span>
          </div>
        </div>

        <div className="flex-1 py-4 space-y-1 px-2">
          {NAV.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-slate-700 text-brand font-medium'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white'
                }`
              }
            >
              <span>{icon}</span>
              {label}
            </NavLink>
          ))}
        </div>

        {/* Real-time queue depths + worker states */}
        <PipelineStatus />

        <div className="p-4 border-t border-slate-700 space-y-1">
          {/* Watcher toggle */}
          <button
            disabled={watcherToggling}
            onClick={async () => {
              setWatcherToggling(true)
              try {
                if (watcherPaused) {
                  await api.resumeWatcher()
                  setWatcherPaused(false)
                  setPipelineAlert(null)
                  setToast('▶ Ingest resumed')
                } else {
                  await api.pauseWatcher()
                  setWatcherPaused(true)
                  setToast('⏸ Ingest paused')
                }
                setTimeout(() => setToast(null), 4000)
              } catch (e) {
                setToast(`❌ ${e.message}`)
                setTimeout(() => setToast(null), 4000)
              } finally {
                setWatcherToggling(false)
              }
            }}
            className={`w-full text-left text-sm px-3 py-2 rounded-md transition-colors ${
              watcherPaused
                ? 'text-yellow-400 hover:bg-slate-700 hover:text-yellow-300'
                : 'text-slate-400 hover:bg-slate-700 hover:text-white'
            } disabled:opacity-50`}
          >
            {watcherToggling ? '⏳ …' : watcherPaused ? '▶ Resume Ingest' : '⏸ Pause Ingest'}
          </button>
          <button
            onClick={logout}
            className="w-full text-left text-sm text-slate-400 hover:text-white transition-colors px-3 py-2 rounded-md hover:bg-slate-700"
          >
            🚪 Logout
          </button>
        </div>
      </nav>

      {/* Main — pb-10 leaves room for the metrics bar */}
      <main
        ref={mainRef}
        className="flex-1 overflow-y-auto bg-slate-900 pb-10"
      >
        {/* Pipeline alert banner — shown when health monitor opens circuit breaker */}
        {pipelineAlert && (
          <div className="bg-red-950 border-b border-red-700 px-4 py-3 flex items-start gap-3">
            <span className="text-red-400 text-lg shrink-0 mt-0.5">⚠️</span>
            <div className="flex-1 min-w-0">
              <p className="text-red-300 text-sm font-semibold">Pipeline stalled — new jobs are being held</p>
              <p className="text-red-400 text-xs mt-0.5 break-words">{pipelineAlert.diagnosis}</p>
              {pipelineAlert.details?.stuck_jobs?.length > 0 && (
                <p className="text-red-500 text-xs mt-0.5">
                  Stuck jobs: {pipelineAlert.details.stuck_jobs.join(', ')}
                  {pipelineAlert.details.dlx_depth > 0 && ` · ${pipelineAlert.details.dlx_depth} in DLX`}
                </p>
              )}
            </div>
            <button
              onClick={() => setPipelineAlert(null)}
              className="text-red-600 hover:text-red-400 text-lg shrink-0"
              title="Dismiss (alert will reappear if problem persists)"
            >×</button>
          </div>
        )}
        <Outlet />
      </main>

      {/* Persistent system metrics strip */}
      <MetricsBar />
    </div>

    {/* Scroll-to-top — outside overflow-hidden div so it can never be clipped */}
    {showScrollTop && (
      <button
        onClick={() => mainRef.current?.scrollTo({ top: 0, behavior: 'smooth' })}
        className="fixed right-5 bottom-14 z-[60] w-9 h-9 flex items-center justify-center bg-slate-700 hover:bg-slate-500 border border-slate-500 text-slate-200 hover:text-white rounded-full shadow-xl transition-all"
        title="Back to top"
      >
        ↑
      </button>
    )}

    {/* Toast — outside overflow-hidden div */}
    {toast && (
      <div className="fixed bottom-14 right-16 bg-slate-700 border border-slate-600 text-white text-sm px-4 py-2 rounded-lg shadow-lg z-[60] transition-all">
        🔔 {toast}
      </div>
    )}
    </WsContext.Provider>
  )
}
