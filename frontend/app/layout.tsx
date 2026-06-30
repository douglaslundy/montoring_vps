'use client';
import './globals.css';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';

const NAV = [
  { href: '/', label: 'Dashboard', icon: '📊' },
  { href: '/containers', label: 'Containers', icon: '🐳' },
  { href: '/historico', label: 'Histórico', icon: '📈' },
  { href: '/alertas', label: 'Alertas', icon: '🔔' },
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
