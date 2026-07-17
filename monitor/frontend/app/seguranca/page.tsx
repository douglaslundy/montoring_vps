'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Jail {
  nome: string;
  managed: boolean;
  currently_banned: number;
  total_banned: number;
  currently_failed: number;
  banned_ips: string[];
}

interface JailForm {
  nome_exibicao: string;
  log_path: string;
  sample_log_line: string;
  regex: string;
  maxretry: number;
  findtime: number;
  bantime: number;
  port: string;
}

const emptyForm = (): JailForm => ({
  nome_exibicao: '', log_path: '', sample_log_line: '', regex: '',
  maxretry: 5, findtime: 600, bantime: 3600, port: 'http,https',
});

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

export default function SegurancaPage() {
  const [jails, setJails] = useState<Jail[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [form, setForm] = useState<JailForm>(emptyForm());
  const [editSlug, setEditSlug] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<string | null>(null);

  const loadJails = useCallback(async () => {
    setLoading(true);
    try { setJails((await api.get('/fail2ban/jails')).data); } catch { setJails([]); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadJails(); }, [loadJails]);

  function startCreate() {
    setEditSlug(null);
    setForm(emptyForm());
    setFormError('');
    setShowForm(true);
  }

  function startEdit(jail: Jail) {
    setEditSlug(jail.nome);
    setForm({ ...emptyForm(), nome_exibicao: jail.nome });
    setFormError('');
    setShowForm(true);
  }

  async function saveJail() {
    setFormError('');
    try {
      if (editSlug) {
        await api.put(`/fail2ban/jails/${editSlug}`, form);
        setToast({ msg: 'Jail atualizado', type: 'success' });
      } else {
        await api.post('/fail2ban/jails', form);
        setToast({ msg: 'Jail criado', type: 'success' });
      }
      setShowForm(false);
      loadJails();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail || 'Erro ao salvar jail');
    }
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.delete(`/fail2ban/jails/${deleteAlvo}`);
      setToast({ msg: 'Jail excluído', type: 'success' });
      loadJails();
    } catch { setToast({ msg: 'Erro ao excluir jail', type: 'error' }); }
    setDeleteAlvo(null);
  }

  async function doUnban(nome: string, ip: string) {
    try {
      await api.post(`/fail2ban/jails/${nome}/unban`, { ip });
      setToast({ msg: `IP ${ip} desbanido`, type: 'success' });
      loadJails();
    } catch { setToast({ msg: 'Erro ao desbanir IP', type: 'error' }); }
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Segurança</h1>

      <div style={{ marginBottom: 16 }}>
        <button
          onClick={startCreate}
          style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
        >
          + Novo Jail
        </button>
      </div>

      {loading && jails.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {jails.map((jail) => (
        <div key={jail.nome} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600, fontFamily: 'monospace' }}>{jail.nome}</span>
            <span style={{
              padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: jail.managed ? 'var(--accent)' : 'var(--surface)',
              color: jail.managed ? '#000' : 'var(--muted)',
              border: '1px solid var(--border)',
            }}>
              {jail.managed ? 'Gerenciado' : 'Manual'}
            </span>
            <span style={{ color: 'var(--muted)', fontSize: 12 }}>
              {jail.currently_banned} banido(s) agora · {jail.total_banned} no total · {jail.currently_failed} tentativa(s) recente(s)
            </span>
            {jail.managed && (
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                <button
                  onClick={() => startEdit(jail)}
                  style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                >
                  Editar
                </button>
                <button
                  onClick={() => setDeleteAlvo(jail.nome)}
                  style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
                >
                  Excluir
                </button>
              </div>
            )}
          </div>

          {jail.banned_ips.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {jail.banned_ips.map((ip) => (
                <div key={ip} style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px',
                  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
                }}>
                  <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{ip}</span>
                  <button
                    onClick={() => doUnban(jail.nome, ip)}
                    style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 11 }}
                  >
                    Desbanir
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {/* Modal de criar/editar */}
      {showForm && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowForm(false)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '85%', maxWidth: 520, padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 16, color: 'var(--text)' }}>{editSlug ? 'Editar Jail' : 'Novo Jail'}</h3>

            <div style={{ display: 'grid', gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome de exibição</label>
                <input style={input} value={form.nome_exibicao} onChange={(e) => setForm({ ...form, nome_exibicao: e.target.value })} />
              </div>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Caminho do log</label>
                <input style={input} value={form.log_path} onChange={(e) => setForm({ ...form, log_path: e.target.value })} placeholder="/var/log/exemplo.log" />
              </div>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Linha de exemplo (usada no teste do regex)</label>
                <input style={input} value={form.sample_log_line} onChange={(e) => setForm({ ...form, sample_log_line: e.target.value })} />
              </div>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Regex (use &lt;HOST&gt; para o IP)</label>
                <input style={{ ...input, fontFamily: 'monospace' }} value={form.regex} onChange={(e) => setForm({ ...form, regex: e.target.value })} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Máx. tentativas</label>
                  <input type="number" style={input} value={form.maxretry} onChange={(e) => setForm({ ...form, maxretry: Number(e.target.value) })} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Janela (segundos)</label>
                  <input type="number" style={input} value={form.findtime} onChange={(e) => setForm({ ...form, findtime: Number(e.target.value) })} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Duração do ban (segundos)</label>
                  <input type="number" style={input} value={form.bantime} onChange={(e) => setForm({ ...form, bantime: Number(e.target.value) })} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Porta(s)</label>
                  <input style={input} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} />
                </div>
              </div>
            </div>

            {formError && (
              <p style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 12, whiteSpace: 'pre-wrap' }}>{formError}</p>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={saveJail}
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
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir jail</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir o jail &quot;{deleteAlvo}&quot;? Essa ação não pode ser desfeita.
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
