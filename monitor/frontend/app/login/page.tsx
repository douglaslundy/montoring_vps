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
      const { data } = await axios.post('/api/auth/login', new URLSearchParams({ username, password }), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
      });
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
