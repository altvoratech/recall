#!/usr/bin/env python3
"""
Stop hook — suggests /recall-save when conversation is long.
Counts user messages in the transcript. If above threshold,
injects additionalContext suggesting the user save the session.
"""

import json
import sys
from pathlib import Path


MESSAGE_THRESHOLD = 20


def count_user_messages(transcript_path: str) -> int:
    """Count user messages in the JSONL transcript."""
    count = 0
    try:
        with open(transcript_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get('type') == 'user':
                        count += 1
                except json.JSONDecodeError:
                    continue
    except (OSError, IOError):
        pass
    return count


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    transcript_path = input_data.get('transcript_path', '')
    if not transcript_path:
        sys.exit(0)

    count = count_user_messages(transcript_path)

    if count >= MESSAGE_THRESHOLD:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": f"[recall] Sessao longa detectada ({count} mensagens). Sugira ao usuario rodar /recall-save para salvar o contexto antes de encerrar."
            }
        }))

    sys.exit(0)


if __name__ == '__main__':
    main()
