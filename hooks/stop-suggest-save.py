#!/usr/bin/env python3
"""
Stop hook — suggests /recall-save when conversation is long.
Counts user messages in the transcript. If above threshold,
injects additionalContext suggesting the user save the session.
"""

import json
import sys
from pathlib import Path


FIRST_THRESHOLD = 20
INCREMENT = 20
STATE_DIR = Path.home() / '.claude' / 'memory'


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


def get_next_threshold(session_id: str) -> int:
    """Read the next threshold for this session. Returns FIRST_THRESHOLD if no state."""
    state_file = STATE_DIR / f'.stop-threshold-{session_id}'
    try:
        return int(state_file.read_text().strip())
    except (OSError, ValueError):
        return FIRST_THRESHOLD


def bump_threshold(session_id: str, current: int) -> None:
    """Advance threshold so the next reminder fires INCREMENT messages later."""
    state_file = STATE_DIR / f'.stop-threshold-{session_id}'
    state_file.write_text(str(current + INCREMENT))


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    transcript_path = input_data.get('transcript_path', '')
    session_id = input_data.get('session_id', '')
    if not transcript_path or not session_id:
        sys.exit(0)

    count = count_user_messages(transcript_path)
    threshold = get_next_threshold(session_id)

    if count >= threshold:
        bump_threshold(session_id, count)
        print(json.dumps({
            "systemMessage": f"[recall] Sessao longa ({count} mensagens). Considere rodar /recall-save."
        }))

    sys.exit(0)


if __name__ == '__main__':
    main()
