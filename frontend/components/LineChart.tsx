'use client';
import { useId } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts';

interface Point { ts: string; value: number | null; }
interface Props {
  data: Point[];
  color?: string;
  unit?: string;
  label?: string;
  height?: number;
}

export default function LineChart({
  data, color = 'var(--accent)', unit = '%', label, height = 180,
}: Props) {
  const uid = useId();
  const gradientId = `gradient-${uid.replace(/:/g, '')}`;
  const formatted = data.map((d) => ({
    ...d,
    time: new Date(d.ts).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
  }));

  return (
    <div>
      {label && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{label}</div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={formatted} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="time" stroke="var(--muted)" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis
            stroke="var(--muted)"
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `${v}${unit}`}
            domain={unit === '%' ? [0, 100] : ['auto', 'auto']}
          />
          <Tooltip
            contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: 'var(--muted)' }}
            formatter={(v: number) => [`${v?.toFixed(1)}${unit}`, label || '']}
          />
          <Area
            type="monotone" dataKey="value" stroke={color} strokeWidth={2}
            fill={`url(#${gradientId})`} dot={false} connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
