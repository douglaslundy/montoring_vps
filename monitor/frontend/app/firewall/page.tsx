'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Regra {
  porta: number;
  protocolo: string;
  permitir: boolean;
  origem_ip: string | null;
  protegida: boolean;
}

interface JobPendente {
  id: number;
  acao: string;
  permitir: boolean;
  porta: number;
  protocolo: string;
  origem_ip: string | null;
  status: string;
}

const PORTAS_PROTEGIDAS = [22, 80, 443];

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

const selectStyle: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 14, width: '100%',
};

export default function FirewallPage() {
  const [regras, setRegras] = useState<Regra[]>([]);
  const [jobsPendentes, setJobsPendentes] = useState<JobPendente[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [porta, setPorta] = useState('');
  const [protocolo, setProtocolo] = useState('tcp');
  const [permitir, setPermitir] = useState('allow');
  const [origemIp, setOrigemIp] = useState('');
  const [formError, setFormError] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<Regra | null>(null);

  const loadRules = useCallback(async () => {
    try {
      const r = await api.get('/firewall/rules');
      setRegras(r.data.regras);
      setJobsPendentes(r.data.jobs_pendentes);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadRules(); }, [loadRules]);

  useEffect(() => {
    const temPendente = jobsPendentes.length > 0;
    const id = setInterval(loadRules, temPendente ? 5000 : 30000);
    return () => clearInterval(id);
  }, [jobsPendentes, loadRules]);

  function portaEhProtegida(valor: string): boolean {
    return PORTAS_PROTEGIDAS.includes(Number(valor));
  }

  function abrirNovaRegra() {
    setShowForm(true);
    setFormError('');
    setPorta('');
    setProtocolo('tcp');
    setPermitir('allow');
    setOrigemIp('');
  }

  async function handleSalvar() {
    setFormError('');
    if (portaEhProtegida(porta)) {
      setFormError('Portas 22, 80 e 443 são protegidas e não podem ser alteradas.');
      return;
    }
    try {
      await api.post('/firewall/rules', {
        acao: 'add',
        permitir: permitir === 'allow',
        porta: Number(porta),
        protocolo,
        origem_ip: origemIp || null,
      });
      setToast({ msg: 'Regra enfileirada — aplica em até 1 minuto', type: 'success' });
      setShowForm(false);
      loadRules();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail || 'Erro ao criar regra');
    }
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.post('/firewall/rules', {
        acao: 'remove',
        permitir: deleteAlvo.permitir,
        porta: deleteAlvo.porta,
        protocolo: deleteAlvo.protocolo,
        origem_ip: deleteAlvo.origem_ip,
      });
      setToast({ msg: 'Remoção enfileirada — aplica em até 1 minuto', type: 'success' });
      loadRules();
    } catch {
      setToast({ msg: 'Erro ao remover regra', type: 'error' });
    }
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Firewall</h1>

      <div style={{ marginBottom: 16 }}>
        <button
          onClick={abrirNovaRegra}
          style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
        >
          + Nova regra
        </button>
      </div>

      {loading && regras.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}
      {!loading && regras.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhuma regra encontrada.</p>
      )}

      {regras.map((r, i) => (
        <div key={`${r.porta}-${r.protocolo}-${r.origem_ip}-${i}`} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600, fontFamily: 'monospace' }}>{r.porta}/{r.protocolo}</span>
            <span style={{
              padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: r.permitir ? 'var(--success)' : 'var(--danger)', color: '#fff',
            }}>
              {r.permitir ? 'Permitir' : 'Negar'}
            </span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>Origem: {r.origem_ip ?? 'Qualquer'}</span>
            {r.protegida && (
              <span style={{
                padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
                background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--muted)',
              }}>
                Protegida
              </span>
            )}
            {!r.protegida && (
              <button
                onClick={() => setDeleteAlvo(r)}
                style={{ marginLeft: 'auto', padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
              >
                Excluir
              </button>
            )}
          </div>
        </div>
      ))}

      {jobsPendentes.length > 0 && (
        <p style={{ color: 'var(--accent)', fontSize: 13, marginTop: 12 }}>
          {jobsPendentes.length} pedido(s) aplicando...
        </p>
      )}

      {showForm && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowForm(false)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '85%', maxWidth: 460, padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 16, color: 'var(--text)' }}>Nova regra</h3>
            <div style={{ display: 'grid', gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Porta</label>
                <input type="number" style={input} value={porta} onChange={(e) => setPorta(e.target.value)} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Protocolo</label>
                  <select style={selectStyle} value={protocolo} onChange={(e) => setProtocolo(e.target.value)}>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Acao</label>
                  <select style={selectStyle} value={permitir} onChange={(e) => setPermitir(e.target.value)}>
                    <option value="allow">Permitir</option>
                    <option value="deny">Negar</option>
                  </select>
                </div>
              </div>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Origem IP/CIDR (opcional)</label>
                <input
                  style={input} value={origemIp} onChange={(e) => setOrigemIp(e.target.value)}
                  placeholder="ex: 203.0.113.5 (vazio = qualquer origem)"
                />
              </div>
            </div>

            {formError && (
              <p style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 12, whiteSpace: 'pre-wrap' }}>{formError}</p>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={handleSalvar}
                disabled={portaEhProtegida(porta)}
                style={{
                  padding: '8px 20px', border: 'none', borderRadius: 6, fontWeight: 700,
                  background: portaEhProtegida(porta) ? 'var(--surface)' : 'var(--accent)',
                  color: portaEhProtegida(porta) ? 'var(--muted)' : '#000',
                  cursor: portaEhProtegida(porta) ? 'not-allowed' : 'pointer',
                }}
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

      {deleteAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setDeleteAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir regra</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir a regra da porta {deleteAlvo.porta}/{deleteAlvo.protocolo}? Essa acao nao pode ser desfeita.
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
