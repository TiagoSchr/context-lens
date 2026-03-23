# Context Lens — Integração com Continue.dev

O [Continue.dev](https://continue.dev) é uma extensão open source para VS Code e JetBrains que suporta MCP nativamente. A integração é automática após a configuração.

---

## Pré-requisito

Instale a extensão Continue no VS Code:
- Acesse o Marketplace e pesquise por **Continue**
- Ou instale via: `ext install Continue.continue`

Instale o Context Lens com suporte a MCP:

```bash
pip install "context-lens[parse,mcp]"
```

Indexe seu projeto:

```bash
cd seu-projeto/
lens index
```

---

## Configuração

O arquivo `.continue/config.json` já está incluído no repositório do Context Lens:

```json
{
  "mcpServers": [
    {
      "name": "context-lens",
      "command": "lens-mcp",
      "args": []
    }
  ]
}
```

Copie a pasta `.continue/` para a raiz do seu projeto e o Continue.dev detecta o servidor automaticamente.

```bash
cp -r /caminho/context-lens/.continue/ ./
```

---

## O que acontece automaticamente

O Continue.dev passa a ter acesso às ferramentas:

| Ferramenta | O que faz |
|------------|-----------|
| `lens_search(query)` | Busca símbolos no índice FTS5 |
| `lens_context(query, task)` | Monta contexto otimizado |
| `lens_status()` | Economia de tokens e saúde do índice |

---

## Verificar que está funcionando

No chat do Continue (`Ctrl+Shift+L`), pergunte:
> "Use lens_status para ver o estado do índice"

Ou no terminal:

```bash
lens status
```

---

## Diferença entre Continue.dev e Copilot

| | Continue.dev | GitHub Copilot |
|-|-------------|----------------|
| MCP nativo | ✅ Sim | ❌ Não ainda |
| Open source | ✅ Sim | ❌ Não |
| Integração Context Lens | Automática via MCP | Via arquivo aberto |
| Modelos suportados | Qualquer (Claude, GPT, local) | Apenas Copilot |

---

← [Voltar ao README](../README.md)
