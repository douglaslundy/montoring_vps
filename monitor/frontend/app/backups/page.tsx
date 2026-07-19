'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Snapshot {
  arquivo: string;
  tamanho_mb: number;
}

interface JobAtivo {
  id: number;
  tipo: string;
  status: string;
}

interface BackupProject {
  nome: string;
  frequencia: 'off' | 'daily' | 'weekly';
  hora: number;
  snapshots: Snapshot[];
  job_ativo: JobAtivo | null;
}

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const selectStyle: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13,
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

const JOB_LABEL: Record<string, string> = {
  snapshot: 'Criando snapshot', restore: 'Restaurando', delete: 'Excluindo',
};

export default function BackupsPage() {
  const [projects, setProjects] = useState<BackupProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [restoreAlvo, setRestoreAlvo] = useState<{ projeto: string; arquivo: string } | null>(null);
  const [restoreConfirmText, setRestoreConfirmText] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<{ projeto: string; arquivo: string } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProjects = useCallback(async () => {
    try { setProjects((await api.get('/backups/projects')).data.projects); } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadProjects(); }, [loadProjects]);

  useEffect(() => {
    const temJobAtivo = projects.some((p) => p.job_ativo !== null);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(loadProjects, temJobAtivo ? 5000 : 30000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [projects, loadProjects]);

  async function handleScheduleChange(nome: string, frequencia: string, hora: number) {
    try {
      await api.put(`/backups/projects/${nome}/schedule`, { frequencia, hora });
      loadProjects();
    } catch {
      setToast({ msg: 'Erro ao salvar agendamento', type: 'error' });
    }
  }

  async function handleSnapshot(nome: string) {
    try {
      await api.post(`/backups/projects/${nome}/snapshot`);
      setToast({ msg: `Snapshot de '${nome}' enfileirado`, type: 'success' });
      loadProjects();
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao criar snapshot', type: 'error' });
    }
  }

  async function handleDownload(projeto: string, arquivo: string) {
    try {
      const resp = await api.get(`/backups/projects/${projeto}/snapshots/${arquivo}/download`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([resp.data]));
      const link = document.createElement('a');
      link.href = url;
      link.download = arquivo;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setToast({ msg: 'Erro ao baixar snapshot', type: 'error' });
    }
  }

  async function confirmRestore() {
    if (!restoreAlvo || restoreConfirmText !== restoreAlvo.projeto) return;
    try {
      await api.post(`/backups/projects/${restoreAlvo.projeto}/snapshots/${restoreAlvo.arquivo}/restore`);
      setToast({ msg: `Restore de '${restoreAlvo.projeto}' enfileirado`, type: 'success' });
      loadProjects();
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao iniciar restore', type: 'error' });
    }
    setRestoreAlvo(null);
    setRestoreConfirmText('');
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.delete(`/backups/projects/${deleteAlvo.projeto}/snapshots/${deleteAlvo.arquivo}`);
      setToast({ msg: 'Snapshot excluído', type: 'success' });
      loadProjects();
    } catch {
      setToast({ msg: 'Erro ao excluir snapshot', type: 'error' });
    }
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Backups</h1>

      {loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {!loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhum projeto encontrado.</p>
      )}

      {projects.map((p) => (
        <div key={p.nome} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600 }}>{p.nome}</span>

            <select
              style={selectStyle}
              value={p.frequencia}
              onChange={(e) => handleScheduleChange(p.nome, e.target.value, p.hora)}
            >
              <option value="off">Desligado</option>
              <option value="daily">Diário</option>
              <option value="weekly">Semanal</option>
            </select>

            {p.frequencia !== 'off' && (
              <select
                style={selectStyle}
                value={p.hora}
                onChange={(e) => handleScheduleChange(p.nome, p.frequencia, Number(e.target.value))}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
                ))}
              </select>
            )}

            <div style={{ marginLeft: 'auto' }}>
              {p.job_ativo ? (
                <span style={{ color: 'var(--accent)', fontSize: 13 }}>
                  {JOB_LABEL[p.job_ativo.tipo] ?? p.job_ativo.tipo} ({p.job_ativo.status})...
                </span>
              ) : (
                <button
                  onClick={() => handleSnapshot(p.nome)}
                  style={{ padding: '6px 14px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700, fontSize: 13 }}
                >
                  Criar snapshot agora
                </button>
              )}
            </div>
          </div>

          {p.snapshots.length === 0 ? (
            <p style={{ color: 'var(--muted)', fontSize: 13 }}>Nenhum snapshot ainda.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {p.snapshots.map((s) => (
                <div key={s.arquivo} style={{
                  display: 'flex', alignItems: 'center', gap: 12, padding: '6px 10px',
                  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
                }}>
                  <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{s.arquivo}</span>
                  <span style={{ color: 'var(--muted)', fontSize: 12 }}>{s.tamanho_mb.toFixed(1)} MB</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    <button
                      onClick={() => handleDownload(p.nome, s.arquivo)}
                      style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                    >
                      Baixar
                    </button>
                    <button
                      onClick={() => { setRestoreAlvo({ projeto: p.nome, arquivo: s.arquivo }); setRestoreConfirmText(''); }}
                      style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                    >
                      Restaurar
                    </button>
                    <button
                      onClick={() => setDeleteAlvo({ projeto: p.nome, arquivo: s.arquivo })}
                      style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
                    >
                      Excluir
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {restoreAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setRestoreAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 460 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Restaurar snapshot</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 12, fontSize: 14 }}>
              Isso vai parar os containers de &quot;{restoreAlvo.projeto}&quot;, substituir todos os dados atuais pelos do snapshot &quot;{restoreAlvo.arquivo}&quot;, e subir de novo. <strong>Essa ação não pode ser desfeita.</strong>
            </p>
            <p style={{ color: 'var(--muted)', marginBottom: 8, fontSize: 13 }}>
              Digite <strong>{restoreAlvo.projeto}</strong> pra confirmar:
            </p>
            <input
              style={{ ...input, marginBottom: 16 }}
              value={restoreConfirmText}
              onChange={(e) => setRestoreConfirmText(e.target.value)}
              autoFocus
            />
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setRestoreAlvo(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={confirmRestore}
                disabled={restoreConfirmText !== restoreAlvo.projeto}
                style={{
                  padding: '8px 20px', border: 'none', borderRadius: 6, fontWeight: 700,
                  background: restoreConfirmText === restoreAlvo.projeto ? 'var(--danger)' : 'var(--surface)',
                  color: restoreConfirmText === restoreAlvo.projeto ? '#fff' : 'var(--muted)',
                  cursor: restoreConfirmText === restoreAlvo.projeto ? 'pointer' : 'not-allowed',
                }}
              >
                Confirmar restore
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setDeleteAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir snapshot</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir o snapshot &quot;{deleteAlvo.arquivo}&quot; de &quot;{deleteAlvo.projeto}&quot;? Essa ação não pode ser desfeita.
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
