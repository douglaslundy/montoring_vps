'use client';
import { useState, useEffect, useCallback } from 'react';
import LineChart from '../../components/LineChart';
import api from '../../lib/api';

type Range = '1h' | '6h' | '24h' | '7d';

interface Point { ts: string; value: number | null; }

const RANGES: { value: Range; label: string }[] = [
  { value: '1h', label: '1 hora' },
  { value: '6h', label: '6 horas' },
  { value: '24h', label: '24 horas' },
  { value: '7d', label: '7 dias' },
];

export default function HistoricoPage() {
  const [range, setRange] = useState<Range>('1h');
  const [data, setData] = useState<Point[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const hoursMap: Record<string, number> = { '1h': 1, '6h': 6, '24h': 24, '7d': 168 };
      const hours = hoursMap[range] ?? 24;
      const r = await api.get(`/api/metrics/history?hours=${hours}`);
      setData(r.data.data);
    } catch { setData([]); }
    finally { setLoading(false); }
  }, [range]);

  useEffect(() => { load(); }, [load]);

  const values = data.map((d) => d.value).filter((v): v is number => v !== null);
  const max = values.length ? Math.max(...values) : null;
  const min = values.length ? Math.min(...values) : null;
  const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  const fmt = (v: number | null) => v != null ? `${v.toFixed(1)}%` : '—';

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Histórico</h1>

      {/* Seletor de Período */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
        <div style={{ display: 'flex', gap: 6 }}>
          {RANGES.map((r) => (
            <button key={r.value} onClick={() => setRange(r.value)} style={{
              padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
              background: range === r.value ? 'var(--accent)' : 'transparent',
              color: range === r.value ? '#000' : 'var(--muted)',
              fontWeight: range === r.value ? 700 : 400,
              cursor: 'pointer', fontSize: 12,
            }}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Gráfico */}
      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>
          CPU (%) — Histórico
          {loading && <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 10 }}>Carregando...</span>}
        </div>
        {data.length > 0 ? (
          <LineChart data={data} color="var(--accent)" unit="%" height={300} />
        ) : (
          <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
            {loading ? 'Carregando dados...' : 'Sem dados para o período selecionado'}
          </div>
        )}
      </div>

      {/* Estatísticas */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        {[
          { label: 'Máximo', value: fmt(max) },
          { label: 'Mínimo', value: fmt(min) },
          { label: 'Média', value: fmt(avg) },
          { label: 'Amostras', value: String(values.length) },
        ].map((stat) => (
          <div key={stat.label} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>{stat.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{stat.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
