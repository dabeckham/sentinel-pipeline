import { useState, useEffect } from 'react'
import { api } from '../api.js'

// Admin storage panel for the sidebar: Postgres DB size + MinIO bucket sizes,
// so admins can keep an eye on data growth (image buckets grow fastest now that
// moving objects save a frame per detection). Backend caches the bucket scan.
function fmtBytes(b) {
  if (b == null) return '—'
  if (b < 1024) return `${b} B`
  const u = ['KB', 'MB', 'GB', 'TB']
  let v = b / 1024, i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${u[i]}`
}

export default function StoragePanel() {
  const [data, setData] = useState(null)
  const [err, setErr]   = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = () => api.storage()
      .then(d => { if (!cancelled) { setData(d); setErr(false) } })
      .catch(() => { if (!cancelled) setErr(true) })
    load()
    const id = setInterval(load, 60000)   // refresh each minute (backend is cached)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  if (err) return null
  if (!data) {
    return (
      <div className="px-4 py-3 border-t border-slate-700">
        <p className="text-slate-500 text-xs uppercase tracking-wide">Storage</p>
        <p className="text-slate-600 text-xs mt-1 animate-pulse">loading…</p>
      </div>
    )
  }

  const buckets = (data.buckets || []).slice().sort((a, b) => b.bytes - a.bytes)
  const total = (data.postgres_bytes || 0) + (data.minio_total_bytes || 0)

  return (
    <div className="px-4 py-3 border-t border-slate-700">
      <div className="flex items-center justify-between">
        <p className="text-slate-500 text-xs uppercase tracking-wide">Storage</p>
        <span className="text-slate-300 text-xs font-mono">{fmtBytes(total)}</span>
      </div>
      <div className="mt-1.5 space-y-1">
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-400">Postgres DB</span>
          <span className="text-slate-300 font-mono tabular-nums">{fmtBytes(data.postgres_bytes)}</span>
        </div>
        {buckets.map(b => (
          <div key={b.bucket} className="flex items-center justify-between text-xs" title={`${b.objects.toLocaleString()} objects`}>
            <span className="text-slate-400 truncate">{b.bucket}</span>
            <span className="text-slate-300 font-mono tabular-nums shrink-0 ml-2">{fmtBytes(b.bytes)}</span>
          </div>
        ))}
      </div>
      {data.cached_age_s != null && (
        <p className="text-slate-600 text-[10px] mt-1.5">updated {data.cached_age_s}s ago</p>
      )}
    </div>
  )
}
