# Context Lens

> CLI local que indexa seu projeto e monta contexto otimizado para assistentes de IA.
> O assistente vê só o que importa — não o projeto inteiro.

**Economia real: 75–98% de tokens** por query.

---

## Como funciona

Assistentes como Claude Code e Copilot têm limite de contexto (tokens). Quanto maior o projeto, mais código irrelevante entra na janela, e as respostas ficam genéricas.

O `lens` resolve em duas etapas:

**1. Indexação — uma vez por projeto**
Lê todos os arquivos e extrai só os símbolos: funções, classes, parâmetros, docstrings, número de linha. Salva num banco SQLite local em `.ctx/index.db`.

**2. Na hora da pergunta**
Busca no índice os símbolos mais relevantes em ~0,2ms (sem ler disco) e monta um contexto focado dentro do orçamento de tokens. O assistente recebe só o trecho certo.

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

## Uso diário

```bash
cd seu-projeto/

lens index                                     # indexa o projeto (primeira vez e após mudanças)
lens status                                    # economia de tokens e saúde do índice
lens context "sua pergunta"                    # gera contexto → cole no assistente
```

Exemplos:

```bash
lens context "fix bug in checkout returning wrong total"
lens context "como funciona o sistema de autenticação" -t explain
lens context "escreva testes para a classe Cart" -t generate_test
lens context "onde está definido calculate_discount" -t navigate
```

---

## Cada projeto é independente

O `lens` funciona **por projeto**, igual ao `git`. Para cada projeto novo:

```bash
cd meu-novo-projeto/
lens index      # cria .ctx/ aqui e indexa
lens status     # confirma que está ativo
```

Não há configuração global. Cada projeto tem seu próprio índice em `.ctx/`.

**Como confirmar que está ativo:**

```
  Context Lens  /  meu-projeto
  ----------------------------------
  47 files  312 symbols  280 KB  |  indexed 22/03 10:30  |  python(47)

  -- Economy -----------------------
  All time   18 queries   saved ~60,000 tokens  (82%)
```

Se aparecer `Index not found`, rode `lens index`.

---

## Integrações com assistentes de IA

### Claude Code — automático via MCP

Com o MCP server, o Claude Code consulta o índice automaticamente a cada pergunta.

**1.** Instale com MCP:
```bash
pip install "context-lens[parse,mcp]"
```

**2.** Crie `.claude/mcp.json` na raiz do projeto:
```json
{
  "mcpServers": {
    "context-lens": {
      "command": "lens-mcp",
      "args": []
    }
  }
}
```

Pronto. O Claude Code passa a usar automaticamente:
- `lens_search` — busca símbolos relevantes
- `lens_context` — monta contexto otimizado
- `lens_status` — economia de tokens

O servidor usa ~5MB RAM, responde em ~1ms, comunica via stdio (sem HTTP, sem porta aberta).

**Sem MCP**, use os slash commands incluídos em `.claude/commands/`:
```
/ctx fix the bug in parse_file    ← gera e mostra o contexto
/status                           ← economia de tokens
/reindex                          ← re-indexa o projeto
```

---

### GitHub Copilot (VS Code)

**Atalho:** `Ctrl+Shift+L` → digita a query → contexto gerado e aberto no editor automaticamente. O Copilot lê o arquivo aberto como contexto.

**Script:**
```bash
python scripts/lens-context.py "fix bug in checkout" --target copilot
# Gera .ctx/ctx.md e abre no VS Code
```

**Task:** `Ctrl+Shift+P` → "Tasks: Run Task" → "Context Lens: Copilot — gerar contexto"

---

### ChatGPT / OpenAI Codex

```bash
python scripts/lens-context.py "fix bug in checkout" --target chatgpt
# Copia contexto para clipboard + abre link direto no ChatGPT
# Cole com Ctrl+V e converse normalmente
```

O script detecta o ambiente automaticamente (`--target auto` é o padrão):
- Dentro do VS Code → modo Copilot (abre arquivo)
- Terminal externo → clipboard

---

### Continue.dev (VS Code)

O [Continue.dev](https://continue.dev) suporta MCP nativamente. O arquivo `.continue/config.json` já está incluído — basta instalar a extensão Continue no VS Code e o `lens-mcp` é detectado automaticamente.

---

### Cursor

Acesse Settings → MCP e adicione:
```json
{
  "name": "context-lens",
  "command": "lens-mcp",
  "args": []
}
```

---

## Resumo de compatibilidade

| Assistente | Modo | Automático? |
|------------|------|-------------|
| Claude Code CLI/IDE | MCP server | ✅ 100% automático |
| Continue.dev (VS Code) | MCP server | ✅ 100% automático |
| Cursor | MCP server | ✅ Um config |
| GitHub Copilot | `Ctrl+Shift+L` + arquivo | ✅ Um atalho |
| ChatGPT / Codex | script + clipboard | ✅ Um comando |

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
lens memory set rule chave "valor"   # adiciona nota de memória
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
  "budget_buffer": 0.12,
  "index_extensions": [".py", ".js", ".ts", ".tsx", ".go", ".rs"],
  "ignore_dirs": [".git", "node_modules", ".venv", "dist"],
  "max_file_size_kb": 512
}
```

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

## Licença

MIT — veja [LICENSE](LICENSE).
