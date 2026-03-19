#!/usr/bin/env python3
"""
PreCompact Hook — Saves checkpoint and reinjects critical context after compaction.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_db, init_db, get_project_id, multi_source_search, DB_PATH, MEMORY_DIR, debug_log


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    session_id = input_data.get('session_id', 'unknown')
    cwd = os.getcwd()

    try:
        project_id = get_project_id(cwd)

        # Salva checkpoint mínimo no JSON da sessão
        project_slug = project_id.split('/')[-1].replace('.git', '')
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"{project_slug}_{date_str}.json"
        json_path = MEMORY_DIR / filename

        checkpoint = {
            'checkpoint_only': True,
            'sessionId': session_id,
            'projectId': project_id,
            'savedAt': datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z'
        }

        if json_path.exists():
            try:
                existing = json.loads(json_path.read_text())
                existing['last_checkpoint'] = checkpoint['savedAt']
                json_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
            except Exception:
                pass
        else:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))

        # Se não há banco ainda, continua silencioso
        if not DB_PATH.exists():
            print(json.dumps({'continue': True, 'suppressOutput': True}))
            return

        # Busca multi-source para reinjetar contexto crítico
        conn = get_db()
        init_db(conn)

        # Query dinâmica: usa o summary gerado pelo Claude Code antes da compactação.
        # É a fonte mais precisa do que estava sendo discutido no momento do autocompact.
        # Fallback para query genérica se summary não estiver disponível.
        compact_summary = input_data.get('summary', '').strip()
        if compact_summary:
            # Usa os primeiros 300 chars — parte mais relevante do resumo
            query = compact_summary[:300]
        else:
            query = "decisões importantes tarefas pendentes contexto crítico do projeto"

        results = multi_source_search(conn, query, project_id, top_k_per_session=2)
        conn.close()

        if not results:
            print(json.dumps({'continue': True, 'suppressOutput': True}))
            return

        # Monta resumo crítico para reinjeção
        lines = ['⚡ Contexto preservado após compactação:\n']
        for r in results:
            lines.append(f"[{r['title']}] {r['content'][:250]}")

        message = '\n'.join(lines)

        print(json.dumps({
            'continue': True,
            'suppressOutput': False,
            'systemMessage': message
        }))

    except Exception as e:
        debug_log('pre-compact', 'Erro ao salvar checkpoint ou buscar contexto', e)
        print(json.dumps({'continue': True, 'suppressOutput': True}))


if __name__ == '__main__':
    main()
