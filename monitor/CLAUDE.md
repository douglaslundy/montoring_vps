# REGRA OBRIGATÓRIA — Gerenciamento de Contexto e Memória de Projeto

> Prioridade máxima. Executar em TODA sessão, sem exceção, antes de qualquer outra ação.

---

## 1. Ao iniciar qualquer ação nesta sessão

- Antes de fazer qualquer outra coisa, leia o arquivo `PROGRESSO.md` na raiz do projeto.
  - Se não existir, crie-o.
- Se ele já tiver uma seção "PRÓXIMA TAREFA", trate-a como o ponto de retomada.
  - Não peça ao usuário para reexplicar o que já está registrado ali.
- Leia somente os arquivos indicados na seção "Contexto necessário" daquela tarefa.
  - Não abra o restante do repositório "por segurança".

---

## 2. Durante a execução de uma tarefa

A cada passo importante concluído (função implementada, bug corrigido, decisão de arquitetura tomada), atualize `PROGRESSO.md` imediatamente com:

- O que foi feito (1–3 linhas, direto ao ponto)
- Arquivos alterados
- Decisões que não sejam óbvias só de olhar o código (ex: "optei por X em vez de Y porque Z")

Não espere terminar a tarefa inteira para salvar. Se a energia acabar no meio, o próximo passo precisa ser recuperável.

---

## 3. Ao concluir uma tarefa

1. Marque a tarefa como concluída em `PROGRESSO.md`.
2. Leia a próxima tarefa em `TAREFAS.md` (ou backlog equivalente).
3. Reescreva a seção "Contexto necessário" contendo apenas o que é relevante para a próxima tarefa (arquivos, decisões, dependências).
   - Descarte explicitamente tudo que pertencia só à tarefa anterior.
4. Se a plataforma permitir (ex: Claude Code), recomende ativamente ao usuário rodar /compact ou /clear neste ponto — explicando que a tarefa anterior já está 100% documentada em disco e pode ser removida da conversa sem perda de informação.

---

## 4. Economia de tokens (regra contínua)

- Nunca recarregue um arquivo inteiro se só uma função/trecho é relevante — use leitura parcial ou busca (grep).
- Nunca repita no chat o conteúdo de arquivos já lidos; referencie por nome/caminho.
- Prefira resumos curtos a transcrições completas de código já existente.
- Se um arquivo de contexto crescer demais, resuma-o em `PROGRESSO.md` em vez de mantê-lo verboso.

---

## 5. Recuperação após interrupção

Se o usuário disser "continue de onde parou" (ou similar):

1. Ler `PROGRESSO.md`.
2. Identificar a última tarefa marcada como "em andamento" ou a "PRÓXIMA TAREFA".
3. Carregar apenas o contexto indicado nela.
4. Retomar sem pedir esclarecimentos adicionais, a menos que estritamente necessário.

---

## 6. Persistência desta regra

Esta regra deve permanecer salva neste arquivo (`CLAUDE.md`) e ser tratada como instrução permanente — não deve ser repetida pelo usuário a cada sessão.

---

### Estrutura de referência

    projeto/
    ├── CLAUDE.md         ← esta regra (lida automaticamente pelo Claude Code)
    ├── PROGRESSO.md       ← estado atual, decisões, próxima tarefa
    └── TAREFAS.md         ← backlog / lista de tarefas

### Modelo sugerido para PROGRESSO.md

    # Progresso do Projeto

    ## Última atualização
    [data e hora]

    ## Tarefa em andamento
    [descrição da tarefa atual]

    ## Contexto necessário
    - [arquivos relevantes]
    - [decisões importantes]

    ## Concluído
    - [x] [tarefas já finalizadas]

    ## Próxima tarefa
    [descrição da próxima tarefa]
