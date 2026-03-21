#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Scalpyn — Apply Architecture Docs to Repository
# ═══════════════════════════════════════════════════════════
#
# COMO USAR:
#   1. Copie a pasta docs/ para a raiz do seu repo Scalpyn
#   2. Execute este script de dentro do repo
#
# OU simplesmente faça manualmente os comandos abaixo.
# ═══════════════════════════════════════════════════════════

set -e

echo "═══ Scalpyn: Aplicando docs de arquitetura ═══"
echo ""

# Verificar se estamos em um repo git
if [ ! -d ".git" ]; then
    echo "ERRO: Este diretório não é um repositório Git."
    echo "Execute este script de dentro do repo Scalpyn."
    exit 1
fi

# Verificar se a pasta docs existe
if [ ! -d "docs" ]; then
    echo "ERRO: Pasta docs/ não encontrada."
    echo "Copie a pasta docs/ para a raiz do repo antes de executar."
    exit 1
fi

# Mostrar status atual
echo "Branch atual: $(git branch --show-current)"
echo "Remote: $(git remote get-url origin 2>/dev/null || echo 'não configurado')"
echo ""

# Criar branch
BRANCH="feature/docs-architecture"
echo "Criando branch: $BRANCH"
git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"

# Adicionar docs
echo "Adicionando arquivos..."
git add docs/

# Mostrar o que vai ser commitado
echo ""
echo "Arquivos a serem commitados:"
git diff --cached --name-only
echo ""

# Commit
echo "Commitando..."
git commit -m "docs: add architecture specs for score-driven engine, futures framework, and Gate.io API mapping

- Score-driven framework (spot + futures, no grids)
- Spot: never sell at loss, HOLDING_UNDERWATER, optional DCA
- Futures: 5-Layer Institutional Scoring, anti-liquidation 3 layers
- Trading Desk navigation (sidebar, routes, wireframes, components)
- Gate.io API v4 complete mapping (leverage, TP/SL, trailing, WebSocket)
- Sell flow improvements (volatility, structure, macro, ATR trailing)
- Implementation roadmap with 5 phases and branch strategy"

echo ""
echo "═══ Commit criado! ═══"
echo ""
echo "Próximos passos:"
echo "  1. Push:  git push origin $BRANCH"
echo "  2. Abrir PR no GitHub: $BRANCH → develop"
echo "  3. Após merge, começar FASE 1: git checkout -b feature/gate-adapter"
echo ""
echo "Para usar Claude Code na implementação:"
echo "  claude"
echo "  > Leia docs/implementation/ROADMAP.md e comece a FASE 1"
echo ""
