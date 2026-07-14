'use client';
import { useEffect } from 'react';

interface Props {
  message: string;
  type?: 'success' | 'error' | 'info';
  onDismiss: () => void;
}

const icons = { success: '✅', error: '❌', info: 'ℹ️' };
const colors = { success: 'var(--success)', error: 'var(--danger)', info: 'var(--info)' };

export default function Toast({ message, type = 'info', onDismiss }: Props) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
      background: 'var(--card)', border: `1px solid ${colors[type]}`,
      borderRadius: 10, padding: '14px 20px', maxWidth: 380,
      display: 'flex', gap: 12, alignItems: 'center',
      boxShadow: '0 4px 20px rgba(0,0,0,0.5)',
    }}>
      <span style={{ fontSize: 18 }}>{icons[type]}</span>
      <span style={{ flex: 1, fontSize: 13 }}>{message}</span>
      <button onClick={onDismiss} style={{
        background: 'none', border: 'none', color: 'var(--muted)',
        cursor: 'pointer', fontSize: 20, lineHeight: 1,
      }}>×</button>
    </div>
  );
}
