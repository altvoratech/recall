# recall

> Persistent semantic memory for Claude Code — no more starting from scratch every session.

**recall** gives Claude Code long-term memory across sessions using local SQLite + hybrid search (FTS5 keyword + vector embeddings via RRF). Save a session, search it later — by project or across all your projects at once.

```
/recall-save        → summarize + index the current session
/recall-load        → search and restore previous context
/recall-load --global "next-devtools"  → search across all projects
```

---

## Why recall

Claude Code forgets everything when you close a session. recall solves this with a local-first pipeline:

1. `/recall-save` — Claude generates a structured summary, chunks it, and indexes embeddings + FTS5 locally in SQLite
2. `/recall-load` — hybrid search (semantic + keyword) over indexed sessions using Reciprocal Rank Fusion (RRF)
3. **PreCompact hook** — automatically reinjects critical context before Claude's context window compresses

No cloud storage. No external database. No API calls. Everything lives in `~/.claude/memory/`.

---

## Features

- **Fully local and offline** — SQLite + sqlite-vec + FTS5 + fastembed, zero API calls, zero cost
- **Hybrid search** — combines FTS5 keyword matching with vector cosine similarity via Reciprocal Rank Fusion (RRF) — exact terms AND semantic meaning
- **Semantic chunking** — chunks by logical section (decisions, tasks, concepts, notes) instead of fixed word count — each chunk is a coherent unit
- **Metadata filtering** — pre-filter by section type (`decisions`, `concepts`, etc.) or by date range (`days_back`) before vector search
- **Query expansion** — domain-specific synonyms (PT/EN) expand search terms automatically (e.g. "deploy api" → "deploy OR implantação OR deployment")
- **Recency re-ranking** — exponential decay boosts recent sessions (half-life: 14 days) — blended score: 80% similarity + 20% recency
- **Multi-source search** — guaranteed slots per session (inspired by NotebookLM) — prevents larger sessions from drowning out older ones
- **Cross-project search** — `/recall-load --global "query"` searches across all indexed projects with a relevance threshold
- **Local embeddings** — `BAAI/bge-small-en-v1.5` via fastembed (384 dimensions, ONNX runtime) — fast, free, no network required
- **Auto-rotation** — max 3 active sessions per project; older ones move to `archived/` automatically

---

## How it compares

| | recall | Supermemory | Claude Code native memory |
|--|--------|-------------|--------------------------|
| **Storage** | Local SQLite | Cloud (SaaS) | Local files |
| **Search** | Hybrid (semantic + keyword via RRF) | Semantic only | Manual / selective |
| **Chunking** | Semantic (by logical section) | Fixed-size | N/A |
| **Metadata filtering** | ✅ section type + date range | ❌ | ❌ |
| **Query expansion** | ✅ domain synonyms (PT/EN) | ❌ | ❌ |
| **Recency re-ranking** | ✅ exponential decay | ❌ | ❌ |
| **Cross-project** | ✅ `--global` flag | ✅ | ❌ |
| **API key required** | No | Yes (service) | No |
| **Cost** | Free | Paid plan | Free |
| **Data privacy** | 100% local | Sent to cloud | 100% local |
| **Network required** | No (fully offline) | Yes | No |
| **Claude Code integration** | Native (hooks + commands) | Via MCP | Native |
| **Automatic saving** | ✅ intentionally manual | ✅ | ❌ manual |
| **Embedding quality** | High (curated summaries) | Low (raw content) | N/A |

**recall vs. Supermemory** — Supermemory is a managed service with basic semantic search. recall goes further with a 5-stage search pipeline (query expansion → embedding → hybrid search → metadata pre-filter → recency re-rank), all running 100% locally with zero cost. Your data never leaves your machine.

**recall vs. Claude Code native memory** — Claude Code's built-in memory system is selective and manual: you explicitly tell Claude what to remember, and it writes structured notes. recall captures full session context, indexes it semantically, and lets you search across sessions and projects. They complement each other well — use native memory for permanent facts, recall for session history.

### Why manual saving is a feature, not a limitation

The `/recall-save` flow is intentionally manual. When you run it, **Claude itself summarizes the session** — extracting decisions, tasks, concepts, and notes into a structured JSON. This curated summary is then chunked by logical section and embedded.

This produces **dramatically richer embeddings** than auto-saving raw conversation transcripts. A raw transcript contains tool outputs, stack traces, code diffs, and verbose back-and-forth — noise that dilutes embedding quality. A curated summary like `"Decided to migrate from Gemini to fastembed for zero-cost local embeddings"` generates a focused, semantically dense vector that surfaces precisely when relevant.

Auto-save systems (like Supermemory) index everything — including noise. recall indexes only what matters, because the model already did the filtering for you.

---

## Architecture

```
~/.claude/memory/
├── memory.db          # SQLite + sqlite-vec (sessions, chunks, embeddings)
├── archived/          # Older sessions (> 3 per project)
└── project_date.json  # Structured JSON summary per session
```

### Session JSON vs. SQLite

`/recall-save` produces two independent artifacts:

| Artifact | Purpose |
|----------|---------|
| `project_date.json` | Human-readable structured summary (title, decisions, tasks, concepts). Loaded by `/recall-load` to display the session overview. |
| `memory.db` (SQLite) | Chunks + vector embeddings for semantic search. Required for multi-source search to work. |

The JSON is the **fallback**: if the database fails (sqlite-vec missing), the summary is still readable by `/recall-load`. Without chunks in the database, hybrid search won't return that content.

### Database schema (`memory.db`)

| Table | Description |
|-------|-------------|
| `sessions` | Session metadata (project_id, title, filename, created_at) |
| `chunks` | Text chunks per session with `section_type` metadata (decisions, concepts, tasks, notes) |
| `chunk_embeddings` | Embedding vectors via sqlite-vec (FLOAT[384]) |
| `chunks_fts` | FTS5 full-text index (external content table pointing to `chunks`) |

### Project identification

Uses `git remote get-url origin` as `project_id`. Fallback: repository root → current directory.

### Embedding model

Embeddings are generated locally using [`fastembed`](https://github.com/qdrant/fastembed) with the `BAAI/bge-small-en-v1.5` model (384 dimensions, ONNX runtime). No API key or network connection required.

The model is downloaded automatically on first use (~50MB) and cached locally.

---

## How orchestration works

This is the most important part to understand — and the most common source of confusion.

### The role of `hooks.json`

`hooks.json` is the contract with Claude Code. It declares which events trigger which scripts:

```
hooks.json → Claude Code reads → executes command (python3 script.py)
                                          ↓
                              outputs JSON with systemMessage
                                          ↓
                              Claude sees and acts
```

**Without `hooks.json` correctly configured, the Python scripts do nothing.** Claude Code has no knowledge of their existence.

### Two execution modes

| Type | How it works |
|------|--------------|
| **Automatic hooks** (`hooks.json`) | Claude Code executes the shell command on the event. The JSON output is injected as `systemMessage` into Claude's context. |
| **Manual commands** (`commands/*.md`) | User types `/recall-save`. Claude Code finds the matching `.md`, Claude reads the instructions and executes them using its own tools (Bash, Read, Write). |

In both cases, **Claude is the final executor** — either acting on the `systemMessage` received from the script, or reading the `.md` and executing directly.

### Timeouts matter

The `timeout` in `hooks.json` is how long Claude Code waits for a script to finish before silently canceling it. The timeout needs to be generous enough for embedding generation:

| Hook | Timeout | Reason |
|------|---------|--------|
| `SessionStart` | 10s | SQLite only, lightweight |
| `PreCompact` | 60s | Generates local embeddings + similarity search |

---

## Hooks

| Hook | Behavior |
|------|----------|
| `SessionStart` | Lists available sessions for the current project. Prompts to use `/recall-load`. Does not inject context automatically (avoids latency + cost). |
| `SessionEnd` | No-op. The only save flow is manual `/recall-save`. |
| `PreCompact` | Reinjects critical context via multi-source search before context window compression. |

> **Note**: `SessionEnd` does not receive the conversation transcript — platform limitation. This is why saving is only possible via the manual `/recall-save`.

---

## Commands

### `/recall-save [optional note]`

Saves the current session:
1. Claude analyzes the conversation and generates a structured summary (title, decisions, tasks, concepts, files, notes)
2. Saves JSON to `~/.claude/memory/project_date.json`
3. Chunks the summary and indexes embeddings locally in SQLite
4. Rotates sessions if needed (max 3 active)

> **Note:** The summary is generated by Claude itself. Embeddings are generated locally via fastembed — no external API calls.

### `/recall-load [number | query | --global query]`

Restores context from previous sessions:
- No argument: lists sessions for the current project
- Number: loads a specific session
- Text: semantic search in the current project
- `--global "query"`: semantic search across **all projects** (cross-project)

Runs **hybrid multi-source search**: for each session, combines FTS5 keyword matches with vector similarity via RRF, then returns the `top_k` most relevant chunks — ensuring all sessions contribute, not just the largest one.

In `--global` mode, results are sorted by score and filtered by a minimum threshold of `0.6` to avoid cross-project noise. Each result includes the source `project_id`.

---

## CLAUDE.md integration

To make Claude use the plugin automatically in any session without being instructed, add to `~/.claude/CLAUDE.md`:

```markdown
## Plugin: recall

Persistent memory across sessions via SQLite + local embeddings. Always available in any project.

**Commands:**
- `/recall-load` — list sessions for the current project
- `/recall-load 1` — load session by number
- `/recall-load "query"` — semantic search in the current project
- `/recall-load --global "query"` — semantic search across all projects
- `/recall-save` — save current session with summary + local embeddings

**When to use proactively:**
- At the start of a development session, if the user is resuming work, suggest `/recall-load`
- If the user mentions something that may have context in previous sessions, use `/recall-load --global "query"` to search

**Implementation path:**
~/.claude/plugins/cache/local/recall/1.0.0/hooks/db.py

Key functions: get_db(), init_db(), get_project_id(), get_active_sessions(), multi_source_search().
multi_source_search(conn, query, project_id=None, top_k_per_session=2) — hybrid search via RRF (FTS5 + cosine). project_id=None enables cross-project mode with score threshold 0.6.
```

This eliminates the need to explain the plugin at the start of every session.

**Tip — automatic context loading via CLAUDE.md:**

Claude Code always reads `CLAUDE.md` before starting any session. You can use this to make Claude run `/recall-load` automatically, without waiting for you to ask:

```markdown
At the start of each session, analyze the user's first message and run
/recall-load "query" with a 5-10 word semantic query derived from what
they are asking — before responding.
```

This bridges the gap between "session just opened" and "meaningful query available" — the reason SessionStart doesn't auto-inject context by default.

---

## Typical workflow

```
# Start of session
/recall-load          # see available sessions
/recall-load 1        # load the most recent

# ... work ...

# End of session
/recall-save          # save with summary + local embeddings
```

> **Why save manually?** `SessionEnd` does not receive the conversation transcript — platform limitation. Without the transcript, generating embeddings and indexing chunks is impossible. Run `/recall-save` **before closing the session**, while the context is still available.

> **The RAG filters for you:** no need to be selective about which sessions to save. Low-relevance conversations will score near zero against technical queries and simply won't appear. Save everything.

---

## Tests and metrics

Results from real session tests (2026-03-19), project `blue-new-layout` (Next.js 16):

### Embeddings and search

| Test | Result |
|------|--------|
| Local embedding generation with fastembed (`bge-small-en-v1.5`) | ✅ 384 dimensions per chunk, ~5ms per embedding |
| Multi-source search across multiple sessions | ✅ Correct semantic scoring, relevant sessions prioritized |
| Cross-project semantic search (`project_id=None`) | ✅ Returns chunks from all projects, sorted by score, filtered by threshold 0.6 |
| Migration from Gemini 3072d → local 384d | ✅ 27/27 chunks re-indexed, quality preserved |

### Hooks

| Hook | Test | Result |
|------|------|--------|
| `SessionStart` | Session listing for project | ✅ Sessions displayed correctly |
| `PreCompact` | Autocompact fired immediately after hook fix | ✅ Hook executed; returned silent (no previously indexed sessions — expected behavior) |
| `SessionEnd` | Hook removed — no-op | ✅ No ghost sessions in database |

### Confirmed limitations

| Limitation | Cause | Impact |
|------------|-------|--------|
| `PreCompact` does not inject context on first session | No previously indexed chunks — multi-source returns empty | Low — Claude Code's native context covers the current session |
| `SessionEnd` does not auto-index chunks | Platform does not pass transcript via stdin | None — fallback removed, `/recall-save` is the only flow |
| `SessionStart` does not auto-inject context | No search query available at session start | Low — intentional design; `/recall-load` with a specific query is more precise |

### Note on the RAG

Sessions with low technical relevance ("casual conversations") do not need to be manually excluded. In tests with a mixed database, irrelevant chunks scored near zero against technical queries — the embedding model naturally discards what isn't pertinent.

---

## Compatibility

| Platform | Status |
|----------|--------|
| Linux | ✅ Tested |
| macOS | ⚠️ Should work — same paths and shell config, not formally tested |
| Windows | ❌ Not supported — path conventions, shell config files, and `sqlite-vec` binaries differ |

Windows support would require at minimum: path handling via `pathlib` across all scripts and a verified `sqlite-vec` Windows build.

> Contributions for macOS validation and Windows support are welcome.

---

## Installation

### 1. Python dependencies

```bash
pip install sqlite-vec fastembed
```

> `fastembed` uses ONNX runtime for local inference — no PyTorch required. The embedding model (`BAAI/bge-small-en-v1.5`, ~50MB) is downloaded automatically on first use.

### 2. Place the plugin files

Claude Code executes plugins from the `cache/local/` directory, not `marketplaces/local/`. Copy the plugin there:

```bash
mkdir -p ~/.claude/plugins/cache/local/recall/1.0.0
cp -r /path/to/recall/. ~/.claude/plugins/cache/local/recall/1.0.0/
```

### 3. Register in the local marketplace

Claude Code needs a marketplace manifest to discover local plugins. Create or update `~/.claude/plugins/marketplaces/local/.claude-plugin/marketplace.json`:

```bash
mkdir -p ~/.claude/plugins/marketplaces/local/.claude-plugin
```

If the file doesn't exist yet, create it:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "local",
  "description": "Local plugins",
  "owner": { "name": "your-name", "email": "local@localhost" },
  "plugins": [
    {
      "name": "recall",
      "description": "Persistent semantic memory across Claude Code sessions",
      "version": "1.0.0",
      "author": { "name": "your-name" },
      "source": "./plugins/recall",
      "category": "development"
    }
  ]
}
```

If the file already exists, add the recall entry to the `plugins` array.

### 4. Enable in settings.json

Add `recall@local` to `enabledPlugins` in `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "recall@local": true
  }
}
```

> If you already have other plugins enabled, just add `"recall@local": true` to the existing object.

### 5. Verify

Restart Claude Code and run `/recall-load`. If it lists sessions (or says no sessions found for the project), the plugin is working.

If you see **"Plugin not found in marketplace"**, check that the `name` in `marketplace.json` matches exactly `recall` and that `source` points to the correct directory.

If you see **"Duplicate hooks file"**, your `plugin.json` has a `"hooks"` field pointing to `hooks.json`. Remove it — Claude Code loads `hooks/hooks.json` automatically and the explicit reference causes a conflict.

---

## File structure

```
recall/
├── plugin.json
├── README.md
├── LICENSE
├── FLUXO-ATUAL.md         # Detailed technical documentation
├── commands/
│   ├── recall-save.md
│   └── recall-load.md
├── hooks/
│   ├── hooks.json
│   ├── db.py              # Shared module: SQLite, FTS5, local embeddings (fastembed), hybrid search (RRF)
│   ├── session-start.py
│   ├── session-end.py
│   ├── pre-compact.py
│   ├── recall_save_cmd.py # Pipeline for /recall-save
│   └── migrate_to_local.py # One-shot migration from Gemini to local embeddings
```

---

## Debug mode

Hooks fail silently by design — errors should not interrupt the user's workflow. To investigate issues, enable debug mode:

```bash
export RECALL_DEBUG=1
```

Or add to `~/.profile` to persist across sessions. When active, all errors are logged to:

```
~/.claude/memory/debug.log
```

The log includes timestamp, hook source, and full traceback:

```
[2026-03-13 21:18:20] [session-start] Failed to list sessions
Traceback (most recent call last):
  ...
```

To clear the log: `rm ~/.claude/memory/debug.log`

---

## Known issues and solutions

| Problem | Cause | Solution |
|---------|-------|----------|
| Stale cache after editing marketplace | Claude Code uses `cache/local/` as the real source | Sync with `cp` after each edit |
| `SessionEnd` doesn't generate summary | Platform doesn't pass transcript via stdin | Hook removed — `/recall-save` is the only save flow |
| Multi-source returning only last session | Sessions without `/recall-save` have 0 chunks | Expected — `/recall-load` filters for sessions with real summaries |
| First embedding call slow (~2-3s) | fastembed model loading on first use | Subsequent calls are ~5ms. Model is cached in memory for the process lifetime |

---

## Search pipeline

The full search pipeline processes a query through five stages:

```
Query → Expansion → Embedding → Hybrid Search → Metadata Filter → Recency Re-rank
```

1. **Query expansion** — domain synonyms (PT/EN) expand search terms via OR groups
2. **Embedding** — fastembed generates 384-dim vector locally
3. **Hybrid search** — FTS5 keyword + sqlite-vec cosine, merged via RRF (k=60)
4. **Metadata pre-filter** — optional `section_types` and `days_back` filters applied before vector search
5. **Recency re-ranking** — blended score (80% similarity + 20% exponential decay, half-life 14 days) for cross-project ordering

### Semantic chunking

Sessions are chunked by logical section, not fixed word count:

| Section | What it captures |
|---------|-----------------|
| `decisions` | Architectural and technical decisions |
| `tasks_completed` | Work done in the session |
| `tasks_pending` | Open work items |
| `concepts` | Key technical concepts discussed |
| `files_modified` | Files changed |
| `notes` | Free-form context |

Each chunk is prefixed with the session title for embedding context.

---

## Roadmap

### Configurable embedding model

The plugin uses `BAAI/bge-small-en-v1.5` (384 dims) via fastembed by default. Future improvements:

- **Model selection** — allow users to choose between fastembed models (e.g., `nomic-embed-text-v1.5` for 768 dims, `BAAI/bge-large-en-v1.5` for higher quality)
- **Ollama integration** — support Ollama-hosted models as an alternative backend
- **Auto-migration** — detect dimension mismatch and re-index automatically when switching models

> The entry point is the `get_embedding()` function and `EMBEDDING_DIM` constant in `hooks/db.py`.

### Cloud storage backend (under analysis)

The current local-only architecture limits recall to a single machine. Session history indexed on your desktop is not available on your laptop or in CI/CD environments. A cloud storage backend would enable cross-device access to your full session memory.

Options being evaluated:

- **Supabase (pgvector)** — PostgreSQL with vector search, free tier available, integrates well with existing project infrastructure
- **Neon (pgvector)** — serverless Postgres with vector support, scales to zero
- **Qdrant Cloud** — managed vector database, purpose-built for similarity search

The goal is an optional sync layer: local SQLite remains the primary store (offline-first), with cloud as an opt-in replication target. Privacy-sensitive users keep everything local; others gain cross-device access.

> This is under analysis and not yet planned for implementation.

---

## License

MIT
