#!/usr/bin/env python3
"""
UserPromptSubmit hook — busca semântica automática no recall.
Injeta contexto relevante via additionalContext quando score > threshold.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_db, init_db, get_project_id, multi_source_search


SCORE_THRESHOLD = 0.75
MIN_WORDS = 4
MAX_CHUNKS = 3
MAX_CHUNK_LEN = 400


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    prompt = input_data.get('prompt', '')

    # Ignora mensagens curtas — "sim", "ok", "faz isso" nao geram buscas uteis
    if len(prompt.split()) < MIN_WORDS:
        sys.exit(0)

    try:
        conn = get_db()
        init_db(conn)
        # Cross-project: busca em todas as sessões indexadas
        results = multi_source_search(conn, prompt, project_id=None, top_k_per_session=2)
        conn.close()
    except Exception as e:
        print(json.dumps({
            "systemMessage": f"[recall] hook falhou: {e}"
        }))
        sys.exit(0)

    # Filtra por threshold
    relevant = [r for r in results if r.get('blended_score', 0) > SCORE_THRESHOLD]

    if not relevant:
        sys.exit(0)

    # Monta contexto com os chunks mais relevantes
    chunks = []
    for r in relevant[:MAX_CHUNKS]:
        content = r['content'][:MAX_CHUNK_LEN]
        score = r.get('blended_score', 0)
        source = r.get('project_id', 'unknown')
        chunks.append(f"[recall score:{score:.2f} source:{source}]\n{content}")

    context = "\n---\n".join(chunks)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"[Recall auto-search] Contexto relevante encontrado:\n{context}"
        }
    }))
    sys.exit(0)


if __name__ == '__main__':
    main()
