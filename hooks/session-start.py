#!/usr/bin/env python3
"""
SessionStart Hook — Notifica sessões disponíveis para o projeto atual.
Injeção de contexto é manual via /recall-load.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_db, init_db, get_project_id, get_active_sessions, DB_PATH, debug_log


def main():
    cwd = os.getcwd()

    if not DB_PATH.exists():
        print(json.dumps({'continue': True, 'suppressOutput': True}))
        return

    try:
        project_id = get_project_id(cwd)
        project_slug = project_id.split('/')[-1].replace('.git', '')

        conn = get_db()
        init_db(conn)
        sessions = get_active_sessions(conn, project_id)
        conn.close()

        # Instructions for Claude (injected into context, not visible to user)
        context_lines = [
            'Plugin recall is active. Persistent semantic memory across sessions.',
            'Commands: /recall-load (search sessions), /recall-save (save current session).',
            '/recall-load "query" — hybrid search in current project.',
            '/recall-load --global "query" — search across all projects.',
            f'Project: {project_slug}. Sessions available: {len(sessions)}.',
        ]
        if sessions:
            context_lines.append(f'Most recent: {sessions[0]["title"]}')
        context_lines.append('Suggest /recall-load when user resumes work or references past sessions.')
        context_lines.append('Suggest /recall-save before user ends the session.')
        additional_context = ' '.join(context_lines)

        if not sessions:
            print(json.dumps({
                'continue': True,
                'suppressOutput': True,
                'hookSpecificOutput': {
                    'hookEventName': 'SessionStart',
                    'additionalContext': additional_context
                }
            }))
            return

        # Notification for the user (visible in UI)
        lines = [f'🧠 Memória disponível para **{project_slug}**:\n']
        for i, s in enumerate(sessions, 1):
            lines.append(f'  {i}. {s["title"]}')

        lines.append('\nUse `/recall-load` para carregar o contexto.')
        lines.append('Ao final da sessão, use `/recall-save` para indexar esta conversa.')
        message = '\n'.join(lines)

        print(json.dumps({
            'continue': True,
            'suppressOutput': False,
            'systemMessage': message,
            'hookSpecificOutput': {
                'hookEventName': 'SessionStart',
                'additionalContext': additional_context
            }
        }))

    except Exception as e:
        debug_log('session-start', 'Erro ao listar sessões', e)
        print(json.dumps({'continue': True, 'suppressOutput': True}))


if __name__ == '__main__':
    main()
