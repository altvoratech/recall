---
name: recall-save
description: Salva memória da sessão atual — indexa no SQLite + sqlite-vec + FTS5 com embeddings locais (fastembed)
argument-hint: "[nota opcional sobre o que está sendo salvo]"
allowed-tools: [Read, Write, Bash]
---

# /recall-save

Salva o estado atual da sessão como checkpoint.

## Diretório de Memória

Sempre `~/.claude/memory/` (global, independente do projeto).

## Comportamento

1. Resume a conversa atual usando o próprio conhecimento do Claude
2. Executa o pipeline de indexação via script Python
3. Confirma ao usuário

## Implementação

Execute os seguintes passos:

### 1. Gere o resumo da sessão

Analise a conversa atual e produza um JSON com esta estrutura **obrigatória**:

```json
{
  "title": "resumo em uma linha do que foi feito nesta sessão",
  "decisions": ["decisão arquitetural 1", "decisão 2"],
  "tasks_pending": ["tarefa que ficou pendente"],
  "tasks_completed": ["tarefa concluída nesta sessão"],
  "files_modified": ["caminho/do/arquivo.py"],
  "concepts": ["conceito importante discutido"],
  "notes": "contexto adicional relevante para sessões futuras"
}
```

### 2. Execute o pipeline de salvamento

Use a ferramenta Bash para rodar:

```bash
python3 ~/.claude/plugins/cache/local/recall/1.0.0/hooks/recall_save_cmd.py \
  --cwd "$PWD" \
  --summary '<JSON_DO_RESUMO>'
```

### 3. Confirme ao usuário

Exiba:
```
✅ Sessão salva: <projeto>_<data>.json
<title da sessão>
```

## Notas

- Se já existe JSON desta sessão (mesmo sessionId), **sobrescreve**
- Se o projeto já tem 3 sessões ativas, a mais antiga vai para `archived/`
- O resumo é escrito pelo Claude — sem parsing automático, contexto livre
