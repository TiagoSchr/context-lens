# Context Compiler — CLAUDE.md

## O que e este projeto

Ferramenta CLI local chamada `ctx` que indexa projetos de codigo e monta contexto
otimizado (por orcamento de tokens) para assistentes de codigo como Claude Code.

## Setup rapido

```bash
pip install -e ".[parse]"    # instala com tree-sitter
lens init                     # cria .ctx/ no projeto
lens index                    # indexa todos os arquivos
```

## Estrutura de pastas

```
src/ctx/
  cli.py          # entry point Click — todos os comandos
  config.py       # configuracao e .ctx/ dir
  log.py          # log estruturado JSONL
  db/
    schema.py     # DDL SQLite + FTS5
    store.py      # CRUD — unico writer
  indexer/
    walker.py     # percorre arquivos com filtros
    hasher.py     # SHA-1 para indexacao incremental
    parser.py     # pool de parsers tree-sitter (lazy load)
    extractor.py  # extrai simbolos (tree-sitter + fallback regex)
  context/
    budget.py     # orcamento de tokens
    levels.py     # builders level0..level3
    builder.py    # monta contexto por task
  retrieval/
    intent.py     # classifica intencao da query
    search.py     # busca FTS5 + grep
    policy.py     # politica de niveis por task
  memory/
    lite.py       # memoria leve opcional
```

## Comandos uteis

```bash
lens index --force -v        # re-indexar tudo com detalhe
lens stats                   # estatisticas do indice
lens search <query>          # busca simbolos
lens show map                # mapa do projeto (level0)
lens show symbol:<name>      # detalhes de um simbolo
lens show file:<path>        # simbolos de um arquivo
lens context "<query>"       # gera contexto (auto-detecta task)
lens context "<query>" -t bugfix --show-meta   # task explicita + metadata
lens context "<query>" -o ctx_out.txt          # salva em arquivo
lens memory set rule key "valor"               # adiciona nota de memoria
lens memory list                               # lista memoria
```

## Convencoes

- Paths armazenados sempre com forward slashes (posix)
- Token budget padrao: 8000 tokens com 12% de buffer
- Log em .ctx/log.jsonl (intent + retrieval + index events)
- DB em WAL mode — leituras concorrentes OK, unico writer
- tree-sitter 0.25+: usar QueryCursor, NAO query.captures()

## Arquivos importantes

- `src/ctx/indexer/extractor.py` — nucleo do parsing de simbolos
- `src/ctx/context/builder.py` — logica de montagem do contexto
- `src/ctx/retrieval/policy.py` — politicas por tipo de tarefa
- `src/ctx/db/store.py` — todas as operacoes de leitura/escrita

## Como rodar testes

```bash
python -m pytest tests/ -v
python bench/benchmark.py   # performance
```

## Dependencias principais

- click >= 8.0 (CLI)
- tree-sitter >= 0.22 + tree-sitter-{python,javascript,typescript,...}
- SQLite (stdlib) com FTS5
- tiktoken (opcional, para contagem exata de tokens)

## Context Lens - Uso automatico

Antes de ler multiplos arquivos para responder uma pergunta, SEMPRE usar:
- `lens_context` (MCP) ou `lens context "<query>"` (CLI) para montar contexto
- `lens_search` (MCP) ou `lens search "<query>"` para localizar simbolos

Nunca ler 3+ arquivos diretamente se o indice .ctx/index.db existir.
O contexto retornado ja inclui apenas o relevante, economizando 75-98% de tokens.

Se .ctx/index.db nao existir no projeto atual: executar /setup-lens antes de tudo.
