'use client'

import { useEffect, useState, useCallback, type CSSProperties } from 'react'
import api from '../../lib/api'
import Toast from '../../components/Toast'
import QrCodeModal from '../../components/QrCodeModal'

type Section = 'geral' | 'smtp' | 'whatsapp' | 'seguranca'

interface Config {
  server_name: string
  public_url: string
  smtp_host: string
  smtp_port: string
  smtp_user: string
  smtp_password: string
  smtp_tls: string
  smtp_from_email: string
  smtp_from_name: string
  smtp_recipients: string
  smtp_enabled: string
  evolution_url: string
  evolution_api_key: string
  evolution_instance: string
  evolution_recipients: string
  evolution_enabled: string
  admin_user: string
  admin_password: string
  require_auth: string
  retention_detailed_days: string
  retention_aggregated_days: string
  [key: string]: string
}

const EMPTY_CONFIG: Config = {
  server_name: '', public_url: '',
  smtp_host: '', smtp_port: '587', smtp_user: '', smtp_password: '',
  smtp_tls: 'starttls', smtp_from_email: '', smtp_from_name: 'VPS Monitor',
  smtp_recipients: '', smtp_enabled: '0',
  evolution_url: '', evolution_api_key: '', evolution_instance: 'vps-monitor',
  evolution_recipients: '', evolution_enabled: '0',
  admin_user: '', admin_password: '', require_auth: '1',
  retention_detailed_days: '7', retention_aggregated_days: '30',
}

const card: CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 24, marginBottom: 20,
}
const inputStyle: CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '7px 11px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
}
const labelStyle: CSSProperties = { color: 'var(--muted)', fontSize: 12, display: 'block', marginBottom: 4 }
const field: CSSProperties = { marginBottom: 14 }
const btn = (color = 'var(--accent)', textColor = '#000'): CSSProperties => ({
  padding: '8px 16px', background: color, color: textColor,
  border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: 14,
})
const tabBtn = (active: boolean): CSSProperties => ({
  padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
  cursor: 'pointer', marginRight: 8, fontWeight: 600, fontSize: 13,
  background: active ? 'var(--accent)' : 'var(--surface)',
  color: active ? '#000' : 'var(--muted)',
})

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    connected: ['var(--success)', 'Conectado'],
    disconnected: ['var(--warning)', 'Desconectado'],
    no_instance: ['var(--muted)', 'Sem instância'],
    not_configured: ['var(--muted)', 'Não configurado'],
    error: ['var(--danger)', 'Erro'],
  }
  const [color, label] = map[status] ?? ['var(--muted)', status]
  return (
    <span style={{ display: 'inline-block', padding: '3px 10px', borderRadius: 4, background: color, color: '#fff', fontSize: 12, fontWeight: 700 }}>
      {label}
    </span>
  )
}

export default function ConfiguracoesPage() {
  const [section, setSection] = useState<Section>('geral')
  const [config, setConfig] = useState<Config>(EMPTY_CONFIG)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | 'info' } | null>(null)
  const [waStatus, setWaStatus] = useState('not_configured')
  const [waError, setWaError] = useState('')
  const [showQr, setShowQr] = useState(false)
  const [showSmtpPassword, setShowSmtpPassword] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [phoneInput, setPhoneInput] = useState('')

  const loadConfig = useCallback(async () => {
    try {
      const r = await api.get('/config')
      // Não carrega admin_password da API — sempre inicia vazio para forçar digitação explícita
      setConfig({ ...EMPTY_CONFIG, ...r.data, admin_password: '' })
    } catch {}
    setLoading(false)
  }, [])

  const loadWaStatus = useCallback(async () => {
    try {
      const r = await api.get('/whatsapp/status')
      setWaStatus(r.data.status)
      setWaError(r.data.detail || '')
    } catch {}
  }, [])

  function maskPhone(value: string): string {
    const d = value.replace(/\D/g, '').slice(0, 11)
    if (d.length <= 2) return d
    if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`
    if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`
    return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`
  }

  function fromStored(stored: string): string {
    const d = stored.replace(/\D/g, '')
    const local = d.startsWith('55') ? d.slice(2) : d
    return maskPhone(local)
  }

  function addPhone() {
    const digits = phoneInput.replace(/\D/g, '')
    if (digits.length < 10) return
    const stored = `55${digits}`
    const current = config.evolution_recipients ? config.evolution_recipients.split(',').filter(Boolean) : []
    if (!current.includes(stored)) {
      setConfig(prev => ({ ...prev, evolution_recipients: [...current, stored].join(',') }))
    }
    setPhoneInput('')
  }

  function removePhone(num: string) {
    const current = config.evolution_recipients ? config.evolution_recipients.split(',').filter(Boolean) : []
    setConfig(prev => ({ ...prev, evolution_recipients: current.filter(r => r !== num).join(',') }))
  }

  useEffect(() => { loadConfig() }, [loadConfig])
  useEffect(() => { if (section === 'whatsapp') loadWaStatus() }, [section, loadWaStatus])

  async function save() {
    setSaving(true)
    try {
      // Não reenvia valores mascarados ou senha vazia que o usuário não alterou
      const payload: Record<string, string> = Object.fromEntries(
        Object.entries(config).filter(([k, v]) => {
          if (k === 'smtp_password' && v.startsWith('****')) return false
          if (k === 'evolution_api_key' && v.startsWith('****')) return false
          if (k === 'admin_password' && !v) return false
          return true
        })
      )
      await api.put('/config', payload)
      setToast({ msg: 'Configurações salvas com sucesso', type: 'success' })
      await loadConfig()
      if (section === 'whatsapp') loadWaStatus()
    } catch {
      setToast({ msg: 'Erro ao salvar configurações', type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  function set(key: keyof Config) {
    return (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
      setConfig(prev => ({ ...prev, [key]: e.target.value }))
    }
  }

  function toggle(key: keyof Config) {
    return () => setConfig(prev => ({ ...prev, [key]: prev[key] === '1' ? '0' : '1' }))
  }

  async function testEmail() {
    try {
      const r = await api.post('/notifications/test/email')
      setToast({ msg: r.data.ok ? 'E-mail de teste enviado!' : `Erro: ${r.data.error}`, type: r.data.ok ? 'success' : 'error' })
    } catch { setToast({ msg: 'Erro ao enviar e-mail de teste', type: 'error' }) }
  }

  async function testWhatsapp() {
    try {
      const r = await api.post('/notifications/test/whatsapp')
      setToast({ msg: r.data.ok ? 'Mensagem de teste enviada!' : `Erro: ${r.data.error}`, type: r.data.ok ? 'success' : 'error' })
    } catch { setToast({ msg: 'Erro ao enviar teste', type: 'error' }) }
  }

  async function waAction(action: 'disconnect' | 'delete-instance') {
    try {
      await api.delete(`/whatsapp/${action}`)
      loadWaStatus()
    } catch {
      setToast({ msg: `Erro ao ${action === 'disconnect' ? 'desconectar' : 'excluir instância'} WhatsApp`, type: 'error' })
    }
  }

  if (loading) return <div style={{ padding: 32, color: 'var(--muted)' }}>Carregando...</div>

  return (
    <div style={{ padding: 24, maxWidth: 760 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      {showQr && (
        <QrCodeModal
          onClose={() => setShowQr(false)}
          onConnected={() => { setShowQr(false); setToast({ msg: '✅ WhatsApp conectado!', type: 'success' }); loadWaStatus() }}
        />
      )}

      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Configurações</h1>

      {/* Tabs */}
      <div style={{ marginBottom: 24 }}>
        {(['geral', 'smtp', 'whatsapp', 'seguranca'] as Section[]).map(s => (
          <button key={s} style={tabBtn(section === s)} onClick={() => setSection(s)}>
            {s === 'geral' ? 'Geral' : s === 'smtp' ? 'E-mail (SMTP)' : s === 'whatsapp' ? 'WhatsApp' : 'Segurança'}
          </button>
        ))}
      </div>

      {/* GERAL */}
      {section === 'geral' && (
        <div style={card}>
          <h3 style={{ color: 'var(--text)', marginBottom: 16 }}>Configurações Gerais</h3>
          <div style={field}>
            <label style={labelStyle}>Nome do servidor</label>
            <input style={inputStyle} value={config.server_name} onChange={set('server_name')} />
          </div>
          <div style={field}>
            <label style={labelStyle}>URL pública do painel</label>
            <input style={inputStyle} value={config.public_url} onChange={set('public_url')} placeholder="https://monitor.exemplo.com" />
          </div>
        </div>
      )}

      {/* SMTP */}
      {section === 'smtp' && (
        <div style={card}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <h3 style={{ color: 'var(--text)', margin: 0 }}>E-mail (SMTP)</h3>
            <label style={{ color: 'var(--muted)', fontSize: 13, display: 'flex', gap: 8, alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={config.smtp_enabled === '1'} onChange={toggle('smtp_enabled')} />
              Ativo
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={field}>
              <label style={labelStyle}>Servidor SMTP</label>
              <input style={inputStyle} value={config.smtp_host} onChange={set('smtp_host')} placeholder="smtp.gmail.com" />
            </div>
            <div style={field}>
              <label style={labelStyle}>Porta</label>
              <input style={inputStyle} type="number" value={config.smtp_port} onChange={set('smtp_port')} />
            </div>
            <div style={field}>
              <label style={labelStyle}>Usuário</label>
              <input style={inputStyle} value={config.smtp_user} onChange={set('smtp_user')} />
            </div>
            <div style={field}>
              <label style={labelStyle}>Senha</label>
              <div style={{ position: 'relative' }}>
                <input style={inputStyle} type={showSmtpPassword ? 'text' : 'password'} value={config.smtp_password} onChange={set('smtp_password')} />
                <button onClick={() => setShowSmtpPassword(v => !v)} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}>
                  {showSmtpPassword ? '🙈' : '👁'}
                </button>
              </div>
            </div>
            <div style={field}>
              <label style={labelStyle}>Criptografia</label>
              <select style={inputStyle} value={config.smtp_tls} onChange={set('smtp_tls')}>
                <option value="starttls">STARTTLS</option>
                <option value="ssl">SSL</option>
                <option value="none">Nenhuma</option>
              </select>
            </div>
            <div style={field}>
              <label style={labelStyle}>E-mail remetente</label>
              <input style={inputStyle} value={config.smtp_from_email} onChange={set('smtp_from_email')} />
            </div>
            <div style={field}>
              <label style={labelStyle}>Nome remetente</label>
              <input style={inputStyle} value={config.smtp_from_name} onChange={set('smtp_from_name')} />
            </div>
          </div>
          <div style={field}>
            <label style={labelStyle}>Destinatários (separados por vírgula)</label>
            <textarea style={{ ...inputStyle, height: 70, resize: 'vertical' }} value={config.smtp_recipients} onChange={set('smtp_recipients')} placeholder="email1@exemplo.com, email2@exemplo.com" />
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
            <button style={btn()} onClick={testEmail}>Enviar e-mail de teste</button>
          </div>
        </div>
      )}

      {/* WHATSAPP */}
      {section === 'whatsapp' && (
        <div style={card}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <h3 style={{ color: 'var(--text)', margin: 0 }}>WhatsApp (Evolution API)</h3>
              <StatusBadge status={waStatus} />
            </div>
            <label style={{ color: 'var(--muted)', fontSize: 13, display: 'flex', gap: 8, alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={config.evolution_enabled === '1'} onChange={toggle('evolution_enabled')} />
              Ativo
            </label>
          </div>
          <div style={field}>
            <label style={labelStyle}>URL da Evolution API</label>
            <input style={inputStyle} value={config.evolution_url} onChange={set('evolution_url')} placeholder="https://ev.seudominio.com" />
          </div>
          <div style={field}>
            <label style={labelStyle}>API Key</label>
            <div style={{ position: 'relative' }}>
              <input style={inputStyle} type={showApiKey ? 'text' : 'password'} value={config.evolution_api_key} onChange={set('evolution_api_key')} />
              <button onClick={() => setShowApiKey(v => !v)} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}>
                {showApiKey ? '🙈' : '👁'}
              </button>
            </div>
          </div>
          <div style={field}>
            <label style={labelStyle}>Nome da instância</label>
            <input style={inputStyle} value={config.evolution_instance} onChange={set('evolution_instance')} />
          </div>
          {/* Números destinatários com máscara */}
          <div style={field}>
            <label style={labelStyle}>Números destinatários (WhatsApp)</label>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <input
                style={{ ...inputStyle, flex: 1 }}
                value={phoneInput}
                onChange={e => setPhoneInput(maskPhone(e.target.value))}
                onKeyDown={e => e.key === 'Enter' && addPhone()}
                placeholder="(11) 99999-0001"
                maxLength={15}
              />
              <button style={{ ...btn(), whiteSpace: 'nowrap' }} onClick={addPhone}>+ Adicionar</button>
            </div>
            {(() => {
              const list = config.evolution_recipients ? config.evolution_recipients.split(',').filter(Boolean) : []
              return list.length === 0
                ? <p style={{ color: 'var(--muted)', fontSize: 12, margin: 0 }}>Nenhum número adicionado</p>
                : <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {list.map(num => (
                      <div key={num} style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        background: 'var(--surface)', borderRadius: 6, padding: '6px 12px',
                        border: '1px solid var(--border)',
                      }}>
                        <span style={{ color: 'var(--text)', fontSize: 13 }}>
                          +{num.slice(0, 2)} {fromStored(num)}
                        </span>
                        <button
                          onClick={() => removePhone(num)}
                          style={{ background: 'none', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: 18, lineHeight: 1, padding: '0 4px' }}
                          title="Remover"
                        >×</button>
                      </div>
                    ))}
                  </div>
            })()}
          </div>

          {/* Detalhe do erro */}
          {waStatus === 'error' && waError && (
            <div style={{ background: 'rgba(var(--danger-rgb,220,38,38),0.1)', border: '1px solid var(--danger)', borderRadius: 6, padding: '8px 12px', marginBottom: 12, fontSize: 12, color: 'var(--danger)' }}>
              {waError}
            </div>
          )}

          {/* Botões contextuais */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 8 }}>
            {waStatus !== 'connected' && (
              <button style={btn()} onClick={() => setShowQr(true)}>
                {waStatus === 'no_instance' ? 'Criar instância e conectar' : 'Conectar via QR Code'}
              </button>
            )}
            {(waStatus === 'disconnected' || waStatus === 'error') && (
              <button style={btn('var(--danger)', '#fff')} onClick={() => waAction('delete-instance')}>Excluir Instância</button>
            )}
            {waStatus === 'connected' && (<>
              <button style={btn('var(--warning)', '#000')} onClick={() => waAction('disconnect')}>Desconectar</button>
              <button style={btn('var(--danger)', '#fff')} onClick={() => waAction('delete-instance')}>Excluir Instância</button>
              <button style={btn()} onClick={testWhatsapp}>Enviar mensagem de teste</button>
            </>)}
            <button style={btn('var(--surface)', 'var(--muted)')} onClick={loadWaStatus}>↻ Atualizar status</button>
          </div>
        </div>
      )}

      {/* SEGURANÇA + RETENÇÃO */}
      {section === 'seguranca' && (
        <div style={card}>
          <h3 style={{ color: 'var(--text)', marginBottom: 16 }}>Segurança</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
            <div style={field}>
              <label style={labelStyle}>Usuário</label>
              <input style={inputStyle} value={config.admin_user} onChange={set('admin_user')} />
            </div>
            <div style={field}>
              <label style={labelStyle}>Nova senha</label>
              <input style={inputStyle} type="password" value={config.admin_password} onChange={set('admin_password')} placeholder="Deixe em branco para manter" />
            </div>
          </div>

          <h3 style={{ color: 'var(--text)', marginBottom: 12 }}>Retenção de Dados</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={field}>
              <label style={labelStyle}>Retenção detalhada (dias)</label>
              <input style={inputStyle} type="number" min="1" value={config.retention_detailed_days} onChange={set('retention_detailed_days')} />
            </div>
            <div style={field}>
              <label style={labelStyle}>Retenção agregada (dias)</label>
              <input style={inputStyle} type="number" min="1" value={config.retention_aggregated_days} onChange={set('retention_aggregated_days')} />
            </div>
          </div>
        </div>
      )}

      {/* Botão Salvar */}
      <div style={{ display: 'flex', gap: 12 }}>
        <button style={btn()} onClick={save} disabled={saving}>
          {saving ? 'Salvando...' : 'Salvar Configurações'}
        </button>
      </div>
    </div>
  )
}
