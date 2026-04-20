# Context Lens — Integração com Claude Code

Compatível com Claude Code CLI e Claude Code IDE (extensão VS Code).

---

## Modo automático via MCP (recomendado)

Com o MCP server, o Claude Code consulta o índice automaticamente a cada pergunta — sem você precisar rodar nada manualmente.

### Instalação

```bash
pip install "context-lens-v2[parse,mcp]"
```

### Configuração

Crie o arquivo `.claude/mcp.json` na raiz do seu projeto:

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

Pronto. O Claude Code detecta o servidor automaticamente na próxima vez que abrir o projeto.

### O que acontece por baixo

O `lens-mcp` expõe 3 ferramentas que o Claude Code usa automaticamente:

| Ferramenta | O que faz |
|------------|-----------|
| `lens_search(query)` | Busca símbolos relevantes no índice FTS5 |
| `lens_context(query, task)` | Monta contexto otimizado para a query |
| `lens_status()` | Retorna economia de tokens e saúde do índice |

O servidor é leve: ~5MB RAM, resposta em ~1ms, comunicação via stdio (sem HTTP, sem porta aberta).

---

## Modo manual via slash commands

Se preferir não usar MCP, os slash commands já estão incluídos em `.claude/commands/`.
Copie a pasta `.claude/` para o seu projeto:

```bash
cp -r /caminho/context-lens/.claude/ ./
lens index
```

Depois use no Claude Code CLI:

```
/ctx fix the bug in parse_file        ← gera e mostra o contexto
/status                               ← economia de tokens
/reindex                              ← re-indexa o projeto
```

---

## Modo manual via script

```bash
python scripts/ctx-for-claude.py "fix the bug in extract_symbols"
python scripts/ctx-for-claude.py "explica o módulo de billing" --task explain

# Cole com Ctrl+V no Claude Code
```

O script gera o contexto, copia para o clipboard e salva em `.ctx/last_context.md`.

---

## Verificar que está funcionando

```bash
lens status
```

```
  Context Lens  /  meu-projeto
  ----------------------------------
  47 files  312 symbols  280 KB  |  indexed 22/03 10:30  |  python(47)

  -- Economy -----------------------
  All time   18 queries   saved ~60,000 tokens  (82%)
```

---

← [Voltar ao README](../README.md)
