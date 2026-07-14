'use client';
import { useState, useEffect, Fragment } from 'react';
import { useWebSocket, ContainerMetric } from '../../lib/ws';
import ContainerRow from '../../components/ContainerRow';
import LineChart from '../../components/LineChart';
import Toast from '../../components/Toast';
import api from '../../lib/api';

type Filter = 'all' | 'running' | 'stopped';
type ContainerAction = 'start' | 'stop' | 'restart';
interface Point { ts: string; value: number | null; }
interface ConfirmState { id: string; name: string; action: ContainerAction; }

const MONITOR_CONTAINERS = ['monitor-backend', 'monitor-frontend', 'monitor-nginx'];
const ACTION_LABEL: Record<ContainerAction, string> = { start: 'iniciar', stop: 'parar', restart: 'reiniciar' };

function wsUrl() {
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const token = localStorage.getItem('vps_token') || '';
  return `${proto}://${window.location.host}/ws/metrics?token=${token}`;
}

export default function ContainersPage() {
  const { data, connected } = useWebSocket(wsUrl());
  const [fallback, setFallback] = useState<ContainerMetric[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [cpuHistory, setCpuHistory] = useState<Record<string, Point[]>>({});
  const [logModal, setLogModal] = useState<{ id: string; name: string } | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [logSearch, setLogSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [confirmAction, setConfirmAction] = useState<ConfirmState | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionToast, setActionToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  async function runAction(id: string, action: ContainerAction) {
    setActionLoading(`${id}:${action}`);
    try {
      await api.post(`/containers/${id}/${action}`);
      setActionToast({ msg: `Comando de ${ACTION_LABEL[action]} enviado.`, type: 'success' });
    } catch {
      setActionToast({ msg: `Falha ao ${ACTION_LABEL[action]} o container.`, type: 'error' });
    } finally {
      setActionLoading(null);
    }
  }

  function requestAction(id: string, name: string, action: ContainerAction) {
    if (action === 'start') {
      runAction(id, action);
      return;
    }
    setConfirmAction({ id, name, action });
  }

  // Polling fallback when WebSocket is disconnected
  useEffect(() => {
    if (connected) return;
    const fetchContainers = async () => {
      try {
        const r = await api.get('/containers');
        setFallback(r.data.containers ?? []);
      } catch { /* ignore */ }
    };
    fetchContainers();
    const interval = setInterval(fetchContainers, 30000);
    return () => clearInterval(interval);
  }, [connected]);

  const allContainers: ContainerMetric[] = data?.containers ?? fallback;

  const visible = allContainers.filter((c) =>
    filter === 'running' ? c.status === 'running' :
    filter === 'stopped' ? c.status !== 'running' : true
  );

  // Accumulate per-container CPU history from WebSocket
  useEffect(() => {
    if (!data) return;
    setCpuHistory((prev) => {
      const next = { ...prev };
      for (const c of data.containers) {
        const arr = prev[c.id] ?? [];
        next[c.id] = [...arr, { ts: data.ts, value: c.cpu_percent }].slice(-240); // 2h @ 30s
      }
      return next;
    });
  }, [data]);

  const openLogs = async (id: string, name: string) => {
    setLogModal({ id, name });
    setLogs([]);
    setLogSearch('');
    try {
      const r = await api.get(`/containers/${id}/logs`, { params: { tail: 100 } });
      setLogs(r.data.logs ?? []);
    } catch {
      setLogs(['Erro ao carregar logs.']);
    }
  };

  const filteredLogs = logSearch
    ? logs.filter((l) => l.toLowerCase().includes(logSearch.toLowerCase()))
    : logs;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700 }}>Containers</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>
            {connected ? 'Ao vivo' : 'Reconectando...'}
          </span>
          <span style={{
            width: 9, height: 9, borderRadius: '50%',
            background: connected ? 'var(--success)' : 'var(--danger)',
            display: 'inline-block',
          }} />
        </div>
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
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
        <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--muted)', alignSelf: 'center' }}>
          {visible.length} container{visible.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Table */}
      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              {/* leading column for expand toggle */}
              <th style={{ padding: '10px 16px', width: 32 }} />
              {['Nome', 'Imagem', 'Status', 'CPU', 'RAM', 'Restarts', 'Ações'].map((h) => (
                <th key={h} style={{
                  padding: '10px 16px', textAlign: 'left', fontSize: 11,
                  color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                  {allContainers.length === 0 ? 'Aguardando dados...' : 'Nenhum container encontrado.'}
                </td>
              </tr>
            ) : (
              visible.map((c) => (
                <Fragment key={c.id}>
                  <ContainerRow
                    container={c}
                    onViewLogs={openLogs}
                    onToggleExpand={() => setExpanded(expanded === c.id ? null : c.id)}
                    isExpanded={expanded === c.id}
                    onAction={requestAction}
                    actionLoading={actionLoading}
                  />
                  {expanded === c.id && (
                    <tr key={`${c.id}-detail`}>
                      <td colSpan={8} style={{
                        background: 'var(--surface)', padding: 20,
                        borderBottom: '1px solid var(--border)',
                      }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                          <div>
                            <LineChart
                              data={cpuHistory[c.id] ?? []}
                              color="var(--accent)"
                              unit="%"
                              label={`CPU — ${c.name} (últimas 2h)`}
                              height={160}
                            />
                          </div>
                          <div>
                            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>Detalhes</div>
                            <table style={{ fontSize: 12, width: '100%', borderCollapse: 'collapse' }}>
                              <tbody>
                                {([
                                  ['Imagem', c.image || '—'],
                                  ['Status', c.status_text],
                                  ['RAM usada', `${c.mem_usage_mb.toFixed(1)} MB / ${c.mem_limit_mb.toFixed(1)} MB`],
                                  ['RX / TX', `${(c.net_rx_mb / 1024).toFixed(1)} KB / ${(c.net_tx_mb / 1024).toFixed(1)} KB`],
                                  ['Restarts', String(c.restart_count)],
                                ] as [string, string][]).map(([k, v]) => (
                                  <tr key={k} style={{ borderBottom: '1px solid var(--border)' }}>
                                    <td style={{ padding: '6px 0', color: 'var(--muted)', width: 120 }}>{k}</td>
                                    <td style={{ padding: '6px 0' }}>{v}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Log modal */}
      {logModal && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
            zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setLogModal(null)}
        >
          <div
            style={{
              background: 'var(--card)', border: '1px solid var(--border)',
              borderRadius: 12, width: '85%', maxWidth: 1000,
              maxHeight: '85vh', display: 'flex', flexDirection: 'column',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid var(--border)',
              display: 'flex', gap: 12, alignItems: 'center',
            }}>
              <span style={{ fontWeight: 600 }}>
                Logs — <span style={{ color: 'var(--accent)', fontFamily: 'monospace' }}>{logModal.name}</span>
              </span>
              <input
                placeholder="Buscar nos logs..."
                value={logSearch}
                onChange={(e) => setLogSearch(e.target.value)}
                style={{
                  flex: 1, padding: '5px 10px', background: 'var(--surface)',
                  border: '1px solid var(--border)', borderRadius: 6,
                  color: 'var(--text)', fontSize: 12,
                }}
              />
              <button
                onClick={() => setLogModal(null)}
                style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}
              >
                ×
              </button>
            </div>

            {/* Log lines */}
            <div style={{
              padding: 16, overflow: 'auto', flex: 1,
              fontFamily: 'monospace', fontSize: 12, lineHeight: 1.7,
            }}>
              {filteredLogs.length === 0 ? (
                <span style={{ color: 'var(--muted)' }}>
                  {logs.length === 0 ? 'Carregando...' : 'Nenhum resultado.'}
                </span>
              ) : (
                filteredLogs.map((l, i) => (
                  <div
                    key={i}
                    style={{
                      paddingBottom: 1,
                      color: (l.includes('ERROR') || l.includes('error'))
                        ? 'var(--danger)'
                        : 'var(--text)',
                    }}
                  >
                    {l}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modal de confirmação de ação */}
      {confirmAction && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
            zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setConfirmAction(null)}
        >
          <div
            style={{
              background: 'var(--card)', border: '1px solid var(--border)',
              borderRadius: 12, padding: 24, maxWidth: 420,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>
              {MONITOR_CONTAINERS.includes(confirmAction.name) ? 'Atenção: container do próprio monitor' : 'Confirmar ação'}
            </h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              {MONITOR_CONTAINERS.includes(confirmAction.name)
                ? `Este é um container do próprio VPS Monitor. ${confirmAction.action === 'stop' ? 'Parar' : 'Reiniciar'} "${confirmAction.name}" pode derrubar o painel de monitoramento temporariamente. Deseja continuar?`
                : `Tem certeza que deseja ${ACTION_LABEL[confirmAction.action]} o container "${confirmAction.name}"?`}
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setConfirmAction(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={() => { runAction(confirmAction.id, confirmAction.action); setConfirmAction(null); }}
                style={{ padding: '8px 20px', background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Confirmar
              </button>
            </div>
          </div>
        </div>
      )}

      {actionToast && (
        <Toast
          message={actionToast.msg}
          type={actionToast.type}
          onDismiss={() => setActionToast(null)}
        />
      )}
    </div>
  );
}
