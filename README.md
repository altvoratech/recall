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

## Arquitetura

```
~/.claude/memory/
├── memory.db          # SQLite com sqlite-vec (sessões + chunks + embeddings)
├── archived/          # Sessões antigas (> 3 por projeto)
└── projeto_data.json  # JSON por sessão com resumo estruturado
```

### JSON por sessão vs. banco SQLite

O `/recall-save` produz **dois artefatos independentes**:

| Artefato | Função |
|----------|--------|
| `projeto_data.json` | Resumo estruturado legível por humanos (título, decisões, tarefas, conceitos). Carregado pelo `/recall-load` para exibir o sumário da sessão. |
| `memory.db` (SQLite) | Chunks + embeddings vetoriais para busca semântica. Necessário para o multi-source search funcionar. |

O JSON é o **fallback**: se o banco falhar (Gemini API fora, sqlite-vec ausente), o resumo ainda é legível e carregável pelo `/recall-load`. Mas sem chunks no banco, a busca semântica não retorna esse conteúdo.

### Banco de dados (`memory.db`)

| Tabela | Descrição |
|--------|-----------|
| `sessions` | Metadados de cada sessão (project_id, título, filename, created_at) |
| `chunks` | Trechos de texto das sessões para busca semântica |
| `chunk_embeddings` | Vetores de embedding via sqlite-vec (FLOAT[3072]) |

### Identificação de projeto

Usa `git remote get-url origin` como `project_id`. Fallback: raiz do repositório → diretório atual.

### GEMINI_API_KEY

O `db.py` lê a key automaticamente na ordem:
1. Variável de ambiente `GEMINI_API_KEY`
2. `~/.profile`, `~/.zshrc`, `~/.bashrc`, `~/.bash_profile`

Não é necessário exportar manualmente em cada sessão.

## Como a orquestração funciona

Este é o ponto mais importante para entender o plugin — e o que mais causa confusão.

### O papel do `hooks.json`

O `hooks.json` é o contrato com o Claude Code. Ele declara quais eventos disparam quais scripts:

```
hooks.json → Claude Code lê → executa o comando (python3 script.py)
                                      ↓
                          output JSON com systemMessage
                                      ↓
                          Claude vê e age
```

**Sem o `hooks.json` corretamente configurado, os scripts Python não fazem nada.** O Claude Code não sabe da existência deles.

### Dois tipos de execução

| Tipo | Como funciona |
|------|---------------|
| **Hooks automáticos** (`hooks.json`) | Claude Code executa o shell command no evento. O output JSON é injetado como `systemMessage` no contexto do Claude. |
| **Comandos manuais** (`commands/*.md`) | O usuário digita `/recall-save`. Claude Code encontra o `.md` correspondente, Claude lê as instruções e as executa usando suas próprias ferramentas (Bash, Read, Write). |

Em ambos os casos, **Claude é o executor final** — ou age sobre o `systemMessage` recebido do script, ou lê o `.md` e executa diretamente.

### Timeouts importam

O `timeout` no `hooks.json` é o tempo que o Claude Code aguarda o script terminar antes de cancelar silenciosamente. Se o script chama uma API externa (Gemini), o timeout precisa ser generoso o suficiente:

| Hook | Timeout | Motivo |
|------|---------|--------|
| `SessionStart` | 10s | Só lê SQLite, sem rede |
| `PreCompact` | 60s | Chama Gemini API para embeddings |

> Lição aprendida: o SessionStart ficou quebrando silenciosamente porque o timeout era 10s e o script chamava Gemini. Mexer só no script Python não resolvia — o gargalo estava no `hooks.json`.

## Hooks

| Hook | Comportamento |
|------|---------------|
| `SessionStart` | Lista sessões disponíveis do projeto atual. Orienta a usar `/recall-load`. Não injeta contexto automaticamente (evita latência + custo). |
| `SessionEnd` | Sem operação. O único fluxo de salvamento é o `/recall-save` manual. |
| `PreCompact` | Salva checkpoint + reinjeção de contexto crítico via multi-source antes da compactação. |

> **Importante**: `SessionEnd` não recebe o transcript do Claude Code — limitação da plataforma. Por isso, o resumo com Gemini Flash só é gerado pelo `/recall-save` manual.

## Comandos

### `/recall-save [nota opcional]`

Salva a sessão atual:
1. Gera resumo estruturado via Gemini Flash (título, decisões, tarefas, conceitos, arquivos, notas)
2. Salva JSON em `~/.claude/memory/projeto_data.json`
3. Indexa chunks com embeddings no SQLite
4. Rotaciona sessões se necessário (máx. 3 ativas)

### `/recall-load [número | query | --global query]`

Recupera contexto de sessões anteriores:
- Sem argumento: lista sessões do projeto atual
- Com número: carrega sessão específica
- Com texto: busca semântica no projeto atual
- `--global "query"`: busca semântica em **todos os projetos** (cross-project)

O comando executa **multi-source search**: para cada sessão, retorna os `top_k` chunks mais relevantes — garantindo que todas as sessões contribuam, não só a maior.

No modo `--global`, os resultados são ordenados por score e filtrados por threshold mínimo de `0.6` para evitar ruído entre projetos. Cada resultado inclui o `project_id` de origem.

O resumo estruturado é carregado da sessão mais recente que tenha dados reais (ignora sessões fallback sem resumo).

## Integração com CLAUDE.md global

Para que o Claude use o plugin automaticamente em qualquer sessão sem precisar ser instruído, adicione ao `~/.claude/CLAUDE.md`:

```markdown
## Plugin: recall

Memória persistente entre sessões via SQLite + embeddings Gemini. Sempre disponível em qualquer projeto.

**Comandos:**
- `/recall-load` — lista sessões do projeto atual
- `/recall-load 1` — carrega sessão por número
- `/recall-load "query"` — busca semântica no projeto atual
- `/recall-load --global "query"` — busca semântica em todos os projetos (cross-project)
- `/recall-save` — salva sessão atual com resumo Gemini + embeddings

**Quando usar sem o usuário pedir:**
- No início de sessões de desenvolvimento, se o usuário retomar um trabalho em andamento, sugira `/recall-load` antes de começar
- Se o usuário mencionar algo que pode ter contexto em sessões anteriores, use `/recall-load --global "query"` para buscar

**Implementação (path do plugin):**
\`\`\`
~/.claude/plugins/cache/local/recall/1.0.0/hooks/db.py
\`\`\`
Funções principais: `get_db()`, `init_db()`, `get_project_id()`, `get_active_sessions()`, `multi_source_search()`.

`multi_source_search(conn, query, project_id=None, top_k_per_session=2)` — `project_id=None` ativa modo cross-project com threshold de score 0.6.
```

Isso elimina a necessidade de explicar o plugin a cada nova sessão.

## Fluxo típico de uso

```
# Início da sessão
/recall-load          # vê sessões disponíveis
/recall-load 1        # carrega a mais recente

# ... trabalho ...

# Fim da sessão
/recall-save          # salva com resumo Gemini
```

> **Por que salvar manualmente?** O `SessionEnd` não recebe o transcript da conversa — limitação da plataforma. Sem o transcript, não é possível gerar embeddings nem indexar chunks. O `/recall-save` deve ser executado **antes de fechar a sessão**, enquanto o contexto ainda está disponível. O `SessionEnd` não faz nada por design.

> **O RAG filtra por você:** não há necessidade de ser seletivo sobre quais sessões salvar. Conversas de baixa relevância técnica terão score baixo na busca semântica e simplesmente não aparecerão. Salve tudo.

## Testes e métricas

Resultados de testes realizados em sessão real (2026-03-19), projeto `blue-new-layout` (Next.js 16):

### Embeddings e busca

| Teste | Resultado |
|-------|-----------|
| Leitura de `GEMINI_API_KEY` via arquivos de config (`~/.zshrc`) | ✅ Funcionou sem exportar manualmente |
| Geração de embeddings com `gemini-embedding-001` | ✅ 3072 dimensões por chunk |
| Multi-source search entre múltiplas sessões | ✅ Scoring semântico correto, sessões com conteúdo relevante priorizadas |
| Busca semântica cross-project (`project_id=None`) | ✅ Retorna chunks de todos os projetos, ordenados por score, filtrados por threshold 0.6 |

### Hooks

| Hook | Teste | Resultado |
|------|-------|-----------|
| `SessionStart` | Listagem de sessões do projeto | ✅ Sessões exibidas corretamente |
| `PreCompact` | Autocompact disparou imediatamente após correção do hook | ✅ Hook executou; retornou silencioso pois não havia sessões indexadas previamente (comportamento esperado) |
| `SessionEnd` | Hook removido — sem operação | ✅ Sem sessões fantasma no banco |

### Limitações confirmadas em teste

| Limitação | Causa | Impacto |
|-----------|-------|---------|
| `PreCompact` não injeta contexto em sessões virgens | Sem chunks indexados previamente, multi-source retorna vazio | Baixo — contexto nativo do Claude Code supre a sessão atual |
| `SessionEnd` não indexa chunks automaticamente | Plataforma não passa transcript via stdin | Sem impacto — fallback removido, `/recall-save` é o único fluxo |
| `SessionStart` não injeta contexto automaticamente | Sem query de busca no início da sessão, RAG não tem direção | Baixo — design intencional; `/recall-load` com query específica é mais preciso |

### Observação sobre o RAG

Sessões com conteúdo de baixa relevância técnica ("conversas informais") não precisam ser excluídas manualmente. Em testes com banco misto, chunks irrelevantes tiveram score de similaridade próximo de zero contra queries técnicas — o modelo de embedding descarta naturalmente o que não é pertinente.

## Instalação

### Dependências Python

```bash
pip install sqlite-vec google-genai
```

### GEMINI_API_KEY

Adicione ao `~/.profile` (disponível em todos os shells, incluindo hooks):

```bash
export GEMINI_API_KEY=sua_chave_aqui
```

### Registro do plugin

O plugin deve estar em:
```
~/.claude/plugins/cache/local/recall/1.0.0/
```

> O Claude Code executa plugins do diretório `cache/local/`, não do `marketplaces/local/`. Após editar arquivos no marketplace, sincronize manualmente com `cp`.

## Estrutura de arquivos

```
recall/
├── plugin.json
├── README.md
├── LICENSE
├── FLUXO-ATUAL.md         # Documentação técnica detalhada
├── commands/
│   ├── recall-save.md
│   └── recall-load.md
├── hooks/
│   ├── hooks.json
│   ├── db.py              # Módulo compartilhado: SQLite, embeddings, multi-source
│   ├── session-start.py
│   ├── session-end.py
│   ├── pre-compact.py
│   └── recall_save_cmd.py # Pipeline do /recall-save
```

## Modo debug

Os hooks falham silenciosamente por design — erros não devem interromper o fluxo do usuário. Para investigar problemas, ative o modo debug:

```bash
export RECALL_DEBUG=1
```

Ou adicione ao `~/.profile` para persistir entre sessões. Com o modo ativo, todos os erros são registrados em:

```
~/.claude/memory/debug.log
```

O log inclui timestamp, hook de origem e traceback completo:

```
[2026-03-13 21:18:20] [session-start] Erro ao listar sessões
Traceback (most recent call last):
  ...
```

Para limpar o log: `rm ~/.claude/memory/debug.log`

## Problemas conhecidos e soluções

| Problema | Causa | Solução |
|----------|-------|---------|
| `GEMINI_API_KEY` não disponível nos hooks | Hooks rodam em shell não-interativo (não carrega `~/.zshrc`) | `db.py` lê a key diretamente dos arquivos de config |
| Cache desatualizado após editar marketplace | Claude Code usa `cache/local/` como fonte real | Sincronizar com `cp` após cada edição |
| SessionEnd não gera resumo | Plataforma não passa transcript via stdin | Hook removido — `/recall-save` é o único fluxo de salvamento |
| SessionStart falhando silenciosamente | Timeout de 10s insuficiente quando script chamava Gemini | Script simplificado (só SQLite) + timeout calibrado por hook em `hooks.json` |
| Multi-source retornando só última sessão | Sessões fallback têm 0 chunks (sem indexação) | Esperado — `recall-load` filtra pela sessão com resumo real |

## Roadmap

### Modelo de embedding local

Atualmente o plugin depende da Gemini API para geração de embeddings — o único componente que exige rede e chave de API. O objetivo de longo prazo é suportar modelos locais como alternativa:

- **[`nomic-embed-text`](https://ollama.com/library/nomic-embed-text)** via Ollama — 768 dimensões, roda 100% offline
- **[`mxbai-embed-large`](https://ollama.com/library/mxbai-embed-large)** via Ollama — 1024 dimensões, melhor qualidade
- **`sentence-transformers`** via Python direto — sem dependência de servidor

A troca seria configurável em `db.py`, mantendo a interface de `get_embedding()` idêntica. O banco SQLite já suporta qualquer dimensão via `sqlite-vec` — a única mudança seria reindexar sessões existentes ao trocar de modelo.

### Configuração de chave de API sem path do sistema

Atualmente a `GEMINI_API_KEY` é lida diretamente dos arquivos de config do shell (`~/.profile`, `~/.zshrc`, etc.) — solução funcional mas acoplada ao sistema de arquivos. O objetivo é suportar formas mais portáveis de configuração:

- **Arquivo de config do plugin** — ex: `~/.claude/recall.json` com `{ "gemini_api_key": "..." }`
- **Variável via Claude Code settings** — configurar a key direto no `settings.json` do Claude Code
- **Prompt interativo** — solicitar a key na primeira execução e armazenar de forma segura

> Contribuições bem-vindas. O ponto de entrada é a função `_get_gemini_api_key()` em `hooks/db.py`.

## Licença

MIT
