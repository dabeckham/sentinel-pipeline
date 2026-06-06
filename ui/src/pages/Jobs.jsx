import { useState, useEffect } from 'react'
import { api } from '../api.js'

const STATUS_COLORS = {
  completed: 'bg-green-900/50 text-green-300 border-green-700',
  oc_processing: 'bg-blue-900/50 text-blue-300 border-blue-700',
  queued: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  motion_processing: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  failed: 'bg-red-900/50 text-red-300 border-red-700',
  dead_letter: 'bg-red-900/50 text-red-300 border-red-700',
}

function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status] || 'bg-slate-700 text-slate-300 border-slate-600'
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${cls}`}>
      {status}
    </span>
  )
}

function fmt(dt) {
  if (!dt) return '—'
  return new Date(dt).toLocaleString()
}

export default function Jobs() {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const PAGE_SIZE = 25

  async function load(p = page, s = statusFilter) {
    setLoading(true)
    try {
      const res = await api.jobs(p, PAGE_SIZE, s)
      setData(res)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page, statusFilter])

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white">Jobs</h2>
          {data && <p className="text-slate-400 text-sm mt-1">{data.total} total</p>}
        </div>
        <div className="flex items-center gap-3">
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
            className="bg-slate-800 border border-slate-600 text-slate-300 text-sm rounded-md px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
          >
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="motion_processing">Motion</option>
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
              {['ID', 'File', 'Status', 'Created', 'Completed', 'Tracks'].map((h) => (
                <th key={h} className="text-left text-slate-400 font-medium px-4 py-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={6} className="text-center text-slate-400 py-8">Loading…</td>
              </tr>
            )}
            {!loading && data?.items?.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center text-slate-400 py-8">No jobs found</td>
              </tr>
            )}
            {!loading && data?.items?.map((job) => (
              <tr key={job.id} className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors">
                <td className="px-4 py-3 text-slate-400 font-mono">{job.id}</td>
                <td className="px-4 py-3 text-slate-200 max-w-xs truncate" title={job.filename}>
                  {job.filename?.split('/').pop() || job.filename}
                </td>
                <td className="px-4 py-3"><StatusBadge status={job.status} /></td>
                <td className="px-4 py-3 text-slate-400">{fmt(job.created_at)}</td>
                <td className="px-4 py-3 text-slate-400">{fmt(job.completed_at)}</td>
                <td className="px-4 py-3 text-slate-400">{job.track_count ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
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
