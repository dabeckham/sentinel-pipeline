const BASE = '/api'

function token() {
  return localStorage.getItem('sentinel_token')
}

function authHeaders() {
  const t = token()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

async function req(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: authHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    localStorage.removeItem('sentinel_token')
    window.location.href = '/login'
    return
  }
  if (!res.ok) {
    const err = await res.text()
    throw new Error(err || res.statusText)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  login: (username, password) => req('POST', '/auth/login', { username, password }),
  stats: () => req('GET', '/stats'),
  jobs: (page = 1, pageSize = 25, status = '') =>
    req('GET', `/jobs?page=${page}&page_size=${pageSize}${status ? `&status=${status}` : ''}`),
  job: (id) => req('GET', `/jobs/${id}`),
  jobTracks: (id) => req('GET', `/jobs/${id}/tracks`),
  tracks: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach(item => qs.append(k, item))
      else if (v != null && v !== '') qs.set(k, v)
    })
    const s = qs.toString()
    return req('GET', `/tracks${s ? '?' + s : ''}`)
  },
  activeDays: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach(item => qs.append(k, item))
      else if (v != null && v !== '') qs.set(k, v)
    })
    return req('GET', `/tracks/active-days?${qs.toString()}`)
  },
  track: (id) => req('GET', `/tracks/${id}`),
  cameras: () => req('GET', '/tracks/cameras'),
  snapshotUrl: (path) => path ? `/api/snapshots/${path}` : null,
  users: () => req('GET', '/users'),
  updateUser: (id, data) => req('PATCH', `/users/${id}`, data),
  createUser: (data) => req('POST', '/users', data),
  deleteUser: (id) => req('DELETE', `/users/${id}`),
  config: () => req('GET', '/config'),
  setConfig: (data) => req('PUT', '/config', data),
}

export function wsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/jobs`
}
