'use client';
import { useState, useEffect, useCallback } from 'react';
import LineChart from './LineChart';
import api from '../lib/api';

type Periodo = 'dia' | 'mes';
type MetricaRecurso = 'cpu' | 'ram';

interface Ponto { ts: string; value: number | null; }
interface PontoRecurso {
  ts: string;
  cpu_percent: number | null;
  mem_percent: number | null;
  net_rx_mb: number | null;
  net_tx_mb: number | null;
}

const METRICAS_RECURSO: { value: MetricaRecurso; label: string; unit: string; color: string; campo: keyof Omit<PontoRecurso, 'ts'> }[] = [
  { value: 'cpu',    label: 'CPU',     unit: '%',  color: 'var(--accent)',  campo: 'cpu_percent' },
  { value: 'ram',    label: 'RAM',     unit: '%',  color: 'var(--info)',    campo: 'mem_percent' },
];

function hojeISO(): string {
  return new Date().toISOString().slice(0, 10);
}
function mesAtualISO(): string {
  return new Date().toISOString().slice(0, 7);
}

export default function AccessProjectCharts() {
  const [periodo, setPeriodo] = useState<Periodo>('dia');
  const [diaEspecifico, setDiaEspecifico] = useState('');
  const [mes, setMes] = useState(mesAtualISO());
  const [sistemas, setSistemas] = useState<string[]>([]);
  const [projeto, setProjeto] = useState('');
  const [acessos, setAcessos] = useState<Ponto[]>([]);
  const [containerName, setContainerName] = useState<string | null>(null);
  const [recursoMetrica, setRecursoMetrica] = useState<MetricaRecurso>('cpu');
  const [recursos, setRecursos] = useState<PontoRecurso[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get('/access-logs/sistemas').then(r => {
      setSistemas(r.data ?? []);
    }).catch(() => setSistemas([]));
  }, []);

  const paramsPeriodo = useCallback((): Record<string, string> => {
    if (periodo === 'mes') return { granularity: 'day', month: mes };
    return diaEspecifico ? { granularity: 'hour', day: diaEspecifico } : { granularity: 'hour' };
  }, [periodo, mes, diaEspecifico]);

  const loadAcessos = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { ...paramsPeriodo() };
      if (projeto) params.sistema = projeto;
      const r = await api.get('/access-logs/timeseries', { params });
      setAcessos(r.data.data ?? []);
    } catch { setAcessos([]); }
    finally { setLoading(false); }
  }, [projeto, paramsPeriodo]);

  const resolveContainer = useCallback(async () => {
    if (!projeto) { setContainerName(null); return; }
    try {
      const r = await api.get('/access-logs/container-para-sistema', { params: { sistema: projeto } });
      setContainerName(r.data?.container_name ?? null);
    } catch { setContainerName(null); }
  }, [projeto]);

  const loadRecursos = useCallback(async () => {
    if (!containerName) { setRecursos([]); return; }
    try {
      const rh = await api.get('/metrics/container-history', { params: { container_name: containerName, ...paramsPeriodo() } });
      setRecursos(rh.data.data ?? []);
    } catch { setRecursos([]); }
  }, [containerName, paramsPeriodo]);

  useEffect(() => { loadAcessos(); }, [loadAcessos]);
  useEffect(() => { resolveContainer(); }, [resolveContainer]);
  useEffect(() => { loadRecursos(); }, [loadRecursos]);

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

  const metricaAtual = METRICAS_RECURSO.find(m => m.value === recursoMetrica)!;
  const dadosRecurso: Ponto[] = recursos.map(p => ({ ts: p.ts, value: p[metricaAtual.campo] }));

  return (
    <div style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 16 }}>Acessos por projeto</h2>

      <div style={{ display: 'flex', gap: 20, marginBottom: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => setPeriodo('dia')} style={tabBtn(periodo === 'dia')}>Dia</button>
            <button onClick={() => setPeriodo('mes')} style={tabBtn(periodo === 'mes')}>Mês</button>
          </div>
        </div>

        {periodo === 'dia' ? (
          <div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Dia</div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <button onClick={() => setDiaEspecifico('')} style={tabBtn(!diaEspecifico)}>Últimas 12h</button>
              <input
                type="date"
                value={diaEspecifico}
                max={hojeISO()}
                onChange={(e) => setDiaEspecifico(e.target.value)}
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12 }}
              />
            </div>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Mês</div>
            <input
              type="month"
              value={mes}
              max={mesAtualISO()}
              onChange={(e) => setMes(e.target.value)}
              style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12 }}
            />
          </div>
        )}

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Projeto</div>
          <select
            value={projeto}
            onChange={(e) => setProjeto(e.target.value)}
            style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12 }}
          >
            <option value="">Todos</option>
            {sistemas.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>
          Acessos
          {loading && <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 10, fontWeight: 400 }}>Carregando...</span>}
        </div>
        {acessos.length > 0 ? (
          <LineChart data={acessos} unit="" label="Acessos" height={240} />
        ) : (
          <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
            {sistemas.length === 0 ? 'Nenhum sistema disponível' : 'Sem dados para o período selecionado'}
          </div>
        )}
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>Recursos utilizados</div>
        {containerName ? (
          <>
            <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
              {METRICAS_RECURSO.map(m => (
                <button key={m.value} onClick={() => setRecursoMetrica(m.value)} style={metricBtn(recursoMetrica === m.value, m.color)}>
                  {m.label}
                </button>
              ))}
            </div>
            {dadosRecurso.some(d => d.value !== null) ? (
              <LineChart data={dadosRecurso} color={metricaAtual.color} unit={metricaAtual.unit} height={240} />
            ) : (
              <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
                Sem amostras de recurso para o período selecionado
              </div>
            )}
          </>
        ) : (
          <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', textAlign: 'center' }}>
            {projeto
              ? 'Recursos não disponíveis para este projeto (nenhum container do Traefik encontrado para este domínio).'
              : 'Selecione um projeto específico para ver recursos utilizados.'}
          </div>
        )}
      </div>
    </div>
  );
}
