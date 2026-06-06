import { useState, useEffect } from 'react'
import { api } from '../api.js'

export default function Tracks() {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [classFilter, setClassFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const PAGE_SIZE = 50

  async function load(p = page, c = classFilter) {
    setLoading(true)
    try {
      const params = { page: p, page_size: PAGE_SIZE }
      if (c) params.class_label = c
      const res = await api.tracks(params)
      setData(res)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page, classFilter])

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  const CLASSES = ['car', 'truck', 'person', 'bicycle', 'motorcycle', 'bus']

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white">Tracks</h2>
          {data && <p className="text-slate-400 text-sm mt-1">{data.total} total</p>}
        </div>
        <select
          value={classFilter}
          onChange={(e) => { setClassFilter(e.target.value); setPage(1) }}
          className="bg-slate-800 border border-slate-600 text-slate-300 text-sm rounded-md px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
        >
          <option value="">All classes</option>
          {CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700">
              {['ID', 'Job', 'Track ID', 'Class', 'Confidence', 'Frames'].map((h) => (
                <th key={h} className="text-left text-slate-400 font-medium px-4 py-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={6} className="text-center text-slate-400 py-8">Loading…</td></tr>
            )}
            {!loading && data?.items?.length === 0 && (
              <tr><td colSpan={6} className="text-center text-slate-400 py-8">No tracks found</td></tr>
            )}
            {!loading && data?.items?.map((t) => (
              <tr key={t.id} className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors">
                <td className="px-4 py-3 text-slate-400 font-mono">{t.id}</td>
                <td className="px-4 py-3 text-slate-400">#{t.job_id}</td>
                <td className="px-4 py-3 text-slate-300">{t.track_id}</td>
                <td className="px-4 py-3">
                  <span className="capitalize bg-slate-700 text-slate-200 text-xs px-2 py-0.5 rounded">
                    {t.class_label}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-300">
                  {t.confidence_max != null ? `${(t.confidence_max * 100).toFixed(0)}%` : '—'}
                </td>
                <td className="px-4 py-3 text-slate-400">
                  {t.first_frame != null && t.last_frame != null
                    ? `${t.first_frame}–${t.last_frame}`
                    : '—'}
                </td>
              </tr>
            ))}
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
