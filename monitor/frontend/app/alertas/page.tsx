'use client'

import { useEffect, useState, useCallback, Fragment, type CSSProperties, type ReactNode } from 'react'
import api from '../../lib/api'
import AlertBadge from '../../components/AlertBadge'
import VpsBadge from '../../components/VpsBadge'
import Toast from '../../components/Toast'
import { AlertNotificationsCompact, AlertNotificationsDetailed, type AlertNotificacao } from '../../components/AlertNotifications'
import AlertRuleModal from '../../components/AlertRuleModal'

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
  contexto: Record<string, any> | null
  notificacoes: AlertNotificacao[]
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

function renderContexto(ctx: Record<string, any> | null): ReactNode {
  if (!ctx) return <span style={{ color: 'var(--muted)' }}>Sem dados de contexto disponíveis para este alerta.</span>

  const linhas: ReactNode[] = []

  if (ctx.top_cpu) {
    linhas.push(
      <div key="top_cpu">
        <strong>Top CPU: </strong>
        {ctx.top_cpu.map((c: any) => `${c.nome} (${c.valor}%)`).join(', ')}
      </div>
    )
  }
  if (ctx.top_mem) {
    linhas.push(
      <div key="top_mem">
        <strong>Top RAM: </strong>
        {ctx.top_mem.map((c: any) => `${c.nome} (${c.valor}%)`).join(', ')}
      </div>
    )
  }
  if (ctx.top_rede) {
    linhas.push(
      <div key="top_rede">
        <strong>Top Rede: </strong>
        {ctx.top_rede.map((c: any) => `${c.nome} (${c.valor_mb} MB)`).join(', ')}
      </div>
    )
  }
  if (ctx.top_disco) {
    linhas.push(
      <div key="top_disco">
        <strong>Top Disco (camada gravável): </strong>
        {ctx.top_disco.map((c: any) => `${c.nome} (${c.valor_mb} MB)`).join(', ')}
      </div>
    )
  }
  if ('exit_code' in ctx || 'oom_killed' in ctx) {
    linhas.push(
      <div key="exit">
        <strong>Motivo: </strong>
        {ctx.oom_killed
          ? 'finalizado por falta de memória (OOM Killed)'
          : `código de saída ${ctx.exit_code ?? '—'}`}
        {ctx.erro ? ` — ${ctx.erro}` : ''}
      </div>
    )
  }

  return linhas.length > 0 ? <div style={{ display: 'grid', gap: 4 }}>{linhas}</div> : renderContexto(null)
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
  const [expandedAlert, setExpandedAlert] = useState<number | null>(null)

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
              <AlertNotificationsCompact notificacoes={a.notificacoes} />
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
                <th style={{ padding: '8px 10px', width: 24 }} />
                {['Severidade', 'Métrica', 'Mensagem', 'VPS', 'Disparado em', 'Resolvido em'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.map(a => (
                <Fragment key={a.id}>
                  <tr
                    style={{ borderBottom: '1px solid var(--border)', color: 'var(--text)', cursor: 'pointer' }}
                    onClick={() => setExpandedAlert(expandedAlert === a.id ? null : a.id)}
                  >
                    <td style={{ padding: '8px 10px', color: 'var(--muted)' }}>{expandedAlert === a.id ? '▼' : '▶'}</td>
                    <td style={{ padding: '8px 10px' }}><AlertBadge severidade={a.severidade} /></td>
                    <td style={{ padding: '8px 10px' }}>{METRICA_LABELS[a.metrica] ?? a.metrica}</td>
                    <td style={{ padding: '8px 10px', maxWidth: 320 }}>{a.mensagem}</td>
                    <td style={{ padding: '8px 10px' }}><VpsBadge name={a.vps_name} /></td>
                    <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>{a.triggered_at ? formatDt(a.triggered_at) : '—'}</td>
                    <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: a.resolved_at ? 'var(--success)' : 'var(--warning)' }}>
                      {a.resolved_at ? formatDt(a.resolved_at) : 'Ativo'}
                    </td>
                  </tr>
                  {expandedAlert === a.id && (
                    <tr>
                      <td colSpan={7} style={{ background: 'var(--surface)', padding: 16, borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                        {renderContexto(a.contexto)}
                        <div style={{ marginTop: 12 }}>
                          <AlertNotificationsDetailed notificacoes={a.notificacoes} />
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
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
            <AlertRuleModal
              form={form}
              editing={editId !== null}
              metricas={METRICAS}
              operadores={OPERADORES}
              severidades={SEVERIDADES}
              metricaLabels={METRICA_LABELS}
              onChange={setForm}
              onSave={saveRule}
              onClose={() => setShowForm(false)}
            />
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
