import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import { wsUrl } from '../api.js'
import MetricsBar from './MetricsBar.jsx'

const NAV = [
  { to: '/dashboard', label: 'Dashboard', icon: '📊' },
  { to: '/jobs', label: 'Jobs', icon: '🎬' },
  { to: '/tracks', label: 'Tracks', icon: '🎯' },
  { to: '/users', label: 'Users', icon: '👥' },
]

export default function Layout() {
  const navigate = useNavigate()
  const [wsStatus, setWsStatus]       = useState('connecting')
  const [toast, setToast]             = useState(null)
  const [pipelineAlert, setPipelineAlert] = useState(null)  // {diagnosis, details} | null
  const wsRef = useRef(null)

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
          if (msg.type === 'job_update' && (msg.status === 'completed' || msg.status === 'failed' || msg.status === 'dead_letter')) {
            const icon = msg.status === 'completed' ? '✅' : '❌'
            setToast(`${icon} Job #${msg.job_id} ${msg.status}`)
            setTimeout(() => setToast(null), 4000)
          } else if (msg.type === 'pipeline_alert') {
            setPipelineAlert({ diagnosis: msg.diagnosis, details: msg })
          } else if (msg.type === 'pipeline_recovery') {
            setPipelineAlert(null)
            setToast(`✅ Pipeline recovered — ${msg.pending_released ?? 0} held job(s) released`)
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

        <div className="p-4 border-t border-slate-700">
          <button
            onClick={logout}
            className="w-full text-left text-sm text-slate-400 hover:text-white transition-colors px-3 py-2 rounded-md hover:bg-slate-700"
          >
            🚪 Logout
          </button>
        </div>
      </nav>

      {/* Main — pb-10 leaves room for the metrics bar */}
      <main className="flex-1 overflow-y-auto bg-slate-900 pb-10">
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

      {/* Toast — sits above metrics bar */}
      {toast && (
        <div className="fixed bottom-12 right-4 bg-slate-700 border border-slate-600 text-white text-sm px-4 py-2 rounded-lg shadow-lg z-50 transition-all">
          🔔 {toast}
        </div>
      )}

      {/* Persistent system metrics strip */}
      <MetricsBar />
    </div>
  )
}
