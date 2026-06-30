import ProgressBar from './ProgressBar';

interface Props {
  title: string;
  value: string;
  subtitle?: string;
  percent?: number;
  icon?: string;
}

export default function MetricCard({ title, value, subtitle, percent, icon }: Props) {
  return (
    <div style={{
      background: 'var(--card)', border: '1px solid var(--border)',
      borderRadius: 12, padding: 20, display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{title}</div>
          <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
          {subtitle && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{subtitle}</div>}
        </div>
        {icon && <span style={{ fontSize: 26, opacity: 0.8 }}>{icon}</span>}
      </div>
      {percent !== undefined && <ProgressBar percent={percent} />}
    </div>
  );
}
