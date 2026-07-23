'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface ProjectContainer {
  name: string;
  status: string;
}

interface Project {
  nome: string;
  dominio: string | null;
  container_count: number;
  cpu_percent: number;
  mem_usage_mb: number;
  mem_percent_do_host: number;
  containers: ProjectContainer[];
}

interface RegraCandidata {
  porta: number;
  protocolo: string;
  permitir: boolean;
  origem_ip: string | null;
}

interface DeletePreview {
  containers: ProjectContainer[];
  volumes: string[];
  rotas_candidatas: string[];
  regras_firewall_candidatas: RegraCandidata[];
}

type DeleteEtapa = 'preview' | 'criando-snapshot' | 'confirmacao';

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8, cursor: 'pointer',
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

const modalOverlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const modalBox: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12,
  width: '90%', maxWidth: 520, padding: 24, maxHeight: '85vh', overflowY: 'auto',
};

export default function ProjetosPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  const [deleteAlvo, setDeleteAlvo] = useState<string | null>(null);
  const [deleteEtapa, setDeleteEtapa] = useState<DeleteEtapa>('preview');
  const [preview, setPreview] = useState<DeletePreview | null>(null);
  const [rotasMarcadas, setRotasMarcadas] = useState<Set<string>>(new Set());
  const [regrasMarcadas, setRegrasMarcadas] = useState<Set<number>>(new Set());
  const [snapshotArquivo, setSnapshotArquivo] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState('');
  const snapshotPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProjects = useCallback(async () => {
    try { setProjects((await api.get('/projects')).data.projects); } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadProjects(); }, [loadProjects]);

  useEffect(() => {
    const id = setInterval(loadProjects, 30000);
    return () => clearInterval(id);
  }, [loadProjects]);

  useEffect(() => {
    return () => { if (snapshotPollRef.current) clearInterval(snapshotPollRef.current); };
  }, []);

  async function abrirExclusao(nome: string, e: React.MouseEvent) {
    e.stopPropagation();
    setDeleteAlvo(nome);
    setDeleteEtapa('preview');
    setPreview(null);
    setSnapshotArquivo(null);
    setConfirmText('');
    try {
      const r = await api.get(`/projects/${nome}/delete-preview`);
      setPreview(r.data);
      setRotasMarcadas(new Set<string>(r.data.rotas_candidatas));
      setRegrasMarcadas(new Set());
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao carregar preview', type: 'error' });
      setDeleteAlvo(null);
    }
  }

  function toggleRota(filename: string) {
    setRotasMarcadas((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename); else next.add(filename);
      return next;
    });
  }

  function toggleRegra(index: number) {
    setRegrasMarcadas((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  }

  async function criarSnapshotEContinuar() {
    if (!deleteAlvo) return;
    setDeleteEtapa('criando-snapshot');
    let contagemAntes = 0;
    try {
      const antes = await api.get('/backups/projects');
      const projetoAntes = antes.data.projects.find((p: any) => p.nome === deleteAlvo);
      contagemAntes = projetoAntes ? projetoAntes.snapshots.length : 0;
      await api.post(`/backups/projects/${deleteAlvo}/snapshot`);
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao criar snapshot', type: 'error' });
      setDeleteEtapa('preview');
      return;
    }

    let tentativas = 0;
    snapshotPollRef.current = setInterval(async () => {
      tentativas += 1;
      try {
        const r = await api.get('/backups/projects');
        const p = r.data.projects.find((pr: any) => pr.nome === deleteAlvo);
        if (p && p.snapshots.length > contagemAntes) {
          if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
          setSnapshotArquivo(p.snapshots[0].arquivo);
          setDeleteEtapa('confirmacao');
          return;
        }
        if (p && !p.job_ativo && p.snapshots.length <= contagemAntes) {
          if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
          setToast({ msg: 'Falha ao criar snapshot, tente novamente', type: 'error' });
          setDeleteEtapa('preview');
        }
      } catch { /* ignore, tenta de novo no proximo ciclo */ }
      if (tentativas > 40) {
        if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
        setToast({ msg: 'Snapshot demorou demais, tente novamente', type: 'error' });
        setDeleteEtapa('preview');
      }
    }, 3000);
  }

  async function confirmarExclusao() {
    if (!deleteAlvo || !snapshotArquivo || confirmText !== deleteAlvo) return;
    try {
      await api.post(`/projects/${deleteAlvo}/delete`, {
        snapshot_arquivo: snapshotArquivo,
        rotas_selecionadas: Array.from(rotasMarcadas),
        regras_selecionadas: (preview?.regras_firewall_candidatas || []).filter((_, i) => regrasMarcadas.has(i)),
      });
      setToast({ msg: `Exclusão de '${deleteAlvo}' enfileirada`, type: 'success' });
      loadProjects();
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao excluir projeto', type: 'error' });
    }
    setDeleteAlvo(null);
  }

  function fecharModal() {
    if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Projetos</h1>

      {loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {!loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhum projeto encontrado.</p>
      )}

      {projects.map((p) => (
        <div key={p.nome} style={card} onClick={() => setExpanded(expanded === p.nome ? null : p.nome)}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600 }}>{p.nome}</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>{p.dominio ?? '—'}</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>{p.container_count} container(s)</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>CPU: {p.cpu_percent.toFixed(1)}%</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>
              RAM: {p.mem_usage_mb.toFixed(0)} MB ({p.mem_percent_do_host.toFixed(1)}% do host)
            </span>
            {p.nome !== 'vps-monitor' && (
              <button
                onClick={(e) => abrirExclusao(p.nome, e)}
                style={{ marginLeft: 'auto', padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
              >
                Excluir projeto
              </button>
            )}
          </div>

          {expanded === p.nome && (
            <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {p.containers.map((c) => (
                <div key={c.name} style={{
                  padding: '4px 8px', background: 'var(--surface)',
                  border: '1px solid var(--border)', borderRadius: 6, fontSize: 12,
                }}>
                  <span style={{ fontFamily: 'monospace' }}>{c.name}</span>
                  <span style={{ color: 'var(--muted)', marginLeft: 6 }}>{c.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {deleteAlvo && (
        <div style={modalOverlay} onClick={fecharModal}>
          <div style={modalBox} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir projeto &quot;{deleteAlvo}&quot;</h3>

            {!preview && (
              <p style={{ color: 'var(--muted)', fontSize: 14 }}>Carregando preview...</p>
            )}

            {preview && deleteEtapa === 'preview' && (
              <>
                <p style={{ color: 'var(--danger)', fontWeight: 600, marginBottom: 12, fontSize: 14 }}>
                  Isso vai parar e remover permanentemente todos os containers e volumes deste projeto. Essa ação não pode ser desfeita.
                </p>

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Containers ({preview.containers.length})</p>
                <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>
                  {preview.containers.map((c) => c.name).join(', ') || 'nenhum'}
                </p>

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Volumes ({preview.volumes.length})</p>
                <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>
                  {preview.volumes.join(', ') || 'nenhum'}
                </p>

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Rotas do Traefik a remover</p>
                {preview.rotas_candidatas.length === 0 ? (
                  <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>Nenhuma rota candidata encontrada.</p>
                ) : (
                  <div style={{ marginBottom: 12 }}>
                    {preview.rotas_candidatas.map((r) => (
                      <label key={r} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', marginBottom: 4 }}>
                        <input type="checkbox" checked={rotasMarcadas.has(r)} onChange={() => toggleRota(r)} />
                        {r}
                      </label>
                    ))}
                  </div>
                )}

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Regras de firewall (sugestões — marque manualmente)</p>
                {preview.regras_firewall_candidatas.length === 0 ? (
                  <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 16 }}>Nenhuma regra candidata encontrada.</p>
                ) : (
                  <div style={{ marginBottom: 16 }}>
                    {preview.regras_firewall_candidatas.map((r, i) => (
                      <label key={`${r.porta}-${r.protocolo}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', marginBottom: 4 }}>
                        <input type="checkbox" checked={regrasMarcadas.has(i)} onChange={() => toggleRegra(i)} />
                        {r.porta}/{r.protocolo} — {r.permitir ? 'Permitir' : 'Negar'} — Origem: {r.origem_ip ?? 'Qualquer'}
                      </label>
                    ))}
                  </div>
                )}

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button onClick={fecharModal} style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}>
                    Cancelar
                  </button>
                  <button onClick={criarSnapshotEContinuar} style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}>
                    Criar snapshot e continuar
                  </button>
                </div>
              </>
            )}

            {deleteEtapa === 'criando-snapshot' && (
              <p style={{ color: 'var(--accent)', fontSize: 14 }}>Criando snapshot de segurança, aguarde...</p>
            )}

            {deleteEtapa === 'confirmacao' && (
              <>
                <p style={{ color: 'var(--muted)', marginBottom: 12, fontSize: 14 }}>
                  Snapshot &quot;{snapshotArquivo}&quot; criado com sucesso. Digite <strong>{deleteAlvo}</strong> pra confirmar a exclusão definitiva:
                </p>
                <input
                  style={{ ...input, marginBottom: 16 }}
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  autoFocus
                />
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button onClick={fecharModal} style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}>
                    Cancelar
                  </button>
                  <button
                    onClick={confirmarExclusao}
                    disabled={confirmText !== deleteAlvo}
                    style={{
                      padding: '8px 20px', border: 'none', borderRadius: 6, fontWeight: 700,
                      background: confirmText === deleteAlvo ? 'var(--danger)' : 'var(--surface)',
                      color: confirmText === deleteAlvo ? '#fff' : 'var(--muted)',
                      cursor: confirmText === deleteAlvo ? 'pointer' : 'not-allowed',
                    }}
                  >
                    Excluir definitivamente
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
