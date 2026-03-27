# Context Lens

> Instala uma vez, configura com `lens setup`, e desaparece.
> Claude Code, Copilot e Codex passam a receber contexto otimizado automaticamente
> — sem copiar, colar ou abrir terminal no dia a dia.

**Economia real: 75–98% de tokens** por query.

> **v0.2 disponível** — automação completa pós-setup: MCP para Claude Code/Cursor/Continue.dev, task automática para Copilot, AGENTS.md para Codex. Ver [Changelog](#changelog).

---

## Como funciona

Assistentes como Claude Code e Copilot têm limite de contexto (tokens). Quanto maior o projeto, mais código irrelevante entra na janela, e as respostas ficam genéricas.

O `lens` resolve em três etapas:

**0. Setup — uma vez por projeto**
`lens setup` detecta qual assistente você usa e configura tudo:
- Claude Code recebe um MCP server que consulta o índice automaticamente.
- Copilot recebe uma task do VS Code que injeta contexto antes de cada sessão.
- Codex recebe um `AGENTS.md` com instrução para usar o índice.

Depois do setup: nenhum passo manual.

**1. Indexação — uma vez por projeto**
Lê todos os arquivos e extrai só os símbolos: funções, classes, parâmetros, docstrings, número de linha. Salva num banco SQLite local em `.ctx/index.db`.

**2. Na hora da pergunta**
Busca no índice os símbolos mais relevantes em ~0,2ms (sem ler disco) e monta um contexto focado dentro do orçamento de tokens. O assistente recebe só o trecho certo — automaticamente.

```
Sem lens:  assistente lê store.py + builder.py + search.py + ...  →  18.828 tokens
Com lens:  "fix bug in upsert_file"                               →   3.320 tokens  (82% menos)
```

O índice fica em `.ctx/` dentro de cada projeto e é ignorado pelo git.

---

## Instalação

**Pré-requisito:** Python 3.10 ou superior.

```bash
# Com suporte a tree-sitter (recomendado — parsing preciso)
pip install "context-lens[parse]"

# Com MCP server (para Claude Code e Continue.dev automáticos)
pip install "context-lens[parse,mcp]"
```

Verificar:

```bash
lens --version
```

> **Windows:** se `lens` não for reconhecido após instalar, adicione o diretório de scripts ao PATH:
> ```powershell
> [Environment]::SetEnvironmentVariable("PATH",
>   [Environment]::GetEnvironmentVariable("PATH","User") + ";$env:LOCALAPPDATA\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts",
>   "User")
> ```
> Feche e reabra o terminal.

**Do código-fonte:**

```bash
git clone https://github.com/TiagoSchr/context-lens
cd context-lens
pip install -e ".[parse,mcp]"
```

**Desinstalar:**

```bash
pip uninstall context-lens
rm -rf .ctx/    # remove o índice do projeto (opcional)
```

---

## Início rápido

Três comandos para começar em qualquer projeto:

```bash
pip install "context-lens[parse,mcp]"
lens index && lens setup --auto
lens status
# Pronto. Abra seu assistente de IA.
```

---

## Uso diário

Após `lens setup --auto`, você não precisa mais abrir o terminal para usar o Context Lens.

| | Antes (v0.1) | Depois (v0.2) |
|---|---|---|
| Claude Code | `lens context "..."` → copiar → colar | Automático via MCP — nada a fazer |
| GitHub Copilot | `Ctrl+Shift+L` → digitar query → abrir arquivo | Task automática ao abrir o projeto |
| OpenAI Codex | `python scripts/lens-codex.py "..."` → clipboard | `AGENTS.md` direciona Codex automaticamente |
| Cursor | Mesmo que Claude Code | Automático via MCP |

**Uso manual via CLI** (para quem quer controle explícito sobre o contexto gerado):

```bash
lens context "fix bug in checkout returning wrong total"
lens context "como funciona o sistema de autenticação" -t explain
lens context "escreva testes para a classe Cart" -t generate_test
lens context "onde está definido calculate_discount" -t navigate
```

---

## Setup por projeto

O `lens` funciona **por projeto**, igual ao `git`. Para cada projeto novo:

```bash
cd meu-novo-projeto/
lens index          # cria .ctx/ aqui e indexa
lens setup --auto   # configura todas as integrações detectadas, sem perguntas
lens status         # confirma que está ativo e mostra economia projetada
```

`lens setup --auto` detecta qual ferramenta você usa (pasta `.claude/`, `.vscode/`, etc.) e cria os arquivos certos (`CLAUDE.md`, `.claude/mcp.json`, `.vscode/tasks.json`, `.github/copilot-instructions.md`, `AGENTS.md`) com as instruções para o assistente usar `lens_context` automaticamente — sem interação.

```bash
lens setup --auto   # recomendado — detecta e configura sem perguntar
lens setup          # interativo — pergunta antes de cada integração
```

**Como confirmar que está ativo:**

```
  Context Lens  /  meu-projeto
  ----------------------------------
  47 files  312 symbols  280 KB  |  indexed 22/03 10:30  |  python(47)

  -- Projected savings  (no queries yet)
  Raw project  ~78,000 tokens  (312 KB  /  47 files)
  Lens budget  8,000 tokens
  Est. saving  ~90%  (~70,000 tokens por query)
```

Após as primeiras queries via MCP ou `lens context`, o status mostra a economia real acumulada.

Se aparecer `Index not found`, rode `lens index`.

---

## Integrações com assistentes de IA

Guias detalhados por assistente:

- [Claude Code](docs/claude-code.md) — MCP automático, slash commands
- [GitHub Copilot](docs/copilot.md) — task automática + instructions
- [ChatGPT / OpenAI Codex](docs/chatgpt-codex.md) — AGENTS.md automático ou clipboard
- [Cursor](docs/cursor.md) — MCP nativo
- [Continue.dev](docs/continue-dev.md) — MCP nativo, open source

---

### Claude Code — 100% automático via MCP

**Setup (uma vez):**
```bash
pip install "context-lens[parse,mcp]"
cd seu-projeto/
lens setup --auto   # cria .claude/mcp.json + CLAUDE.md automaticamente
```

A partir daí, o Claude Code consulta o índice automaticamente antes de responder qualquer pergunta sobre o código.

O que acontece por baixo:
- `lens_search` — busca símbolos relevantes pelo nome
- `lens_context` — monta contexto otimizado para o tipo de tarefa detectado
- `lens_status` — exibe economia acumulada de tokens

O servidor usa ~5MB RAM, responde em ~1ms, comunica via stdio (sem HTTP, sem porta aberta).

**Alternativa sem MCP** (Claude CLI apenas):
```bash
lens context "fix the bug in checkout" | pbcopy   # macOS
lens context "fix the bug in checkout" | clip      # Windows
```

**Slash commands** disponíveis em `.claude/commands/`:
```
/ctx fix the bug in parse_file    ← gera e mostra o contexto
/status                           ← economia de tokens
/reindex                          ← re-indexa o projeto
```

> **Configuração manual** (alternativa ao `lens setup --auto`): crie `.claude/mcp.json` na raiz do projeto:
> ```json
> {
>   "mcpServers": {
>     "context-lens": {
>       "command": "lens-mcp",
>       "args": []
>     }
>   }
> }
> ```

---

### GitHub Copilot (VS Code)

**Setup (uma vez):**
```bash
lens setup --auto
```

Cria automaticamente:
- `.vscode/tasks.json` com task de auto-index ao abrir o projeto
- `.github/copilot-instructions.md` instruindo o Copilot a usar o índice

**Como funciona após setup:**
- Ao abrir o projeto no VS Code: índice é atualizado em background
- Copilot consulta `.github/copilot-instructions.md` para cada sugestão
- Contexto relevante já está disponível sem ação manual

**Uso manual** (quando precisar forçar contexto específico):
```bash
python scripts/lens-context.py "fix bug in checkout" --target copilot
# Gera .ctx/ctx.md e abre no VS Code — Copilot lê como contexto ativo
```

Atalho VS Code: `Ctrl+Shift+P` → "Tasks: Run Task" → "Context Lens: gerar contexto"

---

### ChatGPT / OpenAI Codex

**Modo automático (Codex CLI):**
`lens setup --auto` cria `AGENTS.md` na raiz do projeto com instrução para o Codex usar `lens context` antes de iniciar qualquer tarefa. O Codex lê `AGENTS.md` automaticamente ao iniciar — zero config adicional.

**Modo manual** (ChatGPT web / qualquer LLM):
```bash
python scripts/lens-codex.py "fix bug in checkout"
# Copia contexto otimizado para clipboard → cole no chat
```

Windows alias rápido:
```powershell
doskey lc=python scripts/lens-codex.py $*
```
Uso: `lc "fix bug in checkout"` → clipboard pronto para colar.

---

### Continue.dev (VS Code)

O [Continue.dev](https://continue.dev) suporta MCP nativamente. O arquivo `.continue/config.json` já está incluído — basta instalar a extensão Continue no VS Code e o `lens-mcp` é detectado automaticamente.

---

### Cursor

`lens setup --auto` configura o MCP automaticamente. Para configuração manual, acesse Settings → MCP e adicione:
```json
{
  "name": "context-lens",
  "command": "lens-mcp",
  "args": []
}
```

---

## Resumo de compatibilidade

| Assistente | Modo automático | Configuração |
|------------|----------------|-------------|
| Claude Code IDE/CLI | MCP server | `lens setup --auto` |
| Cursor | MCP server | `lens setup --auto` |
| Continue.dev (VS Code) | MCP server | `lens setup --auto` |
| GitHub Copilot | VS Code task + instructions | `lens setup --auto` |
| OpenAI Codex CLI | AGENTS.md | `lens setup --auto` |
| ChatGPT web | Script + clipboard | `lc "query"` |

---

## Economia de tokens por tipo de tarefa

| Tarefa | Quando usar | Economia típica |
|--------|-------------|-----------------|
| `navigate` | "onde está X definido?" | **86–98%** |
| `generate_test` | "escreva testes para X" | **70–98%** |
| `explain` | "como funciona X?" | **47–79%** |
| `refactor` | "refatora X" | **74–80%** |
| `bugfix` | "corrige bug em X" | **25–65%** |

A tarefa é detectada automaticamente pela query. Use `-t` para forçar:

```bash
lens context "fix bug in checkout" -t bugfix --file src/cart.py
```

`--file` força a inclusão de um arquivo específico — útil quando o bug cruza múltiplos arquivos.

---

## Todos os comandos

```bash
lens index                           # indexação incremental
lens index --force                   # re-indexa tudo do zero
lens index --verbose                 # mostra cada arquivo
lens status                          # saúde + economia de tokens
lens watch                           # monitora mudanças e re-indexa (background)
lens stats                           # arquivos, símbolos, linguagens
lens search "query"                  # busca símbolos
lens context "query"                 # monta contexto (tarefa auto-detectada)
lens context "query" -t bugfix       # tarefa explícita
lens context "query" --file x.py    # força inclusão de arquivo
lens context "query" --budget 12000  # orçamento customizado
lens context "query" -o out.md      # salva em arquivo
lens show map                        # mapa do projeto
lens show symbol:nome                # detalhes de um símbolo
lens show file:src/modulo.py         # símbolos de um arquivo
lens log                             # histórico de queries e tokens
lens log --last 10                   # últimas 10 queries
lens memory list                     # lista memória do projeto
lens memory set rule chave "valor"   # adiciona regra (aparece em todo contexto gerado)
lens memory set hotspot arquivo "src/core.py"  # marca arquivo como crítico
lens setup                           # configura integrações com assistentes de IA
lens setup --auto                    # setup silencioso
lens config                          # configuração atual
```

---

## Estrutura criada no projeto

```
seu-projeto/
  .ctx/
    config.json     ← orçamento, extensões, dirs ignorados
    index.db        ← banco SQLite com símbolos + FTS5
    log.jsonl       ← histórico de queries e tokens
```

Tudo em `.ctx/` é local e nunca vai para o git.

---

## Configuração (`.ctx/config.json`)

```json
{
  "token_budget": 8000,
  "target_budgets": {
    "claude": 8000,
    "copilot": 4000,
    "codex": 6000
  },
  "budget_buffer": 0.12,
  "index_extensions": [".py", ".js", ".ts", ".tsx", ".go", ".rs"],
  "ignore_dirs": [".git", "node_modules", ".venv", "dist"],
  "max_file_size_kb": 512
}
```

Se `LENS_TARGET` estiver definido, o `token_budget` efetivo usa o valor de `target_budgets`.

---

## Linguagens suportadas

| Linguagem | Parser | Extrai |
|-----------|--------|--------|
| Python | tree-sitter | funções, classes, decoradores, docstrings |
| JavaScript | tree-sitter | funções, classes, métodos, arrow functions |
| TypeScript / TSX | tree-sitter | igual JS + interfaces |
| Go, Rust, Java, C, C++ | regex | funções, structs, classes |
| Ruby, PHP, C#, Swift, Kotlin | regex | funções, classes |

---

## Performance

| Operação | Velocidade |
|----------|-----------|
| Indexação completa | ~320 arquivos/seg |
| Re-index incremental (sem mudanças) | ~5.500 arquivos/seg |
| Busca FTS5 | ~0,2ms |
| Montagem de contexto | ~1–5ms |
| RAM durante uso | ~3–5MB |
| Escala | testado com 640 arquivos / 7.000 símbolos |

---

## Changelog

### v0.2 — Março 2025

#### Mudança de paradigma: manual → automático

No v0.1, usar o Context Lens exigia rodar `lens context "..."` e copiar o resultado para o assistente em cada pergunta.

No v0.2, `lens setup --auto` configura todas as integrações detectadas de uma vez. Após o setup, o assistente busca contexto automaticamente — via MCP (Claude Code, Cursor, Continue.dev), via task automática do VS Code (Copilot) ou via `AGENTS.md` (Codex). O usuário não precisa abrir o terminal durante o trabalho normal.

#### Novidades

**`lens setup` — configuração automática multi-ferramenta**
Novo comando que detecta qual assistente você usa e cria os arquivos de instrução certos automaticamente:
- `.claude/` presente → cria `CLAUDE.md` + `.claude/mcp.json` para MCP automático
- `.vscode/` presente → atualiza `tasks.json` + cria `.github/copilot-instructions.md`
- Codex detectado → cria `AGENTS.md` na raiz
- Nenhum dos dois → cria arquivos para todos
```bash
lens setup --auto    # recomendado — silencioso, sem perguntas
lens setup           # interativo
```

**`lens status` — projeção de economia desde o primeiro uso**
Antes de rodar qualquer query, o status agora mostra a economia estimada com base no tamanho real do projeto:
```
-- Projected savings  (no queries yet)
Raw project  ~39,886 tokens  (155 KB  /  39 files)
Lens budget  8,000 tokens
Est. saving  ~80%  (~31,886 tokens por query)
```
Após a primeira query via MCP ou `lens context`, mostra a economia real acumulada.

**Memória do projeto aparece no contexto gerado**
`lens memory set` funcionava mas as regras e notas nunca chegavam ao assistente. Corrigido: o bloco `## Project Memory` agora é injetado em todo contexto gerado, consumindo budget de forma controlada.
```bash
lens memory set rule style "always use type hints"
lens context "add new function"
# → contexto inclui: [rule] style: always use type hints
```

#### Correções de bugs

**`lens memory set` criava duplicatas ao invés de atualizar**
Chamar `lens memory set rule key "valor"` duas vezes criava duas linhas idênticas no banco. Corrigido com `UNIQUE INDEX(kind, key)` + migração automática de banco existente + `ON CONFLICT DO UPDATE`.

**Falha silenciosa no FTS5**
Quando a busca full-text falhava (índice corrompido ou query inválida), o sistema caía para o fallback LIKE sem nenhum aviso. Agora emite `warnings.warn()` com o motivo antes de fazer o fallback.

**`tree-sitter` versão mínima incorreta**
`pyproject.toml` declarava `tree-sitter>=0.22` mas o código usa a API `QueryCursor` disponível apenas na 0.25+. Com versões 0.22–0.24 o parsing falhava silenciosamente. Corrigido para `>=0.25`.

#### Melhorias de performance

**N+1 queries eliminadas no assembler de contexto**
O assembler chamava `store.get_symbols_for_file(path)` individualmente para cada arquivo relevante — um round-trip SQLite por arquivo. Substituído por `store.get_symbols_for_files(paths)` que faz um único `WHERE path IN (...)`. Em projetos com 10 arquivos relevantes: de 10 queries para 1.

**`list_indexed_paths` com limite no SQL**
`find_callers()` carregava todos os paths do projeto na memória para depois fatiar `[:60]`. Em projetos com 5.000+ arquivos isso era desnecessário. Agora usa `LIMIT N` diretamente no SQL.

---

### v0.1 — Lançamento inicial

- Indexação incremental com SHA-1 (só re-indexa arquivos alterados)
- Busca FTS5 com stop words e priorização de identificadores técnicos
- Assembler de contexto budget-driven por nível (L0 mapa, L1 assinaturas, L2 skeleton, L3 source)
- Políticas por tipo de tarefa (navigate, explain, bugfix, refactor, generate_test)
- MCP server para Claude Code, Continue.dev e Cursor
- Slash commands `/ctx`, `/status`, `/reindex`, `/search`
- VS Code tasks para Copilot e ChatGPT (clipboard)
- Memory Lite para hotspots, regras e notas de projeto
- `lens watch` para re-indexação automática em background

---

## Licença

MIT — veja [LICENSE](LICENSE).
