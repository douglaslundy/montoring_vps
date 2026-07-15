# Modal de Regras de Alerta

## Contexto

A página `/alertas` (aba "Regras") já tem CRUD completo de regras de alerta (`AlertRule`): criar, editar, excluir, ativar/desativar. O formulário de criar/editar hoje é renderizado **inline** na página (`frontend/app/alertas/page.tsx`), empurrando o restante do conteúdo da aba para baixo quando aberto.

O projeto já tem um padrão de modal estabelecido em dois componentes (`frontend/components/AccessIpModal.tsx` e `frontend/components/QrCodeModal.tsx`): overlay escurecido fixo, card centralizado, header com título e botão `×`, clique no overlay fecha o modal.

## Objetivo

Migrar o formulário de criar/editar regra de alerta de inline para um modal, seguindo o mesmo padrão visual e de comportamento já usado pelos outros modais do projeto — sem alterar nenhum comportamento funcional (validação, chamadas de API, mensagens de erro).

## Fora de escopo

- Validação de campos (nome obrigatório, threshold numérico, etc.) — form continua aceitando os mesmos valores de hoje, sem validação nova.
- Fechar com tecla ESC — não existe nos outros modais do projeto, não será adicionado aqui.
- Criar um componente `<Modal>` genérico/reutilizável — os outros dois modais (`AccessIpModal`, `QrCodeModal`) não serão tocados/refatorados.
- Qualquer mudança em `backend/api/alerts.py` ou no modelo `AlertRule` — API já é suficiente (`GET/POST/PUT/DELETE /alerts/rules`, `POST /alerts/rules/{id}/toggle`).

## Design

### Componente novo: `frontend/components/AlertRuleModal.tsx`

Componente controlado, sem estado próprio de dados — só recebe e devolve via props (mesmo padrão de `AccessIpModal`).

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
  editing: boolean               // true => título "Editar Regra", false => "Nova Regra"
  metricas: string[]
  operadores: string[]
  severidades: string[]
  metricaLabels: Record<string, string>
  onChange: (form: RuleForm) => void
  onSave: () => void
  onClose: () => void
}
```

`page.tsx` continua dono de todo o estado (`form`, `editId`, `showForm`) e das funções que já existem hoje (`startCreate`, `startEdit`, `saveRule`, `toggleRule`, `deleteRule`). A única mudança em `page.tsx` é trocar o bloco JSX inline (linhas ~354-418, `{showForm && (<div style={card}>...)}`) por:

```tsx
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

### Visual e comportamento

- **Overlay:** `position: fixed`, `inset: 0`, fundo escuro (`rgba(0,0,0,0.75)`, igual ao `AccessIpModal`), `zIndex: 1000`, `onClick={onClose}`.
- **Card:** centralizado (`display: flex; alignItems: center; justifyContent: center` no overlay), `onClick={e => e.stopPropagation()}` para não fechar ao clicar dentro do card.
- **Header:** título dinâmico (`editing ? 'Editar Regra' : 'Nova Regra'`) + botão `×` chamando `onClose`.
- **Corpo:** grid 2 colunas com os mesmos campos de hoje — Nome, Métrica, Operador, Threshold, Duração mínima (min), Severidade, Cooldown (min), checkboxes de canal (E-mail, WhatsApp). Nenhum campo novo, nenhuma remoção.
- **Rodapé:** botões "Salvar" (`onSave`) e "Cancelar" (`onClose`), mesmo estilo visual de hoje.
- **Fechar clicando fora:** clicar no overlay fecha o modal e descarta o formulário — mesmo comportamento de `AccessIpModal`/`QrCodeModal`, mantendo os modais do projeto consistentes entre si.

### Fluxo de dados

Nenhuma mudança em como as regras são buscadas ou salvas. `loadRules`, `saveRule`, `toggleRule`, `deleteRule` continuam exatamente como estão em `page.tsx`, chamando `api.get/post/put/delete` em `/alerts/rules`. O modal é puramente apresentacional — recebe o form atual via prop e notifica mudanças via `onChange`.

### Tratamento de erro

Inalterado. O `try/catch` já existente em `saveRule()` (em `page.tsx`) continua responsável por capturar erro de API e exibir o `Toast` de erro — o modal não sabe nada sobre isso.

### Testes

Não há suíte de testes de frontend neste projeto além do build/typecheck (`npm run build`). Verificação:

1. `npm run build` limpo (sem erros de tipo).
2. Teste manual no navegador: abrir "+ Nova Regra" → modal aparece como overlay; preencher e salvar → regra criada e modal fecha; clicar "Editar" numa regra existente → modal abre com valores preenchidos; clicar fora do modal → fecha e descarta; clicar "Cancelar" → mesmo efeito.

## Arquivos afetados

- **Novo:** `frontend/components/AlertRuleModal.tsx`
- **Modificado:** `frontend/app/alertas/page.tsx` (remove o bloco de formulário inline, adiciona import e uso de `AlertRuleModal`)
