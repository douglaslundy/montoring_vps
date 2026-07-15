'use client'

import type { CSSProperties } from 'react'

export interface RuleForm {
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
}

interface Props {
  form: RuleForm
  editing: boolean
  metricas: string[]
  operadores: string[]
  severidades: string[]
  metricaLabels: Record<string, string>
  onChange: (form: RuleForm) => void
  onSave: () => void
  onClose: () => void
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
  zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
}

const modalCard: CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--accent)', borderRadius: 12,
  width: '85%', maxWidth: 640, maxHeight: '85vh', display: 'flex', flexDirection: 'column',
}

const input: CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
}

export default function AlertRuleModal({
  form, editing, metricas, operadores, severidades, metricaLabels,
  onChange, onSave, onClose,
}: Props) {
  return (
    <div style={overlay} onClick={onClose}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <div style={{
          padding: '14px 20px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ color: 'var(--text)', fontWeight: 600, fontSize: 16 }}>
            {editing ? 'Editar Regra' : 'Nova Regra'}
          </span>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}
          >×</button>
        </div>

        <div style={{ padding: 20, overflow: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome</label>
              <input style={input} value={form.nome} onChange={e => onChange({ ...form, nome: e.target.value })} />
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Métrica</label>
              <select style={input} value={form.metrica} onChange={e => onChange({ ...form, metrica: e.target.value })}>
                {metricas.map(m => <option key={m} value={m}>{metricaLabels[m] ?? m}</option>)}
              </select>
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Operador</label>
              <select style={input} value={form.operador} onChange={e => onChange({ ...form, operador: e.target.value })}>
                {operadores.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Threshold</label>
              <input type="number" style={input} value={form.threshold} onChange={e => onChange({ ...form, threshold: Number(e.target.value) })} />
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Duração mínima (min)</label>
              <input type="number" style={input} value={form.duracao_minutos} onChange={e => onChange({ ...form, duracao_minutos: Number(e.target.value) })} />
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Severidade</label>
              <select style={input} value={form.severidade} onChange={e => onChange({ ...form, severidade: e.target.value })}>
                {severidades.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Cooldown (min)</label>
              <input type="number" style={input} value={form.cooldown_minutos} onChange={e => onChange({ ...form, cooldown_minutos: Number(e.target.value) })} />
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={!!form.canal_email} onChange={e => onChange({ ...form, canal_email: e.target.checked ? 1 : 0 })} />
                E-mail
              </label>
              <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={!!form.canal_whatsapp} onChange={e => onChange({ ...form, canal_whatsapp: e.target.checked ? 1 : 0 })} />
                WhatsApp
              </label>
            </div>
          </div>
          <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
            <button
              onClick={onSave}
              style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
            >
              Salvar
            </button>
            <button
              onClick={onClose}
              style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
            >
              Cancelar
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
