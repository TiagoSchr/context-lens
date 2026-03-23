# Context Lens — Integração com Cursor

O Cursor suporta MCP nativamente. Configure uma vez e o Context Lens passa a ser usado automaticamente a cada conversa.

---

## Pré-requisito

Instale com suporte a MCP:

```bash
pip install "context-lens[parse,mcp]"
```

Indexe seu projeto:

```bash
cd seu-projeto/
lens index
```

---

## Configuração no Cursor

1. Abra **Settings** (`Ctrl+,`)
2. Pesquise por **MCP** ou acesse **Features → MCP**
3. Clique em **Add MCP Server**
4. Preencha:

```json
{
  "name": "context-lens",
  "command": "lens-mcp",
  "args": []
}
```

5. Salve e reinicie o Cursor

---

## O que acontece automaticamente

O Cursor passa a ter acesso às ferramentas do Context Lens:

| Ferramenta | O que faz |
|------------|-----------|
| `lens_search(query)` | Busca símbolos no índice FTS5 |
| `lens_context(query, task)` | Monta contexto otimizado |
| `lens_status()` | Economia de tokens e saúde do índice |

O servidor é leve: ~5MB RAM, ~1ms de resposta, sem porta HTTP.

---

## Verificar que está funcionando

No terminal do Cursor:

```bash
lens status
```

Ou peça diretamente no chat do Cursor:
> "Use lens_status para me mostrar o estado do índice"

---

## Alternativa sem MCP

Se preferir não usar MCP:

```bash
lens context "fix bug in checkout" -t bugfix -o .ctx/ctx.md
```

Abra `.ctx/ctx.md` no Cursor — ele inclui arquivos abertos no contexto do chat automaticamente.

---

← [Voltar ao README](../README.md)
