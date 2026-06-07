import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api.js'

// ── Class colour map ──────────────────────────────────────────────────────────
const CLASS_COLORS = {
  person:     'bg-green-500/20 text-green-300 border-green-500/40',
  car:        'bg-blue-500/20 text-blue-300 border-blue-500/40',
  truck:      'bg-indigo-500/20 text-indigo-300 border-indigo-500/40',
  bus:        'bg-purple-500/20 text-purple-300 border-purple-500/40',
  motorcycle: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40',
  bicycle:    'bg-cyan-500/20 text-cyan-300 border-cyan-500/40',
  boat:       'bg-teal-500/20 text-teal-300 border-teal-500/40',
  airplane:   'bg-sky-500/20 text-sky-300 border-sky-500/40',
  train:      'bg-violet-500/20 text-violet-300 border-violet-500/40',
  bird:       'bg-orange-500/20 text-orange-300 border-orange-500/40',
  cat:        'bg-pink-500/20 text-pink-300 border-pink-500/40',
  dog:        'bg-rose-500/20 text-rose-300 border-rose-500/40',
}
const DEFAULT_CLASS_COLOR = 'bg-slate-500/20 text-slate-300 border-slate-500/40'

function classColor(label) {
  return CLASS_COLORS[label?.toLowerCase()] ?? DEFAULT_CLASS_COLOR
}

function fmtConfidence(v) {
  return v != null ? `${(v * 100).toFixed(0)}%` : '—'
}

function fmtDuration(started, ended) {
  if (!started || !ended) return null
  const ms = new Date(ended) - new Date(started)
  if (ms < 0) return null
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

function fmtTime(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

// ── Snapshot image with fallback ──────────────────────────────────────────────
function SnapshotImg({ path, alt = 'snapshot' }) {
  const [blobUrl, setBlobUrl] = useState(null)
  const [errored, setErrored] = useState(false)

  useEffect(() => {
    if (!path) return
    // Reset on every path change so the old image doesn't linger
    setBlobUrl(null)
    setErrored(false)

    let cancelled = false
    let objectUrl = null
    const token = localStorage.getItem('sentinel_token')
    fetch(`/api/snapshots/${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (!res.ok) throw new Error(res.status)
        return res.blob()
      })
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setBlobUrl(objectUrl)
      })
      .catch(() => { if (!cancelled) setErrored(true) })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path])

  if (!path || errored) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-700/60 text-slate-500 text-3xl">
        🖼️
      </div>
    )
  }
  if (!blobUrl) {
    return <div className="w-full h-full bg-slate-700/60 animate-pulse" />
  }
  return (
    <img
      src={blobUrl}
      alt={alt}
      style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'cover' }}
    />
  )
}

// ── Track card ────────────────────────────────────────────────────────────────
function TrackCard({ track, onClick }) {
  const duration = fmtDuration(track.started_at, track.ended_at)
  const time = fmtTime(track.started_at)

  return (
    <button
      onClick={() => onClick(track)}
      className="group bg-slate-800 border border-slate-700 rounded-xl overflow-hidden hover:border-brand/60 hover:shadow-lg hover:shadow-brand/10 transition-all text-left w-full"
    >
      {/* Thumbnail */}
      <div className="relative w-full overflow-hidden bg-slate-700/40" style={{ height: '160px' }}>
        <SnapshotImg path={track.snapshot_path} />
        {/* Class badge overlay */}
        <div className="absolute top-2 left-2">
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full border capitalize ${classColor(track.class_label)}`}>
            {track.class_label ?? 'unknown'}
          </span>
        </div>
        {/* Confidence top-right */}
        {track.confidence_max != null && (
          <div className="absolute top-2 right-2">
            <span className="text-xs bg-black/60 text-white px-1.5 py-0.5 rounded font-mono">
              {fmtConfidence(track.confidence_max)}
            </span>
          </div>
        )}
      </div>

      {/* Metadata */}
      <div className="p-3 space-y-1.5">
        {/* Camera + time row */}
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-brand font-medium truncate">
            📷 {track.camera_name ?? `Job #${track.job_id}`}
          </span>
          <span className="text-xs text-slate-500 shrink-0">#{track.id}</span>
        </div>

        {time && (
          <p className="text-xs text-slate-400 truncate">{time}</p>
        )}

        {/* Stats row */}
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {duration && <span>⏱ {duration}</span>}
          {track.detection_count != null && (
            <span>🎯 {track.detection_count} detection{track.detection_count !== 1 ? 's' : ''}</span>
          )}
        </div>

        {/* Confidence bar */}
        {track.confidence_max != null && (
          <div className="w-full bg-slate-700 rounded-full h-1 mt-1">
            <div
              className="bg-brand h-1 rounded-full transition-all"
              style={{ width: `${(track.confidence_max * 100).toFixed(0)}%` }}
            />
          </div>
        )}
      </div>
    </button>
  )
}

// ── Detail modal ──────────────────────────────────────────────────────────────
function TrackDrawer({ trackId, onClose }) {
  const [detail, setDetail]     = useState(null)
  const [loading, setLoading]   = useState(true)
  const [detIdx, setDetIdx]     = useState(0)   // current detection index
  const [playing, setPlaying]   = useState(false)
  const playRef = useRef(null)

  useEffect(() => {
    if (!trackId) return
    setLoading(true)
    setDetail(null)
    setDetIdx(0)
    setPlaying(false)
    api.track(trackId).then(setDetail).finally(() => setLoading(false))
  }, [trackId])

  // Auto-play
  useEffect(() => {
    if (!playing || !detail?.detections?.length) return
    playRef.current = setInterval(() => {
      setDetIdx((i) => {
        if (i >= detail.detections.length - 1) { setPlaying(false); return i }
        return i + 1
      })
    }, 250)
    return () => clearInterval(playRef.current)
  }, [playing, detail])

  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const dets = detail?.detections ?? []
  const curDet = dets[detIdx] ?? null
  // Use per-detection crop if available, else fall back to track thumbnail
  const currentSnapshotPath = curDet?.crop_path ?? detail?.snapshot_path

  const duration = detail ? fmtDuration(detail.started_at, detail.ended_at) : null
  const startTime = detail ? fmtTime(detail.started_at) : null
  const endTime = detail ? fmtTime(detail.ended_at) : null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
      <div className="pointer-events-auto w-full max-w-lg max-h-[90vh] bg-slate-900 border border-slate-700 rounded-2xl flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700 shrink-0">
          <div>
            <h3 className="text-white font-semibold text-base">
              {detail ? (
                <span className="capitalize">{detail.class_label ?? 'Unknown'}</span>
              ) : 'Track Detail'}
            </h3>
            {detail && (
              <p className="text-slate-400 text-xs mt-0.5">
                Track #{detail.track_id} · Job #{detail.job_id}
                {detail.camera_name && ` · ${detail.camera_name}`}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors text-xl leading-none"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center h-48 text-slate-400">
              Loading…
            </div>
          )}

          {!loading && detail && (
            <div className="flex flex-col overflow-y-auto">
              {/* Snapshot viewer */}
              <div className="relative w-full bg-black shrink-0" style={{ height: '220px' }}>
                <SnapshotImg path={currentSnapshotPath} />

                {/* Frame counter overlay */}
                {dets.length > 0 && (
                  <div className="absolute top-2 right-2 bg-black/70 text-white text-xs font-mono px-2 py-1 rounded">
                    {detIdx + 1} / {dets.length}
                  </div>
                )}

                {/* Current detection confidence overlay */}
                {curDet && (
                  <div className="absolute top-2 left-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
                    f{curDet.frame_index} · {fmtConfidence(curDet.confidence)}
                  </div>
                )}
              </div>

              {/* Playback controls */}
              {dets.length > 0 && (
                <div className="flex items-center justify-center gap-3 px-4 py-3 bg-slate-800/80 border-b border-slate-700 shrink-0">
                  <button
                    onClick={() => { setPlaying(false); setDetIdx((i) => Math.max(0, i - 1)) }}
                    className="w-8 h-8 flex items-center justify-center rounded-full bg-slate-700 hover:bg-slate-600 text-white transition-colors text-sm"
                    title="Previous frame"
                  >◀</button>

                  <button
                    onClick={() => setPlaying((p) => !p)}
                    className="w-10 h-10 flex items-center justify-center rounded-full bg-brand hover:bg-brand/80 text-white transition-colors text-base font-bold"
                    title={playing ? 'Pause' : 'Play'}
                  >{playing ? '⏸' : '▶'}</button>

                  <button
                    onClick={() => { setPlaying(false); setDetIdx((i) => Math.min(dets.length - 1, i + 1)) }}
                    className="w-8 h-8 flex items-center justify-center rounded-full bg-slate-700 hover:bg-slate-600 text-white transition-colors text-sm"
                    title="Next frame"
                  >▶</button>

                  {/* Scrubber */}
                  <input
                    type="range"
                    min={0}
                    max={dets.length - 1}
                    value={detIdx}
                    onChange={(e) => { setPlaying(false); setDetIdx(Number(e.target.value)) }}
                    className="flex-1 accent-brand"
                  />
                </div>
              )}

              <div className="p-4 space-y-4">
                {/* Metadata grid */}
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: 'Class',      value: <span className={`text-xs px-2 py-0.5 rounded-full border capitalize ${classColor(detail.class_label)}`}>{detail.class_label ?? '—'}</span> },
                    { label: 'Confidence', value: fmtConfidence(detail.confidence_max) },
                    { label: 'Camera',     value: detail.camera_name ?? '—' },
                    { label: 'Detections', value: dets.length || detail.detection_count || '—' },
                    { label: 'Started',    value: startTime ?? '—' },
                    { label: 'Ended',      value: endTime ?? '—' },
                    { label: 'Duration',   value: duration ?? '—' },
                    { label: 'Frames',     value: detail.first_frame != null ? `${detail.first_frame} – ${detail.last_frame}` : '—' },
                  ].map(({ label, value }) => (
                    <div key={label} className="bg-slate-800/60 rounded-lg px-3 py-2.5 border border-slate-700">
                      <p className="text-slate-500 text-xs mb-1">{label}</p>
                      <div className="text-slate-200 text-sm font-medium">{value}</div>
                    </div>
                  ))}
                </div>

                {/* Detection list — clickable rows */}
                {dets.length > 0 && (
                  <div>
                    <h4 className="text-slate-400 text-xs font-medium uppercase tracking-wide mb-2">Detections</h4>
                    <div className="rounded-lg border border-slate-700 divide-y divide-slate-700/50 overflow-hidden">
                      {dets.map((d, i) => (
                        <button
                          key={d.id}
                          onClick={() => { setPlaying(false); setDetIdx(i) }}
                          className={`w-full flex items-center gap-3 px-3 py-2 text-xs text-left transition-colors ${i === detIdx ? 'bg-brand/20 border-l-2 border-brand' : 'hover:bg-slate-800/60'}`}
                        >
                          <span className="text-slate-500 font-mono w-16 shrink-0">f {d.frame_index}</span>
                          <span className="text-slate-300 flex-1 capitalize">{d.class_label ?? '—'}</span>
                          <span className="text-brand font-mono">{fmtConfidence(d.confidence)}</span>
                          {d.bbox && <span className="text-slate-600 font-mono">{d.bbox.w}×{d.bbox.h}</span>}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
      </div>
    </>
  )
}

// ── Filter bar ────────────────────────────────────────────────────────────────
const KNOWN_CLASSES = ['person', 'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'boat', 'airplane', 'train', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear']

function FilterBar({ filters, onChange, cameras, total }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Camera */}
      <select
        value={filters.camera}
        onChange={(e) => onChange({ ...filters, camera: e.target.value, page: 1 })}
        className="bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
      >
        <option value="">All cameras</option>
        {cameras.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>

      {/* Class */}
      <select
        value={filters.class_label}
        onChange={(e) => onChange({ ...filters, class_label: e.target.value, page: 1 })}
        className="bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
      >
        <option value="">All classes</option>
        {KNOWN_CLASSES.map((c) => <option key={c} value={c} className="capitalize">{c}</option>)}
      </select>

      {/* Sort */}
      <select
        value={filters.sort}
        onChange={(e) => onChange({ ...filters, sort: e.target.value, page: 1 })}
        className="bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
      >
        <option value="newest">Newest first</option>
        <option value="oldest">Oldest first</option>
        <option value="confidence">Highest confidence</option>
        <option value="class">Class A–Z</option>
      </select>

      {/* Result count */}
      {total != null && (
        <span className="text-slate-500 text-sm ml-auto">
          {total.toLocaleString()} track{total !== 1 ? 's' : ''}
        </span>
      )}

      {/* Clear filters */}
      {(filters.camera || filters.class_label) && (
        <button
          onClick={() => onChange({ ...filters, camera: '', class_label: '', page: 1 })}
          className="text-xs text-slate-400 hover:text-white border border-slate-600 rounded-lg px-2.5 py-1.5 hover:border-slate-500 transition-colors"
        >
          Clear filters
        </button>
      )}
    </div>
  )
}

// ── Pagination ────────────────────────────────────────────────────────────────
function Pagination({ page, totalPages, onChange }) {
  if (totalPages <= 1) return null
  const pages = []
  const start = Math.max(1, page - 2)
  const end = Math.min(totalPages, page + 2)
  for (let i = start; i <= end; i++) pages.push(i)

  return (
    <div className="flex items-center justify-center gap-1 mt-6">
      <button
        onClick={() => onChange(1)}
        disabled={page === 1}
        className="px-2.5 py-1.5 bg-slate-800 border border-slate-700 text-slate-400 text-xs rounded-lg disabled:opacity-40 hover:bg-slate-700 transition-colors"
      >
        ««
      </button>
      <button
        onClick={() => onChange(page - 1)}
        disabled={page === 1}
        className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg disabled:opacity-40 hover:bg-slate-700 transition-colors"
      >
        ← Prev
      </button>
      {start > 1 && <span className="text-slate-600 px-1">…</span>}
      {pages.map((p) => (
        <button
          key={p}
          onClick={() => onChange(p)}
          className={`px-3 py-1.5 border text-sm rounded-lg transition-colors ${
            p === page
              ? 'bg-brand border-brand text-white'
              : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
          }`}
        >
          {p}
        </button>
      ))}
      {end < totalPages && <span className="text-slate-600 px-1">…</span>}
      <button
        onClick={() => onChange(page + 1)}
        disabled={page === totalPages}
        className="px-3 py-1.5 bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg disabled:opacity-40 hover:bg-slate-700 transition-colors"
      >
        Next →
      </button>
      <button
        onClick={() => onChange(totalPages)}
        disabled={page === totalPages}
        className="px-2.5 py-1.5 bg-slate-800 border border-slate-700 text-slate-400 text-xs rounded-lg disabled:opacity-40 hover:bg-slate-700 transition-colors"
      >
        »»
      </button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
const PAGE_SIZE = 24

export default function Tracks() {
  const [data, setData] = useState(null)
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [filters, setFilters] = useState({
    camera: '',
    class_label: '',
    sort: 'newest',
    page: 1,
  })

  // Load camera list once
  useEffect(() => {
    api.cameras().then(setCameras).catch(() => {})
  }, [])

  // Load tracks whenever filters change
  const load = useCallback(async (f) => {
    setLoading(true)
    try {
      const params = { page: f.page, page_size: PAGE_SIZE, sort: f.sort }
      if (f.camera) params.camera = f.camera
      if (f.class_label) params.class_label = f.class_label
      const res = await api.tracks(params)
      setData(res)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(filters) }, [filters, load])

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  return (
    <div className="p-6 min-h-full">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-white">Tracked Objects</h2>
        <p className="text-slate-400 text-sm mt-1">
          Objects detected and classified across all camera feeds
        </p>
      </div>

      {/* Filters */}
      <div className="mb-5">
        <FilterBar
          filters={filters}
          onChange={setFilters}
          cameras={cameras}
          total={data?.total}
        />
      </div>

      {/* Grid */}
      {loading && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
          {Array.from({ length: PAGE_SIZE }).map((_, i) => (
            <div key={i} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden animate-pulse">
              <div className="aspect-video bg-slate-700" />
              <div className="p-3 space-y-2">
                <div className="h-3 bg-slate-700 rounded w-3/4" />
                <div className="h-2 bg-slate-700 rounded w-1/2" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!loading && data?.items?.length === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-slate-500">
          <span className="text-5xl mb-4">🎯</span>
          <p className="text-lg font-medium text-slate-400">No tracked objects found</p>
          <p className="text-sm mt-1">
            {filters.camera || filters.class_label
              ? 'Try clearing your filters'
              : 'Drop a video into the ingest folder to get started'}
          </p>
        </div>
      )}

      {!loading && data?.items?.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
          {data.items.map((track) => (
            <TrackCard key={track.id} track={track} onClick={(t) => setSelectedId(t.id)} />
          ))}
        </div>
      )}

      {/* Pagination */}
      <Pagination
        page={filters.page}
        totalPages={totalPages}
        onChange={(p) => setFilters((f) => ({ ...f, page: p }))}
      />

      {/* Detail drawer */}
      {selectedId != null && (
        <TrackDrawer
          trackId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  )
}
