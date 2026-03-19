#!/usr/bin/env python3
"""
recall-save command pipeline — called by /recall-save.
Saves session JSON, indexes chunks with Gemini embeddings.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import (
    get_db, init_db, get_project_id, rotate_sessions,
    save_session_metadata, index_chunks, MEMORY_DIR
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-id', required=False, default=None)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--summary', required=True)
    args = parser.parse_args()

    try:
        summary = json.loads(args.summary)
    except json.JSONDecodeError as e:
        print(f"Erro ao parsear summary JSON: {e}", file=sys.stderr)
        sys.exit(1)

    project_id = get_project_id(args.cwd)
    title = summary.get('title', 'Sessão sem título')

    project_slug = project_id.split('/')[-1].replace('.git', '')
    date_str = datetime.now().strftime('%Y-%m-%d')

    # Gera session_id se não fornecido (CLAUDE_SESSION_ID não está disponível no ambiente)
    session_id = args.session_id or f"{project_slug}_{date_str}"

    conn = get_db()
    init_db(conn)

    # Verifica se já existe sessão com este ID (sobrescreve)
    existing = conn.execute(
        "SELECT filename FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()

    if existing:
        filename = existing['filename']
    else:
        rotate_sessions(conn, project_id, max_sessions=3)
        # Gera filename único — verifica colisão com sessões do mesmo dia
        base_filename = f"{project_slug}_{date_str}.json"
        taken = conn.execute(
            "SELECT filename FROM sessions WHERE project_id = ? AND archived = 0",
            (project_id,)
        ).fetchall()
        taken_names = {r['filename'] for r in taken}
        if base_filename in taken_names:
            filename = f"{project_slug}_{date_str}_{session_id[-8:]}.json"
        else:
            filename = base_filename

    # Salva JSON
    session_data = {
        'version': '2.0',
        'sessionId': session_id,
        'projectId': project_id,
        'cwd': args.cwd,
        'createdAt': datetime.now(timezone.utc).isoformat(timespec='seconds') + 'Z',
        'summary': summary
    }
    json_path = MEMORY_DIR / filename
    json_path.write_text(json.dumps(session_data, indent=2, ensure_ascii=False))

    # Salva metadados no SQLite
    save_session_metadata(conn, session_id, project_id, args.cwd, filename, title)

    # Indexa chunks
    full_text = f"{title}\n\n"
    full_text += '\n'.join(summary.get('decisions', []))
    full_text += '\n'.join(summary.get('tasks_pending', []))
    full_text += '\n'.join(summary.get('concepts', []))
    full_text += f"\n{summary.get('notes', '')}"

    index_chunks(conn, session_id, full_text)
    conn.close()

    print(json.dumps({
        'filename': filename,
        'title': title,
        'project_id': project_id
    }))


if __name__ == '__main__':
    main()
