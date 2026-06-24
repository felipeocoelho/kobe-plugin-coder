# SPR — Security & Performance Review

## 1. O que é SPR

SPR = Security & Performance Review. Procedimento curto pra auditoria pontual de um projeto contra os baselines globais (`security-baseline.md`, `performance-baseline.md`, e — quando aplicável — `code-quality-baseline.md`).

Não é refatoração nem code review feature-a-feature. É varredura focada em **gaps de risco**: o que pode quebrar produção, vazar dado, ou degradar UX percebida.

## 2. Quando rodar

Três gatilhos:

1. **Antes de expor feature nova a usuário externo** — SPR mira só a feature; foco em superfície adicionada.
2. **Auditoria periódica** — a cada 90 dias em projeto com tráfego real; a cada 30 dias em projeto financeiro/auth/PII.
3. **Após incidente** — qualquer incidente classificado P0/P1 dispara SPR escopado na área afetada.

Documente data de cada SPR em `docs/spr-log.md` do projeto.

## 3. Como invocar

O operador invoca dizendo: "roda SPR no projeto X" ou "SPR escopado em <área>". O agente:

1. Lê `harness/baselines/security-baseline.md` + `harness/baselines/performance-baseline.md` na íntegra.
2. Faz varredura do projeto-alvo focada nos itens dos baselines.
3. Produz relatório markdown estruturado (formato abaixo).
4. Entrega o relatório em `docs/spr/<YYYY-MM-DD>-<escopo>.md` do projeto + resumo curto pelo canal ativo (Telegram, se Kobe).

## 4. Formato do relatório

```markdown
# SPR — <projeto> — <escopo> — <data>

## Resumo executivo
3-5 linhas: qual o estado geral, quantos gaps por nível, recomendação imediata.

## Gaps críticos (P0 — bloquear release / corrigir já)
- [ ] <descrição> — `arquivo:linha` — risco concreto — fix sugerido (1 linha).

## Gaps médios (P1 — corrigir em < 2 semanas)
- [ ] ...

## Gaps baixos (P2 — backlog, registrar e priorizar)
- [ ] ...

## O que está bom (reforço)
- ✓ ...

## Métricas de performance observadas (se aplicável)
| Endpoint/handler | p50 | p95 | p99 | observação |

## Próximos passos
1. ...
2. ...
```

## 5. Critérios de severidade

- **P0 (crítico)**: dado sensível em risco (vazamento, RCE, IDOR), perda de dado, indisponibilidade prolongada, brecha de auth. **Bloqueia release.**
- **P1 (médio)**: degradação clara de performance acima do SLO, log com PII parcial, ausência de rate limit em endpoint sensível, dep desatualizada com CVE conhecido sem exploit ativo. **Corrigir em sprint corrente.**
- **P2 (baixo)**: dívida técnica sem impacto imediato, falta de cobertura de teste, comentário ausente em decisão importante. **Backlog priorizado.**

## 6. Checklist prático (passos do agente durante SPR)

1. Confirmar escopo (feature, módulo, projeto inteiro).
2. Rodar `security-baseline.md` mentalmente, varrendo o código de borda externa pra dentro.
3. Verificar instrumentação (`performance-baseline.md`) — se não há métrica, é gap P1.
4. Listar dependências e checar CVEs (`npm audit`, `pip-audit`, etc.).
5. Inspecionar logs recentes (se acessíveis) por erro silencioso ou PII vazada.
6. Compilar relatório no formato acima.
7. Entregar artefato + resumo executivo.

## 7. Anti-padrões em SPR

- Listar tudo como crítico ("perde força do alerta").
- Recomendação vaga ("considere revisar a área de auth").
- Esquecer de mencionar o que está bom (operador precisa de balanço, não só problemas).
- Confundir SPR com code review estilístico — SPR é risco, não preferência.
