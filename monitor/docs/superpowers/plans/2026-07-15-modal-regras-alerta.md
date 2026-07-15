# Modal de Regras de Alerta Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrar o formulário de criar/editar regra de alerta em `/alertas` (aba "Regras") de um bloco inline para um modal, seguindo o mesmo padrão visual/comportamental já usado por `AccessIpModal`/`QrCodeModal`.

**Architecture:** Novo componente controlado `frontend/components/AlertRuleModal.tsx` que recebe o form e callbacks via props (sem estado próprio). `frontend/app/alertas/page.tsx` continua dono de todo o estado (`form`, `editId`, `showForm`) e das funções de API (`saveRule`, `startEdit`, `startCreate`) — só troca o bloco JSX inline pelo novo componente.

**Tech Stack:** Next.js 16 / React 18 / TypeScript, sem suíte de testes de frontend (verificação via `next build` + teste manual no navegador).

## Global Constraints

- Nenhuma validação de campo nova (nome obrigatório, threshold numérico, etc.) — comportamento do form permanece idêntico ao atual.
- Sem tecla ESC para fechar — não existe em nenhum modal do projeto hoje.
- Não tocar em `AccessIpModal.tsx` nem `QrCodeModal.tsx` — sem extrair um `<Modal>` genérico.
- Não alterar `backend/api/alerts.py` nem o modelo `AlertRule` — API já é suficiente.
- Clicar no overlay (fora do card) fecha o modal e descarta o form — mesmo comportamento de `AccessIpModal`.

---

### Task 1: Criar o componente `AlertRuleModal`

**Files:**
- Create: `frontend/components/AlertRuleModal.tsx`

**Interfaces:**
- Produces: `export default function AlertRuleModal(props: Props)` onde:
  ```ts
  interface RuleForm {
    nome: string
    metrica: string
    operador: string
    threshold: number
    duracao_minutos: number
    severidade: string
    canal_email: number
    canal_whatsapp: number
    cooldown_minutos: number
    ativo: number
  }

  interface Props {
    form: RuleForm
    editing: boolean
    metricas: string[]
    operadores: string[]
    severidades: string[]
    metricaLabels: Record<string, string>
    onChange: (form: RuleForm) => void
    onSave: () => void
    onClose: () => void
  }
  ```
  Task 2 consome exatamente essa assinatura.

- [ ] **Step 1: Criar o arquivo do componente**

Conteúdo completo de `frontend/components/AlertRuleModal.tsx`:

```tsx
'use client'

import type { CSSProperties } from 'react'

export interface RuleForm {
  nome: string
  metrica: string
  operador: string
  threshold: number
  duracao_minutos: number
  severidade: string
  canal_email: number
  canal_whatsapp: number
  cooldown_minutos: number
  ativo: number
}

interface Props {
  form: RuleForm
  editing: boolean
  metricas: string[]
  operadores: string[]
  severidades: string[]
  metricaLabels: Record<string, string>
  onChange: (form: RuleForm) => void
  onSave: () => void
  onClose: () => void
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
  zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
}

const modalCard: CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--accent)', borderRadius: 12,
  width: '85%', maxWidth: 640, maxHeight: '85vh', display: 'flex', flexDirection: 'column',
}

const input: CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
}

export default function AlertRuleModal({
  form, editing, metricas, operadores, severidades, metricaLabels,
  onChange, onSave, onClose,
}: Props) {
  return (
    <div style={overlay} onClick={onClose}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <div style={{
          padding: '14px 20px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ color: 'var(--text)', fontWeight: 600, fontSize: 16 }}>
            {editing ? 'Editar Regra' : 'Nova Regra'}
          </span>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}
          >×</button>
        </div>

        <div style={{ padding: 20, overflow: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome</label>
              <input style={input} value={form.nome} onChange={e => onChange({ ...form, nome: e.target.value })} />
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Métrica</label>
              <select style={input} value={form.metrica} onChange={e => onChange({ ...form, metrica: e.target.value })}>
                {metricas.map(m => <option key={m} value={m}>{metricaLabels[m] ?? m}</option>)}
              </select>
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Operador</label>
              <select style={input} value={form.operador} onChange={e => onChange({ ...form, operador: e.target.value })}>
                {operadores.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Threshold</label>
              <input type="number" style={input} value={form.threshold} onChange={e => onChange({ ...form, threshold: Number(e.target.value) })} />
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Duração mínima (min)</label>
              <input type="number" style={input} value={form.duracao_minutos} onChange={e => onChange({ ...form, duracao_minutos: Number(e.target.value) })} />
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Severidade</label>
              <select style={input} value={form.severidade} onChange={e => onChange({ ...form, severidade: e.target.value })}>
                {severidades.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label style={{ color: 'var(--muted)', fontSize: 12 }}>Cooldown (min)</label>
              <input type="number" style={input} value={form.cooldown_minutos} onChange={e => onChange({ ...form, cooldown_minutos: Number(e.target.value) })} />
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={!!form.canal_email} onChange={e => onChange({ ...form, canal_email: e.target.checked ? 1 : 0 })} />
                E-mail
              </label>
              <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                <input type="checkbox" checked={!!form.canal_whatsapp} onChange={e => onChange({ ...form, canal_whatsapp: e.target.checked ? 1 : 0 })} />
                WhatsApp
              </label>
            </div>
          </div>
          <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
            <button
              onClick={onSave}
              style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
            >
              Salvar
            </button>
            <button
              onClick={onClose}
              style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
            >
              Cancelar
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Rodar o typecheck/build para confirmar que o arquivo compila isoladamente**

Run: `cd frontend && npm run build`
Expected: build completo sem erros de tipo (o componente ainda não é importado em lugar nenhum, então não altera o bundle final, mas confirma que o TSX é válido).

- [ ] **Step 3: Commit**

```bash
git add monitor/frontend/components/AlertRuleModal.tsx
git commit -m "feat: adiciona componente AlertRuleModal"
```

---

### Task 2: Substituir o formulário inline pelo modal em `page.tsx`

**Files:**
- Modify: `frontend/app/alertas/page.tsx:8` (import)
- Modify: `frontend/app/alertas/page.tsx:353-418` (bloco do formulário inline)

**Interfaces:**
- Consumes: `AlertRuleModal` de Task 1, com a `Props` exata definida ali (`form`, `editing`, `metricas`, `operadores`, `severidades`, `metricaLabels`, `onChange`, `onSave`, `onClose`).

- [ ] **Step 1: Adicionar o import**

Em `frontend/app/alertas/page.tsx`, logo após a linha do import de `AlertNotifications` (linha 8):

```tsx
import { AlertNotificationsCompact, AlertNotificationsDetailed, type AlertNotificacao } from '../../components/AlertNotifications'
import AlertRuleModal from '../../components/AlertRuleModal'
```

- [ ] **Step 2: Substituir o bloco do formulário inline**

Localizar em `frontend/app/alertas/page.tsx` o bloco (atualmente linhas 353-418):

```tsx
          {/* Formulário */}
          {showForm && (
            <div style={{ ...card, marginBottom: 20, border: '1px solid var(--accent)' }}>
              <h3 style={{ color: 'var(--text)', marginBottom: 16 }}>{editId ? 'Editar Regra' : 'Nova Regra'}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome</label>
                  <input style={input} value={form.nome} onChange={e => setForm(f => ({ ...f, nome: e.target.value }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Métrica</label>
                  <select style={input} value={form.metrica} onChange={e => setForm(f => ({ ...f, metrica: e.target.value }))}>
                    {METRICAS.map(m => <option key={m} value={m}>{METRICA_LABELS[m]}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Operador</label>
                  <select style={input} value={form.operador} onChange={e => setForm(f => ({ ...f, operador: e.target.value }))}>
                    {OPERADORES.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Threshold</label>
                  <input type="number" style={input} value={form.threshold} onChange={e => setForm(f => ({ ...f, threshold: Number(e.target.value) }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Duração mínima (min)</label>
                  <input type="number" style={input} value={form.duracao_minutos} onChange={e => setForm(f => ({ ...f, duracao_minutos: Number(e.target.value) }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Severidade</label>
                  <select style={input} value={form.severidade} onChange={e => setForm(f => ({ ...f, severidade: e.target.value }))}>
                    {SEVERIDADES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Cooldown (min)</label>
                  <input type="number" style={input} value={form.cooldown_minutos} onChange={e => setForm(f => ({ ...f, cooldown_minutos: Number(e.target.value) }))} />
                </div>
                <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                  <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={!!form.canal_email} onChange={e => setForm(f => ({ ...f, canal_email: e.target.checked ? 1 : 0 }))} />
                    E-mail
                  </label>
                  <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={!!form.canal_whatsapp} onChange={e => setForm(f => ({ ...f, canal_whatsapp: e.target.checked ? 1 : 0 }))} />
                    WhatsApp
                  </label>
                </div>
              </div>
              <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
                <button
                  onClick={saveRule}
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
          )}
```

Substituir por:

```tsx
          {/* Formulário */}
          {showForm && (
            <AlertRuleModal
              form={form}
              editing={editId !== null}
              metricas={METRICAS}
              operadores={OPERADORES}
              severidades={SEVERIDADES}
              metricaLabels={METRICA_LABELS}
              onChange={setForm}
              onSave={saveRule}
              onClose={() => setShowForm(false)}
            />
          )}
```

- [ ] **Step 3: Rodar o build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo (o `input` style local em `page.tsx` continua usado em outros lugares da página — filtros de histórico — então não deve ser removido).

- [ ] **Step 4: Teste manual no navegador**

Com `npm run dev` rodando (ou `npm run build && npm start`):

1. Abrir `/alertas`, ir para a aba "Regras".
2. Clicar em "+ Nova Regra" → o modal deve aparecer como overlay centralizado sobre a lista de regras (não mais inline empurrando o conteúdo).
3. Preencher os campos e clicar "Salvar" → regra criada, modal fecha, lista atualizada.
4. Clicar em "Editar" numa regra existente → modal abre com os valores da regra já preenchidos, título "Editar Regra".
5. Clicar fora do card (na área escura) → modal fecha, formulário descartado.
6. Reabrir e clicar em "Cancelar" → mesmo efeito de fechar sem salvar.

- [ ] **Step 5: Commit**

```bash
git add monitor/frontend/app/alertas/page.tsx
git commit -m "feat: migra formulário de regra de alerta para modal"
```
