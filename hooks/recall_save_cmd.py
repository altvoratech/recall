#!/usr/bin/env python3
"""
recall-save command pipeline — called by /recall-save.
Saves session JSON, indexes chunks with local embeddings (fastembed).
Supports intelligent merge: when a session already exists for the same day,
merges arrays (union + dedup) instead of overwriting.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import (
    get_db, init_db, get_project_id, rotate_sessions,
    save_session_metadata, index_chunks, chunk_structured, MEMORY_DIR,
    debug_log
)


def _deduplicate_list(items: list) -> list:
    """Deduplicates a list preserving order. Uses normalized string comparison."""
    seen = set()
    result = []
    for item in items:
        key = item.strip().lower() if isinstance(item, str) else str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _merge_summaries(existing: dict, new: dict) -> dict:
    """Merges two session summaries intelligently.

    - List fields: union + deduplicate (new items appended after existing)
    - String fields (title, notes): new replaces existing
    - tasks_pending: items that appear in new tasks_completed are removed
    """
    merged = {}

    # String fields: new wins
    merged['title'] = new.get('title') or existing.get('title', '')
    merged['notes'] = new.get('notes') or existing.get('notes', '')

    # List fields: union + dedup
    list_fields = ['decisions', 'tasks_completed', 'files_modified', 'concepts']
    for field in list_fields:
        combined = (existing.get(field) or []) + (new.get(field) or [])
        merged[field] = _deduplicate_list(combined)

    # tasks_pending: merge then remove items that moved to tasks_completed
    pending_combined = (existing.get('tasks_pending') or []) + (new.get('tasks_pending') or [])
    pending_deduped = _deduplicate_list(pending_combined)

    # Remove resolved tasks (present in tasks_completed)
    completed_keys = {t.strip().lower() for t in merged['tasks_completed']}
    merged['tasks_pending'] = [t for t in pending_deduped if t.strip().lower() not in completed_keys]

    return merged


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
    project_slug = project_id.split('/')[-1].replace('.git', '')
    date_str = datetime.now().strftime('%Y-%m-%d')

    # Gera session_id se não fornecido (CLAUDE_SESSION_ID não está disponível no ambiente)
    session_id = args.session_id or f"{project_slug}_{date_str}"

    conn = get_db()
    init_db(conn)

    # Verifica se já existe sessão com este ID
    existing = conn.execute(
        "SELECT filename FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()

    if existing:
        filename = existing['filename']

        # ── Merge inteligente: ler JSON existente e fazer merge dos arrays ──
        json_path = MEMORY_DIR / filename
        if json_path.exists():
            try:
                existing_data = json.loads(json_path.read_text())
                existing_summary = existing_data.get('summary', {})
                summary = _merge_summaries(existing_summary, summary)
                debug_log('recall_save', f'Merged session {session_id}: '
                          f'{len(summary.get("decisions", []))} decisions, '
                          f'{len(summary.get("tasks_completed", []))} completed, '
                          f'{len(summary.get("tasks_pending", []))} pending')
            except (json.JSONDecodeError, KeyError) as e:
                debug_log('recall_save', f'Merge failed, overwriting: {e}')
                # Fallback: usa o summary novo como está
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

    title = summary.get('title', 'Sessão sem título')

    # Salva JSON consolidado (merged ou novo)
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

    # Indexa chunks usando chunking semântico (por seção lógica)
    chunks = chunk_structured(summary)
    index_chunks(conn, session_id, '', precomputed_chunks=chunks)
    conn.close()

    print(json.dumps({
        'filename': filename,
        'title': title,
        'project_id': project_id
    }))


if __name__ == '__main__':
    main()
