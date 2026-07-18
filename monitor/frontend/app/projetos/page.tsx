'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';

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

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8, cursor: 'pointer',
};

export default function ProjetosPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    try { setProjects((await api.get('/projects')).data.projects); } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadProjects(); }, [loadProjects]);

  useEffect(() => {
    const id = setInterval(loadProjects, 30000);
    return () => clearInterval(id);
  }, [loadProjects]);

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
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
    </div>
  );
}
