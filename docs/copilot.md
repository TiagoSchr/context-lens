# Context Lens — Integração com GitHub Copilot

O GitHub Copilot não suporta MCP ainda. A integração funciona via arquivo aberto no editor — o Copilot lê automaticamente os arquivos visíveis no VS Code como contexto.

---

## Pré-requisito

Indexe seu projeto uma vez:

```bash
cd seu-projeto/
lens index
```

---

## Opção 1 — Atalho de teclado (recomendado)

Pressione `Ctrl+Shift+L` no VS Code:

1. Uma caixa de texto aparece pedindo sua query
2. O `lens` gera o contexto otimizado
3. O arquivo `.ctx/ctx.md` é aberto automaticamente no editor
4. O Copilot lê o arquivo aberto e usa como contexto

> O atalho já está configurado em `.vscode/keybindings.json`. Se não funcionar, adicione manualmente em File → Preferences → Keyboard Shortcuts.

---

## Opção 2 — Script no terminal

```bash
python scripts/lens-context.py "fix bug in checkout" --target copilot
```

Gera `.ctx/ctx.md` e abre no VS Code automaticamente via `code` CLI.

---

## Opção 3 — Task do VS Code

`Ctrl+Shift+P` → "Tasks: Run Task" → **"Context Lens: gerar contexto para Copilot"**

Digita a query e o contexto é gerado e aberto no editor.

---

## Como usar após gerar o contexto

1. O arquivo `.ctx/ctx.md` estará aberto no editor
2. Abra o Copilot Chat (`Ctrl+Shift+I`)
3. Faça sua pergunta normalmente — o Copilot já enxerga o contexto do arquivo aberto

> **Dica:** Use `-t navigate` para queries de localização — gera contexto muito compacto (< 500 tokens) e o Copilot responde com precisão.

---

## Verificar economia

```bash
lens status
lens log --last 5
```

---

← [Voltar ao README](../README.md)
