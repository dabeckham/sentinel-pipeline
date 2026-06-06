import { useState, useEffect } from 'react'
import { api } from '../api.js'

function StatCard({ label, value, sub, color = 'text-brand' }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
      <p className="text-slate-400 text-sm">{label}</p>
      <p className={`text-3xl font-bold mt-1 ${color}`}>{value ?? '—'}</p>
      {sub && <p className="text-slate-500 text-xs mt-1">{sub}</p>}
    </div>
  )
}

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function load() {
    try {
      const s = await api.stats()
      setStats(s)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="p-8">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-white">Dashboard</h2>
        <p className="text-slate-400 text-sm mt-1">Pipeline overview — auto-refreshes every 15s</p>
      </div>

      {loading && <p className="text-slate-400">Loading…</p>}
      {error && <p className="text-red-400">Error: {error}</p>}

      {stats && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <StatCard label="Total Jobs" value={stats.jobs_total} />
            <StatCard label="Completed" value={stats.jobs_completed} color="text-green-400" />
            <StatCard label="Processing" value={stats.jobs_processing + stats.jobs_queued} color="text-yellow-400" sub="queued + processing" />
            <StatCard label="Failed" value={stats.jobs_failed} color="text-red-400" />
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-2 gap-4 mb-8">
            <StatCard label="Total Tracks" value={stats.tracks_total} />
            <StatCard label="Total Detections" value={stats.detections_total} />
          </div>

          {stats.class_breakdown?.length > 0 && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
              <h3 className="text-slate-300 font-semibold mb-4">Detection Class Breakdown</h3>
              <div className="space-y-3">
                {stats.class_breakdown.map((cls) => {
                  const pct = stats.tracks_total > 0 ? Math.round((cls.count / stats.tracks_total) * 100) : 0
                  return (
                    <div key={cls.class_label}>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-300 capitalize">{cls.class_label}</span>
                        <span className="text-slate-400">
                          {cls.count} tracks &middot; avg {(cls.avg_confidence * 100).toFixed(0)}% conf
                        </span>
                      </div>
                      <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-brand rounded-full transition-all"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
