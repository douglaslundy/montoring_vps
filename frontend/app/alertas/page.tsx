'use client'

import { useEffect, useState, useCallback, type CSSProperties } from 'react'
import api from '../../lib/api'
import AlertBadge from '../../components/AlertBadge'
import VpsBadge from '../../components/VpsBadge'
import Toast from '../../components/Toast'

interface AlertLog {
  id: number
  rule_id: number | null
  triggered_at: string
  resolved_at: string | null
  severidade: string
  metrica: string
  valor_no_disparo: number | null
  threshold: number | null
  mensagem: string | null
  vps_name: string | null
}

interface AlertRule {
  id: number
  nome: string
  metrica: string
  operador: string
  threshold: number
  duracao_minutos: number
  severidade: string
  canal_email: number
  canal_whatsapp: number
  cooldown_minutos: number
  ativo: number
  criado_em: string | null
}

type Tab = 'ativas' | 'historico' | 'regras'

const METRICAS = ['cpu_percent', 'ram_percent', 'disk_percent', 'temperature_c', 'load_1m', 'container_stopped']
const OPERADORES = ['>', '<', '>=', '<=']
const SEVERIDADES = ['critico', 'aviso', 'info']

const METRICA_LABELS: Record<string, string> = {
  cpu_percent: 'CPU (%)',
  ram_percent: 'RAM (%)',
  disk_percent: 'Disco (%)',
  temperature_c: 'Temperatura (°C)',
  load_1m: 'Load Average 1m',
  container_stopped: 'Container Parado',
}

function elapsed(from: string): string {
  const diff = Math.floor((Date.now() - new Date(from).getTime()) / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`
}

function formatDt(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR')
}

const emptyForm = (): Omit<AlertRule, 'id' | 'criado_em'> => ({
  nome: '', metrica: 'cpu_percent', operador: '>', threshold: 80,
  duracao_minutos: 5, severidade: 'aviso',
  canal_email: 1, canal_whatsapp: 1, cooldown_minutos: 30, ativo: 1,
})

const card: CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
}

const tabBadge = (active: boolean): CSSProperties => ({
  display: 'inline-block', padding: '4px 12px', borderRadius: 6,
  fontSize: 13, fontWeight: 600, cursor: 'pointer',
  background: active ? 'var(--accent)' : 'var(--surface)',
  color: active ? '#000' : 'var(--muted)',
  border: '1px solid var(--border)',
  marginRight: 8,
})

const input: CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
}

export default function AlertasPage() {
  const [tab, setTab] = useState<Tab>('ativas')
  const [active, setActive] = useState<AlertLog[]>([])
  const [history, setHistory] = useState<AlertLog[]>([])
  const [rules, setRules] = useState<AlertRule[]>([])
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | 'info' } | null>(null)
  const [form, setForm] = useState(emptyForm())
  const [editId, setEditId] = useState<number | null>(null)
  const [showForm, setShowForm] = useState(false)

  // Filtros histórico
  const [filtSeveridade, setFiltSeveridade] = useState('')
  const [filtMetrica, setFiltMetrica] = useState('')

  const loadActive = useCallback(async () => {
    try { setActive((await api.get('/alerts/active')).data) } catch {}
  }, [])

  const loadHistory = useCallback(async () => {
    const params = new URLSearchParams()
    if (filtSeveridade) params.set('severidade', filtSeveridade)
    if (filtMetrica) params.set('metrica', filtMetrica)
    params.set('limit', '200')
    try { setHistory((await api.get(`/alerts/history?${params}`)).data) } catch {}
  }, [filtSeveridade, filtMetrica])

  const loadRules = useCallback(async () => {
    try { setRules((await api.get('/alerts/rules')).data) } catch {}
  }, [])

  useEffect(() => { loadActive(); loadRules() }, [loadActive, loadRules])
  useEffect(() => { if (tab === 'historico') loadHistory() }, [tab, loadHistory])

  // Auto-refresh ativas a cada 30s
  useEffect(() => {
    const id = setInterval(loadActive, 30000)
    return () => clearInterval(id)
  }, [loadActive])

  async function toggleRule(rule: AlertRule) {
    try {
      const r = await api.post(`/alerts/rules/${rule.id}/toggle`)
      setRules(prev => prev.map(x => x.id === rule.id ? { ...x, ativo: r.data.ativo } : x))
    } catch { setToast({ msg: 'Erro ao alterar regra', type: 'error' }) }
  }

  async function deleteRule(id: number) {
    if (!confirm('Excluir esta regra?')) return
    try {
      await api.delete(`/alerts/rules/${id}`)
      setRules(prev => prev.filter(r => r.id !== id))
      setToast({ msg: 'Regra excluída', type: 'success' })
    } catch { setToast({ msg: 'Erro ao excluir', type: 'error' }) }
  }

  function startEdit(rule: AlertRule) {
    setEditId(rule.id)
    setForm({
      nome: rule.nome, metrica: rule.metrica, operador: rule.operador,
      threshold: rule.threshold, duracao_minutos: rule.duracao_minutos,
      severidade: rule.severidade, canal_email: rule.canal_email,
      canal_whatsapp: rule.canal_whatsapp, cooldown_minutos: rule.cooldown_minutos,
      ativo: rule.ativo,
    })
    setShowForm(true)
  }

  function startCreate() {
    setEditId(null)
    setForm(emptyForm())
    setShowForm(true)
  }

  async function saveRule() {
    try {
      if (editId !== null) {
        await api.put(`/alerts/rules/${editId}`, form)
        setToast({ msg: 'Regra atualizada', type: 'success' })
      } else {
        await api.post('/alerts/rules', form)
        setToast({ msg: 'Regra criada', type: 'success' })
      }
      setShowForm(false)
      loadRules()
    } catch { setToast({ msg: 'Erro ao salvar regra', type: 'error' }) }
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      {toast && (
        <Toast
          message={toast.msg}
          type={toast.type}
          onDismiss={() => setToast(null)}
        />
      )}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Alertas</h1>

      {/* Tabs */}
      <div style={{ marginBottom: 20 }}>
        {(['ativas', 'historico', 'regras'] as Tab[]).map(t => (
          <button key={t} style={tabBadge(tab === t)} onClick={() => setTab(t)}>
            {t === 'ativas' ? `Ativas (${active.length})` : t === 'historico' ? 'Histórico' : 'Regras'}
          </button>
        ))}
      </div>

      {/* TAB: ATIVAS */}
      {tab === 'ativas' && (
        <div>
          {active.length === 0 && (
            <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>
              Nenhum alerta ativo no momento
            </p>
          )}
          {active.map(a => (
            <div key={a.id} style={{ ...card, borderLeft: `4px solid ${a.severidade === 'critico' ? 'var(--danger)' : 'var(--warning)'}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                <AlertBadge severidade={a.severidade} />
                <span style={{ color: 'var(--text)', fontWeight: 600 }}>{a.mensagem}</span>
                <VpsBadge name={a.vps_name} />
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>
                Iniciado: {formatDt(a.triggered_at)} · Duração: {elapsed(a.triggered_at)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB: HISTÓRICO */}
      {tab === 'historico' && (
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <select style={{ ...input, width: 180 }} value={filtSeveridade} onChange={e => setFiltSeveridade(e.target.value)}>
              <option value="">Todas severidades</option>
              {SEVERIDADES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select style={{ ...input, width: 220 }} value={filtMetrica} onChange={e => setFiltMetrica(e.target.value)}>
              <option value="">Todas métricas</option>
              {METRICAS.map(m => <option key={m} value={m}>{METRICA_LABELS[m]}</option>)}
            </select>
            <button
              style={{ padding: '6px 14px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 }}
              onClick={loadHistory}
            >
              Filtrar
            </button>
          </div>
          {history.length === 0 && (
            <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhum alerta no histórico</p>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                {['Severidade', 'Métrica', 'Mensagem', 'VPS', 'Disparado em', 'Resolvido em'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.map(a => (
                <tr key={a.id} style={{ borderBottom: '1px solid var(--border)', color: 'var(--text)' }}>
                  <td style={{ padding: '8px 10px' }}><AlertBadge severidade={a.severidade} /></td>
                  <td style={{ padding: '8px 10px' }}>{METRICA_LABELS[a.metrica] ?? a.metrica}</td>
                  <td style={{ padding: '8px 10px', maxWidth: 320 }}>{a.mensagem}</td>
                  <td style={{ padding: '8px 10px' }}><VpsBadge name={a.vps_name} /></td>
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>{a.triggered_at ? formatDt(a.triggered_at) : '—'}</td>
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: a.resolved_at ? 'var(--success)' : 'var(--warning)' }}>
                    {a.resolved_at ? formatDt(a.resolved_at) : 'Ativo'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* TAB: REGRAS */}
      {tab === 'regras' && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <button
              onClick={startCreate}
              style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
            >
              + Nova Regra
            </button>
          </div>

          {/* Formulário */}
          {showForm && (
            <div style={{ ...card, marginBottom: 20, border: '1px solid var(--accent)' }}>
              <h3 style={{ color: 'var(--text)', marginBottom: 16 }}>{editId ? 'Editar Regra' : 'Nova Regra'}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome</label>
                  <input style={input} value={form.nome} onChange={e => setForm(f => ({ ...f, nome: e.target.value }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Métrica</label>
                  <select style={input} value={form.metrica} onChange={e => setForm(f => ({ ...f, metrica: e.target.value }))}>
                    {METRICAS.map(m => <option key={m} value={m}>{METRICA_LABELS[m]}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Operador</label>
                  <select style={input} value={form.operador} onChange={e => setForm(f => ({ ...f, operador: e.target.value }))}>
                    {OPERADORES.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Threshold</label>
                  <input type="number" style={input} value={form.threshold} onChange={e => setForm(f => ({ ...f, threshold: Number(e.target.value) }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Duração mínima (min)</label>
                  <input type="number" style={input} value={form.duracao_minutos} onChange={e => setForm(f => ({ ...f, duracao_minutos: Number(e.target.value) }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Severidade</label>
                  <select style={input} value={form.severidade} onChange={e => setForm(f => ({ ...f, severidade: e.target.value }))}>
                    {SEVERIDADES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Cooldown (min)</label>
                  <input type="number" style={input} value={form.cooldown_minutos} onChange={e => setForm(f => ({ ...f, cooldown_minutos: Number(e.target.value) }))} />
                </div>
                <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                  <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={!!form.canal_email} onChange={e => setForm(f => ({ ...f, canal_email: e.target.checked ? 1 : 0 }))} />
                    E-mail
                  </label>
                  <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={!!form.canal_whatsapp} onChange={e => setForm(f => ({ ...f, canal_whatsapp: e.target.checked ? 1 : 0 }))} />
                    WhatsApp
                  </label>
                </div>
              </div>
              <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
                <button
                  onClick={saveRule}
                  style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
                >
                  Salvar
                </button>
                <button
                  onClick={() => setShowForm(false)}
                  style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
                >
                  Cancelar
                </button>
              </div>
            </div>
          )}

          {/* Lista de regras */}
          {rules.map(rule => (
            <div key={rule.id} style={{ ...card, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ color: 'var(--text)', fontWeight: 600 }}>{rule.nome}</div>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                  {METRICA_LABELS[rule.metrica] ?? rule.metrica} {rule.operador} {rule.threshold}
                  {rule.duracao_minutos > 0 && ` · ${rule.duracao_minutos}min`}
                  {` · cooldown ${rule.cooldown_minutos}min`}
                </div>
              </div>
              <AlertBadge severidade={rule.severidade} />
              <button
                onClick={() => toggleRule(rule)}
                style={{
                  padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
                  cursor: 'pointer', fontSize: 12, fontWeight: 600,
                  background: rule.ativo ? 'var(--success)' : 'var(--surface)',
                  color: rule.ativo ? '#fff' : 'var(--muted)',
                }}
              >
                {rule.ativo ? 'Ativo' : 'Inativo'}
              </button>
              <button
                onClick={() => startEdit(rule)}
                style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
              >
                Editar
              </button>
              <button
                onClick={() => deleteRule(rule.id)}
                style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
              >
                Excluir
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
