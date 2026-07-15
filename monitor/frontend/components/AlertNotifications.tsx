'use client'

export interface AlertNotificacao {
  canal: 'email' | 'whatsapp'
  tipo: 'disparo' | 'resolucao'
  status: 'enviado' | 'falhou' | 'desabilitado'
  erro: string | null
  tentativa_em: string
}

const CANAL_ICON: Record<string, string> = { email: '✉️', whatsapp: '📱' }
const CANAL_LABEL: Record<string, string> = { email: 'E-mail', whatsapp: 'WhatsApp' }
const STATUS_COLOR: Record<string, string> = {
  enviado: 'var(--success)', falhou: 'var(--danger)', desabilitado: 'var(--muted)',
}
const STATUS_LABEL: Record<string, string> = {
  enviado: 'Enviado', falhou: 'Falhou', desabilitado: 'Desabilitado',
}

function formatDt(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR')
}

function ultimaPorCanal(notificacoes: AlertNotificacao[]): AlertNotificacao[] {
  const porCanal = new Map<string, AlertNotificacao>()
  for (const n of notificacoes) {
    const atual = porCanal.get(n.canal)
    if (!atual || n.tentativa_em > atual.tentativa_em) porCanal.set(n.canal, n)
  }
  return Array.from(porCanal.values())
}

export function AlertNotificationsCompact({ notificacoes }: { notificacoes: AlertNotificacao[] }) {
  if (notificacoes.length === 0) return null
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
      {ultimaPorCanal(notificacoes).map(n => (
        <span
          key={n.canal}
          title={n.erro ?? STATUS_LABEL[n.status]}
          style={{ fontSize: 12, color: STATUS_COLOR[n.status] ?? 'var(--muted)' }}
        >
          {CANAL_ICON[n.canal] ?? n.canal} {STATUS_LABEL[n.status] ?? n.status}
        </span>
      ))}
    </div>
  )
}

export function AlertNotificationsDetailed({ notificacoes }: { notificacoes: AlertNotificacao[] }) {
  if (notificacoes.length === 0) {
    return <span style={{ color: 'var(--muted)' }}>Nenhuma notificação configurada para esta regra.</span>
  }
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <strong>Notificações</strong>
      {notificacoes.map((n, i) => (
        <div key={i} style={{ fontSize: 12, display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span>{CANAL_ICON[n.canal] ?? n.canal}</span>
          <span>{CANAL_LABEL[n.canal] ?? n.canal}</span>
          <span style={{ color: 'var(--muted)' }}>({n.tipo === 'disparo' ? 'disparo' : 'resolução'})</span>
          <span style={{ color: STATUS_COLOR[n.status] ?? 'var(--muted)', fontWeight: 600 }}>
            {STATUS_LABEL[n.status] ?? n.status}
          </span>
          <span style={{ color: 'var(--muted)' }}>{formatDt(n.tentativa_em)}</span>
          {n.erro && <span style={{ color: 'var(--danger)' }}>— {n.erro}</span>}
        </div>
      ))}
    </div>
  )
}
