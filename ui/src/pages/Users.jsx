import { useState, useEffect } from 'react'
import { api } from '../api.js'

export default function Users() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [editId, setEditId] = useState(null)
  const [editData, setEditData] = useState({})
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'viewer', email: '' })
  const [showCreate, setShowCreate] = useState(false)
  const [message, setMessage] = useState(null)

  async function load() {
    setLoading(true)
    try {
      setUsers(await api.users())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  function flash(msg, isErr = false) {
    setMessage({ text: msg, err: isErr })
    setTimeout(() => setMessage(null), 4000)
  }

  async function saveEdit(id) {
    try {
      await api.updateUser(id, editData)
      flash('User updated')
      setEditId(null)
      load()
    } catch (e) {
      flash(e.message, true)
    }
  }

  async function deleteUser(id, username) {
    if (!confirm(`Delete user "${username}"?`)) return
    try {
      await api.deleteUser(id)
      flash('User deleted')
      load()
    } catch (e) {
      flash(e.message, true)
    }
  }

  async function createUser() {
    try {
      await api.createUser(newUser)
      flash('User created')
      setShowCreate(false)
      setNewUser({ username: '', password: '', role: 'viewer', email: '' })
      load()
    } catch (e) {
      flash(e.message, true)
    }
  }

  const ROLES = ['admin', 'operator', 'viewer']

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white">Users</h2>
          <p className="text-slate-400 text-sm mt-1">Admin-only user management</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="bg-brand hover:bg-brand-dark text-slate-900 font-semibold text-sm px-4 py-2 rounded-md transition-colors"
        >
          + Add User
        </button>
      </div>

      {message && (
        <div className={`mb-4 px-4 py-2 rounded-md text-sm border ${message.err ? 'bg-red-900/40 border-red-700 text-red-300' : 'bg-green-900/40 border-green-700 text-green-300'}`}>
          {message.text}
        </div>
      )}

      {showCreate && (
        <div className="mb-6 bg-slate-800 border border-slate-700 rounded-xl p-5">
          <h3 className="text-slate-300 font-semibold mb-4">Create New User</h3>
          <div className="grid grid-cols-2 gap-3">
            {[['username', 'Username'], ['email', 'Email (optional)']].map(([k, label]) => (
              <div key={k}>
                <label className="block text-xs text-slate-400 mb-1">{label}</label>
                <input
                  type="text"
                  value={newUser[k]}
                  onChange={(e) => setNewUser({ ...newUser, [k]: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-600 rounded-md px-3 py-1.5 text-white text-sm focus:outline-none focus:ring-2 focus:ring-brand"
                />
              </div>
            ))}
            <div>
              <label className="block text-xs text-slate-400 mb-1">Password</label>
              <input
                type="password"
                value={newUser.password}
                onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-md px-3 py-1.5 text-white text-sm focus:outline-none focus:ring-2 focus:ring-brand"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Role</label>
              <select
                value={newUser.role}
                onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                className="w-full bg-slate-900 border border-slate-600 rounded-md px-3 py-1.5 text-white text-sm focus:outline-none focus:ring-2 focus:ring-brand"
              >
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button onClick={createUser} className="bg-brand hover:bg-brand-dark text-slate-900 font-semibold text-sm px-4 py-1.5 rounded-md transition-colors">
              Create
            </button>
            <button onClick={() => setShowCreate(false)} className="bg-slate-700 hover:bg-slate-600 text-white text-sm px-4 py-1.5 rounded-md transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700">
              {['ID', 'Username', 'Email', 'Role', 'Active', 'Actions'].map((h) => (
                <th key={h} className="text-left text-slate-400 font-medium px-4 py-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={6} className="text-center text-slate-400 py-8">Loading…</td></tr>
            )}
            {!loading && users.map((u) => (
              <tr key={u.id} className="border-b border-slate-700/50">
                <td className="px-4 py-3 text-slate-400 font-mono">{u.id}</td>
                <td className="px-4 py-3 text-slate-200 font-medium">{u.username}</td>
                <td className="px-4 py-3 text-slate-400">{u.email || '—'}</td>
                <td className="px-4 py-3">
                  {editId === u.id ? (
                    <select
                      value={editData.role || u.role}
                      onChange={(e) => setEditData({ ...editData, role: e.target.value })}
                      className="bg-slate-900 border border-slate-600 text-white text-xs rounded px-2 py-1"
                    >
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : (
                    <span className={`text-xs px-2 py-0.5 rounded border ${
                      u.role === 'admin' ? 'bg-purple-900/50 text-purple-300 border-purple-700' :
                      u.role === 'operator' ? 'bg-blue-900/50 text-blue-300 border-blue-700' :
                      'bg-slate-700 text-slate-300 border-slate-600'
                    }`}>{u.role}</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {editId === u.id ? (
                    <input
                      type="checkbox"
                      checked={editData.is_active ?? u.is_active}
                      onChange={(e) => setEditData({ ...editData, is_active: e.target.checked })}
                      className="accent-brand"
                    />
                  ) : (
                    <span className={u.is_active ? 'text-green-400' : 'text-red-400'}>
                      {u.is_active ? '✓' : '✗'}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {editId === u.id ? (
                    <div className="flex gap-2">
                      <button onClick={() => saveEdit(u.id)} className="text-xs text-green-400 hover:text-green-300">Save</button>
                      <button onClick={() => setEditId(null)} className="text-xs text-slate-400 hover:text-white">Cancel</button>
                    </div>
                  ) : (
                    <div className="flex gap-3">
                      <button
                        onClick={() => { setEditId(u.id); setEditData({}) }}
                        className="text-xs text-brand hover:text-brand-dark"
                      >Edit</button>
                      <button
                        onClick={() => deleteUser(u.id, u.username)}
                        className="text-xs text-red-400 hover:text-red-300"
                      >Delete</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
