'use client';
import React, { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import AccessIpModal from '../../components/AccessIpModal';
import AccessProjectCharts from '../../components/AccessProjectCharts';

type Range = '24h' | '7d' | '30d';

interface IpCount { ip: string; count: number; ultimo_acesso: string; }
interface SistemaSummaryRow { sistema: string; total_acessos: number; ips: IpCount[]; }

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
  const [ipFiltro, setIpFiltro] = useState('');
  const [rows, setRows] = useState<SistemaSummaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set());
  const [ipSelecionado, setIpSelecionado] = useState<string | null>(null);

  const days = RANGES.find(r => r.value === range)!.days;

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { days };
      if (ipFiltro) params.ip = ipFiltro;
      const r = await api.get('/access-logs/summary-por-sistema', { params });
      setRows(r.data ?? []);
    } catch { setRows([]); }
    finally { setLoading(false); }
  }, [days, ipFiltro]);

  useEffect(() => {
    const t = setTimeout(loadSummary, 300);
    return () => clearTimeout(t);
  }, [loadSummary]);

  const toggleExpandido = (sistema: string) => {
    setExpandidos(prev => {
      const next = new Set(prev);
      if (next.has(sistema)) next.delete(sistema); else next.add(sistema);
      return next;
    });
  };

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

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden', marginBottom: 32 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              {['Sistema', 'Total de acessos', ''].map((h) => (
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
                <td colSpan={3} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                  {loading ? 'Carregando...' : 'Nenhum acesso registrado no período.'}
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const aberto = expandidos.has(row.sistema);
                return (
                  <React.Fragment key={row.sistema}>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '10px 16px', fontFamily: 'monospace', fontSize: 13 }}>{row.sistema}</td>
                      <td style={{ padding: '10px 16px' }}>{row.total_acessos}</td>
                      <td style={{ padding: '10px 16px', textAlign: 'right' }}>
                        <button
                          onClick={() => toggleExpandido(row.sistema)}
                          style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 16 }}
                        >
                          {aberto ? '▾' : '▸'}
                        </button>
                      </td>
                    </tr>
                    {aberto && (
                      <tr>
                        <td colSpan={3} style={{ padding: '0 16px 16px', background: 'var(--surface)' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
                            <thead>
                              <tr>
                                {['IP', 'Acessos', 'Último acesso'].map(h => (
                                  <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase' }}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {row.ips.map(ipRow => (
                                <tr key={ipRow.ip} style={{ borderTop: '1px solid var(--border)' }}>
                                  <td style={{ padding: '6px 10px' }}>
                                    <button
                                      onClick={() => setIpSelecionado(ipRow.ip)}
                                      style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontFamily: 'monospace', fontSize: 13, padding: 0 }}
                                    >
                                      {ipRow.ip}
                                    </button>
                                  </td>
                                  <td style={{ padding: '6px 10px' }}>{ipRow.count}</td>
                                  <td style={{ padding: '6px 10px', color: 'var(--muted)', fontSize: 12 }}>{fmtRelativeDay(ipRow.ultimo_acesso)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <AccessProjectCharts />

      {ipSelecionado && (
        <AccessIpModal ip={ipSelecionado} days={days} onClose={() => setIpSelecionado(null)} />
      )}
    </div>
  );
}
