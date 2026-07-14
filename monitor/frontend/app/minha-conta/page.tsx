'use client'

import { useEffect, useState, type CSSProperties } from 'react'
import api from '../../lib/api'
import Toast from '../../components/Toast'

const card: CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 24, maxWidth: 480,
}
const inputStyle: CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '7px 11px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
}
const labelStyle: CSSProperties = { color: 'var(--muted)', fontSize: 12, display: 'block', marginBottom: 4 }
const field: CSSProperties = { marginBottom: 14 }

export default function MinhaContaPage() {
  const [username, setUsername] = useState('')
  const [currentUser, setCurrentUser] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)

  useEffect(() => {
    api.get('/config').then(r => {
      const u = r.data.admin_user || 'admin'
      setCurrentUser(u)
      setUsername(u)
    }).finally(() => setLoading(false))
  }, [])

  async function save() {
    if (password && password !== confirmPassword) {
      setToast({ msg: 'As senhas não coincidem', type: 'error' })
      return
    }
    if (!username.trim()) {
      setToast({ msg: 'O usuário não pode ser vazio', type: 'error' })
      return
    }
    setSaving(true)
    try {
      const payload: Record<string, string> = { admin_user: username.trim() }
      if (password) payload.admin_password = password
      await api.put('/config', payload)
      setCurrentUser(username.trim())
      setPassword('')
      setConfirmPassword('')
      setToast({ msg: 'Dados atualizados com sucesso', type: 'success' })
    } catch {
      setToast({ msg: 'Erro ao salvar', type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: 32, color: 'var(--muted)' }}>Carregando...</div>

  return (
    <div style={{ padding: 24 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Meus Dados</h1>

      <div style={card}>
        <h3 style={{ color: 'var(--text)', marginBottom: 6 }}>Conta de acesso</h3>
        <p style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 20 }}>
          Usuário atual: <strong style={{ color: 'var(--text)' }}>{currentUser}</strong>
        </p>

        <div style={field}>
          <label style={labelStyle}>Usuário</label>
          <input
            style={inputStyle}
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoComplete="username"
          />
        </div>

        <div style={field}>
          <label style={labelStyle}>Nova senha</label>
          <input
            style={inputStyle}
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Deixe em branco para manter a atual"
            autoComplete="new-password"
          />
        </div>

        <div style={{ ...field, marginBottom: 20 }}>
          <label style={labelStyle}>Confirmar nova senha</label>
          <input
            style={inputStyle}
            type="password"
            value={confirmPassword}
            onChange={e => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
          />
        </div>

        <button
          style={{
            padding: '8px 20px', background: 'var(--accent)', color: '#000',
            border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: 14,
            opacity: saving ? 0.7 : 1,
          }}
          onClick={save}
          disabled={saving}
        >
          {saving ? 'Salvando...' : 'Salvar alterações'}
        </button>
      </div>
    </div>
  )
}
