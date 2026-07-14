'use client';
import { useEffect, useState } from 'react';
import api from '../lib/api';

interface Geo {
  is_private: boolean;
  country: string | null;
  region: string | null;
  city: string | null;
  isp: string | null;
  org: string | null;
  lat: number | null;
  lon: number | null;
}
interface SistemaDetalhe { sistema: string; count: number; ultimo_acesso: string | null; }
interface AcessoRecente { sistema: string; path: string; method: string; status_code: number | null; accessed_at: string; }
interface IpDetail {
  ip: string;
  geo: Geo;
  total_acessos: number;
  sistemas: SistemaDetalhe[];
  acessos_recentes: AcessoRecente[];
}

interface Props {
  ip: string;
  days: number;
  onClose: () => void;
}

export default function AccessIpModal({ ip, days, onClose }: Props) {
  const [detail, setDetail] = useState<IpDetail | null>(null);
  const [erro, setErro] = useState('');

  useEffect(() => {
    let cancelado = false;
    setDetail(null);
    setErro('');
    api.get(`/access-logs/ip/${ip}`, { params: { days } })
      .then(r => { if (!cancelado) setDetail(r.data); })
      .catch(() => { if (!cancelado) setErro('Erro ao carregar detalhes do IP.'); });
    return () => { cancelado = true; };
  }, [ip, days]);

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
    zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
  };
  const modal: React.CSSProperties = {
    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12,
    width: '85%', maxWidth: 640, maxHeight: '85vh', display: 'flex', flexDirection: 'column',
  };

  return (
    <div style={overlay} onClick={onClose}>
      <div style={modal} onClick={(e) => e.stopPropagation()}>
        <div style={{
          padding: '14px 20px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ fontWeight: 600, fontFamily: 'monospace' }}>{ip}</span>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}
          >×</button>
        </div>

        <div style={{ padding: 20, overflow: 'auto' }}>
          {erro && <p style={{ color: 'var(--danger)' }}>{erro}</p>}
          {!detail && !erro && <p style={{ color: 'var(--muted)' }}>Carregando...</p>}

          {detail && (
            <>
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, textTransform: 'uppercase' }}>Localização</div>
                {detail.geo.is_private ? (
                  <p style={{ fontSize: 13 }}>IP privado/local (rede interna).</p>
                ) : detail.geo.country ? (
                  <table style={{ fontSize: 13, width: '100%', borderCollapse: 'collapse' }}>
                    <tbody>
                      {([
                        ['País', detail.geo.country],
                        ['Região', detail.geo.region ?? '—'],
                        ['Cidade', detail.geo.city ?? '—'],
                        ['Provedor (ISP)', detail.geo.isp ?? '—'],
                        ['Organização', detail.geo.org ?? '—'],
                      ] as [string, string][]).map(([k, v]) => (
                        <tr key={k} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '4px 0', color: 'var(--muted)', width: 140 }}>{k}</td>
                          <td style={{ padding: '4px 0' }}>{v}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p style={{ fontSize: 13, color: 'var(--muted)' }}>Localização indisponível.</p>
                )}
              </div>

              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, textTransform: 'uppercase' }}>
                  Sistemas acessados ({detail.total_acessos} acessos no período)
                </div>
                <table style={{ fontSize: 13, width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    {detail.sistemas.map(s => (
                      <tr key={s.sistema} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '4px 0' }}>{s.sistema}</td>
                        <td style={{ padding: '4px 0', textAlign: 'right' }}>{s.count} acessos</td>
                        <td style={{ padding: '4px 0', textAlign: 'right', color: 'var(--muted)' }}>{s.ultimo_acesso ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, textTransform: 'uppercase' }}>Acessos recentes</div>
                <div style={{ maxHeight: 240, overflow: 'auto', fontFamily: 'monospace', fontSize: 11 }}>
                  {detail.acessos_recentes.length === 0 ? (
                    <p style={{ color: 'var(--muted)' }}>Sem detalhe disponível para este período.</p>
                  ) : (
                    detail.acessos_recentes.map((a, i) => (
                      <div key={i} style={{ padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                        {a.accessed_at} — {a.method} {a.sistema}{a.path} ({a.status_code ?? '—'})
                      </div>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
