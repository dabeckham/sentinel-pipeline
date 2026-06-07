import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
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
const KNOWN_CLASSES = ['person','car','truck','bus','motorcycle','bicycle','boat','airplane','train','bird','cat','dog','horse','sheep','cow','elephant','bear']

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
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}
function toDateStr(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function toTimeStr(d) {
  return d.toTimeString().slice(0, 5)
}

// ── Snapshot image with no-flicker crossfade ──────────────────────────────────
function SnapshotImg({ path, alt = 'snapshot', onNaturalSize }) {
  const [blobUrl, setBlobUrl] = useState(null)
  const [errored, setErrored] = useState(false)
  const currentUrlRef = useRef(null)

  useEffect(() => () => { if (currentUrlRef.current) URL.revokeObjectURL(currentUrlRef.current) }, [])

  useEffect(() => {
    if (!path) return
    setErrored(false)
    let cancelled = false
    const token = localStorage.getItem('sentinel_token')
    fetch(`/api/snapshots/${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(res => { if (!res.ok) throw new Error(res.status); return res.blob() })
      .then(blob => {
        if (cancelled) return
        const url = URL.createObjectURL(blob)
        if (currentUrlRef.current) URL.revokeObjectURL(currentUrlRef.current)
        currentUrlRef.current = url
        setBlobUrl(url)
      })
      .catch(() => { if (!cancelled) setErrored(true) })
    return () => { cancelled = true }
  }, [path])

  if (!path || errored) {
    return <div className="w-full h-full flex items-center justify-center bg-slate-700/60 text-slate-500 text-3xl">🖼️</div>
  }
  if (!blobUrl) {
    return <div className="w-full h-full bg-slate-700/60 animate-pulse" />
  }
  return (
    <img
      src={blobUrl}
      alt={alt}
      onLoad={e => onNaturalSize?.({ w: e.target.naturalWidth, h: e.target.naturalHeight })}
      style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'contain' }}
    />
  )
}

// ── Bbox overlay — SVG rect positioned over the snapshot ─────────────────────
// When transformState is provided, maps bbox coords through the CSS transform
// (translate+scale) so the rect lands at the correct screen position even when
// the image container has been zoomed/panned.
function BboxOverlay({ bbox, imgSize, viewerEl, zoom = 1, transformState = null }) {
  if (!bbox || !imgSize || !viewerEl) return null
  const vw = viewerEl.clientWidth
  const vh = viewerEl.clientHeight
  if (!vw || !vh) return null

  const fitScale = Math.min(vw / imgSize.w, vh / imgSize.h)
  const ox = (vw - imgSize.w * fitScale) / 2
  const oy = (vh - imgSize.h * fitScale) / 2

  // Bbox position in the unzoomed container coordinate space
  let rx = ox + bbox.x * fitScale
  let ry = oy + bbox.y * fitScale
  let rw = bbox.w * fitScale
  let rh = bbox.h * fitScale

  // If there is a CSS transform (translate(tx,ty) scale(z), origin = center),
  // map from child coords to parent screen coords.
  if (transformState) {
    const { tx, ty, z } = transformState
    const cx = vw / 2
    const cy = vh / 2
    rx = cx + (rx - cx) * z + tx
    ry = cy + (ry - cy) * z + ty
    rw = rw * z
    rh = rh * z
  }

  const sw = Math.max(0.5, 2 / (zoom || 1))

  return (
    <svg
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
      viewBox={`0 0 ${vw} ${vh}`}
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        x={rx} y={ry} width={Math.max(1, rw)} height={Math.max(1, rh)}
        fill="rgba(34,211,238,0.08)"
        stroke="#22d3ee"
        strokeWidth={sw}
        strokeDasharray="4 2"
      />
    </svg>
  )
}

// ── Track card ────────────────────────────────────────────────────────────────
function TrackCard({ track, onClick }) {
  const duration = fmtDuration(track.started_at, track.ended_at)
  const time = fmtTime(track.started_at)
  const [thumbSize, setThumbSize] = useState(null)
  const thumbRef = useRef(null)

  // Compute zoom transform to center the bbox in the thumbnail.
  // Returns { css, tx, ty, z } or null.
  const thumbMemo = useMemo(() => {
    const bbox = track.snapshot_bbox
    if (!bbox || !thumbSize || !thumbRef.current) return null
    const cw = thumbRef.current.clientWidth
    const ch = thumbRef.current.clientHeight
    const { w: iw, h: ih } = thumbSize
    const fitScale = Math.min(cw / iw, ch / ih)
    const ox = (cw - iw * fitScale) / 2
    const oy = (ch - ih * fitScale) / 2
    const bcx = ox + (bbox.x + bbox.w / 2) * fitScale
    const bcy = oy + (bbox.y + bbox.h / 2) * fitScale
    const z = Math.min(6, Math.max(1.2,
      Math.min(cw * 0.7 / (bbox.w * fitScale), ch * 0.7 / (bbox.h * fitScale))
    ))
    const tx = (cw / 2 - bcx) * z
    const ty = (ch / 2 - bcy) * z
    return { css: `translate(${tx}px, ${ty}px) scale(${z})`, tx, ty, z }
  }, [track.snapshot_bbox, thumbSize])

  return (
    <button
      onClick={() => onClick(track)}
      className="group bg-slate-800 border border-slate-700 rounded-xl overflow-hidden hover:border-brand/60 hover:shadow-lg hover:shadow-brand/10 transition-all text-left w-full"
    >
      {/* Thumbnail */}
      <div ref={thumbRef} className="relative w-full overflow-hidden bg-slate-700/40" style={{ height: '160px' }}>
        {/* Zoomed image layer */}
        <div style={{
          position: 'absolute', inset: 0,
          transform: thumbMemo?.css ?? 'none',
          transformOrigin: 'center center',
          transition: 'transform 0.3s ease',
        }}>
          <SnapshotImg path={track.snapshot_path} onNaturalSize={setThumbSize} />
        </div>

        {/* BboxOverlay is OUTSIDE the transform div so it maps to screen coords */}
        {thumbMemo && (
          <BboxOverlay
            bbox={track.snapshot_bbox}
            imgSize={thumbSize}
            viewerEl={thumbRef.current}
            zoom={1}
            transformState={{ tx: thumbMemo.tx, ty: thumbMemo.ty, z: thumbMemo.z }}
          />
        )}

        {/* Class badge */}
        <div className="absolute top-2 left-2">
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full border capitalize ${classColor(track.class_label)}`}>
            {track.class_label ?? 'unknown'}
          </span>
        </div>
        {/* Confidence */}
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
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-brand font-medium truncate">
            📷 {track.camera_name ?? `Job #${track.job_id}`}
          </span>
          <span className="text-xs text-slate-500 shrink-0">#{track.id}</span>
        </div>
        {time && <p className="text-xs text-slate-400 truncate">{time}</p>}
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {duration && <span>⏱ {duration}</span>}
          {track.detection_count != null && (
            <span>🎯 {track.detection_count} detection{track.detection_count !== 1 ? 's' : ''}</span>
          )}
        </div>
        {track.confidence_max != null && (
          <div className="w-full bg-slate-700 rounded-full h-1 mt-1">
            <div className="bg-brand h-1 rounded-full transition-all"
              style={{ width: `${(track.confidence_max * 100).toFixed(0)}%` }} />
          </div>
        )}
      </div>
    </button>
  )
}

// ── Detail modal ──────────────────────────────────────────────────────────────
function TrackDrawer({ trackId, onClose }) {
  const [detail, setDetail]   = useState(null)
  const [loading, setLoading] = useState(true)
  const [detIdx, setDetIdx]   = useState(0)
  const [playing, setPlaying] = useState(false)
  const playRef = useRef(null)

  useEffect(() => {
    if (!trackId) return
    setLoading(true); setDetail(null); setDetIdx(0); setPlaying(false)
    api.track(trackId).then(setDetail).finally(() => setLoading(false))
  }, [trackId])

  useEffect(() => {
    if (!playing || !detail?.detections?.length) return
    playRef.current = setInterval(() => {
      setDetIdx(i => {
        if (i >= detail.detections.length - 1) { setPlaying(false); return i }
        return i + 1
      })
    }, 250)
    return () => clearInterval(playRef.current)
  }, [playing, detail])

  useEffect(() => {
    const h = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const dets = detail?.detections ?? []
  const curDet = dets[detIdx] ?? null
  const hasCrops = dets.some(d => d.crop_path)
  const currentSnapshotPath = hasCrops
    ? (curDet?.crop_path ?? detail?.snapshot_path)
    : detail?.snapshot_path

  const [autoZoom, setAutoZoom] = useState(() => localStorage.getItem('sentinel_autoZoom') !== 'false')
  const toggleAutoZoom = v => {
    setAutoZoom(v)
    localStorage.setItem('sentinel_autoZoom', v ? 'true' : 'false')
    if (!v) { setZoom(1); setPan({ x: 0, y: 0 }) }
  }

  const [zoom, setZoom] = useState(1)
  const [pan, setPan]   = useState({ x: 0, y: 0 })
  const [imgSize, setImgSize] = useState(null)
  const dragRef   = useRef(null)
  const viewerRef = useRef(null)

  useEffect(() => {
    if (!autoZoom) return
    const bbox = curDet?.bbox
    const viewer = viewerRef.current
    if (!bbox || !imgSize || !viewer) { setZoom(1); setPan({ x: 0, y: 0 }); return }
    const vw = viewer.clientWidth; const vh = viewer.clientHeight
    const fitScale = Math.min(vw / imgSize.w, vh / imgSize.h)
    const ox = (vw - imgSize.w * fitScale) / 2; const oy = (vh - imgSize.h * fitScale) / 2
    const bcx = ox + (bbox.x + bbox.w / 2) * fitScale
    const bcy = oy + (bbox.y + bbox.h / 2) * fitScale
    const z = Math.min(8, Math.max(1.5, Math.min(vw * 0.6 / (bbox.w * fitScale), vh * 0.6 / (bbox.h * fitScale))))
    setZoom(z)
    setPan({ x: (vw / 2 - bcx) * z, y: (vh / 2 - bcy) * z })
  }, [detIdx, imgSize, autoZoom]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleWheel = useCallback(e => {
    e.preventDefault(); e.stopPropagation()
    setZoom(z => { const n = Math.min(8, Math.max(1, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15))); if (n === 1) setPan({ x: 0, y: 0 }); return n })
  }, [])

  useEffect(() => {
    const el = viewerRef.current
    if (!el) return
    el.addEventListener('wheel', handleWheel, { passive: false, capture: true })
    return () => el.removeEventListener('wheel', handleWheel, { capture: true })
  }, [handleWheel])

  const handleMouseDown  = e => { if (zoom <= 1) return; e.preventDefault(); dragRef.current = { startX: e.clientX - pan.x, startY: e.clientY - pan.y } }
  const handleMouseMove  = e => { if (!dragRef.current) return; setPan({ x: e.clientX - dragRef.current.startX, y: e.clientY - dragRef.current.startY }) }
  const handleMouseUp    = () => { dragRef.current = null }
  const handleDoubleClick = () => { setZoom(1); setPan({ x: 0, y: 0 }) }

  const duration = detail ? fmtDuration(detail.started_at, detail.ended_at) : null
  const startTime = detail ? fmtTime(detail.started_at) : null
  const endTime   = detail ? fmtTime(detail.ended_at)   : null

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto w-full max-w-lg max-h-[90vh] bg-slate-900 border border-slate-700 rounded-2xl flex flex-col shadow-2xl overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700 shrink-0">
            <div>
              <h3 className="text-white font-semibold text-base">
                {detail ? <span className="capitalize">{detail.class_label ?? 'Unknown'}</span> : 'Track Detail'}
              </h3>
              {detail && (
                <p className="text-slate-400 text-xs mt-0.5">
                  Track #{detail.track_id} · Job #{detail.job_id}
                  {detail.camera_name && ` · ${detail.camera_name}`}
                </p>
              )}
            </div>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
                <input type="checkbox" checked={autoZoom}
                  onChange={e => toggleAutoZoom(e.target.checked)}
                  className="accent-brand w-3 h-3" />
                Auto-zoom
              </label>
              <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors text-xl leading-none">✕</button>
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto">
            {loading && <div className="flex items-center justify-center h-48 text-slate-400">Loading…</div>}
            {!loading && detail && (
              <div className="flex flex-col overflow-y-auto">
                {/* Snapshot viewer */}
                <div
                  ref={viewerRef}
                  className="relative w-full bg-black shrink-0 overflow-hidden"
                  style={{ height: '280px', cursor: zoom > 1 ? (dragRef.current ? 'grabbing' : 'grab') : 'default' }}
                  onMouseDown={handleMouseDown} onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp} onMouseLeave={handleMouseUp}
                  onDoubleClick={handleDoubleClick}
                >
                  <div style={{
                    position: 'absolute', inset: 0,
                    transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                    transformOrigin: 'center center',
                    transition: dragRef.current ? 'none' : 'transform 0.15s ease',
                    willChange: 'transform',
                  }}>
                    <SnapshotImg path={currentSnapshotPath} onNaturalSize={setImgSize} />
                    <BboxOverlay bbox={curDet?.bbox} imgSize={imgSize} viewerEl={viewerRef.current} zoom={zoom} />
                  </div>
                  {dets.length > 0 && (
                    <div className="absolute top-2 right-2 bg-black/70 text-white text-xs font-mono px-2 py-1 rounded pointer-events-none">
                      {detIdx + 1} / {dets.length}
                    </div>
                  )}
                  {curDet && (
                    <div className="absolute top-2 left-2 bg-black/70 text-white text-xs px-2 py-1 rounded pointer-events-none">
                      f{curDet.frame_index} · {fmtConfidence(curDet.confidence)}
                    </div>
                  )}
                  {zoom === 1 && (
                    <div className="absolute bottom-2 right-2 bg-black/50 text-slate-400 text-xs px-2 py-1 rounded pointer-events-none">
                      scroll to zoom · drag to pan · dbl-click reset
                    </div>
                  )}
                  {zoom > 1 && (
                    <div className="absolute bottom-2 right-2 bg-black/50 text-slate-400 text-xs px-2 py-1 rounded pointer-events-none">
                      {Math.round(zoom * 100)}%
                    </div>
                  )}
                </div>

                {!hasCrops && dets.length > 0 && (
                  <div className="text-center text-xs text-slate-500 py-2 bg-slate-800/60 border-b border-slate-700">
                    Frame-by-frame playback available for tracks processed after v0.5.1
                  </div>
                )}

                {hasCrops && dets.length > 0 && (
                  <div className="flex items-center justify-center gap-3 px-4 py-3 bg-slate-800/80 border-b border-slate-700 shrink-0">
                    <button onClick={() => { setPlaying(false); setDetIdx(i => Math.max(0, i - 1)) }}
                      className="w-8 h-8 flex items-center justify-center rounded-full bg-slate-700 hover:bg-slate-600 text-white transition-colors text-sm">◀</button>
                    <button onClick={() => setPlaying(p => !p)}
                      className="w-10 h-10 flex items-center justify-center rounded-full bg-brand hover:bg-brand/80 text-white transition-colors text-base font-bold">
                      {playing ? '⏸' : '▶'}
                    </button>
                    <button onClick={() => { setPlaying(false); setDetIdx(i => Math.min(dets.length - 1, i + 1)) }}
                      className="w-8 h-8 flex items-center justify-center rounded-full bg-slate-700 hover:bg-slate-600 text-white transition-colors text-sm">▶</button>
                    <input type="range" min={0} max={dets.length - 1} value={detIdx}
                      onChange={e => { setPlaying(false); setDetIdx(Number(e.target.value)) }}
                      className="flex-1 accent-brand" />
                  </div>
                )}

                <div className="p-4 space-y-4">
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
                  {dets.length > 0 && (
                    <div>
                      <h4 className="text-slate-400 text-xs font-medium uppercase tracking-wide mb-2">Detections</h4>
                      <div className="rounded-lg border border-slate-700 divide-y divide-slate-700/50 overflow-hidden">
                        {dets.map((d, i) => (
                          <button key={d.id}
                            onClick={() => hasCrops && (setPlaying(false), setDetIdx(i))}
                            className={`w-full flex items-center gap-3 px-3 py-2 text-xs text-left transition-colors ${hasCrops && i === detIdx ? 'bg-brand/20 border-l-2 border-brand' : hasCrops ? 'hover:bg-slate-800/60' : 'cursor-default'}`}
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

// ── MultiSelectDropdown ───────────────────────────────────────────────────────
// selected: string[] — empty means "All"
function MultiSelectDropdown({ label, options, selected, onChange, placeholder }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const h = e => { if (!ref.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const isAll = selected.length === 0

  const toggleItem = val => {
    if (selected.includes(val)) {
      onChange(selected.filter(v => v !== val))
    } else {
      onChange([...selected, val])
    }
  }

  const displayLabel = isAll
    ? (placeholder ?? `All ${label}`)
    : selected.length === 1
      ? selected[0]
      : `${selected.length} ${label}`

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`flex items-center gap-2 bg-slate-800 border text-sm rounded-lg px-3 py-1.5 focus:outline-none transition-colors whitespace-nowrap
          ${open ? 'border-brand text-white' : 'border-slate-700 text-slate-300 hover:border-slate-600'}`}
      >
        <span className="capitalize">{displayLabel}</span>
        <svg className={`w-3.5 h-3.5 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full mt-1 left-0 z-30 bg-slate-800 border border-slate-700 rounded-xl shadow-xl min-w-[180px] py-1.5 max-h-72 overflow-y-auto">
          {/* All option */}
          <button
            onClick={() => { onChange([]); setOpen(false) }}
            className={`w-full flex items-center justify-between px-3 py-2 text-sm text-left transition-colors
              ${isAll ? 'text-brand bg-brand/10' : 'text-slate-300 hover:bg-slate-700/60'}`}
          >
            <span>All {label}</span>
            {isAll && (
              <svg className="w-4 h-4 text-brand shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </button>
          <div className="h-px bg-slate-700/50 mx-2 my-1" />
          {options.map(opt => {
            const active = selected.includes(opt.value)
            return (
              <button
                key={opt.value}
                onClick={() => toggleItem(opt.value)}
                className={`w-full flex items-center justify-between px-3 py-2 text-sm text-left transition-colors capitalize
                  ${active ? 'text-cyan-300 bg-cyan-500/10' : 'text-slate-300 hover:bg-slate-700/60'}`}
              >
                <span>{opt.label}</span>
                {/* Toggle indicator on the right */}
                <span className={`ml-3 w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors
                  ${active ? 'bg-brand border-brand' : 'border-slate-600'}`}>
                  {active && (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Mini calendar for date range modal ───────────────────────────────────────
const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December']

function MiniCalendar({ year, month, fromDate, toDate, activeDays, onDayClick, onMonthNav }) {
  const daysInMonth = new Date(year, month, 0).getDate()   // month is 1-indexed
  const firstDow    = new Date(year, month - 1, 1).getDay() // 0=Sun
  const todayStr    = toDateStr(new Date())

  const cells = Array(firstDow).fill(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)
  while (cells.length % 7 !== 0) cells.push(null)

  const pad = n => String(n).padStart(2, '0')

  return (
    <div className="select-none">
      {/* Month nav */}
      <div className="flex items-center justify-between mb-4">
        <button onClick={() => onMonthNav(-1)}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-slate-700 text-slate-300 transition-colors text-lg">
          ‹
        </button>
        <span className="text-white font-semibold text-sm">{MONTH_NAMES[month - 1]} {year}</span>
        <button onClick={() => onMonthNav(1)}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-slate-700 text-slate-300 transition-colors text-lg">
          ›
        </button>
      </div>

      {/* DOW headers */}
      <div className="grid grid-cols-7 mb-1">
        {['Su','Mo','Tu','We','Th','Fr','Sa'].map(d => (
          <div key={d} className="text-center text-slate-500 text-xs py-1 font-medium">{d}</div>
        ))}
      </div>

      {/* Day cells */}
      <div className="grid grid-cols-7 gap-y-0.5">
        {cells.map((day, i) => {
          if (!day) return <div key={`e${i}`} />
          const ds = `${year}-${pad(month)}-${pad(day)}`
          const isFrom  = ds === fromDate
          const isTo    = ds === toDate
          const inRange = fromDate && toDate && fromDate < toDate && ds > fromDate && ds < toDate
          const hasData = activeDays.includes(ds)
          const isToday = ds === todayStr
          return (
            <div key={day} className="flex justify-center">
              <button
                onClick={() => onDayClick(ds)}
                className={`
                  relative w-8 h-8 flex items-center justify-center rounded-full text-xs font-medium transition-colors
                  ${isFrom || isTo
                    ? 'bg-brand text-white shadow-md shadow-brand/30'
                    : inRange
                      ? 'bg-cyan-500/15 text-cyan-300 rounded-none'
                      : 'text-slate-300 hover:bg-slate-700'}
                  ${isToday && !isFrom && !isTo ? 'ring-1 ring-slate-400' : ''}
                `}
              >
                {day}
                {hasData && !isFrom && !isTo && (
                  <span className="absolute bottom-0.5 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-brand/70" />
                )}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Date range modal ──────────────────────────────────────────────────────────
function DateRangeModal({ initialFrom, initialTo, cameras, classes, onApply, onClose }) {
  const now = new Date()

  const defaultFrom = initialFrom ?? (() => { const d = new Date(now); d.setHours(d.getHours() - 1); return d })()
  const defaultTo   = initialTo ?? now

  const [fromDate, setFromDate] = useState(toDateStr(defaultFrom))
  const [toDate,   setToDate]   = useState(toDateStr(defaultTo))
  const [fromTime, setFromTime] = useState(toTimeStr(defaultFrom))
  const [toTime,   setToTime]   = useState(toTimeStr(defaultTo))
  const [calYear,  setCalYear]  = useState(now.getFullYear())
  const [calMonth, setCalMonth] = useState(now.getMonth() + 1)
  const [activeDays, setActiveDays] = useState([])
  const [phase, setPhase] = useState('from')  // 'from' | 'to'

  // Close on Escape
  useEffect(() => {
    const h = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  // Fetch active days for current calendar month + current filter context
  useEffect(() => {
    api.activeDays({
      year: calYear, month: calMonth,
      camera: cameras, class_label: classes,
    })
      .then(days => setActiveDays(days))
      .catch(() => setActiveDays([]))
  }, [calYear, calMonth, cameras.join(','), classes.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleMonthNav = delta => {
    let m = calMonth + delta, y = calYear
    if (m < 1) { m = 12; y-- }
    if (m > 12) { m = 1;  y++ }
    setCalMonth(m); setCalYear(y)
  }

  const handleDayClick = ds => {
    if (phase === 'from' || ds < fromDate) {
      setFromDate(ds); setToDate(ds); setPhase('to')
    } else {
      setToDate(ds); setPhase('from')
    }
  }

  const handleApply = () => {
    const from = new Date(`${fromDate}T${fromTime}:00`)
    const to   = new Date(`${toDate}T${toTime}:00`)
    if (isNaN(from) || isNaN(to)) return
    onApply(from, to)
  }

  const rangeLabel = fromDate === toDate
    ? fromDate
    : `${fromDate} → ${toDate}`

  return (
    <>
      <div className="fixed inset-0 bg-black/60 z-50 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
            <div>
              <h3 className="text-white font-semibold text-sm">Custom Date Range</h3>
              <p className="text-slate-400 text-xs mt-0.5">{rangeLabel}</p>
            </div>
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl leading-none transition-colors">✕</button>
          </div>

          {/* Calendar */}
          <div className="px-5 py-4 border-b border-slate-700/50">
            <MiniCalendar
              year={calYear} month={calMonth}
              fromDate={fromDate} toDate={toDate}
              activeDays={activeDays}
              onDayClick={handleDayClick}
              onMonthNav={handleMonthNav}
            />
            <p className="text-slate-500 text-xs text-center mt-3">
              {phase === 'from' ? 'Click to set start date' : 'Click to set end date'}
              {activeDays.length > 0 && <> · <span className="text-brand/70">●</span> = has data</>}
            </p>
          </div>

          {/* Time inputs */}
          <div className="px-5 py-4 space-y-3 border-b border-slate-700/50">
            <div className="flex items-center gap-3">
              <span className="text-slate-400 text-xs w-8 shrink-0">From</span>
              <span className="text-slate-300 text-xs font-mono bg-slate-800 px-2 py-1 rounded flex-1 text-center border border-slate-700">{fromDate}</span>
              <input
                type="time"
                value={fromTime}
                onChange={e => setFromTime(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-brand"
              />
            </div>
            <div className="flex items-center gap-3">
              <span className="text-slate-400 text-xs w-8 shrink-0">To</span>
              <span className="text-slate-300 text-xs font-mono bg-slate-800 px-2 py-1 rounded flex-1 text-center border border-slate-700">{toDate}</span>
              <input
                type="time"
                value={toTime}
                onChange={e => setToTime(e.target.value)}
                className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-brand"
              />
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-2 px-5 py-4">
            <button onClick={onClose}
              className="flex-1 px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 text-sm rounded-lg transition-colors">
              Cancel
            </button>
            <button onClick={handleApply}
              className="flex-1 px-4 py-2 bg-brand hover:bg-brand/80 text-white text-sm font-medium rounded-lg transition-colors">
              Apply
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

// ── Time preset selector ──────────────────────────────────────────────────────
function TimeFilter({ preset, customRange, onPresetChange, onCustomOpen }) {
  const options = [
    { value: 'all',    label: 'All time' },
    { value: 'today',  label: 'Today' },
    { value: 'week',   label: 'This week' },
    { value: 'month',  label: 'This month' },
    { value: 'custom', label: preset === 'custom' && customRange
        ? `${toDateStr(customRange.from)} → ${toDateStr(customRange.to)}`
        : 'Custom…' },
  ]

  return (
    <select
      value={preset}
      onChange={e => {
        const v = e.target.value
        if (v === 'custom') { onCustomOpen(); return }
        onPresetChange(v)
      }}
      className="bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand"
    >
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
const PAGE_SIZE = 24

function getDateRange(preset, customRange) {
  const now = new Date()
  switch (preset) {
    case 'today': {
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
      return { from_dt: start.toISOString(), to_dt: now.toISOString() }
    }
    case 'week':
      return { from_dt: new Date(now - 7 * 86400000).toISOString(), to_dt: now.toISOString() }
    case 'month':
      return { from_dt: new Date(now - 30 * 86400000).toISOString(), to_dt: now.toISOString() }
    case 'custom':
      if (!customRange) return {}
      return { from_dt: customRange.from.toISOString(), to_dt: customRange.to.toISOString() }
    default:
      return {}
  }
}

export default function Tracks() {
  const [cameras, setCameraList] = useState([])
  const [selectedCameras, setSelectedCameras] = useState([])
  const [selectedClasses,  setSelectedClasses]  = useState([])
  const [sort, setSort] = useState('newest')
  const [timePreset, setTimePreset] = useState('all')
  const [customRange, setCustomRange] = useState(null)
  const [showDateModal, setShowDateModal] = useState(false)

  // Infinite scroll state
  const [items,   setItems]   = useState([])
  const [total,   setTotal]   = useState(null)
  const [page,    setPage]    = useState(1)
  const [loading, setLoading] = useState(false)
  const sentinelRef = useRef(null)

  const [selectedId, setSelectedId] = useState(null)

  // Load camera list once
  useEffect(() => { api.cameras().then(setCameraList).catch(() => {}) }, [])

  // A key that uniquely represents the current filter state (excluding page).
  // When this changes, reset the item list and go back to page 1.
  const filterKey = useMemo(() =>
    JSON.stringify({
      selectedCameras: [...selectedCameras].sort(),
      selectedClasses: [...selectedClasses].sort(),
      sort,
      timePreset,
      customFrom: customRange?.from?.toISOString() ?? '',
      customTo:   customRange?.to?.toISOString() ?? '',
    }),
    [selectedCameras, selectedClasses, sort, timePreset, customRange]
  )

  // Reset to page 1 whenever filters change
  const prevFilterKey = useRef(filterKey)
  useEffect(() => {
    if (prevFilterKey.current !== filterKey) {
      prevFilterKey.current = filterKey
      setItems([])
      setTotal(null)
      setPage(1)
    }
  }, [filterKey])

  // Load a page whenever page or filterKey changes
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const dateRange = getDateRange(timePreset, customRange)
    api.tracks({
      page,
      page_size: PAGE_SIZE,
      sort,
      camera:      selectedCameras,
      class_label: selectedClasses,
      ...dateRange,
    })
      .then(res => {
        if (cancelled) return
        setItems(prev => page === 1 ? res.items : [...prev, ...res.items])
        setTotal(res.total)
      })
      .catch(console.error)
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [page, filterKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const hasMore = total === null || items.length < total

  // Infinite scroll — observe the sentinel div at the bottom of the list
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting && hasMore && !loading) setPage(p => p + 1) },
      { threshold: 0.1 }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [hasMore, loading])

  const hasActiveFilters = selectedCameras.length > 0 || selectedClasses.length > 0 || timePreset !== 'all'

  const cameraOptions = cameras.map(c => ({ value: c, label: c }))
  const classOptions  = KNOWN_CLASSES.map(c => ({ value: c, label: c }))

  return (
    <div className="p-6 min-h-full">
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-white">Tracked Objects</h2>
        <p className="text-slate-400 text-sm mt-1">
          Objects detected and classified across all camera feeds
        </p>
      </div>

      {/* Filter bar */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <MultiSelectDropdown
          label="cameras"
          placeholder="All cameras"
          options={cameraOptions}
          selected={selectedCameras}
          onChange={v => setSelectedCameras(v)}
        />
        <MultiSelectDropdown
          label="classes"
          placeholder="All classes"
          options={classOptions}
          selected={selectedClasses}
          onChange={v => setSelectedClasses(v)}
        />
        <TimeFilter
          preset={timePreset}
          customRange={customRange}
          onPresetChange={v => { setTimePreset(v); setCustomRange(null) }}
          onCustomOpen={() => setShowDateModal(true)}
        />
        {/* Sort */}
        <select
          value={sort}
          onChange={e => setSort(e.target.value)}
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
        {hasActiveFilters && (
          <button
            onClick={() => {
              setSelectedCameras([]); setSelectedClasses([])
              setTimePreset('all'); setCustomRange(null)
            }}
            className="text-xs text-slate-400 hover:text-white border border-slate-600 rounded-lg px-2.5 py-1.5 hover:border-slate-500 transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
        {items.map(track => (
          <TrackCard key={track.id} track={track} onClick={t => setSelectedId(t.id)} />
        ))}

        {/* Skeleton cards while loading first page */}
        {loading && items.length === 0 && Array.from({ length: PAGE_SIZE }).map((_, i) => (
          <div key={i} className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden animate-pulse">
            <div style={{ height: '160px' }} className="bg-slate-700" />
            <div className="p-3 space-y-2">
              <div className="h-3 bg-slate-700 rounded w-3/4" />
              <div className="h-2 bg-slate-700 rounded w-1/2" />
            </div>
          </div>
        ))}
      </div>

      {/* Empty state */}
      {!loading && items.length === 0 && total === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-slate-500">
          <span className="text-5xl mb-4">🎯</span>
          <p className="text-lg font-medium text-slate-400">No tracked objects found</p>
          <p className="text-sm mt-1">
            {hasActiveFilters ? 'Try clearing your filters' : 'Drop a video into the ingest folder to get started'}
          </p>
        </div>
      )}

      {/* Infinite scroll sentinel + load-more spinner */}
      <div ref={sentinelRef} className="flex justify-center py-8">
        {loading && items.length > 0 && (
          <div className="flex items-center gap-2 text-slate-500 text-sm">
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            Loading more…
          </div>
        )}
        {!loading && !hasMore && items.length > 0 && (
          <p className="text-slate-600 text-xs">All {total?.toLocaleString()} tracks loaded</p>
        )}
      </div>

      {/* Date range modal */}
      {showDateModal && (
        <DateRangeModal
          initialFrom={customRange?.from}
          initialTo={customRange?.to}
          cameras={selectedCameras}
          classes={selectedClasses}
          onApply={(from, to) => {
            setCustomRange({ from, to })
            setTimePreset('custom')
            setShowDateModal(false)
          }}
          onClose={() => setShowDateModal(false)}
        />
      )}

      {/* Detail modal */}
      {selectedId != null && (
        <TrackDrawer trackId={selectedId} onClose={() => setSelectedId(null)} />
      )}
    </div>
  )
}
