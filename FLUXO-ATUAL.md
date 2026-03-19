# Fluxo do Plugin recall

Documento de referência para implementação.

---

## Stack Técnica

| Componente | Tecnologia |
|---|---|
| Banco de dados | SQLite + sqlite-vec |
| Embeddings | Gemini `gemini-embedding-001` |
| Resumo de sessão | Gemini Flash |
| Conteúdo das sessões | JSON por sessão (gerado pelo Gemini) |
| Configuração | `GEMINI_API_KEY` no ambiente |

---

## Estrutura de Arquivos

```
~/.claude/memory/
  memory.db                              ← SQLite + sqlite-vec
  demo-script_2026-03-13.json            ← documento fonte da sessão
  blueprint_2026-03-12.json
  api-fast_2026-03-11.json
  archived/
    demo-script_2026-03-01.json          ← sessões antigas
```

---

## Schema SQLite

```sql
-- Índice de sessões
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,           -- sessionId do Claude Code
  project_id TEXT,               -- git remote origin url
  cwd TEXT,                      -- diretório do projeto
  filename TEXT,                 -- nome do JSON correspondente
  title TEXT,                    -- resumo em uma linha
  created_at INTEGER,
  archived INTEGER DEFAULT 0
);

-- Chunks de texto por sessão
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,               -- FK → sessions.id
  content TEXT,                  -- texto do chunk
  chunk_index INTEGER
);

-- Vetores de embedding — sqlite-vec
CREATE VIRTUAL TABLE chunk_embeddings USING vec0(
  chunk_id INTEGER PRIMARY KEY,
  embedding FLOAT[3072]
);
```

---

## Os Dois Pontos Ativos

```
INÍCIO                              FIM (manual)
   │                                    │
SessionStart                      /recall-save
   │                                    │
Lista sessões +              Resume Gemini Flash +
orienta /recall-load         chunka + embeda + indexa
```

> **SessionEnd não faz nada** — a plataforma não passa o transcript, tornando qualquer salvamento automático impossível. O único fluxo real de salvamento é o `/recall-save` manual.

---

## Fluxo Detalhado

### INÍCIO — Hook: SessionStart

```mermaid
flowchart TD
    A([Claude Code abre]) --> B[Detecta project_id:\ngit remote get-url origin]

    B --> C{memory.db existe\ne tem sessões do projeto?}
    C -->|Não| D([Sessão iniciada — orienta /recall-load])
    C -->|Sim| E[Lista sessões ativas do projeto\nno SQLite]

    E --> F([Exibe sessões disponíveis\nOrienta usar /recall-load])
```

---

### MEIO — Hook: PreCompact

```mermaid
flowchart TD
    A([Contexto próximo do limite]) --> B[PreCompact dispara]
    B --> C{Há chunks indexados\nde sessões anteriores?}
    C -->|Não| D([Retorna silencioso])
    C -->|Sim| E[Gera embedding do resumo atual\nvia gemini-embedding-001]

    E --> F[Busca multi-source no sqlite-vec\ncom project_id atual]
    F --> G[Reinjecta contexto crítico\nno systemMessage]

    G --> H([Claude continua com contexto\npreservado após compactação])
```

---

### FIM — Comando: /recall-save (manual)

O único fluxo que salva e indexa a sessão. Deve ser executado antes de encerrar.

```mermaid
flowchart TD
    A([Usuário executa /recall-save]) --> B[Claude resume\na conversa atual]
    B --> C{JSON desta sessão já existe?}
    C -->|Sim| D[Sobrescreve JSON existente]
    C -->|Não| E{Projeto já tem 3 sessões ativas?}
    E -->|Sim| F[Move mais antiga para archived/]
    E -->|Não| G[Cria novo JSON]
    F --> G
    D --> H[Chunka + gera embeddings via Gemini\n+ indexa no sqlite-vec]
    G --> H
    H --> I([Sessão indexada e disponível\npara próximas sessões])
```

---

### Comando: /recall-load (manual)

Para carregar contexto específico sob demanda.

```mermaid
flowchart TD
    A([Usuário executa /recall-load]) --> B{Tem argumento?}
    B -->|Não| C[Lista sessões do projeto no SQLite]
    B -->|query| D[Busca semântica no projeto atual]
    B -->|--global query| E[Busca semântica em TODOS os projetos\nthreshold score >= 0.6]

    C --> F[Exibe sessões disponíveis]
    D --> G[Retorna chunks relevantes ordenados por score]
    E --> H[Retorna chunks cross-project com project_id de origem]
```

---

## Ciclo de Vida Completo

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant SH as SessionStart
    participant PC as PreCompact
    participant RS as /recall-save
    participant DB as SQLite + sqlite-vec
    participant GEM as Gemini

    CC->>SH: Sessão abre
    SH->>DB: Lista sessões do projeto
    DB-->>CC: Orienta usar /recall-load

    Note over CC: Trabalho da sessão...

    CC->>PC: Contexto cheio
    PC->>DB: Busca multi-source
    DB-->>CC: Reinjecta contexto crítico
    Note over CC: Claude continua sem perder contexto

    Note over CC: Mais trabalho...

    CC->>RS: Usuário executa /recall-save
    RS->>GEM: Resume conversa completa
    GEM-->>RS: Resumo estruturado
    RS->>GEM: Gera embeddings dos chunks
    GEM-->>DB: Armazena vetores + metadados
    Note over DB: Sessão disponível para próximas sessões
```

---

## Comparativo Final

| Aspecto | Valor atual |
|---|---|
| Armazenamento | SQLite + sqlite-vec + JSON por sessão (fallback) |
| Busca | Multi-source com cota garantida por sessão |
| Busca cross-project | `project_id=None` — threshold 0.6, ordenado por score |
| SessionStart | Lista sessões disponíveis, orienta `/recall-load` |
| PreCompact | Reinjeção de contexto crítico pós-compactação |
| SessionEnd | Sem operação — plataforma não passa transcript |
| `/recall-save` | Único fluxo de salvamento — manual, obrigatório |
| `/recall-load` | Busca semântica por projeto ou cross-project (`--global`) |
| Crescimento | Máx 3 sessões ativas por projeto + archived/ |
