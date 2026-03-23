#!/usr/bin/env bash
# context-lens-autoload.sh — auto-index when entering a project directory
#
# Add to ~/.bashrc or ~/.zshrc:
#   source /path/to/shell/ctx-autoload.sh

_lens_autoindex() {
    command -v lens &>/dev/null || return
    local markers=("pyproject.toml" "package.json" "Cargo.toml" "go.mod" ".git" ".ctx")
    for m in "${markers[@]}"; do
        if [[ -e "$m" ]]; then
            lens index &>/dev/null &
            return
        fi
    done
}

if [[ -n "$ZSH_VERSION" ]]; then
    autoload -U add-zsh-hook
    add-zsh-hook chpwd _lens_autoindex
else
    _orig_cd() { builtin cd "$@" && _lens_autoindex; }
    alias cd='_orig_cd'
fi

_lens_autoindex
