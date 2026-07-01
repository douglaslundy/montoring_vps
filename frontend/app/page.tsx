'use client';
import { useState, useEffect, useRef } from 'react';
import { useWebSocket, MetricsPayload, ContainerMetric } from '../lib/ws';
import MetricCard from '../components/MetricCard';
import LineChart from '../components/LineChart';
import ContainerRow from '../components/ContainerRow';
import api from '../lib/api';

type Filter = 'all' | 'running' | 'stopped';
interface Point { ts: string; value: number | null; }

function wsUrl() {
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const token = localStorage.getItem('vps_token') || '';
  return `${proto}://${window.location.host}/ws/metrics?token=${token}`;
}

const MAX_POINTS = 120; // 1h @ 30s

export default function DashboardPage() {
  const { data, connected } = useWebSocket(wsUrl());
  const [metrics, setMetrics] = useState<MetricsPayload | null>(null);
  const effectiveData = data ?? metrics;
  const [cpuH, setCpuH] = useState<Point[]>([]);
  const [ramH, setRamH] = useState<Point[]>([]);
  const [netRxH, setNetRxH] = useState<Point[]>([]);
  const [netTxH, setNetTxH] = useState<Point[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [countdown, setCountdown] = useState(30);
  const [logsModal, setLogsModal] = useState<{ id: string; name: string } | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  // Carregar histórico inicial
  useEffect(() => {
    api.get('/metrics/history?metric=cpu&hours=1').then(r => {
      setCpuH(r.data.data ?? []);
    }).catch(() => {});
    api.get('/metrics/history?metric=ram&hours=1').then(r => {
      setRamH(r.data.data ?? []);
    }).catch(() => {});
    api.get('/metrics/history?metric=net_rx&hours=1').then(r => {
      setNetRxH((r.data.data ?? []).map((p: Point) => ({ ts: p.ts, value: p.value != null ? p.value / 1048576 : null })));
    }).catch(() => {});
    api.get('/metrics/history?metric=net_tx&hours=1').then(r => {
      setNetTxH((r.data.data ?? []).map((p: Point) => ({ ts: p.ts, value: p.value != null ? p.value / 1048576 : null })));
    }).catch(() => {});
  }, []);

  // Fallback GET quando WS desconectado
  useEffect(() => {
    if (connected) return;
    const fetchMetrics = async () => {
      try {
        const res = await api.get('/metrics/current');
        if (res.data) setMetrics(res.data);
      } catch {}
    };
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 30000);
    return () => clearInterval(interval);
  }, [connected]);

  // Acumular pontos do WebSocket
  useEffect(() => {
    if (!data) return;
    const add = (arr: Point[], v: number | null) => [...arr, { ts: data.ts, value: v }].slice(-MAX_POINTS);
    setCpuH((h) => add(h, data.cpu.percent));
    setRamH((h) => add(h, data.ram.percent));
    setNetRxH((h) => add(h, data.net.rx_bytes_s / 1048576));
    setNetTxH((h) => add(h, data.net.tx_bytes_s / 1048576));
    setCountdown(30);
  }, [data]);

  // Countdown
  useEffect(() => {
    const t = setInterval(() => setCountdown((c) => (c <= 1 ? 30 : c - 1)), 1000);
    return () => clearInterval(t);
  }, []);

  const openLogs = async (id: string, name: string) => {
    setLogsModal({ id, name });
    setLogs([]);
    try {
      const r = await api.get(`/containers/${id}/logs`);
      setLogs(r.data.logs);
    } catch { setLogs(['Erro ao carregar logs.']); }
  };

  const containers = effectiveData?.containers ?? [];
  const visible = containers.filter((c: ContainerMetric) =>
    filter === 'running' ? c.status === 'running' :
    filter === 'stopped' ? c.status !== 'running' : true
  );
  const runningCount = containers.filter((c: ContainerMetric) => c.status === 'running').length;
  const alertCount = effectiveData?.active_alerts?.length ?? 0;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700 }}>Dashboard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>
            {connected ? `Atualiza em ${countdown}s` : 'Reconectando...'}
          </span>
          <span style={{
            width: 9, height: 9, borderRadius: '50%',
            background: connected ? 'var(--success)' : 'var(--danger)',
            display: 'inline-block',
          }} />
        </div>
      </div>

      {/* Cards de resumo */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 14, marginBottom: 20 }}>
        <MetricCard
          title="Saúde Geral"
          value={alertCount > 0 ? `${alertCount} alerta(s)` : 'Tudo normal'}
          icon={!effectiveData ? '⏳' : alertCount > 0 ? '⚠️' : '✅'}
        />
        <MetricCard
          title="Containers"
          value={`${runningCount} / ${containers.length}`}
          subtitle="rodando / total"
          icon="🐳"
        />
        <MetricCard
          title="RAM"
          value={`${effectiveData?.ram?.percent?.toFixed(1) ?? '—'}%`}
          subtitle={effectiveData ? `${(effectiveData.ram.used_mb / 1024).toFixed(1)} GB / ${(effectiveData.ram.total_mb / 1024).toFixed(1)} GB` : undefined}
          percent={effectiveData?.ram?.percent}
        />
        <MetricCard
          title="Disco"
          value={`${effectiveData?.disk?.percent?.toFixed(1) ?? '—'}%`}
          subtitle={effectiveData ? `${effectiveData.disk.used_gb} GB / ${effectiveData.disk.total_gb} GB` : undefined}
          percent={effectiveData?.disk?.percent}
        />
        <MetricCard
          title="Uptime"
          value={effectiveData ? `${effectiveData.uptime.days}d ${effectiveData.uptime.hours}h ${effectiveData.uptime.minutes}m` : '—'}
          icon="⏱️"
        />
        <MetricCard
          title="CPU%"
          value={`${effectiveData?.cpu?.percent?.toFixed(1) ?? '—'}%`}
          subtitle={effectiveData ? `${effectiveData.cpu.cores} cores` : undefined}
          percent={effectiveData?.cpu?.percent ?? undefined}
        />
        <MetricCard
          title="Temperatura"
          value={effectiveData?.temperature_c != null ? `${effectiveData.temperature_c.toFixed(1)}°C` : 'N/A'}
          icon="🌡️"
        />
        <MetricCard
          title="Load Average"
          value={effectiveData?.cpu?.load?.[0] != null ? effectiveData.cpu.load[0].toFixed(2) : '—'}
          icon="📊"
        />
      </div>

      {/* Gráficos */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 20 }}>
        <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>CPU</span>
            <span style={{ color: 'var(--accent)', fontWeight: 700 }}>
              {effectiveData?.cpu?.percent != null ? `${effectiveData.cpu.percent.toFixed(1)}%` : '—'}
            </span>
          </div>
          <LineChart data={cpuH} color="var(--accent)" unit="%" label="CPU %" />
          {effectiveData && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)' }}>
              Load: {effectiveData.cpu.load.map((l: number) => l.toFixed(2)).join(' / ')} · {effectiveData.cpu.cores} cores
            </div>
          )}
        </div>

        <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>Memória RAM</span>
            <span style={{ color: 'var(--info)', fontWeight: 700 }}>
              {effectiveData?.ram?.percent?.toFixed(1) ?? '—'}%
            </span>
          </div>
          <LineChart data={ramH} color="var(--info)" unit="%" label="RAM %" />
          {effectiveData && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)' }}>
              {(effectiveData.ram.used_mb / 1024).toFixed(1)} GB / {(effectiveData.ram.total_mb / 1024).toFixed(1)} GB
            </div>
          )}
        </div>

        <div style={{
          background: 'var(--card)', border: '1px solid var(--border)',
          borderRadius: 12, padding: 20, gridColumn: 'span 2',
        }}>
          <div style={{ marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>
              Rede — {effectiveData?.net?.interface ?? 'eth0'}
            </span>
            {effectiveData && (
              <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 12 }}>
                ↓ {(effectiveData.net.rx_bytes_s / 1024).toFixed(1)} KB/s · ↑ {(effectiveData.net.tx_bytes_s / 1024).toFixed(1)} KB/s
              </span>
            )}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <LineChart data={netRxH} color="var(--success)" unit=" MB/s" label="Recebido (MB/s)" />
            <LineChart data={netTxH} color="var(--warning)" unit=" MB/s" label="Enviado (MB/s)" />
          </div>
        </div>
      </div>

      {/* Tabela de containers */}
      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>
            Containers
            <span style={{ color: 'var(--muted)', fontWeight: 400, marginLeft: 8 }}>
              — atualiza em {countdown}s
            </span>
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['all', 'running', 'stopped'] as Filter[]).map((f) => (
              <button key={f} onClick={() => setFilter(f)} style={{
                padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
                background: filter === f ? 'var(--border)' : 'transparent',
                color: filter === f ? 'var(--text)' : 'var(--muted)',
                cursor: 'pointer', fontSize: 12,
              }}>
                {f === 'all' ? 'Todos' : f === 'running' ? 'Rodando' : 'Parados'}
              </button>
            ))}
          </div>
        </div>

        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['Nome', 'Imagem', 'Status', 'CPU', 'RAM', 'Restarts', 'Ações'].map((h) => (
                <th key={h} style={{ padding: '8px 16px', textAlign: 'left', fontSize: 11, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                {effectiveData ? 'Nenhum container encontrado' : 'Aguardando dados...'}
              </td></tr>
            ) : (
              visible.map((c: ContainerMetric) => (
                <ContainerRow key={c.id} container={c} onViewLogs={openLogs} />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Alertas ativos */}
      {alertCount > 0 && (
        <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 16 }}>Alertas Ativos</div>
          {effectiveData!.active_alerts.map((a: any) => (
            <div key={a.id} style={{ display: 'flex', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontSize: 16 }}>{a.severidade === 'critico' ? '🔴' : '⚠️'}</span>
              <div>
                <div style={{ fontSize: 13 }}>{a.mensagem}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
                  {new Date(a.triggered_at).toLocaleString('pt-BR')}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal de logs */}
      {logsModal && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setLogsModal(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '80%', maxWidth: 900, maxHeight: '80vh', display: 'flex', flexDirection: 'column' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 600 }}>Logs — <span style={{ color: 'var(--accent)', fontFamily: 'monospace' }}>{logsModal.name}</span></span>
              <button onClick={() => setLogsModal(null)} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}>×</button>
            </div>
            <div style={{ padding: 16, overflow: 'auto', flex: 1, fontFamily: 'monospace', fontSize: 12, lineHeight: 1.7 }}>
              {logs.length === 0
                ? <span style={{ color: 'var(--muted)' }}>Carregando...</span>
                : logs.map((l, i) => <div key={i}>{l}</div>)
              }
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
