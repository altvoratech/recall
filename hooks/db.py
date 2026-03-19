#!/usr/bin/env python3
"""
Shared database utilities for persistent-context plugin.
SQLite + sqlite-vec + Gemini embeddings.
"""

import json
import os
import sqlite3
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sqlite_vec

# ─── Paths ───────────────────────────────────────────────────────────────────

MEMORY_DIR = Path(os.environ.get('HOME', os.path.expanduser('~'))) / '.claude' / 'memory'
ARCHIVE_DIR = MEMORY_DIR / 'archived'
DB_PATH = MEMORY_DIR / 'memory.db'
LOG_PATH = MEMORY_DIR / 'debug.log'

EMBEDDING_DIM = 3072  # gemini-embedding-001


# ─── Debug logger ────────────────────────────────────────────────────────────

def debug_log(source: str, message: str, exc: Exception = None):
    """Logs to debug.log when RECALL_DEBUG=1."""
    if not os.environ.get('RECALL_DEBUG'):
        # Tenta ler dos arquivos de config como fazemos com GEMINI_API_KEY
        for config_file in ['~/.profile', '~/.zshrc', '~/.bashrc']:
            path = Path(os.path.expanduser(config_file))
            if path.exists():
                try:
                    for line in path.read_text().splitlines():
                        if 'RECALL_DEBUG' in line and '=' in line:
                            value = line.split('=', 1)[1].strip().strip('"').strip("'")
                            if value == '1':
                                break
                    else:
                        continue
                    break
                except Exception:
                    pass
        else:
            return

    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        lines = [f"[{timestamp}] [{source}] {message}"]
        if exc:
            import traceback
            lines.append(traceback.format_exc())
        with LOG_PATH.open('a') as f:
            f.write('\n'.join(lines) + '\n')
    except Exception:
        pass


# ─── Database ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Returns a connection with sqlite-vec loaded."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    """Creates tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            cwd TEXT,
            filename TEXT,
            title TEXT,
            created_at INTEGER,
            archived INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            content TEXT,
            chunk_index INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding FLOAT[3072]
        );
    """)
    conn.commit()


# ─── Project ID ──────────────────────────────────────────────────────────────

def get_project_id(cwd: str = None) -> Optional[str]:
    """Gets project identifier from git remote or fallback to repo root."""
    try:
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            capture_output=True, text=True,
            cwd=cwd or os.getcwd(), timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True,
            cwd=cwd or os.getcwd(), timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return cwd or os.getcwd()


# ─── Embeddings ──────────────────────────────────────────────────────────────

def _get_gemini_api_key() -> Optional[str]:
    """Gets GEMINI_API_KEY from env or shell config files."""
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        return key

    # Try reading from shell config files (hooks run in non-login shells)
    for config_file in ['~/.profile', '~/.zshrc', '~/.bashrc', '~/.bash_profile']:
        path = Path(os.path.expanduser(config_file))
        if path.exists():
            try:
                for line in path.read_text().splitlines():
                    line = line.strip()
                    if 'GEMINI_API_KEY' in line and '=' in line:
                        # Handles: export GEMINI_API_KEY=xxx or GEMINI_API_KEY=xxx
                        value = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if value and not value.startswith('$'):
                            return value
            except Exception:
                pass

    return None


def get_embedding(text: str) -> list[float]:
    """Generates embedding via Gemini gemini-embedding-001."""
    from google import genai

    api_key = _get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    response = client.models.embed_content(
        model='gemini-embedding-001',
        contents=text,
        config={'task_type': 'RETRIEVAL_QUERY'}
    )
    return response.embeddings[0].values


def serialize_vector(v: list[float]) -> bytes:
    return struct.pack(f'{len(v)}f', *v)


# ─── Chunking ────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Splits text into overlapping chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


# ─── Session operations ──────────────────────────────────────────────────────

def get_active_sessions(conn: sqlite3.Connection, project_id: Optional[str]) -> list[sqlite3.Row]:
    """Returns active sessions. If project_id is None, returns all active sessions (cross-project)."""
    if project_id is None:
        return conn.execute(
            "SELECT * FROM sessions WHERE archived = 0 ORDER BY created_at DESC"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM sessions WHERE project_id = ? AND archived = 0 ORDER BY created_at DESC",
        (project_id,)
    ).fetchall()


def rotate_sessions(conn: sqlite3.Connection, project_id: str, max_sessions: int = 3):
    """Ensures max_sessions active per project — moves oldest to archived/."""
    sessions = get_active_sessions(conn, project_id)
    while len(sessions) >= max_sessions:
        oldest = sessions[-1]
        src = MEMORY_DIR / oldest['filename']
        if src.exists():
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            src.rename(ARCHIVE_DIR / oldest['filename'])
        conn.execute(
            "UPDATE sessions SET archived = 1 WHERE id = ?",
            (oldest['id'],)
        )
        conn.commit()
        sessions = get_active_sessions(conn, project_id)


def save_session_metadata(
    conn: sqlite3.Connection,
    session_id: str,
    project_id: str,
    cwd: str,
    filename: str,
    title: str
):
    conn.execute("""
        INSERT INTO sessions (id, project_id, cwd, filename, title, created_at, archived)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(id) DO UPDATE SET
            filename = excluded.filename,
            title = excluded.title
    """, (session_id, project_id, cwd, filename, title, int(datetime.now(timezone.utc).timestamp())))
    conn.commit()


def index_chunks(conn: sqlite3.Connection, session_id: str, text: str):
    """Chunks text, generates embeddings, stores in sqlite-vec."""
    # Remove chunks anteriores desta sessão
    old_chunks = conn.execute(
        "SELECT id FROM chunks WHERE session_id = ?", (session_id,)
    ).fetchall()
    for c in old_chunks:
        conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (c['id'],))
    conn.execute("DELETE FROM chunks WHERE session_id = ?", (session_id,))
    conn.commit()

    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        cursor = conn.execute(
            "INSERT INTO chunks (session_id, content, chunk_index) VALUES (?, ?, ?)",
            (session_id, chunk, i)
        )
        chunk_id = cursor.lastrowid
        embedding = get_embedding(chunk)
        conn.execute(
            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, serialize_vector(embedding))
        )
    conn.commit()


# ─── Multi-source search ─────────────────────────────────────────────────────

def multi_source_search(
    conn: sqlite3.Connection,
    query: str,
    project_id: Optional[str],
    top_k_per_session: int = 2,
    cross_project_min_score: float = 0.6
) -> list[dict]:
    """
    Multi-source search with guaranteed slots per session.
    Inspired by NotebookLLM approach.

    If project_id is None, searches across ALL projects (cross-project mode)
    and filters by cross_project_min_score to avoid low-relevance noise.
    Results are sorted by score descending in cross-project mode.
    """
    query_embedding = get_embedding(query)
    query_vec = serialize_vector(query_embedding)

    cross_project = project_id is None
    sessions = get_active_sessions(conn, project_id)
    results = []

    for session in sessions:
        rows = conn.execute("""
            SELECT c.content, c.session_id,
                   vec_distance_cosine(ce.embedding, ?) AS distance
            FROM chunk_embeddings ce
            JOIN chunks c ON ce.chunk_id = c.id
            WHERE c.session_id = ?
            ORDER BY distance ASC
            LIMIT ?
        """, (query_vec, session['id'], top_k_per_session)).fetchall()

        for row in rows:
            score = 1 - row['distance']
            if cross_project and score < cross_project_min_score:
                continue
            results.append({
                'session_id': row['session_id'],
                'content': row['content'],
                'score': score,
                'title': session['title'],
                'project_id': session['project_id'],
            })

    if cross_project:
        results.sort(key=lambda r: r['score'], reverse=True)

    return results


# ─── Gemini summary ──────────────────────────────────────────────────────────

def summarize_session(conversation: str, project_id: str) -> dict:
    """Uses Gemini Flash to summarize a session into structured JSON."""
    from google import genai

    api_key = _get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    prompt = f"""Você é um assistente que resume sessões de desenvolvimento de software.

Analise a conversa abaixo e gere um resumo estruturado em JSON.

Projeto: {project_id}

CONVERSA:
{conversation[:8000]}

Gere um JSON com esta estrutura mínima obrigatória:
{{
  "title": "resumo em uma linha do que foi feito",
  "decisions": ["decisão 1", "decisão 2"],
  "tasks_pending": ["tarefa pendente 1"],
  "tasks_completed": ["tarefa concluída 1"],
  "files_modified": ["caminho/arquivo.py"],
  "concepts": ["conceito importante discutido"],
  "notes": "contexto adicional relevante para sessões futuras"
}}

Retorne APENAS o JSON, sem texto adicional."""

    response = client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt
    )

    text = response.text.strip()
    # Remove markdown code blocks se presentes
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]

    return json.loads(text)
