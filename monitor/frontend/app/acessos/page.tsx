'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import AccessIpModal from '../../components/AccessIpModal';

type Range = '24h' | '7d' | '30d';

interface SistemaCount { sistema: string; count: number; }
interface AccessSummaryRow {
  ip: string;
  total_acessos: number;
  sistemas: SistemaCount[];
  primeiro_acesso: string;
  ultimo_acesso: string;
}

const RANGES: { value: Range; label: string; days: number }[] = [
  { value: '24h', label: '24 horas', days: 1 },
  { value: '7d', label: '7 dias', days: 7 },
  { value: '30d', label: '30 dias', days: 30 },
];

function fmtRelativeDay(day: string): string {
  const todayStr = new Date().toISOString().slice(0, 10);
  const diffDays = Math.round((Date.parse(todayStr) - Date.parse(day)) / 86400000);
  if (diffDays === 0) return 'hoje';
  if (diffDays === 1) return 'ontem';
  if (diffDays < 0) return day;
  return `há ${diffDays} dias`;
}

export default function AcessosPage() {
  const [range, setRange] = useState<Range>('7d');
  const [sistemaFiltro, setSistemaFiltro] = useState('');
  const [ipFiltro, setIpFiltro] = useState('');
  const [sistemas, setSistemas] = useState<string[]>([]);
  const [rows, setRows] = useState<AccessSummaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [ipSelecionado, setIpSelecionado] = useState<string | null>(null);

  const days = RANGES.find(r => r.value === range)!.days;

  const loadSistemas = useCallback(async () => {
    try {
      const r = await api.get('/access-logs/sistemas');
      setSistemas(r.data ?? []);
    } catch { setSistemas([]); }
  }, []);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { days };
      if (sistemaFiltro) params.sistema = sistemaFiltro;
      if (ipFiltro) params.ip = ipFiltro;
      const r = await api.get('/access-logs/summary', { params });
      setRows(r.data ?? []);
    } catch { setRows([]); }
    finally { setLoading(false); }
  }, [days, sistemaFiltro, ipFiltro]);

  useEffect(() => { loadSistemas(); }, [loadSistemas]);
  useEffect(() => {
    const t = setTimeout(loadSummary, 300);
    return () => clearTimeout(t);
  }, [loadSummary]);

  const tabBtn = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#000' : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Acessos</h1>

      <div style={{ display: 'flex', gap: 20, marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {RANGES.map(r => (
              <button key={r.value} onClick={() => setRange(r.value)} style={tabBtn(range === r.value)}>
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Sistema</div>
          <select
            value={sistemaFiltro}
            onChange={(e) => setSistemaFiltro(e.target.value)}
            style={{
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'var(--surface)', color: 'var(--text)', fontSize: 12,
            }}
          >
            <option value="">Todos</option>
            {sistemas.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Filtrar por IP</div>
          <input
            placeholder="ex: 203.0.113"
            value={ipFiltro}
            onChange={(e) => setIpFiltro(e.target.value)}
            style={{
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'var(--surface)', color: 'var(--text)', fontSize: 12, width: 160,
            }}
          />
        </div>
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              {['IP', 'Acessos', 'Sistemas acessados', 'Último acesso'].map((h) => (
                <th key={h} style={{
                  padding: '10px 16px', textAlign: 'left', fontSize: 11,
                  color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                  {loading ? 'Carregando...' : 'Nenhum acesso registrado no período.'}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.ip} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 16px' }}>
                    <button
                      onClick={() => setIpSelecionado(row.ip)}
                      style={{
                        background: 'none', border: 'none', color: 'var(--accent)',
                        cursor: 'pointer', fontFamily: 'monospace', fontSize: 13, padding: 0,
                      }}
                    >
                      {row.ip}
                    </button>
                  </td>
                  <td style={{ padding: '10px 16px' }}>{row.total_acessos}</td>
                  <td style={{ padding: '10px 16px' }}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {row.sistemas.slice(0, 3).map(s => (
                        <span key={s.sistema} style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 10,
                          background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--muted)',
                        }}>
                          {s.sistema} ({s.count})
                        </span>
                      ))}
                      {row.sistemas.length > 3 && (
                        <span style={{ fontSize: 11, color: 'var(--muted)' }}>+{row.sistemas.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: '10px 16px', color: 'var(--muted)', fontSize: 12 }}>{fmtRelativeDay(row.ultimo_acesso)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {ipSelecionado && (
        <AccessIpModal ip={ipSelecionado} days={days} onClose={() => setIpSelecionado(null)} />
      )}
    </div>
  );
}
