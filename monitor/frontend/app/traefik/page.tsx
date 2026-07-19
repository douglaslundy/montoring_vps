'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Route {
  filename: string;
  managed: boolean;
  content: string;
}

const TEMPLATE = `# Nome do router e do service podem ser iguais ao nome do projeto.
http:
  routers:
    meu-projeto:
      rule: "Host(\`meuprojeto.dlsistemas.com.br\`)"
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt
      service: meu-projeto
  services:
    meu-projeto:
      loadBalancer:
        servers:
          - url: "http://172.17.0.1:8080"
`;

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const textarea: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '8px 10px', color: 'var(--text)',
  fontSize: 13, width: '100%', boxSizing: 'border-box',
  fontFamily: 'monospace', minHeight: 220, resize: 'vertical',
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

export default function TraefikPage() {
  const [routes, setRoutes] = useState<Route[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [nomeExibicao, setNomeExibicao] = useState('');
  const [yamlContent, setYamlContent] = useState(TEMPLATE);
  const [editFilename, setEditFilename] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<string | null>(null);

  const loadRoutes = useCallback(async () => {
    setLoading(true);
    try { setRoutes((await api.get('/traefik/routes')).data); } catch { setRoutes([]); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadRoutes(); }, [loadRoutes]);

  function startCreate() {
    setEditFilename(null);
    setNomeExibicao('');
    setYamlContent(TEMPLATE);
    setFormError('');
    setShowForm(true);
  }

  function startEdit(route: Route) {
    setEditFilename(route.filename);
    setYamlContent(route.content);
    setFormError('');
    setShowForm(true);
  }

  async function saveRoute() {
    setFormError('');
    try {
      if (editFilename) {
        await api.put(`/traefik/routes/${editFilename}`, { yaml_content: yamlContent });
        setToast({ msg: 'Rota atualizada — Traefik recarrega em segundos', type: 'success' });
      } else {
        await api.post('/traefik/routes', { nome_exibicao: nomeExibicao, yaml_content: yamlContent });
        setToast({ msg: 'Rota criada — Traefik recarrega em segundos', type: 'success' });
      }
      setShowForm(false);
      loadRoutes();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail || 'Erro ao salvar rota');
    }
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.delete(`/traefik/routes/${deleteAlvo}`);
      setToast({ msg: 'Rota excluída', type: 'success' });
      loadRoutes();
    } catch { setToast({ msg: 'Erro ao excluir rota', type: 'error' }); }
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Traefik</h1>

      <div style={{ marginBottom: 16 }}>
        <button
          onClick={startCreate}
          style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
        >
          + Nova Rota
        </button>
      </div>

      {loading && routes.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {routes.map((route) => (
        <div key={route.filename} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600, fontFamily: 'monospace' }}>{route.filename}</span>
            <span style={{
              padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: route.managed ? 'var(--accent)' : 'var(--surface)',
              color: route.managed ? '#000' : 'var(--muted)',
              border: '1px solid var(--border)',
            }}>
              {route.managed ? 'Gerenciado' : 'Manual'}
            </span>
            {route.managed && (
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                <button
                  onClick={() => startEdit(route)}
                  style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                >
                  Editar
                </button>
                <button
                  onClick={() => setDeleteAlvo(route.filename)}
                  style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
                >
                  Excluir
                </button>
              </div>
            )}
          </div>
          <pre style={{
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
            padding: 10, fontSize: 12, color: 'var(--muted)', overflowX: 'auto', margin: 0,
          }}>
            {route.content}
          </pre>
        </div>
      ))}

      {/* Modal de criar/editar */}
      {showForm && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowForm(false)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '90%', maxWidth: 640, padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 16, color: 'var(--text)' }}>{editFilename ? 'Editar Rota' : 'Nova Rota'}</h3>

            <div style={{ display: 'grid', gap: 12, marginBottom: 16 }}>
              {!editFilename && (
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome de exibição</label>
                  <input style={input} value={nomeExibicao} onChange={(e) => setNomeExibicao(e.target.value)} />
                </div>
              )}
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>YAML (config do Traefik file provider)</label>
                <textarea style={textarea} value={yamlContent} onChange={(e) => setYamlContent(e.target.value)} />
              </div>
            </div>

            {formError && (
              <p style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 12, whiteSpace: 'pre-wrap' }}>{formError}</p>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={saveRoute}
                style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Salvar
              </button>
              <button
                onClick={() => setShowForm(false)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de confirmação de exclusão */}
      {deleteAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setDeleteAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir rota</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir a rota &quot;{deleteAlvo}&quot;? Essa ação não pode ser desfeita.
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setDeleteAlvo(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={confirmDelete}
                style={{ padding: '8px 20px', background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Confirmar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
