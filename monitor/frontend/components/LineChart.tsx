'use client';
import { useId } from 'react';
import {
  AreaChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts';

interface Point { ts: string; value: number | null; value2?: number | null; }
interface AlertRange { start: string; end: string | null; }
interface Props {
  data: Point[];
  color?: string;
  unit?: string;
  label?: string;
  height?: number;
  threshold?: number | null;
  thresholdLabel?: string;
  alertRanges?: AlertRange[];
  series2Label?: string;
}

export default function LineChart({
  data, color = 'var(--accent)', unit = '%', label, height = 180,
  threshold = null, thresholdLabel, alertRanges, series2Label,
}: Props) {
  const uid = useId();
  const gradientId = `gradient-${uid.replace(/:/g, '')}`;

  // Intervalos em que havia alerta aberto. Um alerta sem resolved_at
  // continua aberto agora, entao vale ate o fim da serie.
  const ranges = (alertRanges ?? []).map((r) => [
    Date.parse(r.start),
    r.end ? Date.parse(r.end) : Number.POSITIVE_INFINITY,
  ] as const);

  const formatted = data.map((d) => {
    const t = Date.parse(d.ts);
    const emAlerta = ranges.some(([ini, fim]) => t >= ini && t <= fim);
    return {
      ...d,
      time: d.ts.includes('T')
        ? new Date(d.ts).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
        : new Date(d.ts).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }),
      // Serie so com os pontos que estavam em alerta — vira o marcador.
      marcador: emAlerta ? d.value : null,
    };
  });

  const temSegunda = series2Label != null && data.some((d) => d.value2 != null);

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
            formatter={(v: number, nome: string) => [`${v?.toFixed(1)}${unit}`, nome]}
          />
          {threshold != null && (
            <ReferenceLine
              y={threshold}
              stroke="var(--muted)"
              strokeDasharray="6 4"
              label={{
                value: thresholdLabel ?? `limite ${threshold}`,
                position: 'insideTopRight',
                fill: 'var(--muted)',
                fontSize: 10,
              }}
            />
          )}
          <Area
            type="monotone" dataKey="value" name={label || 'valor'} stroke={color} strokeWidth={2}
            fill={`url(#${gradientId})`} dot={false} connectNulls
          />
          {temSegunda && (
            <Line
              type="monotone" dataKey="value2" name={series2Label} stroke={color}
              strokeWidth={1} strokeDasharray="4 3" dot={false} opacity={0.6}
              connectNulls isAnimationActive={false}
            />
          )}
          <Line
            type="monotone" dataKey="marcador" name="em alerta" stroke="none"
            dot={{ r: 3, fill: 'var(--danger)', stroke: 'none' }}
            connectNulls={false} isAnimationActive={false} legendType="none"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
