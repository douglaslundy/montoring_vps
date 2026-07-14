'use client';
import ProgressBar from './ProgressBar';
import { ContainerMetric } from '../lib/ws';

interface Props {
  container: ContainerMetric;
  onViewLogs?: (id: string, name: string) => void;
  onToggleExpand?: () => void;
  isExpanded?: boolean;
  onAction?: (id: string, name: string, action: 'start' | 'stop' | 'restart') => void;
  actionLoading?: string | null;
}

function StatusBadge({ status }: { status: string }) {
  const running = status === 'running';
  const paused = status === 'paused';
  const c = running ? 'var(--success)' : paused ? 'var(--warning)' : 'var(--danger)';
  const label = running ? 'Rodando' : paused ? 'Pausado' : 'Parado';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 20,
      background: `${c}22`, border: `1px solid ${c}`,
      color: c, fontSize: 11, fontWeight: 600,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: c }} />
      {label}
    </span>
  );
}

export default function ContainerRow({ container, onViewLogs, onToggleExpand, isExpanded, onAction, actionLoading }: Props) {
  const actionBtn = {
    padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)',
    background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 13, lineHeight: 1,
  } as const;
  const actionBtnDisabled = { ...actionBtn, opacity: 0.35, cursor: 'not-allowed' } as const;

  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
      {onToggleExpand && (
        <td style={{ padding: '10px 16px', width: 32 }}>
          <button
            onClick={onToggleExpand}
            style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 16, lineHeight: 1 }}
          >
            {isExpanded ? '▼' : '▶'}
          </button>
        </td>
      )}
      <td style={{ padding: '10px 16px', fontFamily: 'monospace', color: 'var(--accent)', fontSize: 13 }}>
        {container.name}
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--muted)', fontSize: 12, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {container.image || '—'}
      </td>
      <td style={{ padding: '10px 16px' }}>
        <StatusBadge status={container.status} />
      </td>
      <td style={{ padding: '10px 16px', minWidth: 110 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, minWidth: 40 }}>{(container.cpu_percent || 0).toFixed(1)}%</span>
          <div style={{ flex: 1 }}><ProgressBar percent={container.cpu_percent || 0} height={4} /></div>
        </div>
      </td>
      <td style={{ padding: '10px 16px', minWidth: 110 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, minWidth: 40 }}>{(container.mem_percent || 0).toFixed(1)}%</span>
          <div style={{ flex: 1 }}><ProgressBar percent={container.mem_percent || 0} height={4} /></div>
        </div>
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>
        {container.restart_count ?? 0}
      </td>
      <td style={{ padding: '10px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            onClick={() => onViewLogs?.(container.id, container.name)}
            style={{
              padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 12,
            }}
          >
            Ver Logs
          </button>
          {onAction && (
            <>
              <button
                title="Iniciar"
                disabled={container.status === 'running' || actionLoading === `${container.id}:start`}
                onClick={() => onAction(container.id, container.name, 'start')}
                style={container.status === 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:start` ? '…' : '▶'}
              </button>
              <button
                title="Reiniciar"
                disabled={container.status !== 'running' || actionLoading === `${container.id}:restart`}
                onClick={() => onAction(container.id, container.name, 'restart')}
                style={container.status !== 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:restart` ? '…' : '⟳'}
              </button>
              <button
                title="Parar"
                disabled={container.status !== 'running' || actionLoading === `${container.id}:stop`}
                onClick={() => onAction(container.id, container.name, 'stop')}
                style={container.status !== 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:stop` ? '…' : '⏹'}
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}
