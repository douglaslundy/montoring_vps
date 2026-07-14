'use client'

interface Props {
  severidade: string
}

const COLORS: Record<string, string> = {
  critico: 'var(--danger)',
  aviso: 'var(--warning)',
  info: 'var(--info)',
}

const LABELS: Record<string, string> = {
  critico: 'CRÍTICO',
  aviso: 'AVISO',
  info: 'INFO',
}

export default function AlertBadge({ severidade }: Props) {
  const color = COLORS[severidade] ?? 'var(--muted)'
  const label = LABELS[severidade] ?? severidade.toUpperCase()
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: '0.05em',
      color: '#fff',
      background: color,
    }}>
      {label}
    </span>
  )
}
