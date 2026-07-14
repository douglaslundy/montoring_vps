# VPS Monitor Fase 1 — Parte 2: Backend Build + Frontend (Tasks 10–17)

> Continuação do plano. Leia a Parte 1 antes desta.

---

## Task 10: Backend Dockerfile + requirements.txt

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/Dockerfile`

**Interfaces:**
- Produces: imagem `monitor-backend` que inicia com `uvicorn main:app`

- [ ] **Step 1: Criar backend/requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy==2.0.35
apscheduler==3.10.4
httpx==0.27.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
cryptography==43.0.3
slowapi==0.1.9
python-multipart==0.0.12
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Criar backend/Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/app/data/monitor.db
ENV PROC_BASE=/host/proc
ENV SYS_BASE=/host/sys

RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Verificar build**

```bash
docker build -t monitor-backend-test ./backend
```
Esperado: build sem erros

- [ ] **Step 4: Testar inicialização**

```bash
docker run --rm -e JWT_SECRET=test-secret-aqui -e MONITOR_USER=admin \
  -e MONITOR_PASSWORD=admin monitor-backend-test \
  uvicorn main:app --host 0.0.0.0 --port 8000 &
sleep 3
curl http://localhost:8000/api/health
# Esperado: {"status":"ok"}
```

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/Dockerfile
git commit -m "feat: Dockerfile e requirements do backend"
```

---

## Task 11: Frontend — Setup do Projeto

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/next.config.ts`
- Create: `frontend/app/globals.css`
- Create: `frontend/app/layout.tsx` (estrutura base apenas — detalhado na Task 14)
- Create: `frontend/Dockerfile`

**Interfaces:**
- Produces: projeto Next.js 14 buildável com `npm run build`

- [ ] **Step 1: Criar frontend/package.json**

```json
{
  "name": "vps-monitor-frontend",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start"
  },
  "dependencies": {
    "next": "14.2.16",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "axios": "^1.7.7",
    "recharts": "^2.13.3"
  },
  "devDependencies": {
    "@types/node": "^22.7.5",
    "@types/react": "^18.3.11",
    "@types/react-dom": "^18.3.1",
    "typescript": "^5.6.3"
  }
}
```

- [ ] **Step 2: Criar frontend/tsconfig.json**

```json
{
  "compilerOptions": {
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: Criar frontend/next.config.ts**

```typescript
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
};

export default nextConfig;
```

- [ ] **Step 4: Criar frontend/app/globals.css**

```css
:root {
  --bg: #0f1117;
  --surface: #161b27;
  --card: #1c2333;
  --border: #2a3347;
  --text: #e8eaf0;
  --muted: #6b7a99;
  --accent: #f5a623;
  --success: #43a047;
  --danger: #e53935;
  --warning: #fb8c00;
  --info: #1e88e5;
}

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body {
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}

a { color: inherit; text-decoration: none; }
button { font-family: inherit; }
```

- [ ] **Step 5: Criar frontend/app/layout.tsx (estrutura mínima para build)**

```tsx
import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'VPS Monitor',
  description: 'Monitoramento de servidor',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 6: Criar frontend/app/page.tsx (stub para build)**

```tsx
export default function Page() {
  return <div>VPS Monitor</div>;
}
```

- [ ] **Step 7: Criar frontend/Dockerfile**

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public 2>/dev/null || true

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
CMD ["node", "server.js"]
```

- [ ] **Step 8: Verificar build**

```bash
cd frontend && npm install && npm run build
```
Esperado: build sem erros, pasta `.next/` gerada

- [ ] **Step 9: Commit**

```bash
git add frontend/
git commit -m "feat: setup inicial do projeto Next.js 14"
```

---

## Task 12: Frontend — Bibliotecas (api.ts + ws.ts)

**Files:**
- Create: `frontend/lib/api.ts`
- Create: `frontend/lib/ws.ts`

**Interfaces:**
- Produces: `api` (instância axios com auth), `useWebSocket(url)` hook que retorna `{ data: MetricsPayload | null, connected: boolean }`

- [ ] **Step 1: Criar frontend/lib/api.ts**

```typescript
import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

api.interceptors.request.use((config) => {
  const token = typeof window !== 'undefined' ? localStorage.getItem('vps_token') : null;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && typeof window !== 'undefined') {
      localStorage.removeItem('vps_token');
      window.location.href = '/login';
    }
    return Promise.reject(err);
  }
);

export default api;
```

- [ ] **Step 2: Criar frontend/lib/ws.ts**

```typescript
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
```

- [ ] **Step 3: Verificar tipos (sem erros TypeScript)**

```bash
cd frontend && npx tsc --noEmit
```
Esperado: sem erros

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/
git commit -m "feat: biblioteca axios com auth e hook useWebSocket"
```

---

## Task 13: Frontend — Componentes Compartilhados

**Files:**
- Create: `frontend/components/MetricCard.tsx`
- Create: `frontend/components/ProgressBar.tsx`
- Create: `frontend/components/Toast.tsx`
- Create: `frontend/components/LineChart.tsx`
- Create: `frontend/components/ContainerRow.tsx`

**Interfaces:**
- Produces: componentes React reutilizáveis

- [ ] **Step 1: Criar frontend/components/ProgressBar.tsx**

```tsx
interface Props { percent: number; height?: number; }

function color(p: number) {
  if (p >= 90) return 'var(--danger)';
  if (p >= 75) return 'var(--warning)';
  return 'var(--success)';
}

export default function ProgressBar({ percent, height = 6 }: Props) {
  const v = Math.max(0, Math.min(100, percent));
  return (
    <div style={{ background: 'var(--border)', borderRadius: height, height, overflow: 'hidden' }}>
      <div style={{
        width: `${v}%`, height: '100%', background: color(v),
        borderRadius: height, transition: 'width 0.4s ease',
      }} />
    </div>
  );
}
```

- [ ] **Step 2: Criar frontend/components/MetricCard.tsx**

```tsx
import ProgressBar from './ProgressBar';

interface Props {
  title: string;
  value: string;
  subtitle?: string;
  percent?: number;
  icon?: string;
}

export default function MetricCard({ title, value, subtitle, percent, icon }: Props) {
  return (
    <div style={{
      background: 'var(--card)', border: '1px solid var(--border)',
      borderRadius: 12, padding: 20, display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{title}</div>
          <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
          {subtitle && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{subtitle}</div>}
        </div>
        {icon && <span style={{ fontSize: 26, opacity: 0.8 }}>{icon}</span>}
      </div>
      {percent !== undefined && <ProgressBar percent={percent} />}
    </div>
  );
}
```

- [ ] **Step 3: Criar frontend/components/Toast.tsx**

```tsx
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
```

- [ ] **Step 4: Criar frontend/components/LineChart.tsx**

```tsx
'use client';
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
            <linearGradient id={`g-${label}`} x1="0" y1="0" x2="0" y2="1">
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
            fill={`url(#g-${label})`} dot={false} connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 5: Criar frontend/components/ContainerRow.tsx**

```tsx
'use client';
import ProgressBar from './ProgressBar';
import { ContainerMetric } from '../lib/ws';

interface Props {
  container: ContainerMetric;
  onViewLogs?: (id: string, name: string) => void;
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

export default function ContainerRow({ container, onViewLogs }: Props) {
  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
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
        <button
          onClick={() => onViewLogs?.(container.id, container.name)}
          style={{
            padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
            background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 12,
          }}
        >
          Ver Logs
        </button>
      </td>
    </tr>
  );
}
```

- [ ] **Step 6: Verificar tipos**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 7: Commit**

```bash
git add frontend/components/
git commit -m "feat: componentes compartilhados (MetricCard, ProgressBar, LineChart, ContainerRow, Toast)"
```

---

## Task 14: Frontend — Layout Global + Página de Login

**Files:**
- Modify: `frontend/app/layout.tsx` (substituir stub por layout completo)
- Create: `frontend/app/login/page.tsx`

**Interfaces:**
- Produces: sidebar de navegação, proteção de rotas via localStorage, página de login

- [ ] **Step 1: Substituir frontend/app/layout.tsx**

```tsx
'use client';
import './globals.css';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';

const NAV = [
  { href: '/', label: 'Dashboard', icon: '📊' },
  { href: '/containers', label: 'Containers', icon: '🐳' },
  { href: '/historico', label: 'Histórico', icon: '📈' },
  { href: '/alertas', label: 'Alertas', icon: '🔔', disabled: true },
  { href: '/configuracoes', label: 'Configurações', icon: '⚙️', disabled: true },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (pathname === '/login') { setReady(true); return; }
    if (!localStorage.getItem('vps_token')) { router.replace('/login'); return; }
    setReady(true);
  }, [pathname, router]);

  if (!ready) return (
    <html lang="pt-BR"><body style={{ background: 'var(--bg)' }} /></html>
  );

  if (pathname === '/login') return (
    <html lang="pt-BR"><body>{children}</body></html>
  );

  return (
    <html lang="pt-BR">
      <body>
        <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
          <aside style={{
            width: 220, flexShrink: 0,
            background: 'var(--surface)', borderRight: '1px solid var(--border)',
            display: 'flex', flexDirection: 'column',
          }}>
            <div style={{ padding: '20px 20px 16px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--accent)' }}>VPS Monitor</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>monitor.dlsistemas.com.br</div>
            </div>

            <nav style={{ flex: 1, padding: '12px 8px', overflowY: 'auto' }}>
              {NAV.map((item) =>
                item.disabled ? (
                  <div key={item.href} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '9px 12px', borderRadius: 8, marginBottom: 2,
                    color: 'var(--muted)', opacity: 0.4, cursor: 'not-allowed', fontSize: 13,
                  }}>
                    <span>{item.icon}</span>{item.label}
                  </div>
                ) : (
                  <Link key={item.href} href={item.href} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '9px 12px', borderRadius: 8, marginBottom: 2,
                    background: pathname === item.href ? 'var(--card)' : 'transparent',
                    color: pathname === item.href ? 'var(--text)' : 'var(--muted)',
                    fontSize: 13, transition: 'background 0.15s',
                  }}>
                    <span>{item.icon}</span>{item.label}
                  </Link>
                )
              )}
            </nav>

            <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
              <button
                onClick={() => { localStorage.removeItem('vps_token'); router.replace('/login'); }}
                style={{
                  width: '100%', padding: '7px', borderRadius: 6,
                  border: '1px solid var(--border)', background: 'transparent',
                  color: 'var(--muted)', cursor: 'pointer', fontSize: 12,
                }}
              >
                Sair
              </button>
            </div>
          </aside>

          <main style={{ flex: 1, overflow: 'auto', padding: 24 }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
```

- [ ] **Step 2: Criar frontend/app/login/page.tsx**

```tsx
'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import axios from 'axios';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const { data } = await axios.post('/api/auth/login', { username, password });
      localStorage.setItem('vps_token', data.token);
      router.replace('/');
    } catch {
      setError('Usuário ou senha incorretos.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh', background: 'var(--bg)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: 'var(--card)', border: '1px solid var(--border)',
        borderRadius: 16, padding: 40, width: 360,
      }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{ fontSize: 28 }}>📊</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--accent)', marginTop: 8 }}>VPS Monitor</div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>Acesse o painel de monitoramento</div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>Usuário</label>
            <input
              type="text" value={username} onChange={(e) => setUsername(e.target.value)}
              required autoFocus
              style={{
                width: '100%', padding: '10px 14px', background: 'var(--surface)',
                border: '1px solid var(--border)', borderRadius: 8,
                color: 'var(--text)', fontSize: 14, outline: 'none',
              }}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>Senha</label>
            <input
              type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              required
              style={{
                width: '100%', padding: '10px 14px', background: 'var(--surface)',
                border: '1px solid var(--border)', borderRadius: 8,
                color: 'var(--text)', fontSize: 14, outline: 'none',
              }}
            />
          </div>

          {error && (
            <div style={{ color: 'var(--danger)', fontSize: 13, textAlign: 'center' }}>{error}</div>
          )}

          <button
            type="submit" disabled={loading}
            style={{
              padding: '11px', borderRadius: 8, border: 'none',
              background: loading ? 'var(--border)' : 'var(--accent)',
              color: loading ? 'var(--muted)' : '#000',
              fontWeight: 700, fontSize: 14, cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background 0.2s',
            }}
          >
            {loading ? 'Entrando...' : 'Entrar'}
          </button>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verificar build**

```bash
cd frontend && npm run build
```
Esperado: sem erros TypeScript nem de build

- [ ] **Step 4: Commit**

```bash
git add frontend/app/layout.tsx frontend/app/login/
git commit -m "feat: layout global com sidebar e página de login"
```

---

## Task 15: Dashboard Page

**Files:**
- Modify: `frontend/app/page.tsx` (substituir stub por dashboard completo)

**Interfaces:**
- Consumes: `useWebSocket()`, `api.get('/metrics/history')`, `api.get('/containers/{id}/logs')`
- Produces: dashboard com 5 cards, 3 gráficos, tabela de containers, timeline de alertas

- [ ] **Step 1: Criar frontend/app/page.tsx**

```tsx
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
```

- [ ] **Step 2: Verificar build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat: dashboard em tempo real com gráficos, containers e alertas"
```

---

## Task 16: Página de Containers

**Files:**
- Create: `frontend/app/containers/page.tsx`

**Interfaces:**
- Consumes: `useWebSocket()`, `api.get('/metrics/history')` por container, `api.get('/containers/{id}/logs')`
- Produces: tabela expandível com mini-gráficos e modal de logs

- [ ] **Step 1: Criar frontend/app/containers/page.tsx**

```tsx
'use client';
import { useState, useEffect } from 'react';
import { useWebSocket, ContainerMetric } from '../../lib/ws';
import ContainerRow from '../../components/ContainerRow';
import LineChart from '../../components/LineChart';
import api from '../../lib/api';

function wsUrl() {
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/metrics`;
}

interface Point { ts: string; value: number | null; }

export default function ContainersPage() {
  const { data } = useWebSocket(wsUrl());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [cpuHistory, setCpuHistory] = useState<Record<string, Point[]>>({});
  const [logModal, setLogModal] = useState<{ id: string; name: string } | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [logSearch, setLogSearch] = useState('');

  const containers = data?.containers ?? [];

  // Acumular histórico por container
  useEffect(() => {
    if (!data) return;
    setCpuHistory((prev) => {
      const next = { ...prev };
      for (const c of data.containers) {
        const arr = prev[c.id] ?? [];
        next[c.id] = [...arr, { ts: data.ts, value: c.cpu_percent }].slice(-240); // 2h
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
      setLogs(r.data.logs);
    } catch { setLogs(['Erro ao carregar logs.']); }
  };

  const filteredLogs = logSearch
    ? logs.filter((l) => l.toLowerCase().includes(logSearch.toLowerCase()))
    : logs;

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Containers</h1>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              <th style={{ padding: '10px 16px', width: 32 }} />
              {['Nome', 'Imagem', 'Status', 'CPU', 'RAM', 'Restarts', 'Ações'].map((h) => (
                <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: 11, color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {containers.length === 0 ? (
              <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>Aguardando dados...</td></tr>
            ) : containers.map((c) => (
              <>
                <tr key={c.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 16px' }}>
                    <button
                      onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                      style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 16 }}
                    >
                      {expanded === c.id ? '▼' : '▶'}
                    </button>
                  </td>
                  <ContainerRow container={c} onViewLogs={openLogs} />
                </tr>
                {expanded === c.id && (
                  <tr key={`${c.id}-detail`}>
                    <td colSpan={8} style={{ background: 'var(--surface)', padding: 20, borderBottom: '1px solid var(--border)' }}>
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
                            {[
                              ['Imagem', c.image],
                              ['Status', c.status_text],
                              ['RAM usada', `${c.mem_used_mb.toFixed(1)} MB / ${c.mem_limit_mb.toFixed(1)} MB`],
                              ['RX / TX', `${(c.net_rx_bytes / 1024).toFixed(1)} KB / ${(c.net_tx_bytes / 1024).toFixed(1)} KB`],
                              ['Restarts', String(c.restart_count)],
                            ].map(([k, v]) => (
                              <tr key={k} style={{ borderBottom: '1px solid var(--border)' }}>
                                <td style={{ padding: '6px 0', color: 'var(--muted)', width: 120 }}>{k}</td>
                                <td style={{ padding: '6px 0' }}>{v}</td>
                              </tr>
                            ))}
                          </table>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {/* Modal de logs */}
      {logModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setLogModal(null)}>
          <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '85%', maxWidth: 1000, maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}
            onClick={(e) => e.stopPropagation()}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center' }}>
              <span style={{ fontWeight: 600 }}>Logs — <span style={{ color: 'var(--accent)', fontFamily: 'monospace' }}>{logModal.name}</span></span>
              <input
                placeholder="Buscar nos logs..."
                value={logSearch}
                onChange={(e) => setLogSearch(e.target.value)}
                style={{ flex: 1, padding: '5px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12 }}
              />
              <button onClick={() => setLogModal(null)} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}>×</button>
            </div>
            <div style={{ padding: 16, overflow: 'auto', flex: 1, fontFamily: 'monospace', fontSize: 12, lineHeight: 1.7 }}>
              {filteredLogs.length === 0
                ? <span style={{ color: 'var(--muted)' }}>{logs.length === 0 ? 'Carregando...' : 'Nenhum resultado.'}</span>
                : filteredLogs.map((l, i) => (
                    <div key={i} style={{ paddingBottom: 1, color: l.includes('ERROR') || l.includes('error') ? 'var(--danger)' : 'var(--text)' }}>{l}</div>
                  ))
              }
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verificar build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/containers/
git commit -m "feat: página de containers com expansão e logs com busca"
```

---

## Task 17: Página de Histórico + README

**Files:**
- Create: `frontend/app/historico/page.tsx`
- Create: `README.md`

**Interfaces:**
- Consumes: `api.get('/metrics/history')`
- Produces: página de histórico com seletor de métrica e range; README com instruções de deploy

- [ ] **Step 1: Criar frontend/app/historico/page.tsx**

```tsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import LineChart from '../../components/LineChart';
import api from '../../lib/api';

type Metric = 'cpu' | 'ram' | 'disk' | 'load' | 'net_rx' | 'net_tx' | 'temperature';
type Range = '1h' | '6h' | '24h' | '7d';

interface Point { ts: string; value: number | null; }

const METRICS: { value: Metric; label: string; unit: string; color: string }[] = [
  { value: 'cpu', label: 'CPU', unit: '%', color: 'var(--accent)' },
  { value: 'ram', label: 'Memória RAM', unit: '%', color: 'var(--info)' },
  { value: 'disk', label: 'Disco', unit: '%', color: 'var(--warning)' },
  { value: 'load', label: 'Load Average (1m)', unit: '', color: 'var(--success)' },
  { value: 'net_rx', label: 'Rede — Recebido', unit: ' B/s', color: 'var(--success)' },
  { value: 'net_tx', label: 'Rede — Enviado', unit: ' B/s', color: 'var(--warning)' },
  { value: 'temperature', label: 'Temperatura', unit: '°C', color: 'var(--danger)' },
];

const RANGES: { value: Range; label: string }[] = [
  { value: '1h', label: '1 hora' },
  { value: '6h', label: '6 horas' },
  { value: '24h', label: '24 horas' },
  { value: '7d', label: '7 dias' },
];

export default function HistoricoPage() {
  const [metric, setMetric] = useState<Metric>('cpu');
  const [range, setRange] = useState<Range>('1h');
  const [data, setData] = useState<Point[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get(`/metrics/history?metric=${metric}&range=${range}`);
      setData(r.data.data);
    } catch { setData([]); }
    finally { setLoading(false); }
  }, [metric, range]);

  useEffect(() => { load(); }, [load]);

  const selected = METRICS.find((m) => m.value === metric)!;
  const values = data.map((d) => d.value).filter((v): v is number => v !== null);
  const max = values.length ? Math.max(...values) : null;
  const min = values.length ? Math.min(...values) : null;
  const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  const fmt = (v: number | null) => v != null ? `${v.toFixed(1)}${selected.unit}` : '—';

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Histórico</h1>

      {/* Seletores */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Métrica</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {METRICS.map((m) => (
              <button key={m.value} onClick={() => setMetric(m.value)} style={{
                padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
                background: metric === m.value ? 'var(--border)' : 'transparent',
                color: metric === m.value ? 'var(--text)' : 'var(--muted)',
                cursor: 'pointer', fontSize: 12,
              }}>
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {RANGES.map((r) => (
              <button key={r.value} onClick={() => setRange(r.value)} style={{
                padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
                background: range === r.value ? 'var(--accent)' : 'transparent',
                color: range === r.value ? '#000' : 'var(--muted)',
                fontWeight: range === r.value ? 700 : 400,
                cursor: 'pointer', fontSize: 12,
              }}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Gráfico */}
      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>
          {selected.label}
          {loading && <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 10 }}>Carregando...</span>}
        </div>
        {data.length > 0 ? (
          <LineChart data={data} color={selected.color} unit={selected.unit} height={300} />
        ) : (
          <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
            {loading ? 'Carregando dados...' : 'Sem dados para o período selecionado'}
          </div>
        )}
      </div>

      {/* Estatísticas */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        {[
          { label: 'Máximo', value: fmt(max) },
          { label: 'Mínimo', value: fmt(min) },
          { label: 'Média', value: fmt(avg) },
          { label: 'Amostras', value: String(values.length) },
        ].map((stat) => (
          <div key={stat.label} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>{stat.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{stat.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Criar README.md**

```markdown
# VPS Monitor

Painel web de monitoramento para servidor Linux com Docker.
Acesse: https://monitor.dlsistemas.com.br

## Pré-requisitos

- Docker 24+ e Docker Compose v2+
- Traefik rodando na rede Docker `proxy` com certresolver `letsencrypt`
- (Opcional) Evolution API auto-hospedada para notificações WhatsApp

## Instalação

```bash
cd /opt
git clone <repo> vps-monitor
cd vps-monitor
cp .env.example .env
nano .env   # defina JWT_SECRET, MONITOR_USER, MONITOR_PASSWORD, PUBLIC_URL
bash deploy.sh
```

## Configuração do Domínio

O Traefik detecta automaticamente o container `monitor-nginx` na rede `proxy`.
Certifique-se de que o DNS de `monitor.dlsistemas.com.br` aponta para o IP da VPS.

## SMTP (E-mail)

Configure em Configurações > SMTP no painel. Teste com "Enviar e-mail de teste".

## WhatsApp (Evolution API)

1. Configure URL da API, API Key e nome da instância em Configurações > WhatsApp
2. Clique "Criar Instância" → depois "Conectar (QR)"
3. Escaneie o QR code com o WhatsApp do celular

## Regras de Alerta

Em Alertas > Regras, 9 regras padrão já estão configuradas.
Edite thresholds ou adicione novas regras conforme necessário.

## Troubleshooting

**Container não inicia:** `docker compose logs monitor-backend`
**Métricas zeradas:** verifique se `/proc` e `/sys` estão montados (`docker compose exec monitor-backend ls /host/proc`)
**WebSocket não conecta:** verifique o nginx.conf e os headers de Upgrade
**WhatsApp QR expira rápido:** normal — o sistema solicita novo QR automaticamente
```

- [ ] **Step 3: Build final + todos os testes**

```bash
# Backend
cd backend && python -m pytest tests/ -v
# Esperado: todos os testes passando

# Frontend
cd ../frontend && npm run build
# Esperado: build sem erros
```

- [ ] **Step 4: Teste de integração com docker compose**

```bash
cd ..
cp .env.example .env
# Editar .env com valores de teste
docker compose build
docker compose up -d
sleep 10
curl http://localhost/api/health
# Esperado: {"status":"ok"}
docker compose down
```

- [ ] **Step 5: Commit final**

```bash
git add frontend/app/historico/ README.md
git commit -m "feat: página de histórico e README — Fase 1 completa"
```

---

## Self-Review

**Cobertura do spec:**
- ✅ Coleta CPU/RAM/Disco/Rede/Uptime/Temperatura via /proc e /sys
- ✅ Docker socket com stats paralelos e logs
- ✅ SQLite WAL mode com todas as tabelas
- ✅ WebSocket broadcast a cada 30s
- ✅ Auth JWT com middleware
- ✅ Rate limiting 60 req/min
- ✅ Dashboard com 5 cards + 3 gráficos + tabela containers + alertas
- ✅ Página de containers com expansão e logs com busca
- ✅ Página de histórico com seletor de métrica e range
- ✅ Docker Compose com Traefik labels para monitor.dlsistemas.com.br
- ✅ Layout com sidebar de navegação e logout
- ✅ Página de login com JWT no localStorage
- ✅ deploy.sh e README

**Tipos consistentes:** `ContainerMetric` definido em `ws.ts` e reutilizado em `ContainerRow.tsx`, `containers/page.tsx`, `page.tsx`. Nenhuma divergência de nomes.

**Placeholders:** Nenhum TBD. Todos os endpoints, schemas e componentes têm implementação completa.
