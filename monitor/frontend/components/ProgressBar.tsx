'use client'

interface Props { percent: number; height?: number; }

function color(p: number) {
  if (p >= 90) return 'var(--danger)';
  if (p >= 75) return 'var(--warning)';
  return 'var(--success)';
}

export default function ProgressBar({ percent, height = 6 }: Props) {
  const v = Math.max(0, Math.min(100, percent));
  return (
    <div style={{ background: 'var(--border)', borderRadius: height, height, overflow: 'hidden' }}>
      <div style={{
        width: `${v}%`, height: '100%', background: color(v),
        borderRadius: height, transition: 'width 0.4s ease',
      }} />
    </div>
  );
}
