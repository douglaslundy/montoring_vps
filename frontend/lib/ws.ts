'use client';
import { useState, useEffect, useRef, useCallback } from 'react';

export interface ContainerMetric {
  id: string;
  id_full?: string;
  name: string;
  image: string;
  status: string;
  status_text: string;
  cpu_percent: number;
  mem_used_mb: number;
  mem_limit_mb: number;
  mem_percent: number;
  net_rx_bytes: number;
  net_tx_bytes: number;
  restart_count: number;
}

export interface ActiveAlert {
  id: number;
  severidade: 'aviso' | 'critico';
  metrica: string;
  mensagem: string;
  triggered_at: string;
}

export interface MetricsPayload {
  ts: string;
  cpu: { percent: number | null; load: number[]; cores: number; model: string };
  ram: { total_mb: number; used_mb: number; available_mb: number; percent: number };
  disk: { total_gb: number; used_gb: number; available_gb: number; percent: number; mountpoint?: string };
  net: { rx_bytes_s: number; tx_bytes_s: number; interface: string };
  temperature_c: number | null;
  uptime: { days: number; hours: number; minutes: number; seconds: number };
  containers: ContainerMetric[];
  active_alerts: ActiveAlert[];
}

export function useWebSocket(url: string) {
  const [data, setData] = useState<MetricsPayload | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(1000);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (!url || wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => { setConnected(true); retryRef.current = 1000; };
    ws.onmessage = (e) => { try { setData(JSON.parse(e.data)); } catch {} };
    ws.onclose = () => {
      setConnected(false);
      timerRef.current = setTimeout(() => {
        retryRef.current = Math.min(retryRef.current * 2, 30000);
        connect();
      }, retryRef.current);
    };
    ws.onerror = () => ws.close();
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      timerRef.current && clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { data, connected };
}
