'use client'

interface Props {
  name?: string | null
}

export default function VpsBadge({ name }: Props) {
  if (!name) return null
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      color: 'var(--muted)',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      whiteSpace: 'nowrap',
    }}>
      🖥️ {name}
    </span>
  )
}
