# Context Lens

> CLI local que indexa seu projeto e monta contexto otimizado para assistentes de IA.
> Envia só o que importa — não o projeto inteiro.

**Economia real: 75–98% de tokens** comparado a colar arquivos manualmente.

---

## Como funciona

Assistentes como Claude Code e Copilot têm limite de contexto (tokens). Quando você cola arquivos inteiros, gasta tokens com código irrelevante e chega no limite rápido.

O `lens` resolve em duas etapas:

**1. Indexação — roda uma vez por projeto**
Lê todos os arquivos e extrai só os símbolos: nome de funções, classes, parâmetros, docstrings, número de linha. Salva num banco SQLite local em `.ctx/index.db`.

**2. Busca na hora da query**
Você descreve o que quer. O `lens` busca no índice os símbolos e arquivos mais relevantes em ~0,2ms (sem ler disco) e monta um contexto focado dentro do seu orçamento de tokens.

```
Sem lens:  cola store.py + builder.py + search.py + ...  →  18.828 tokens
Com lens:  "fix bug in upsert_file"  →  ~3.320 tokens    →  82% menos
```

O índice fica em `.ctx/` dentro de cada projeto e é ignorado pelo git.

---

## Instalação

**Pré-requisito:** Python 3.10 ou superior.

```bash
pip install "context-lens[parse]"
```

Verificar:

```bash
lens --version
```

> **Windows:** se `lens` não for reconhecido, adicione o diretório de scripts ao PATH:
> ```powershell
> # Cole no terminal e feche/reabra
> [Environment]::SetEnvironmentVariable("PATH",
>   $env:PATH + ";$env:APPDATA\..\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts",
>   "User")
> ```

**Do código-fonte:**

```bash
git clone https://github.com/seu-usuario/context-lens
cd context-lens
pip install -e ".[parse]"
```

**Desinstalar:**

```bash
pip uninstall context-lens
rm -rf .ctx/    # remove o índice do projeto (opcional)
```

---

## Uso diário — 3 comandos

```bash
cd seu-projeto/

lens index                                    # indexa o projeto (primeira vez e após mudanças)
lens status                                   # confere economia de tokens e saúde do índice
lens context "sua pergunta aqui"              # gera contexto → cole no Claude/Copilot
```

Exemplos reais:

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
lens index           # cria .ctx/ nesta pasta e indexa
lens status          # confirma que está ativo
```

Não há configuração global — cada projeto tem seu próprio índice em `.ctx/`.

**Como saber que está ativo:**

```bash
lens status
```

Saída esperada:
```
  Context Lens  /  meu-projeto
  ----------------------------------
  47 files  312 symbols  280 KB  |  indexed 22/03 10:30  |  python(47)

  -- Economy -----------------------
  All time   18 queries   saved ~60,000 tokens  (82%)
```

Se aparecer `Index not found — run: lens index`, basta rodar `lens index`.

---

## Integração com Claude Code CLI

O jeito mais simples: gerar o contexto, copiar para o clipboard e colar no Claude.

```bash
# Gera contexto + copia automaticamente para o clipboard
python scripts/ctx-for-claude.py "fix the bug in extract_symbols"
python scripts/ctx-for-claude.py "explica o módulo de billing" --task explain

# Cole com Ctrl+V no Claude Code CLI
```

**Ou use os slash commands** (já incluídos em `.claude/commands/`):

No Claude Code CLI, dentro do projeto:

```
/ctx fix the bug in parse_file        ← gera e mostra o contexto
/status                               ← mostra economia de tokens
/reindex                              ← re-indexa o projeto
```

Esses comandos funcionam automaticamente em qualquer projeto que tenha a pasta `.claude/commands/` com os arquivos do Context Lens.

---

## Integração com Claude Code IDE (VS Code)

1. Gere o contexto e salve num arquivo:
   ```bash
   lens context "fix bug in upsert_file" -t bugfix -o .ctx/ctx.md
   ```

2. Abra `.ctx/ctx.md` no editor — o Claude Code IDE usa arquivos abertos como contexto.

3. Ou cole diretamente no chat da extensão.

---

## Integração com GitHub Copilot (VS Code)

1. Gere o contexto:
   ```bash
   lens context "fix bug in parse_file" -t bugfix -o .ctx/ctx.md
   ```

2. Abra `.ctx/ctx.md` no VS Code — o Copilot inclui arquivos abertos no contexto automaticamente.

3. Faça sua pergunta no Copilot Chat com o arquivo visível.

> **Dica:** Para o Copilot, use `-t navigate` para uma lista compacta de assinaturas — cabe facilmente sem saturar o contexto.

---

## Integração com OpenAI Codex / ChatGPT no VS Code

Funciona da mesma forma: gere o contexto com `lens context`, abra o arquivo `.md` gerado no editor ou cole no chat. O `lens` é agnóstico de assistente — gera texto puro compatível com qualquer IA.

---

## Como saber que está economizando tokens

```bash
lens status
```

```
  -- Economy -----------------------
  This session    5 queries   saved ~16,515 tokens  (47%)
  All time       23 queries   saved ~77,003 tokens  (48%)

  -- By task  (all time) -----------
  Task               n   Avg used   Saved
  navigate           3     1017t     86%  ########..
  generate_test      5     2132t     70%  ######....
  explain            5     3701t     47%  ####......
  bugfix            10     5270t     25%  ##........
```

```bash
lens log          # histórico detalhado de todas as queries
```

A economia aumenta quanto maior o projeto — num projeto com 400 arquivos, `navigate` economiza até 98%.

---

## Tipos de tarefa e quando usar

| Tarefa | Quando | Tokens usados | Economia típica |
|--------|--------|--------------|-----------------|
| `navigate` | "onde está X definido?" | mínimo — só assinaturas | **86–98%** |
| `generate_test` | "escreva testes para X" | fonte do símbolo | **70–98%** |
| `explain` | "como funciona X?" | estrutura + skeleton | **47–79%** |
| `refactor` | "refatora X" | estrutura + código | **74–80%** |
| `bugfix` | "corrige bug em X" | código completo | **25–65%** |

Se `-t` for omitido, a tarefa é detectada automaticamente pela query.

**Por que bugfix economiza menos?**
Para corrigir um bug o Claude precisa ver o código real, não só as assinaturas. O `lens` inclui o código-fonte completo dos arquivos mais relevantes — ainda assim economiza comparado a colar tudo.

**Bug que cruza múltiplos arquivos:**

```bash
lens context "fix bug in checkout" -t bugfix --file src/cart.py --file src/discount.py
```

Use `--file` para forçar a inclusão de arquivos específicos quando o bug envolve mais de um arquivo.

---

## Todos os comandos

```bash
lens index                          # indexação incremental (só re-indexa arquivos alterados)
lens index --force                  # re-indexa tudo do zero
lens index --verbose                # mostra cada arquivo processado
lens status                         # saúde do índice + economia de tokens
lens watch                          # monitora mudanças e re-indexa automaticamente
lens stats                          # estatísticas: arquivos, símbolos, linguagens
lens search "query"                 # busca símbolos por nome ou docstring
lens context "query"                # monta contexto (tarefa auto-detectada)
lens context "query" -t bugfix      # tarefa explícita
lens context "query" --file x.py   # força inclusão de arquivo
lens context "query" --budget 12000 # orçamento de tokens customizado
lens context "query" -o out.md     # salva contexto em arquivo
lens show map                       # mapa do projeto
lens show symbol:nome               # detalhes de um símbolo
lens show file:src/modulo.py        # símbolos de um arquivo
lens log                            # histórico completo de queries e tokens
lens log --last 10                  # últimas 10 queries
lens memory list                    # lista memória do projeto
lens memory set rule chave "valor"  # adiciona nota de memória
lens config                         # configuração atual
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
| Re-index (sem mudanças) | ~5.500 arquivos/seg |
| Busca FTS5 | ~0,2ms |
| Montagem de contexto | ~1–5ms |
| RAM durante indexação | ~2MB |
| Projeto com 640 arquivos / 7.000 símbolos | FTS < 5ms |

---

## Licença

MIT — veja [LICENSE](LICENSE).
