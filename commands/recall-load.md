---
name: memory-load
description: Carrega memória de sessões anteriores via busca multi-source no SQLite + sqlite-vec
argument-hint: "[número da sessão | query de busca]"
allowed-tools: [Read, Bash, AskUserQuestion]
---

# /recall-load

Carrega contexto de sessões anteriores usando busca semântica multi-source.

## Diretório de Memória

Sempre `~/.claude/memory/` (global, independente do projeto).
Banco: `~/.claude/memory/memory.db`

> ⚠️ Nunca buscar em diretórios de projeto (`.claude/projects/*/memory/`).

## Modos de Uso

```
/recall-load                        # Lista sessões do projeto atual
/recall-load 1                      # Carrega sessão número 1 da lista
/recall-load "RAG pipeline"         # Busca semântica no projeto atual
/recall-load --global "next-devtools"  # Busca semântica cross-project (todos os projetos)
```

## Implementação

> O `db.py` lê `GEMINI_API_KEY` automaticamente dos arquivos de config do shell (`~/.profile`, `~/.zshrc`, etc.) — não é necessário passá-la explicitamente.

### 1. Listar sessões disponíveis (`/recall-load` sem argumento)

Execute via Bash:

```bash
python3 -c "
import sys
sys.path.insert(0, '$HOME/.claude/plugins/cache/local/recall/1.0.0/hooks')
from db import get_db, init_db, get_project_id, get_active_sessions
import os, json

cwd = os.getcwd()
project_id = get_project_id(cwd)
conn = get_db()
init_db(conn)
sessions = get_active_sessions(conn, project_id)
for i, s in enumerate(sessions, 1):
    print(f'{i}. {s[\"filename\"]} — {s[\"title\"]}')
conn.close()
"
```

Exiba a lista ao usuário e pergunte qual deseja carregar.

### 2. Carregar sessão por número (`/recall-load 1`)

**Antes de executar o Bash**, sintetize o contexto da conversa atual em uma query de busca semântica.
Analise as últimas mensagens trocadas e gere uma string de 5 a 10 palavras que capture:
- A tarefa ou problema principal sendo discutido
- Tecnologias ou conceitos centrais mencionados
- Qualquer decisão ou dúvida em aberto

Exemplos:
- "refatoração Tailwind CSS componentes Next.js blog"
- "MCP next-devtools configuração dev server"
- "design system tokens cor tipografia blueprint"

Guarde essa string como `CONTEXT_QUERY` e use ela na chamada ao `multi_source_search` abaixo.

Execute via Bash para obter a sessão e fazer busca multi-source:

```bash
python3 -c "
import sys, os, json
sys.path.insert(0, '$HOME/.claude/plugins/cache/local/recall/1.0.0/hooks')
from db import get_db, init_db, get_project_id, get_active_sessions, multi_source_search

cwd = os.getcwd()
project_id = get_project_id(cwd)
conn = get_db()
init_db(conn)

# Busca multi-source — query gerada dinamicamente a partir do contexto da conversa atual
results = multi_source_search(conn, '$CONTEXT_QUERY', project_id, top_k_per_session=3)

# Lê o JSON da sessão mais recente que tem resumo real
sessions = get_active_sessions(conn, project_id)
import json as _json
from pathlib import Path

data = {}
chosen_session = None
for s in sessions:
    json_path = Path.home() / '.claude' / 'memory' / s['filename']
    if json_path.exists():
        candidate = _json.loads(json_path.read_text())
        summary = candidate.get('summary', {})
        if summary and any(summary.get(k) for k in ['decisions', 'tasks_pending', 'concepts']):
            data = candidate
            chosen_session = s
            break

if not chosen_session and sessions:
    chosen_session = sessions[0]

print(_json.dumps({'results': results, 'summary': data.get('summary', {}), 'session_title': chosen_session['title'] if chosen_session else ''}, ensure_ascii=False))
conn.close()
"
```

### 3. Busca semântica (`/recall-load "RAG pipeline"`)

Usa o argumento como query de similaridade no projeto atual:

```bash
python3 -c "
import sys, os, json
sys.path.insert(0, '$HOME/.claude/plugins/cache/local/recall/1.0.0/hooks')
from db import get_db, init_db, get_project_id, multi_source_search

cwd = os.getcwd()
project_id = get_project_id(cwd)
conn = get_db()
init_db(conn)
results = multi_source_search(conn, 'RAG pipeline', project_id, top_k_per_session=3)
print(json.dumps(results, ensure_ascii=False))
conn.close()
"
```

### 4. Busca cross-project (`/recall-load --global "next-devtools"`)

Quando o argumento começa com `--global`, busca em **todos os projetos** indexados.
Útil quando uma decisão técnica ou ferramenta foi descoberta em outro projeto e você quer recuperar esse contexto.

O resultado inclui o `project_id` de cada chunk, ordenado por score descendente.
Threshold mínimo de similaridade: `0.6` (evita ruído cross-project).

```bash
python3 -c "
import sys, os, json
sys.path.insert(0, '$HOME/.claude/plugins/cache/local/recall/1.0.0/hooks')
from db import get_db, init_db, multi_source_search

conn = get_db()
init_db(conn)
# project_id=None → cross-project
results = multi_source_search(conn, 'next-devtools', project_id=None, top_k_per_session=2)
print(json.dumps(results, ensure_ascii=False))
conn.close()
"
```

Ao exibir, inclua o `project_id` de cada resultado para o usuário saber a origem:

```
[Cross-Project] score: 0.66 — blue-new-layout
  MCP next-devtools integrado e funcionando...

[Cross-Project] score: 0.61 — demo-script
  Debug mode, calibração de timeouts...
```

### 5. Exibir resultado

Após obter os dados, exiba ao usuário no formato:

```
Contexto Carregado — <projeto> (<data>)

Decisões:
  • decisão 1
  • decisão 2

Tarefas Pendentes:
  • tarefa 1

Conceitos:
  • conceito 1

Notas: contexto adicional...

[Chunks relevantes por sessão via multi-source]
```

## Tratamento de Erros

- **Banco não encontrado**: Informa que não há memória salva e orienta a usar `/recall-save`
- **Sem sessões do projeto**: Exibe todas as sessões disponíveis de outros projetos para escolha manual
- **GEMINI_API_KEY ausente**: Lista sessões sem busca semântica, carrega JSON diretamente
