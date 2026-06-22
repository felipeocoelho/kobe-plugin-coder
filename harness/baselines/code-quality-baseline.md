# Code Quality Baseline

Padrão de qualidade aplicável a qualquer projeto onde o agente codifica, revisa ou refatora. Complementa `harness/baselines/engineering-tradeoffs.md` — quando um princípio aqui colide com a realidade do projeto, a matriz de tradeoffs ajuda a decidir o caminho.

---

## 1. Princípios universais

- **Função pequena e focada** — uma função faz uma coisa. Limite prático: 30-40 linhas; 60 é o teto raro. Quando passa, é cheiro de responsabilidade misturada. Refatore extraindo blocos coesos com nome próprio, não fatiando à força.

- **DRY com critério** — duplicação ruim é duplicação de regra de negócio, não de estrutura sintática parecida. Não abstraia antes do terceiro caso real. Duas funções com forma parecida mas significado diferente devem ficar duplicadas; juntá-las cedo gera abstração errada que custa caro pra desfazer.

- **Acoplamento baixo, coesão alta** — módulo expõe o mínimo necessário; mudança interna não vaza pra fora. Interface pública pequena e estável; implementação livre pra evoluir. Quando uma mudança interna obriga alterar callers, o limite foi mal desenhado.

- **Estrutura de dado certa primeiro** — código complexo costuma ser dado mal modelado. Antes de escrever a função, desenhe o tipo/tabela/schema. "Show me your flowcharts and conceal your tables, and I shall continue to be mystified. Show me your tables, and I won't usually need your flowcharts."

- **Sem otimização prematura** — clareza vence performance teórica. Otimize quando o profiler mostrar gargalo real, não quando você "achar" que algo é lento. Exceção: algoritmo claramente quadrático em loop quente (medir e trocar antes mesmo de profilar).

- **Defensividade só na borda externa** — valide entrada do mundo (HTTP body, env var, arquivo, payload de fila, retorno de API externa). Confie no código interno; `if` defensivo dentro do módulo não vira documentação — vira ruído. Ver a linha "auth check vs N+1" em `engineering-tradeoffs.md` pra aplicação prática.

- **Erros explícitos** — nunca silenciar exception sem comentário declarando o motivo. `except: pass` sem justificativa é bug futuro garantido. Quando precisar engolir erro, comente o porquê (retry externo cuida, é fallback intencional, etc.).

- **Comente o porquê, não o quê** — o código mostra o quê. Comentário só pra contexto não-óbvio: decisão arquitetural, workaround de bug externo, restrição de regulação, invariante sutil. Comentário que parafraseia o código é dívida (vai dessincronizar).

- **Sem código morto** — função/var/import não usado vai embora no mesmo PR. Histórico vive no git, não no arquivo. "Pode ser útil depois" é mentira que cresce; quando precisar, recupere do git ou reescreva.

- **Composição > herança** — herança longa amarra. Composição mantém liberdade. Use herança só pra casos claros de subtipo verdadeiro (`Square is-a Shape`); use composição quando a relação é "usa" ou "tem". Mixin é ferramenta de último recurso.

- **Fail fast** — erro detectado deve estourar perto da causa, não três camadas adiante mascarado como `None` ou string vazia. Validar precondições no início da função; lançar exceção específica que diga o que faltou.

- **Imutabilidade onde não doer** — variável reatribuída é ponto de leitura difícil. Em linguagens com suporte (TS `const`, Python `Final`, Rust por padrão), prefira imutável. Não force em código que precisa de mutação real (loops, acumuladores) — dogma vira cerimônia.

---

## 2. Ferramentas obrigatórias por linguagem

### Python

- **Linter**: `ruff` com regras `E, F, W, I, B, C90, UP, SIM, RUF` no mínimo.
- **Type checker**: `mypy` com `--strict` em código novo; `--ignore-missing-imports` aceito em libs sem stub.
- **Formatter**: `ruff format` (substitui black).
- Toda função pública anotada. Sem `Any` salvo justificado em comentário.
- `pyproject.toml` centraliza config. Dependências com versão pinada (mínimo major.minor).

### TypeScript / JavaScript

- **Linter**: `eslint` com config recomendada + `@typescript-eslint/recommended-type-checked`.
- **Type checker**: `tsc --strict` obrigatório; `noUncheckedIndexedAccess: true`, `noImplicitAny: true`, `strictNullChecks: true`.
- **Formatter**: `prettier`.
- TS preferido a JS em código novo. JS legado pode ficar mas não cresce — qualquer arquivo novo é `.ts`/`.tsx`.
- `tsconfig.json` extends preset reconhecido (`@tsconfig/strictest` ou equivalente).

### Go

- **Linter**: `golangci-lint` com `errcheck, gosimple, govet, ineffassign, staticcheck, unused, gocyclo` ligados.
- **Formatter**: `gofmt`/`goimports` (rodar em pre-commit).
- Erros sempre tratados (não use `_` pra descartar erro fora de teste).
- Interface pequena no consumidor, não no produtor. `context.Context` como primeiro parâmetro em qualquer função que faça I/O.

### Bash / shell

- `shellcheck` obrigatório (CI falha se warning).
- `set -euo pipefail` no topo de todo script.
- Aspas em toda expansão de variável: `"$var"`, `"${arr[@]}"`.
- Funções > 30 linhas em bash é sinal pra trocar de linguagem (Python/Go).

### SQL

- Formatter: `sqlfluff` ou equivalente.
- Migrations versionadas, idempotentes quando possível.
- Toda query em produção tem índice planejado — rodar `EXPLAIN` antes de mergear consulta nova em tabela grande.

---

## 3. Limite de complexidade

- Complexidade ciclomática por função: **alvo ≤ 10, teto duro 15**. Acima disso, refatorar antes de mergear.
- Aninhamento: **alvo ≤ 3 níveis, teto duro 4**. Early return é amigo — substitui o `if-else-if-else` aninhado.
- Parâmetros por função: **alvo ≤ 4**. Acima disso, agrupar em objeto/struct/dataclass nomeado.
- Tamanho de arquivo: alvo ≤ 300 linhas. Acima disso, suspeitar de responsabilidade misturada (ou de ter virado catch-all).
- Tamanho de função: alvo ≤ 40 linhas. Tetos justificáveis quando lógica é genuinamente linear (parser, state machine pequena).

Como medir:
- Python: `radon cc -a -nc <path>` (B ou pior = revisar).
- TS/JS: `eslint` com `complexity: ["warn", 10]`.
- Go: `gocyclo -over 10 .`.
- Bash: revisão manual (ferramentas escassas).

Acima do teto duro, refatorar é bloqueante. Acima do alvo mas abaixo do teto, justificar no PR se mantiver.

---

## 4. Code review estruturado

Quando o agente revisa código (próprio ou de outro), entregar relatório markdown com 4 seções fixas:

```
## Bloqueante
(coisas que NÃO podem mergear: bug claro, falha de segurança, quebra de contrato, regressão de teste)

## Forte sugestão
(deveria mudar; se decidir manter, justifique no PR)

## Polimento
(estilo, nome, comentário; opcional — não bloqueia merge)

## Elogio
(o que ficou bom — reforça padrão correto pro próximo PR)
```

Regras do relatório:

- Cada item cita `arquivo:linha` (ou `arquivo:linha-linha` pra range). Sem item vago tipo "considere refatorar essa área".
- Bloqueante exige descrição do impacto: "vaza email no log linha 42" > "log inseguro".
- Forte sugestão traz alternativa concreta, não só crítica.
- Polimento curto, uma linha cada item.
- Elogio é genuíno — não inventa pra suavizar. Se nada se destacou, omita a seção.
- Ordem das seções fixa (Bloqueante → Forte sugestão → Polimento → Elogio) pra leitura previsível.

Quando o PR não tem bloqueante e tem poucos itens, relatório pode ser uma seção só com bullets — não force estrutura vazia.

---

## Referências cruzadas

- Tradeoffs concretos onde esses princípios encontram realidade: `harness/baselines/engineering-tradeoffs.md`.
- Regras de operação do agente (postura, deploy, comunicação): o harness do Coder, `harness/CONTRACT.md`.
