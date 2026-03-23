# Context Lens — Integração com ChatGPT / OpenAI Codex

O ChatGPT e o Codex não suportam MCP. A integração funciona via clipboard — o contexto é gerado, copiado automaticamente e você cola no chat.

---

## Pré-requisito

Indexe seu projeto uma vez:

```bash
cd seu-projeto/
lens index
```

---

## Uso

```bash
python scripts/lens-context.py "fix bug in checkout" --target chatgpt
```

O script:
1. Gera o contexto otimizado para a query
2. Copia para o clipboard automaticamente
3. Imprime o link direto para o ChatGPT

```
[lens] target=chatgpt
[ctx] task=bugfix | tokens=3320/8000
[ctx] salvo em: .ctx/last_context.md
[lens] Link ChatGPT: https://chat.openai.com/
[ctx] contexto copiado para o clipboard — cole no ChatGPT!
```

4. Cole com `Ctrl+V` no chat e faça sua pergunta

---

## Detecção automática de ambiente

O script detecta o ambiente automaticamente com `--target auto` (padrão):

```bash
python scripts/lens-context.py "sua query"
# Se estiver no VS Code → modo copilot (abre arquivo)
# Se estiver no terminal → modo clipboard
```

---

## Via comando `lens` direto

Também funciona gerando o contexto e copiando manualmente:

```bash
lens context "fix bug in checkout" -t bugfix | clip    # Windows
lens context "fix bug in checkout" -t bugfix | pbcopy  # Mac
```

Cole com `Ctrl+V` no ChatGPT.

---

## Salvar em arquivo para reusar

```bash
lens context "fix bug in checkout" -t bugfix -o contexto.md
```

Abra `contexto.md` e cole quando precisar — útil para queries longas.

---

## Tipos de tarefa disponíveis

| Flag | Quando usar |
|------|-------------|
| `-t explain` | "como funciona X?" |
| `-t bugfix` | "corrige o bug em X" |
| `-t generate_test` | "escreve testes para X" |
| `-t navigate` | "onde está X definido?" |
| `-t refactor` | "refatora X" |

Sem `-t`, a tarefa é detectada automaticamente pela query.

---

← [Voltar ao README](../README.md)
