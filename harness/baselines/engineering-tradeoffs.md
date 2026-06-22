# Engineering Tradeoffs — matriz viva

Cada linha é uma tensão real entre dois objetivos legítimos e a resolução defendida (sujeita a contexto). Vai crescendo conforme casos reais aparecem em projetos. Não é regra rígida; é memória de decisões.

Convenção: quando enfrentar tradeoff novo num projeto, registrar aqui com 1 linha de contexto. Após 3+ ocorrências, virar regra explícita em outro baseline (ex: `harness/baselines/code-quality-baseline.md`, `harness/baselines/security-baseline.md`).

---

| Dimensão A | Dimensão B | Resolução conhecida |
|---|---|---|
| Hash forte (bcrypt/argon2 cost alto) | Latência de login | bcrypt cost 12 ou argon2id m=64MB,t=3,p=4 é o ponto de equilíbrio (~250-400ms num server moderno). Cost 14+ só se ataque dirigido for cenário real (auth de admin de produção crítica). |
| Rate limiting agressivo | UX em burst legítimo | Janela deslizante + token bucket pra absorver pico curto. Resposta 429 com `Retry-After` explícito. Whitelist por sessão autenticada quando aplicável. Limite separado por rota de auth (mais restrito) e por rota normal. |
| CSP strict (sem `unsafe-inline`) | Embeds e widgets externos | CSP com nonce por response; embeds em iframe sandboxado com `sandbox="allow-scripts allow-same-origin"` mínimo. Nunca `unsafe-inline` global. Quando widget exige inline, isolar em subdomínio com CSP própria. |
| Auth check repetido por rota | N+1 de consulta em middleware | Middleware único decoda token e injeta `user` no request; checagem por permissão na rota. Cache de role do usuário em memória curta (30-60s) quando perfil raramente muda. Aplica princípio "defensividade só na borda externa" de `code-quality-baseline.md`. |
| Logs verbosos (debug) | I/O e custo de armazenamento | Log estruturado com nível dinâmico via env var em prod. DEBUG só em janela ativa de investigação. Sampling (1-5%) em endpoints de alto volume. Retention curta (7-30 dias) com archive frio pra compliance. |
| Cache fresco (TTL curto) | Custo de invalidação coordenada | TTL curto (30-120s) cobre 90% dos casos; invalidação explícita só em escrita de dado crítico (saldo, permissão). Cache-aside com versioning de chave (`user:42:v3`) evita invalidação distribuída cara. |
| Migration zero-downtime (expand/contract) | Simplicidade da migration | Expand-contract obrigatório em tabela > 1M linhas ou serviço com SLA. Migration "drop column then add" simples só em dev VPS ou tabela < 100k. Sempre dois deploys (expand → backfill → contract) com gap pra rollback. |
| Multi-tenant compartilhado (uma DB) | Isolamento por schema/DB | Schema compartilhado com `tenant_id` em toda tabela + RLS é o default (custo operacional baixo). Schema-per-tenant quando tenant exige compliance específico ou volume desbalanceado. DB-per-tenant só em B2B enterprise com >10k tenants ou exigência regulatória. |
| Validação no client (UX rápida) | Autoridade no server (segurança) | **Sempre os dois.** Client valida pra feedback imediato; server valida pra segurança. Nunca confiar que o client validou. Schema compartilhado (Zod/Pydantic) reduz duplicação sem comprometer autoridade. |
| Streaming de LLM (UX rápida) | Custo de bookkeeping de cache de prompt | Stream sempre que UX justifica (chat, geração longa). Cache de prompt vale a pena com prefixo > 1024 tokens reutilizado em >5 requests/hora — abaixo disso o overhead de gerenciar TTL custa mais que economiza. |
| Sync vs async em handler HTTP | Simplicidade do código | Handler sync default; async quando há I/O bloqueante em ≥2 lugares ou throughput é gargalo medido. Misturar sync/async sem fronteira clara gera bug sutil — prefira um modelo só por serviço. |
| Strong consistency | Eventual consistency | Strong consistency default em dado financeiro, auth, contagem que aparece pro usuário. Eventual aceitável em feed, recomendação, analytics, cache de leitura. Documentar consistência esperada no contrato da API. |
| Test coverage alto (>90%) | Custo de manter testes frágeis | Coverage é métrica, não meta. Mira 70-85% em código de domínio; aceita 40-60% em camada de I/O (testes de integração cobrem). Teste que quebra a cada refactor é sinal de teste mal escrito, não de cobertura útil. |
| Type checking estrito | Pragmatismo em código exploratório | Strict default em código que vai pra produção. Em script de análise ad-hoc ou notebook, relaxar é OK — mas marcar arquivo (`# pyright: basic`, `// @ts-nocheck`) pra deixar explícito. Nunca mistura sem marcar. |
| Reuso de componente | Duplicação local quando customização é alta | Reusar quando comportamento é idêntico e mudança em um lugar deve refletir em todos. Duplicar quando 80% do código é igual mas semântica diverge — abstração forçada (8 props opcionais) custa mais que duplicar. Regra de 3: na terceira variação, considerar extração. |
| Streaming response | Idempotência da resposta (retry seguro) | Stream perde idempotência natural (cliente pode receber parcial). Mitigar com idempotency key no header e dedupe server-side quando operação é mutação. Stream puramente de leitura não precisa. |
| Feature flag (deploy desacoplado de release) | Complexidade de código com branches | Flag vale a pena pra mudança com risco real (algoritmo novo, migração de fornecedor). Não vale pra ajuste cosmético — flag vira lixo no código. Toda flag nasce com data de remoção; sem data, vira dívida permanente. |
| Logs estruturados (JSON) | Legibilidade humana em dev | JSON em prod (parseável por Loki/Datadog/etc.). Em dev local, formatter que renderiza JSON como texto colorido (pino-pretty, structlog dev renderer). Nunca dois formatos de log no mesmo deploy. |
| ORM (produtividade) | SQL cru (controle e performance) | ORM pra CRUD simples e queries de domínio. SQL cru em report, agregação complexa, query que precisa de índice específico. ORM que gera SQL imprevisível em hot path é antipattern — prefira `raw` explícito quando importa. |
| Monorepo (atomicidade de mudança) | Polyrepo (isolamento de release) | Monorepo quando times compartilham código e merge atômico cross-pacote tem valor. Polyrepo quando cada serviço tem ciclo de release independente e ownership claro. Tamanho do time é mais determinante que escolha técnica. |

---

## Como usar

1. **Caso novo**: registra linha com a tensão e a resolução que tomou. 1 linha de contexto basta (ex: "decidido em projeto X em 2026-MM-DD").
2. **Caso recorrente**: quando a mesma resolução aparece em 3+ projetos, vira regra fixa em outro baseline e some daqui.
3. **Caso conflituoso**: quando a resolução defendida não funcionou num projeto novo, atualizar a linha com a ressalva ("default X, exceto em contexto Y").

Resolução **"depende"** não é resolução. Se honestamente depende de algo, nomear o algo: "Default X; quando contexto Y, então Z".
