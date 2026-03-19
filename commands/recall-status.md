---
name: memory-status
description: Mostra status atual da memória persistente (SQLite + sessões do projeto)
argument-hint: "[sem argumentos]"
allowed-tools: [Bash]
---

# /memory-status

Exibe estatísticas do banco de memória e sessões do projeto atual.

## Implementação

Execute via Bash:

```bash
python3 -c "
import sys, os
sys.path.insert(0, '$HOME/.claude/plugins/cache/local/persistent-context/1.0.0/hooks')
from db import get_db, init_db, get_project_id, get_active_sessions, DB_PATH, ARCHIVE_DIR
from pathlib import Path
import json

cwd = os.getcwd()
project_id = get_project_id(cwd)
project_slug = project_id.split('/')[-1].replace('.git', '')

if not DB_PATH.exists():
    print('Nenhuma memória encontrada. Use /memory-save para criar.')
    exit()

conn = get_db()
init_db(conn)

sessions = get_active_sessions(conn, project_id)
total_chunks = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
total_sessions = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
archived = conn.execute('SELECT COUNT(*) FROM sessions WHERE archived = 1').fetchone()[0]

print(f'Projeto: {project_slug}')
print(f'Sessoes ativas: {len(sessions)} / 3')
print(f'Sessoes arquivadas: {archived}')
print(f'Total chunks indexados: {total_chunks}')
print(f'Banco: {DB_PATH} ({DB_PATH.stat().st_size // 1024} KB)')
print()

for i, s in enumerate(sessions, 1):
    chunks = conn.execute('SELECT COUNT(*) FROM chunks WHERE session_id = ?', (s[\"id\"],)).fetchone()[0]
    print(f'  {i}. [{\"sem resumo\" if chunks == 0 else f\"{chunks} chunks\"}] {s[\"title\"]}')
    print(f'     arquivo: {s[\"filename\"]}')

conn.close()
"
```

## Saída esperada

```
Projeto: demo-script
Sessoes ativas: 3 / 3
Sessoes arquivadas: 2
Total chunks indexados: 9
Banco: /home/user/.claude/memory/memory.db (48 KB)

  1. [3 chunks] Correções do persistent-context: GEMINI_API_KEY e multi-source
     arquivo: demo-script_2026-03-13.json
  2. [2 chunks] Redesign com SQLite + Gemini
     arquivo: demo-script_2026-03-12.json
  3. [sem resumo] Sessão 2026-03-11 — sem resumo (use /memory-save)
     arquivo: demo-script_2026-03-11_fallback.json
```

## Sem memória

Se o banco não existir, informa e orienta:

```
Nenhuma memória encontrada. Use /memory-save para criar.
```
