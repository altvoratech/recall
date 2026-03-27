#!/usr/bin/env python3
"""
Shared database utilities for persistent-context plugin.
SQLite + sqlite-vec + FTS5 hybrid search + local embeddings (fastembed).
"""

import json
import os
import re
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

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5 via fastembed


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
    """Creates tables if they don't exist, including FTS5 for hybrid search."""
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
            section_type TEXT DEFAULT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding FLOAT[384]
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content,
            content=chunks,
            content_rowid=id
        );
    """)
    conn.commit()

    # Migration: add section_type column if missing (existing DBs)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()]
        if 'section_type' not in cols:
            conn.execute("ALTER TABLE chunks ADD COLUMN section_type TEXT DEFAULT NULL")
            conn.commit()
            debug_log('init_db', 'Added section_type column to chunks table')
    except Exception as e:
        debug_log('init_db', 'section_type migration failed', e)

    # Populate FTS5 index from existing chunks (one-time migration)
    try:
        count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        if count > 0 and fts_count == 0:
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            conn.commit()
            debug_log('init_db', f'FTS5 index rebuilt for {count} existing chunks')
    except Exception as e:
        debug_log('init_db', 'FTS5 rebuild failed', e)


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


# ─── Embeddings (local via fastembed) ────────────────────────────────────────

_embedding_model = None

def _get_model():
    """Lazy-loads the fastembed model (singleton)."""
    global _embedding_model
    if _embedding_model is None:
        from fastembed import TextEmbedding
        _embedding_model = TextEmbedding('BAAI/bge-small-en-v1.5')
    return _embedding_model


def get_embedding(text: str) -> list[float]:
    """Generates embedding locally via fastembed (BAAI/bge-small-en-v1.5, 384 dims)."""
    model = _get_model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist()


def serialize_vector(v: list[float]) -> bytes:
    return struct.pack(f'{len(v)}f', *v)


# ─── FTS5 helpers ─────────────────────────────────────────────────────────────

def _sanitize_fts_query(query: str) -> str:
    """Converts a natural language query into a safe FTS5 query string."""
    words = re.findall(r'\w+', query)
    return ' '.join(words) if words else ''


# ─── Query transformation ─────────────────────────────────────────────────────

# Domain-specific synonyms for dev/programming context
_SYNONYMS = {
    'api': ['endpoint', 'rota', 'route'],
    'endpoint': ['api', 'rota', 'route'],
    'banco': ['database', 'db', 'sqlite', 'postgres'],
    'database': ['banco', 'db'],
    'db': ['banco', 'database'],
    'deploy': ['implantação', 'deployment', 'vps'],
    'bug': ['erro', 'error', 'fix'],
    'erro': ['bug', 'error', 'fix'],
    'auth': ['autenticação', 'authentication', 'login'],
    'login': ['auth', 'autenticação'],
    'ui': ['interface', 'frontend', 'componente'],
    'frontend': ['ui', 'interface', 'client'],
    'backend': ['server', 'api', 'fastapi'],
    'test': ['teste', 'testing'],
    'teste': ['test', 'testing'],
    'refactor': ['refatoração', 'refatorar', 'cleanup'],
    'migration': ['migração', 'migrate'],
    'migração': ['migration', 'migrate'],
    'embedding': ['embeddings', 'vetor', 'vector'],
    'rag': ['retrieval', 'busca', 'search'],
    'hook': ['hooks', 'evento', 'event'],
    'plugin': ['plugins', 'extensão'],
    'skill': ['skills', 'comando', 'command'],
}


def _expand_query(query: str, max_expansions: int = 3) -> str:
    """Expands query with domain-specific synonyms for better FTS5 recall.

    Adds OR-based synonym expansion to the sanitized query.
    Limited to max_expansions to avoid query bloat.
    """
    words = re.findall(r'\w+', query.lower())
    if not words:
        return ''

    expanded_parts = []
    expansions_added = 0

    for word in words:
        if word in _SYNONYMS and expansions_added < max_expansions:
            # OR group: (original OR synonym1 OR synonym2)
            synonyms = _SYNONYMS[word][:2]  # max 2 synonyms per word
            group = ' OR '.join([word] + synonyms)
            expanded_parts.append(f'({group})')
            expansions_added += 1
        else:
            expanded_parts.append(word)

    return ' '.join(expanded_parts)


# ─── Recency boost ────────────────────────────────────────────────────────────

def _recency_boost(created_at: int, half_life_days: int = 14) -> float:
    """Exponential decay boost based on session age.

    Returns a value between 0 and 1:
      - 1.0 for sessions created now
      - 0.5 for sessions created half_life_days ago
      - ~0.25 for sessions 2x half_life_days ago

    Uses: boost = 2^(-age_days / half_life_days)
    """
    import math
    if not created_at:
        return 0.5
    now = datetime.now(timezone.utc).timestamp()
    age_days = max(0, (now - created_at) / 86400)
    return math.pow(2, -age_days / half_life_days)


def _fts_search(
    conn: sqlite3.Connection,
    query: str,
    session_id: str,
    limit: int
) -> list[dict]:
    """FTS5 keyword search within a single session."""
    try:
        rows = conn.execute("""
            SELECT c.id AS chunk_id, c.content, chunks_fts.rank
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ? AND c.session_id = ?
            ORDER BY chunks_fts.rank
            LIMIT ?
        """, (query, session_id, limit)).fetchall()
        return [{'chunk_id': r['chunk_id'], 'content': r['content'], 'rank': r['rank']} for r in rows]
    except Exception:
        return []


# ─── Chunking ────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Splits text into overlapping word-based chunks. Fallback for unstructured text."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def chunk_structured(summary: dict, chunk_size: int = 500) -> list[tuple[str, str]]:
    """Semantic chunking for structured session summaries.

    Creates chunks respecting logical boundaries (decisions, tasks, concepts, notes).
    Each chunk is prefixed with the session title for context.
    Falls back to word-based splitting if a section exceeds chunk_size.

    Returns list of (text, section_type) tuples.
    """
    title = summary.get('title', '')
    sections = {
        'decisions': summary.get('decisions', []),
        'tasks_completed': summary.get('tasks_completed', []),
        'tasks_pending': summary.get('tasks_pending', []),
        'files_modified': summary.get('files_modified', []),
        'concepts': summary.get('concepts', []),
    }
    notes = summary.get('notes', '')

    chunks = []

    for section_name, items in sections.items():
        if not items:
            continue
        section_text = f"{title}\n[{section_name}] " + ' '.join(items)
        words = section_text.split()
        if len(words) <= chunk_size:
            chunks.append((section_text, section_name))
        else:
            i = 0
            while i < len(words):
                chunk = ' '.join(words[i:i + chunk_size])
                chunks.append((chunk, section_name))
                i += chunk_size - 50

    if notes and notes.strip():
        notes_text = f"{title}\n[notes] {notes}"
        words = notes_text.split()
        if len(words) <= chunk_size:
            chunks.append((notes_text, 'notes'))
        else:
            i = 0
            while i < len(words):
                chunk = ' '.join(words[i:i + chunk_size])
                chunks.append((chunk, 'notes'))
                i += chunk_size - 50

    # Fallback: if no structured sections produced chunks, use full text
    if not chunks:
        full_text = f"{title}\n{notes}"
        return [(c, 'unknown') for c in chunk_text(full_text, chunk_size)]

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


def index_chunks(conn: sqlite3.Connection, session_id: str, text: str, precomputed_chunks=None):
    """Chunks text, generates embeddings, stores in sqlite-vec and FTS5.

    precomputed_chunks can be:
      - list[str]: plain text chunks (backward compatible)
      - list[tuple[str, str]]: (text, section_type) from chunk_structured
      - None: falls back to chunk_text(text)
    """
    # Remove chunks anteriores desta sessão
    old_chunks = conn.execute(
        "SELECT id, content FROM chunks WHERE session_id = ?", (session_id,)
    ).fetchall()
    for c in old_chunks:
        conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (c['id'],))
        try:
            conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', ?, ?)",
                (c['id'], c['content'])
            )
        except Exception:
            pass
    conn.execute("DELETE FROM chunks WHERE session_id = ?", (session_id,))
    conn.commit()

    # Normalize chunks to (text, section_type) tuples
    if precomputed_chunks is None:
        raw_chunks = [(c, None) for c in chunk_text(text)]
    elif precomputed_chunks and isinstance(precomputed_chunks[0], tuple):
        raw_chunks = precomputed_chunks
    else:
        raw_chunks = [(c, None) for c in precomputed_chunks]

    for i, (chunk_content, section_type) in enumerate(raw_chunks):
        cursor = conn.execute(
            "INSERT INTO chunks (session_id, content, chunk_index, section_type) VALUES (?, ?, ?, ?)",
            (session_id, chunk_content, i, section_type)
        )
        chunk_id = cursor.lastrowid
        embedding = get_embedding(chunk_content)
        conn.execute(
            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, serialize_vector(embedding))
        )
        try:
            conn.execute(
                "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
                (chunk_id, chunk_content)
            )
        except Exception:
            pass
    conn.commit()


# ─── Multi-source search ─────────────────────────────────────────────────────

def multi_source_search(
    conn: sqlite3.Connection,
    query: str,
    project_id: Optional[str],
    top_k_per_session: int = 2,
    cross_project_min_score: float = 0.6,
    section_types: list[str] = None,
    days_back: int = None
) -> list[dict]:
    """
    Hybrid multi-source search: FTS5 (keyword) + sqlite-vec (semantic).
    Uses Reciprocal Rank Fusion (RRF) to rank, cosine similarity as score.

    Metadata filters (pre-filtering):
      - section_types: filter chunks by type (e.g. ['decisions', 'concepts'])
      - days_back: only search sessions from the last N days

    If project_id is None, searches across ALL projects (cross-project mode)
    and filters by cross_project_min_score to avoid low-relevance noise.
    Results are sorted by score descending in cross-project mode.
    """
    query_embedding = get_embedding(query)
    query_vec = serialize_vector(query_embedding)
    fts_query = _expand_query(query) or _sanitize_fts_query(query)

    cross_project = project_id is None
    sessions = get_active_sessions(conn, project_id)

    # Pre-filter sessions by date
    if days_back is not None:
        cutoff = int((datetime.now(timezone.utc).timestamp())) - (days_back * 86400)
        sessions = [s for s in sessions if s['created_at'] and s['created_at'] >= cutoff]

    results = []

    fetch_k = max(top_k_per_session * 3, 6)
    RRF_K = 60

    # Build section_type SQL filter for pre-filtering
    type_filter = ""
    type_params = []
    if section_types:
        placeholders = ','.join('?' for _ in section_types)
        type_filter = f" AND c.section_type IN ({placeholders})"
        type_params = list(section_types)

    for session in sessions:
        # --- Vector search (with optional section_type pre-filter) ---
        vec_sql = f"""
            SELECT c.id, c.content, c.session_id, c.section_type,
                   vec_distance_cosine(ce.embedding, ?) AS distance
            FROM chunk_embeddings ce
            JOIN chunks c ON ce.chunk_id = c.id
            WHERE c.session_id = ?{type_filter}
            ORDER BY distance ASC
            LIMIT ?
        """
        vec_params = [query_vec, session['id']] + type_params + [fetch_k]
        vec_rows = conn.execute(vec_sql, vec_params).fetchall()

        # Build candidates from vector results
        candidates = {}
        for rank_pos, r in enumerate(vec_rows):
            candidates[r['id']] = {
                'content': r['content'],
                'session_id': r['session_id'],
                'section_type': r['section_type'],
                'cosine_score': 1.0 - r['distance'],
                'rrf': 1.0 / (RRF_K + rank_pos + 1),
            }

        # --- FTS5 search ---
        if fts_query:
            fts_rows = _fts_search(conn, fts_query, session['id'], fetch_k)
            for rank_pos, fr in enumerate(fts_rows):
                cid = fr['chunk_id']
                # Pre-filter FTS results by section_type if needed
                if section_types:
                    row_type = conn.execute(
                        "SELECT section_type FROM chunks WHERE id = ?", (cid,)
                    ).fetchone()
                    if row_type and row_type['section_type'] not in section_types:
                        continue
                rrf_add = 1.0 / (RRF_K + rank_pos + 1)
                if cid in candidates:
                    candidates[cid]['rrf'] += rrf_add
                else:
                    # FTS-only hit: compute actual cosine score
                    try:
                        dist_row = conn.execute("""
                            SELECT vec_distance_cosine(ce.embedding, ?) AS distance
                            FROM chunk_embeddings ce WHERE ce.chunk_id = ?
                        """, (query_vec, cid)).fetchone()
                        cosine_score = 1.0 - dist_row['distance'] if dist_row else 0.0
                    except Exception:
                        cosine_score = 0.0
                    candidates[cid] = {
                        'content': fr['content'],
                        'session_id': session['id'],
                        'section_type': None,
                        'cosine_score': cosine_score,
                        'rrf': rrf_add,
                    }

        # --- Rank by RRF, take top_k_per_session ---
        sorted_candidates = sorted(
            candidates.values(), key=lambda x: x['rrf'], reverse=True
        )

        recency = _recency_boost(session['created_at'])

        for c in sorted_candidates[:top_k_per_session]:
            score = c['cosine_score']
            if cross_project and score < cross_project_min_score:
                continue
            # Blended score: 80% similarity + 20% recency
            blended = (score * 0.8) + (recency * 0.2)
            results.append({
                'session_id': c['session_id'],
                'content': c['content'],
                'score': score,
                'blended_score': blended,
                'recency': recency,
                'section_type': c.get('section_type'),
                'title': session['title'],
                'project_id': session['project_id'],
            })

    if cross_project:
        results.sort(key=lambda r: r['blended_score'], reverse=True)

    return results


# ─── Gemini summary (legacy, não usado ativamente) ──────────────────────────

def _get_gemini_api_key() -> Optional[str]:
    """Gets GEMINI_API_KEY from env or shell config files."""
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        return key
    for config_file in ['~/.profile', '~/.zshrc', '~/.bashrc', '~/.bash_profile']:
        path = Path(os.path.expanduser(config_file))
        if path.exists():
            try:
                for line in path.read_text().splitlines():
                    line = line.strip()
                    if 'GEMINI_API_KEY' in line and '=' in line:
                        value = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if value and not value.startswith('$'):
                            return value
            except Exception:
                pass
    return None


def summarize_session(conversation: str, project_id: str) -> dict:
    """Uses Gemini Flash to summarize a session into structured JSON (legacy, não usado)."""
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
