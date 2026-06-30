'use client';
import { useState, useEffect, useRef } from 'react';
import { useWebSocket } from '../lib/ws';
import MetricCard from '../components/MetricCard';
import LineChart from '../components/LineChart';
import ContainerRow from '../components/ContainerRow';
import api from '../lib/api';

type Filter = 'all' | 'running' | 'stopped';
interface Point { ts: string; value: number | null; }

function wsUrl() {
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/metrics`;
}

const MAX_POINTS = 120; // 1h @ 30s

export default function DashboardPage() {
  const { data, connected } = useWebSocket(wsUrl());
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
    Promise.all([
      api.get('/metrics/history?metric=cpu&range=1h'),
      api.get('/metrics/history?metric=ram&range=1h'),
      api.get('/metrics/history?metric=net_rx&range=1h'),
      api.get('/metrics/history?metric=net_tx&range=1h'),
    ]).then(([c, r, rx, tx]) => {
      setCpuH(c.data.data);
      setRamH(r.data.data);
      setNetRxH(rx.data.data.map((d: Point) => ({ ...d, value: d.value ? d.value / 1048576 : 0 })));
      setNetTxH(tx.data.data.map((d: Point) => ({ ...d, value: d.value ? d.value / 1048576 : 0 })));
    }).catch(() => {});
  }, []);

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

  const containers = data?.containers ?? [];
  const visible = containers.filter((c) =>
    filter === 'running' ? c.status === 'running' :
    filter === 'stopped' ? c.status !== 'running' : true
  );
  const runningCount = containers.filter((c) => c.status === 'running').length;
  const alertCount = data?.active_alerts?.length ?? 0;

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
          icon={!data ? '⏳' : alertCount > 0 ? '⚠️' : '✅'}
        />
        <MetricCard
          title="Containers"
          value={`${runningCount} / ${containers.length}`}
          subtitle="rodando / total"
          icon="🐳"
        />
        <MetricCard
          title="RAM"
          value={`${data?.ram.percent.toFixed(1) ?? '—'}%`}
          subtitle={data ? `${(data.ram.used_mb / 1024).toFixed(1)} GB usados` : undefined}
          percent={data?.ram.percent}
        />
        <MetricCard
          title="Disco"
          value={`${data?.disk.percent.toFixed(1) ?? '—'}%`}
          subtitle={data ? `${data.disk.used_gb} GB usados` : undefined}
          percent={data?.disk.percent}
        />
        <MetricCard
          title="Uptime"
          value={data ? `${data.uptime.days}d ${data.uptime.hours}h ${data.uptime.minutes}m` : '—'}
          icon="⏱️"
        />
      </div>

      {/* Gráficos */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 20 }}>
        <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>CPU</span>
            <span style={{ color: 'var(--accent)', fontWeight: 700 }}>
              {data?.cpu.percent != null ? `${data.cpu.percent.toFixed(1)}%` : '—'}
            </span>
          </div>
          <LineChart data={cpuH} color="var(--accent)" unit="%" label="CPU %" />
          {data && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)' }}>
              Load: {data.cpu.load.map((l) => l.toFixed(2)).join(' / ')} · {data.cpu.cores} cores
            </div>
          )}
        </div>

        <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>Memória RAM</span>
            <span style={{ color: 'var(--info)', fontWeight: 700 }}>
              {data?.ram.percent.toFixed(1) ?? '—'}%
            </span>
          </div>
          <LineChart data={ramH} color="var(--info)" unit="%" label="RAM %" />
          {data && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)' }}>
              {(data.ram.used_mb / 1024).toFixed(1)} GB / {(data.ram.total_mb / 1024).toFixed(1)} GB
            </div>
          )}
        </div>

        <div style={{
          background: 'var(--card)', border: '1px solid var(--border)',
          borderRadius: 12, padding: 20, gridColumn: 'span 2',
        }}>
          <div style={{ marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>
              Rede — {data?.net.interface ?? 'eth0'}
            </span>
            {data && (
              <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 12 }}>
                ↓ {(data.net.rx_bytes_s / 1024).toFixed(1)} KB/s · ↑ {(data.net.tx_bytes_s / 1024).toFixed(1)} KB/s
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
                {data ? 'Nenhum container encontrado' : 'Aguardando dados...'}
              </td></tr>
            ) : (
              visible.map((c) => (
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
          {data!.active_alerts.map((a) => (
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
