// Browser playback profiling.
//
// Source clips are 11MP HEVC, which browsers can't decode. The backend serves
// an H.264 rendition sized to what THIS browser can decode and the network can
// carry. We profile that once and cache it (keyed by a fingerprint) in
// localStorage so we don't re-probe on every clip.
//
// Two inputs:
//   • max decodable H.264 height — probed via navigator.mediaCapabilities
//   • bandwidth estimate — Network Information API where available, else LAN default
// The chosen rung is min(decodeCeiling, bandwidthCeiling), snapped to the
// backend ladder.

const LADDER = [360, 480, 720, 1080, 1440]
const BITRATE_K = { 360: 800, 480: 1500, 720: 3000, 1080: 6000, 1440: 12000 }

// Ascending probe tiers. codecs strings are H.264 profile/level hints; the
// width/height/bitrate are what actually drive the smooth/powerEfficient answer.
const PROBE = [
  { h: 360,  w: 640,  codec: 'avc1.42E01E' }, // Constrained Baseline L3.0
  { h: 480,  w: 854,  codec: 'avc1.4D401F' }, // Main L3.1
  { h: 720,  w: 1280, codec: 'avc1.4D4020' }, // Main L3.2
  { h: 1080, w: 1920, codec: 'avc1.640028' }, // High L4.0
  { h: 1440, w: 2560, codec: 'avc1.640032' }, // High L5.0
]

const STORAGE_KEY = 'sentinel_playback_profile'
const PROFILE_VERSION = 1 // bump to invalidate cached profiles after logic changes

async function maxDecodableHeight() {
  const mc = navigator.mediaCapabilities
  if (!mc || !mc.decodingInfo) return 1080 // safe, widely-decodable default
  let best = LADDER[0]
  for (const p of PROBE) {
    try {
      const info = await mc.decodingInfo({
        type: 'file',
        video: {
          contentType: `video/mp4; codecs="${p.codec}"`,
          width: p.w,
          height: p.h,
          bitrate: (BITRATE_K[p.h] || 6000) * 1000,
          framerate: 30,
        },
      })
      // Tiers ascend; stop at the first that isn't smoothly decodable.
      if (info.supported && info.smooth) best = p.h
      else break
    } catch {
      break
    }
  }
  return best
}

function bandwidthKbps() {
  const c = navigator.connection
  if (c && typeof c.downlink === 'number' && c.downlink > 0) {
    return Math.round(c.downlink * 1000 * 0.8) // Mbps → kbps, keep 20% headroom
  }
  return 25000 // no Network Information API (Firefox/Safari) → assume LAN-grade
}

function rungForBandwidth(kbps) {
  let best = LADDER[0]
  for (const h of LADDER) if (BITRATE_K[h] <= kbps) best = h
  return best
}

/**
 * Returns the cached profile, or profiles the browser once and caches it.
 * Shape: { v, maxH, kbps, bwRung, targetHeight, fingerprint }
 */
export async function getPlaybackProfile() {
  try {
    const cached = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (cached && cached.v === PROFILE_VERSION && cached.targetHeight) return cached
  } catch {
    /* ignore corrupt cache */
  }
  const maxH = await maxDecodableHeight()
  const kbps = bandwidthKbps()
  const bwRung = rungForBandwidth(kbps)
  const targetHeight = Math.min(maxH, bwRung)
  const profile = {
    v: PROFILE_VERSION,
    maxH,
    kbps,
    bwRung,
    targetHeight,
    fingerprint: `${PROFILE_VERSION}-${maxH}-${bwRung}`,
  }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile))
  } catch {
    /* storage full / disabled — fine, we'll re-profile next time */
  }
  return profile
}
