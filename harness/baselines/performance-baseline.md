# Performance Baseline — método universal para projetos do operador

Documento de método. Não é checklist binário. Define **o que medir**, **como medir**, e **como cada projeto registra os próprios SLOs no próprio CLAUDE.md**. Performance não tem estado "fechado/aberto" — tem "dentro do alvo / fora do alvo".

---

## 1. Por que esse baseline existe

Performance percebida define se o usuário volta ou não. Tela travada por 4 segundos sem feedback é desinstalação garantida, independente da qualidade do código por baixo.

Sem instrumentação, otimização é palpite — você mexe em algo achando que vai ajudar e descobre depois que o gargalo era outro. Sem SLO declarado, "está rápido" é opinião — cada pessoa do time tem uma régua diferente. Esse baseline obriga o projeto a ter **número alvo + jeito de medir** antes de discutir se está rápido.

---

## 2. O que medir (métricas universais)

Quatro famílias de métrica que valem pra qualquer projeto, do bot CLI até a SPA pesada:

| Métrica | O que é | Vira alarme quando |
|---|---|---|
| **Latência p50** | mediana — metade das requisições vem em menos que isso | sobe sem mudança de carga = degradação geral |
| **Latência p95** | 95% das requisições estão abaixo desse tempo | é o número que o usuário "sente" no dia ruim |
| **Latência p99** | a cauda — 1% pior | violação aqui é dor pra usuário específico, mas dói **muito** |
| **Throughput** | req/s ou ops/s sustentadas | queda sem queda de carga = capacidade vazando |
| **Taxa de erro** | % de 5xx, exceções não tratadas, timeouts | passou de 0.1% global = investigar agora |
| **Saturação** | CPU, memória, fila pendente, conexões DB usadas vs disponíveis | > 80% sustentado = sem margem pra pico |

**Regra dura: mediana mascara cauda.** "Está rápido em média" é desculpa de quem nunca olhou p99. É a cauda que faz usuário desistir do produto.

### Quando aplicar RED vs USE

- **RED — Rate / Errors / Duration**: pra **serviços que respondem requisição** (HTTP handlers, RPC, workers de fila). Pergunta que responde: "o serviço está atendendo bem quem chama?"
- **USE — Utilization / Saturation / Errors**: pra **recursos** (CPU, memória, disco, conexão DB, fila). Pergunta que responde: "o recurso está sufocando?"

Os dois andam juntos: RED detecta sintoma no cliente, USE explica a causa no recurso. Latência subiu (RED) — saturação de conexões DB (USE) explica.

---

## 3. Como medir (instrumentação obrigatória)

Sem instrumentação não tem baseline. Sem baseline não tem otimização — tem palpite. Mínimo obrigatório em todo projeto:

- **Logs estruturados** (JSON) com `timestamp`, `request_id`, `duration_ms` de cada chamada externa. Padrão: `structlog` (Python), `pino` (Node), `zerolog` (Go).
- **Métricas em pontos críticos**: cada handler HTTP, cada query DB > 50ms, cada chamada a serviço externo, cada job processado. Contador + histograma de duração.
- **Tracing distribuído** quando há mais de 1 serviço: propagar `request_id` no header (`X-Request-ID` ou `traceparent` do OpenTelemetry). Sem isso, debug em produção vira arqueologia.
- **Dashboard mínimo viável**: precisa conseguir responder "qual o p95 da última hora?" em **menos de 1 minuto**. Pode ser Grafana, pode ser `grep | awk` no log — o requisito é o tempo de resposta, não a ferramenta.

### Ferramentas concretas

| Necessidade | Ferramenta sugerida |
|---|---|
| Métricas + dashboard self-hosted | **Prometheus + Grafana** |
| Tracing distribuído (vendor-neutral) | **OpenTelemetry** (SDK + collector) |
| Captura e agregação de erros | **Sentry** (free tier cobre projeto pessoal) |
| APM completo (projeto pago) | **Datadog**, **New Relic**, **Honeycomb** |
| Logs estruturados | **structlog** (Py), **pino** (Node), **zerolog** (Go) |
| Load test rápido | **k6** (script JS) ou **Locust** (script Python) ou `ab` pra teste cru |

Em projeto pessoal/VPS o trio honesto é: **structlog + Prometheus exporter + Grafana** (tudo grátis, tudo roda no mesmo VPS). Sentry no free tier pra erro. OpenTelemetry quando virar mais de 1 serviço.

---

## 4. Template de SLO no CLAUDE.md do projeto

Todo projeto **copia o bloco abaixo** pro próprio `CLAUDE.md` e preenche. Sem isso, "está rápido" segue sendo opinião.

```markdown
## SLOs deste projeto

| Métrica                       | Alvo       | Janela    | Ação se violar                       |
|-------------------------------|------------|-----------|--------------------------------------|
| Latência p95 do handler /api/X| < 200ms    | 5 min     | Investigar query lenta + cache miss  |
| Taxa de erro 5xx global       | < 0.1%     | 1 hora    | Page on-call, ver Sentry             |
| LCP da página /dashboard      | < 2.5s     | p75 / 7d  | Reduzir bundle inicial, lazy-load    |
| Tempo até primeiro token (LLM)| < 1.5s     | p95 / 1h  | Trocar provider ou warm cache prompt |
```

**SLO sem ação definida é decoração.** Cada linha precisa do "o que fazer quando violar" — senão o alerta dispara, vira ruído, e em 2 semanas tá silenciado. Se você não sabe o que fazer quando a métrica cair fora, o SLO ainda não está pronto.

Regra prática: se um SLO foi violado três vezes seguidas sem ação executada, **ou o alvo está errado, ou a ação está errada** — revise o quadro.

---

## 5. Domínios específicos

### 5.1 Backend (HTTP, API, worker)

- **Latência por handler** instrumentada — p50/p95/p99 separados por rota. Agregado global esconde rota lenta.
- **Queries lentas**: log automático de qualquer query > 100ms; alerta visual em > 500ms; investigação obrigatória em > 1s.
- **N+1**: detecção em CI quando a stack permite (`bullet` no Rails, `sequelize-typescript-options` no Node, `Django Debug Toolbar` em dev, `nplusone` no Python). Revisão manual obrigatória em todo endpoint que faz loop em ORM.
- **Cache**: toda chave de cache tem TTL explícito e dono declarado no código. Cache silencioso (sem TTL ou sem invalidação clara) é bug futuro garantido — vai servir dado velho um dia.
- **Async vs sync**: I/O bloqueante dentro de request HTTP é bug, não trade-off. `requests.get()` síncrono no meio de handler async é exemplo clássico.
- **Conexão DB**: pool dimensionado e monitorado. Saturação de pool é causa #1 de "API ficou lenta de repente".

### 5.2 Frontend (SPA, página web)

**Core Web Vitals** com alvos do Google (medidos no p75 dos usuários reais):

| Métrica | Alvo | O que mata |
|---|---|---|
| **LCP** (Largest Contentful Paint) | < 2.5s | imagem hero gigante, fonte custom bloqueante, render no servidor lento |
| **INP** (Interaction to Next Paint) | < 200ms | handler JS pesado na thread principal, re-render gigante no React |
| **CLS** (Cumulative Layout Shift) | < 0.1 | imagem sem width/height, ad carregado depois empurrando conteúdo, fonte trocando |

Outros pontos não-negociáveis:

- **Bundle budget por rota**: declarar limite (ex: < 200KB gzipped no entry inicial). Falhar build se passar.
- **Imagens**: WebP/AVIF com fallback, `loading="lazy"`, **width/height explícitos** no markup (evita CLS).
- **Code splitting por rota**: framework já faz; verificar que está acontecendo de fato (olhe o `Network` no DevTools).
- **Lazy import** em componente pesado (charts, editores de texto, mapas). Não carregar Monaco no primeiro paint só porque a página tem um botão "abrir editor".
- **Skeleton screens** em qualquer chamada > 200ms. Tela em branco é desistência.

### 5.3 Real-time e conversacional (apps tipo Kobe, chat, voz)

- **Streaming obrigatório** em resposta de LLM. Primeiro token em < 1.5s, tokens subsequentes em fluxo contínuo. Esperar resposta completa antes de mostrar = produto morto na água.
- **Paralelismo de I/O independente**: chamadas que não dependem entre si rodam em paralelo. Em Python `asyncio.gather`, em JS `Promise.all`. Buscar contexto de 3 fontes diferentes em série quando podiam ser paralelas é 3x mais lento à toa.
- **Cache de contexto entre turnos**: prompt caching da API (Anthropic, OpenAI) quando suportado. Re-mandar 10k tokens de system prompt idênticos a cada turno é desperdício de latência **e** de dinheiro.
- **Latência percebida por turno**: medir do envio da mensagem ao primeiro byte da resposta. Esse é o número que o operador sente — não o `total_duration` do log.
- **Timeout com fallback gracioso**: quando provedor demora mais que o orçamento, ter plano. "Demorou demais, tentando provider secundário" é melhor que silêncio de 30s.
- **Sinal de vida em operação longa**: tarefa > 30s precisa emitir progresso (no Kobe, `kobe-notify`). Silêncio total é o pior erro de UX.

---

## 6. Quando medir vs quando otimizar

**Medir é barato e contínuo — sempre ligado.** Custo de instrumentação é baixíssimo comparado ao custo de descobrir gargalo em produção sem dado nenhum.

**Otimizar é caro — só quando há justificativa.** Justificativa válida:
1. Métrica violou SLO declarado.
2. Dado de produção mostra dor real (usuário reclamando, taxa de abandono na rota X).
3. Capacity planning aponta que crescimento esperado vai estourar o limite atual.

"Otimização prematura é a raiz de todo mal" **não é desculpa pra não instrumentar**. É razão pra não refatorar antes do número aparecer. Confundir as duas leva a código sem métrica nenhuma porque "ainda não precisa otimizar" — e quando precisar, você não vai saber onde está o gargalo.

Regra: instrumente sempre, otimize quando o dado mandar.

---

## 7. Definição de "pronto" pra feature exposta a usuário

Antes de marcar feature como entregue, checklist mínimo:

1. **Métricas-chave instrumentadas** — latência do handler/rota, taxa de erro, contador de uso. Aparecem no log/dashboard.
2. **SLO definido no CLAUDE.md do projeto** — pelo menos uma linha na tabela de SLO, com alvo e ação.
3. **Teste de carga mínimo passou** — mesmo que seja `ab -n 1000 -c 10` rodando localmente. Você precisa ter visto a feature sob carga **uma vez** antes de mandar pra usuário.
4. **Dashboard ou log permite ver p95 da última hora em menos de 1 minuto** — se você precisa de 15 min de query SQL pra responder "como está a latência?", o sistema de observabilidade ainda não está pronto.

Feature que viola qualquer um desses quatro pontos **não está pronta pra produção**, independente da funcionalidade estar 100% codada.
