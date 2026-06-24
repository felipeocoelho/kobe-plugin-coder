#!/usr/bin/env bash
# Teste de portabilidade — o repositório do plugin é PÚBLICO.
#
# Nenhum dado pessoal do operador pode reaparecer em arquivo versionado: nome do
# operador, nome do agente, caminhos absolutos do ambiente dele, ou termos de
# deploy específicos. Este guard faz grep do tree RASTREADO e FALHA (exit 1) se
# achar um termo pessoal — é a regressão permanente contra re-vazamento.
#
# Permitido: as URLs `felipeocoelho/*` (são URLs reais de instalação; decisão do
# operador em manter). Por isso NÃO existe regra de "felipe" minúsculo solto —
# só `/home/felipe` (path) e `Felipe` (nome, capital), que não casam com a URL.
#
# O próprio script contém os termos (como padrões de grep), então se exclui da
# varredura via pathspec `:(exclude)`.
set -u

cd "$(dirname "$0")/.." || exit 2
SELF=':(exclude)tests/portability_guard.sh'

fail=0
check() {  # <descrição> <regex-egrep>
  local desc="$1" pat="$2" hits
  hits=$(git grep -nE "$pat" -- . "$SELF" 2>/dev/null)
  if [ -n "$hits" ]; then
    echo "❌ VAZAMENTO — $desc:"
    echo "$hits"
    fail=1
  fi
}

check "nome do operador (Felipe)"            '\bFelipe\b'
check "path do ambiente (/home/felipe)"      '/home/felipe'
check "termo de ambiente cravado (dev/prod VPS)" 'dev VPS|prod VPS'
check "personalização de profissão (DBA experiente)" 'DBA experiente'
check "nome do agente do operador (Hal)"     '\b(o|no) Hal\b'

if [ "$fail" -eq 0 ]; then
  echo "✅ portabilidade ok — nenhum dado pessoal do operador no tree versionado."
fi
exit "$fail"
