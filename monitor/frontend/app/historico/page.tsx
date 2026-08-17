'use client';
import { useState, useEffect, useCallback } from 'react';
import LineChart from '../../components/LineChart';
import api from '../../lib/api';

type Range = '1h' | '6h' | '24h' | '7d';
type Metric = 'cpu' | 'ram' | 'disk' | 'net_rx' | 'net_tx' | 'load' | 'temperature';

interface Point { ts: string; value: number | null; value2?: number | null; }
interface Alerta { triggered_at: string; resolved_at: string | null; valor: number; severidade: string; }

const RANGES: { value: Range; label: string }[] = [
  { value: '1h', label: '1 hora' },
  { value: '6h', label: '6 horas' },
  { value: '24h', label: '24 horas' },
  { value: '7d', label: '7 dias' },
];

const METRICS: { value: Metric; label: string; unit: string; color: string }[] = [
  { value: 'cpu',         label: 'CPU',         unit: '%',    color: 'var(--accent)'  },
  { value: 'ram',         label: 'RAM',         unit: '%',    color: 'var(--info)'    },
  { value: 'disk',        label: 'Disco',       unit: '%',    color: 'var(--warning)' },
  { value: 'net_rx',      label: 'Rede ↓ RX',  unit: ' B/s', color: 'var(--success)' },
  { value: 'net_tx',      label: 'Rede ↑ TX',  unit: ' B/s', color: '#a78bfa'        },
  { value: 'load',        label: 'Load Avg',    unit: '',     color: '#f97316'        },
  { value: 'temperature', label: 'Temperatura', unit: '°C',   color: '#ef4444'        },
];

function fmtValue(v: number | null, unit: string): string {
  if (v == null) return '—';
  if (unit === ' B/s') {
    if (v >= 1048576) return `${(v / 1048576).toFixed(2)} MB/s`;
    if (v >= 1024)    return `${(v / 1024).toFixed(1)} KB/s`;
    return `${v.toFixed(0)} B/s`;
  }
  return `${v.toFixed(unit === '' ? 2 : 1)}${unit}`;
}

export default function HistoricoPage() {
  const [range, setRange]   = useState<Range>('1h');
  const [metric, setMetric] = useState<Metric>('cpu');
  const [data, setData]     = useState<Point[]>([]);
  const [loading, setLoading] = useState(false);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [regraNome, setRegraNome] = useState<string | null>(null);
  const [alertas, setAlertas] = useState<Alerta[]>([]);
  const [companion, setCompanion] = useState<string | null>(null);

  const current = METRICS.find(m => m.value === metric)!;
  const companionLabel = companion === 'load_5m' ? 'média 5 min' : null;

  const load = useCallback(async () => {
    setLoading(true);
    const hoursMap: Record<string, number> = { '1h': 1, '6h': 6, '24h': 24, '7d': 168 };
    const hours = hoursMap[range] ?? 24;

    // As duas chamadas sao independentes: uma falha nas anotacoes (ex: backend
    // antigo sem o endpoint, numa janela de deploy) nao pode derrubar a serie
    // principal e trocar o grafico por "Sem dados para o periodo selecionado".
    try {
      const serie = await api.get(`/metrics/history?metric=${metric}&hours=${hours}`);
      setData(serie.data.data ?? []);
      setCompanion(serie.data.companion ?? null);
    } catch {
      setData([]); setCompanion(null);
    }

    try {
      const anot = await api.get(`/metrics/history/annotations?metric=${metric}&hours=${hours}`);
      setThreshold(anot.data.threshold ?? null);
      setRegraNome(anot.data.regra ?? null);
      setAlertas(anot.data.alertas ?? []);
    } catch {
      setThreshold(null); setRegraNome(null); setAlertas([]);
    }

    setLoading(false);
  }, [range, metric]);

  useEffect(() => { load(); }, [load]);

  const values = data.map(d => d.value).filter((v): v is number => v !== null);
  const max = values.length ? Math.max(...values) : null;
  const min = values.length ? Math.min(...values) : null;
  const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;

  const tabBtn = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#000' : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  const metricBtn = (active: boolean, color: string): React.CSSProperties => ({
    padding: '5px 12px', borderRadius: 6, border: `1px solid ${active ? color : 'var(--border)'}`,
    background: active ? color + '22' : 'transparent',
    color: active ? color : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Histórico</h1>

      {/* Seletor de Métrica */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Métrica</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {METRICS.map(m => (
            <button key={m.value} onClick={() => setMetric(m.value)} style={metricBtn(metric === m.value, m.color)}>
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Seletor de Período */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
        <div style={{ display: 'flex', gap: 6 }}>
          {RANGES.map(r => (
            <button key={r.value} onClick={() => setRange(r.value)} style={tabBtn(range === r.value)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Gráfico */}
      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16, color: current.color }}>
          {current.label}
          {loading && <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 10, fontWeight: 400 }}>Carregando...</span>}
        </div>
        {data.length > 0 ? (
          <LineChart
            data={data}
            color={current.color}
            unit={current.unit}
            height={300}
            threshold={threshold}
            thresholdLabel={regraNome ? `${regraNome} (${threshold})` : undefined}
            alertRanges={alertas.map(a => ({ start: a.triggered_at, end: a.resolved_at }))}
            series2Label={companionLabel ?? undefined}
          />
        ) : (
          <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
            {loading ? 'Carregando dados...' : 'Sem dados para o período selecionado'}
          </div>
        )}
      </div>

      {(threshold != null || alertas.length > 0 || companionLabel) && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16 }}>
          {threshold != null && <>Linha tracejada cinza = limite do alerta ({regraNome ?? threshold}). </>}
          {companionLabel && <>Linha tracejada na cor da série = {companionLabel}. </>}
          {alertas.length > 0 && <>Pontos vermelhos = alerta disparado ({alertas.length} no período).</>}
        </div>
      )}

      {/* Estatísticas */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        {[
          { label: 'Máximo', value: fmtValue(max, current.unit) },
          { label: 'Mínimo', value: fmtValue(min, current.unit) },
          { label: 'Média',  value: fmtValue(avg, current.unit) },
          { label: 'Amostras', value: String(values.length) },
        ].map(stat => (
          <div key={stat.label} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>{stat.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{stat.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
