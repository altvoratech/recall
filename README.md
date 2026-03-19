# recall

> Persistent semantic memory for Claude Code — no more starting from scratch every session.

**recall** gives Claude Code long-term memory across sessions using local SQLite + vector embeddings. Save a session, search it later — by project or across all your projects at once.

```
/recall-save        → summarize + index the current session
/recall-load        → search and restore previous context
/recall-load --global "next-devtools"  → search across all projects
```

---

## Why recall

Claude Code forgets everything when you close a session. recall solves this with a local-first pipeline:

1. `/recall-save` — generates a structured summary via Gemini Flash, chunks it, and indexes embeddings in SQLite
2. `/recall-load` — does semantic search over indexed sessions using cosine similarity
3. **PreCompact hook** — automatically reinjects critical context before Claude's context window compresses

No cloud storage. No external database. Everything lives in `~/.claude/memory/`.

---

## Features

- **Local and offline-first** — SQLite + sqlite-vec, zero external dependencies at runtime
- **Multi-source search** — guaranteed slots per session (inspired by NotebookLM) — prevents larger sessions from drowning out older ones
- **Cross-project search** — `/recall-load --global "query"` searches across all indexed projects with a relevance threshold
- **Gemini embeddings** — `gemini-embedding-001` (3072 dimensions) for high-quality semantic retrieval
- **Auto-rotation** — max 3 active sessions per project; older ones move to `archived/` automatically

---

## How it compares

| | recall | Supermemory | Claude Code native memory |
|--|--------|-------------|--------------------------|
| **Storage** | Local SQLite | Cloud (SaaS) | Local files |
| **Search** | Semantic (vector embeddings) | Semantic | Manual / selective |
| **Cross-project** | ✅ `--global` flag | ✅ | ❌ |
| **API key required** | Gemini (embeddings only) | Yes (service) | No |
| **Cost** | Free (Gemini free tier) | Paid plan | Free |
| **Data privacy** | 100% local | Sent to cloud | 100% local |
| **Claude Code integration** | Native (hooks + commands) | Via MCP | Native |
| **Automatic saving** | ❌ manual `/recall-save` | ✅ | ❌ manual |

**recall vs. Supermemory** — Supermemory is a managed service: easier to set up, but your data leaves your machine and you pay per usage. recall keeps everything local — the only external call is embedding generation via Gemini, which can be replaced with a local model (see Roadmap).

**recall vs. Claude Code native memory** — Claude Code's built-in memory system is selective and manual: you explicitly tell Claude what to remember, and it writes structured notes. recall captures full session context, indexes it semantically, and lets you search across sessions and projects. They complement each other well — use native memory for permanent facts, recall for session history.

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

The JSON is the **fallback**: if the database fails (Gemini API down, sqlite-vec missing), the summary is still readable by `/recall-load`. Without chunks in the database, semantic search won't return that content.

### Database schema (`memory.db`)

| Table | Description |
|-------|-------------|
| `sessions` | Session metadata (project_id, title, filename, created_at) |
| `chunks` | Text chunks per session for semantic search |
| `chunk_embeddings` | Embedding vectors via sqlite-vec (FLOAT[3072]) |

### Project identification

Uses `git remote get-url origin` as `project_id`. Fallback: repository root → current directory.

### GEMINI_API_KEY

`db.py` reads the key automatically in this order:
1. `GEMINI_API_KEY` environment variable
2. `~/.profile`, `~/.zshrc`, `~/.bashrc`, `~/.bash_profile`

No need to export manually in each session.

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

The `timeout` in `hooks.json` is how long Claude Code waits for a script to finish before silently canceling it. If the script calls an external API (Gemini), the timeout needs to be generous enough:

| Hook | Timeout | Reason |
|------|---------|--------|
| `SessionStart` | 10s | SQLite only, no network |
| `PreCompact` | 60s | Calls Gemini API for embeddings |

> Lesson learned: SessionStart was silently failing because the 10s timeout was too short when the script called Gemini. Fixing only the Python script didn't help — the bottleneck was in `hooks.json`.

---

## Hooks

| Hook | Behavior |
|------|----------|
| `SessionStart` | Lists available sessions for the current project. Prompts to use `/recall-load`. Does not inject context automatically (avoids latency + cost). |
| `SessionEnd` | No-op. The only save flow is manual `/recall-save`. |
| `PreCompact` | Reinjects critical context via multi-source search before context window compression. |

> **Note**: `SessionEnd` does not receive the conversation transcript — platform limitation. This is why Gemini Flash summarization is only possible via the manual `/recall-save`.

---

## Commands

### `/recall-save [optional note]`

Saves the current session:
1. Generates structured summary via Gemini Flash (title, decisions, tasks, concepts, files, notes)
2. Saves JSON to `~/.claude/memory/project_date.json`
3. Chunks + indexes embeddings in SQLite
4. Rotates sessions if needed (max 3 active)

### `/recall-load [number | query | --global query]`

Restores context from previous sessions:
- No argument: lists sessions for the current project
- Number: loads a specific session
- Text: semantic search in the current project
- `--global "query"`: semantic search across **all projects** (cross-project)

Runs **multi-source search**: for each session, returns the `top_k` most relevant chunks — ensuring all sessions contribute, not just the largest one.

In `--global` mode, results are sorted by score and filtered by a minimum threshold of `0.6` to avoid cross-project noise. Each result includes the source `project_id`.

---

## CLAUDE.md integration

To make Claude use the plugin automatically in any session without being instructed, add to `~/.claude/CLAUDE.md`:

```markdown
## Plugin: recall

Persistent memory across sessions via SQLite + Gemini embeddings. Always available in any project.

**Commands:**
- `/recall-load` — list sessions for the current project
- `/recall-load 1` — load session by number
- `/recall-load "query"` — semantic search in the current project
- `/recall-load --global "query"` — semantic search across all projects
- `/recall-save` — save current session with Gemini summary + embeddings

**When to use proactively:**
- At the start of a development session, if the user is resuming work, suggest `/recall-load`
- If the user mentions something that may have context in previous sessions, use `/recall-load --global "query"` to search

**Implementation path:**
~/.claude/plugins/cache/local/recall/1.0.0/hooks/db.py

Key functions: get_db(), init_db(), get_project_id(), get_active_sessions(), multi_source_search().
multi_source_search(conn, query, project_id=None, top_k_per_session=2) — project_id=None enables cross-project mode with score threshold 0.6.
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
/recall-save          # save with Gemini summary
```

> **Why save manually?** `SessionEnd` does not receive the conversation transcript — platform limitation. Without the transcript, generating embeddings and indexing chunks is impossible. Run `/recall-save` **before closing the session**, while the context is still available.

> **The RAG filters for you:** no need to be selective about which sessions to save. Low-relevance conversations will score near zero against technical queries and simply won't appear. Save everything.

---

## Tests and metrics

Results from real session tests (2026-03-19), project `blue-new-layout` (Next.js 16):

### Embeddings and search

| Test | Result |
|------|--------|
| Reading `GEMINI_API_KEY` from shell config (`~/.zshrc`) | ✅ Worked without manual export |
| Embedding generation with `gemini-embedding-001` | ✅ 3072 dimensions per chunk |
| Multi-source search across multiple sessions | ✅ Correct semantic scoring, relevant sessions prioritized |
| Cross-project semantic search (`project_id=None`) | ✅ Returns chunks from all projects, sorted by score, filtered by threshold 0.6 |

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

## Installation

### Python dependencies

```bash
pip install sqlite-vec google-genai
```

### GEMINI_API_KEY

Add to `~/.profile` (available in all shells, including hooks):

```bash
export GEMINI_API_KEY=your_key_here
```

### Plugin registration

The plugin must be placed at:
```
~/.claude/plugins/cache/local/recall/1.0.0/
```

> Claude Code executes plugins from the `cache/local/` directory, not `marketplaces/local/`. After editing files in the marketplace, sync manually with `cp`.

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
│   ├── db.py              # Shared module: SQLite, embeddings, multi-source search
│   ├── session-start.py
│   ├── session-end.py
│   ├── pre-compact.py
│   └── recall_save_cmd.py # Pipeline for /recall-save
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
| `GEMINI_API_KEY` not available in hooks | Hooks run in non-interactive shell (doesn't load `~/.zshrc`) | `db.py` reads the key directly from config files |
| Stale cache after editing marketplace | Claude Code uses `cache/local/` as the real source | Sync with `cp` after each edit |
| `SessionEnd` doesn't generate summary | Platform doesn't pass transcript via stdin | Hook removed — `/recall-save` is the only save flow |
| `SessionStart` failing silently | 10s timeout too short when script called Gemini | Script simplified (SQLite only) + timeout calibrated per hook in `hooks.json` |
| Multi-source returning only last session | Sessions without `/recall-save` have 0 chunks | Expected — `/recall-load` filters for sessions with real summaries |

---

## Roadmap

### Local embedding model

The plugin currently depends on the Gemini API for embedding generation — the only component that requires network and an API key. The long-term goal is to support local models as an alternative:

- **[`nomic-embed-text`](https://ollama.com/library/nomic-embed-text)** via Ollama — 768 dimensions, fully offline
- **[`mxbai-embed-large`](https://ollama.com/library/mxbai-embed-large)** via Ollama — 1024 dimensions, better quality
- **`sentence-transformers`** via Python directly — no server dependency

The switch would be configurable in `db.py`, keeping the `get_embedding()` interface identical. The SQLite database already supports any dimension via `sqlite-vec` — the only change would be re-indexing existing sessions when switching models.

### API key configuration without system path

Currently `GEMINI_API_KEY` is read directly from shell config files (`~/.profile`, `~/.zshrc`, etc.) — functional but coupled to the filesystem. The goal is to support more portable configuration options:

- **Plugin config file** — e.g. `~/.claude/recall.json` with `{ "gemini_api_key": "..." }`
- **Claude Code settings variable** — configure the key directly in Claude Code's `settings.json`
- **Interactive prompt** — request the key on first run and store it securely

> Contributions welcome. The entry point is the `_get_gemini_api_key()` function in `hooks/db.py`.

---

## License

MIT
