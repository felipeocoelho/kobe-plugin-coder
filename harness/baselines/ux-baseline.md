# UX Baseline

Padrão de experiência do usuário aplicável a qualquer projeto do operador que tenha um humano do outro lado — bot de mensageiro, web app, app nativo, CLI. Complementa `harness/baselines/performance-baseline.md` (latência *real*; aqui cuidamos da latência *percebida* e do resto da experiência), `harness/baselines/security-baseline.md` e `harness/baselines/code-quality-baseline.md`.

## Por que este baseline tem forma diferente dos outros

Segurança, performance e qualidade de código têm um **núcleo universal** que vale igual em qualquer projeto — existe certo e errado quase objetivo. **UX não tem isso.** Boa UX depende da **superfície**: o que é certo num app mensageiro (Telegram) não é o mesmo que numa web app, que não é o mesmo que num app nativo de loja. O "depende" é maior que a regra fixa.

Por isso este documento é **núcleo universal + módulos por superfície**, espelhando a estrutura do manual global (Core + camadas):

- **Parte 1 — Núcleo universal.** Princípios que valem em toda superfície. Ancorados em pesquisa consolidada (heurísticas de Nielsen, Laws of UX), não em opinião.
- **Parte 2 — Módulo Mensageiro/Conversacional.** A superfície que o Kobe **é** hoje. É o módulo mais maduro porque metade dele já virou lei na marra ao longo de meses de uso.
- **Parte 3 — Módulo Web.** Esboço; preenchido quando um projeto web (ex: Flow) exigir.
- **Parte 4 — Módulo App Nativo.** Esboço; preenchido quando houver app de loja.

### Aviso de calibração honesto

Diferente da baseline de segurança (engenharia assentada, tem certo e errado), UX carrega **mais gosto e mais opinião**. O **núcleo universal** e o **módulo mensageiro** têm alta confiança — o primeiro é pesquisa consolidada, o segundo é consolidação do que o operador já ensinou pagando com erro. Os módulos web/nativo, quando escritos, trazem expertise que o operador não tem vocabulário pra auditar — então a responsabilidade de acertar é do agente, e a calibração vem do **gosto de usuário** do operador ("isso me irritaria? isso me ajudaria?"), não de vocabulário de UX. Onde houver dúvida real de gosto, **marcar** em vez de fingir certeza.

### Como usar

- Toda vez que uma decisão tiver um humano como usuário final, este baseline é uma das lentes do crivo (junto com segurança, performance, qualidade).
- O agente **audita a decisão pronta** contra os itens da superfície ativa — não basta "achei bom no olhômetro"; tem que citar qual princípio cobre e o veredito.
- Em projeto novo com UI: clonar o módulo da superfície relevante no `CLAUDE.md` do projeto como checklist vivo.

---

## Parte 1 — Núcleo universal

Vale em **qualquer** superfície. Base: as 10 heurísticas de Nielsen (1994, inalteradas até hoje) + Laws of UX. Reescritas em linguagem de operador, não de designer.

### 1.1 Nunca deixe o usuário no escuro (visibilidade do estado do sistema)

A heurística nº 1 de Nielsen, e a mais violada. O sistema sempre comunica o que está acontecendo, em tempo razoável. Toda ação do usuário tem **resposta visível**. Se algo está processando, o usuário vê que está processando — nunca uma tela/chat morto onde ele não sabe se travou, se foi recebido, se está pensando.

**Régua de tempo (Doherty Threshold + escala de Nielsen/Miller, derivada da arquitetura cognitiva humana — universal, não cultural):**
- **< 0,4s** — percebido como instantâneo; usuário e sistema em sincronia.
- **< 1s** — fluxo mantido, mas já há leve hesitação; não precisa de indicador, mas é o teto do "imediato".
- **1–10s** — **exige indicação explícita de progresso** (spinner, "digitando…", "processando 2 de 3"). Sem isso, o usuário acha que quebrou.
- **> 10s** — exige status comunicado ativamente, e idealmente a opção de seguir fazendo outra coisa. Silêncio aqui = abandono.

**O que importa é o tempo até o primeiro feedback, não até a conclusão.** Acuse a ação instantaneamente e processe em seguida. Latência *percebida* manda mais que a real.

### 1.2 O caminho comum é o mais curto (eficiência + Lei de Fitts/Hick)

A ação que o usuário faz toda hora tem que ser a mais fácil de alcançar. Quanto mais opções numa decisão, mais tempo pra decidir (Hick) — então não enterre a ação frequente num menu de 20 itens. O atalho existe pro experiente; o caminho óbvio existe pro iniciante. Os dois convivem.

### 1.3 Não faça o usuário repetir o que já disse (reconhecer > lembrar)

Minimize a carga de memória do usuário. O sistema lembra do contexto; o usuário não deveria ter que recarregá-lo. Se ele já informou algo, não pergunte de novo. Mostre as opções em vez de exigir que ele decore comandos. (Heurística nº 6 + Lei de Miller: memória de trabalho humana segura ~7±2 itens — não exija mais.)

### 1.4 Erro nunca é beco sem saída (recuperação + prevenção)

Três camadas, nesta ordem de prioridade:
1. **Prevenir** é melhor que tratar (heurística nº 5): desenhe pra que o erro não aconteça — confirmação no destrutivo, validação antes de submeter, defaults seguros.
2. **Quando o erro acontece**, a mensagem diz **o que houve E o que fazer** (heurística nº 9). "Erro 500" é inútil. "Provedor instável, tente de novo em 30s" é útil. Nunca jargão técnico cru pro usuário final.
3. **Saída sempre disponível** (heurística nº 3, "saída de emergência"): desfazer, cancelar, voltar. O usuário tem que sentir que controla, não que está preso.

### 1.5 Consistência e previsibilidade (Lei de Jakob)

Os usuários passam a maior parte do tempo em **outros** produtos. Eles esperam que o seu funcione como os que já conhecem. Não reinvente convenção estabelecida sem motivo forte. Dentro do produto: mesma palavra pra mesma coisa, mesmo padrão pra mesma ação, mesmo tom. Comportamento estável e previsível é o que constrói confiança.

### 1.6 Clareza acima de esperteza (estética + minimalismo)

Heurística nº 8 + Tesler's Law (conservação da complexidade: toda complexidade que você não absorve, sobra pro usuário). Diga só o que importa; cada elemento extra compete com o relevante. Mas minimalismo ≠ esconder o necessário — é cortar o ruído, não a informação. Linguagem do mundo do usuário, não do sistema (heurística nº 2).

### 1.7 Respeite a atenção

Não interrompa à toa. Notificação só quando vale a interrupção. O peak-end rule diz que a memória de uma experiência é dominada pelo **pico** e pelo **fim** — então cuide especialmente do momento de maior fricção e do encerramento. Um fim limpo vale mais que dez detalhes no meio.

### 1.8 Acessibilidade não é opcional

Contraste adequado, alvo de toque grande o suficiente, navegação por teclado, semântica correta pra leitor de tela, alt em imagem. Frequentemente o produto é o único canal pra quem não consegue usar a alternativa.

---

## Parte 2 — Módulo Mensageiro / Conversacional (Telegram)

A superfície do Kobe hoje. Aqui o núcleo universal se **traduz** pras restrições e affordances de um app de mensagem. Metade disto já era lei no `CLAUDE.md` global e na memória — aqui consolida.

### 2.1 Streaming num mensageiro ≠ streaming num chat app

**Esta é a adaptação que mais pega.** No app do ChatGPT, "streaming" é texto aparecendo token a token numa bolha que cresce. **Num mensageiro isso não existe** — a mensagem é uma unidade atômica que chega pronta. Editar uma bolha caractere a caractere via API é frágil, espalha notificação e fica feio. Já foi problema concreto aqui.

Então a tradução de "visibilidade do estado" (núcleo 1.1) pro mensageiro é:
- **Indicador "digitando…"** é o sinal de vida contínuo enquanto o turno processa. O código o mantém aceso; o agente não gerencia isso.
- **ACK que nomeia a ação** é o "primeiro byte". Antes de uma operação com latência perceptível (ler vários arquivos, varrer repo, WebFetch, abrir MCP, rodar script), emita **primeiro** uma mensagem curta que diz **o que vai fazer** — e só depois aja. Específico, não genérico: "Vou abrir o repo e ver como o handler trata o lock — já volto" ✅; "Vou verificar" ❌.
- **Progresso em tarefa longa** via mensagens discretas ("processando 2 de 3"), não via stream contínuo.
- **Entrega final** é uma mensagem coerente, completa.

### 2.2 Não fique mudo (núcleo 1.1 levado a sério)

O pior erro de UX no mensageiro é **silêncio** numa tarefa > ~30s. Sinal de vida (ACK antes, progresso durante, entrega no fim) não é cortesia — é o que sustenta a sensação de que o sistema está vivo. Silêncio total lê como "travou" ou "ignorou".

### 2.3 Quando NÃO dar ACK

Resposta de bate-pronto (papo, pergunta que já se sabe, confirmação, ajuste pequeno, comando de memória) **não** leva ACK. Anunciar "vou responder" e responder na mesma é ruído duplicado. ACK só quando você vai *sumir um pouco pra agir*.

### 2.4 Não duplique resposta (coalescência de mensagens)

Quando várias mensagens do usuário chegam quase juntas — clássico: áudio atrasa na transcrição, o usuário manda um follow-up ("caramba, demorou"), e os dois entram quase no mesmo turno — **responda uma vez só, de forma coerente**. Não entregue a mesma resposta duas vezes (uma pro áudio, outra "por cima" do follow-up). Trate o lote como uma conversa única e responda o conjunto. Resposta duplicada é fricção e parece bug. *(Falha observada em produção 2026-06-09.)*

### 2.5 Brevidade e um assunto por mensagem

Três frases batem três parágrafos — no mensageiro o usuário lê rolando o polegar. Parede de texto é hostil. Se a resposta cabe em 3 linhas, não use 30. Quando o conteúdo é longo e legítimo (resumo, lista de decisões), quebre em partes digeríveis, não num bloco único. Um assunto por mensagem quando possível.

### 2.6 Markdown contido

Mensageiro renderiza um subconjunto de markdown e renderiza mal o resto. Use formatação a favor da leitura (negrito pontual, lista quando há itens reais), não como enfeite. Tabela gigante, heading aninhado e markdown decorativo viram ruído. Clareza > sofisticação visual.

### 2.7 Emoji de status como sinal rápido

Estado comunicado em 1 caractere no início: 🟢 ok · 🟡 bloqueado · 🔴 erro · ✅ marco · ℹ️ info. O operador entende o estado antes de ler a frase. (Aplicação do peak-end + visibilidade de status.)

### 2.8 Threading: respeite o reply

Quando o usuário responde a uma mensagem específica (reply), essa mensagem citada é **contexto principal**, não pano de fundo. Reconstrua o tema dela antes de interpretar a nova. No envio, responder na thread/tópico certo é parte de não fazer o usuário se reorientar.

### 2.9 Áudio e texto são simétricos

O operador fala ou escreve, como for melhor pra ele. A resposta não muda de qualidade por causa do canal de entrada. Gírias/nomes próprios mal-transcritos são problema conhecido — usar dicas de transcrição quando existirem.

### 2.10 Linguagem natural é o caminho principal; slash é atalho

Todo recurso tem que funcionar conversando ("lista as conversas", "retoma aquela sobre X"). O comando slash é conveniência pra quem decorou, não a única porta. Comando sem parâmetro (clique no menu mobile) precisa de comportamento gracioso — degrada pra algo útil, não pra um erro.

### 2.11 Confirme o destrutivo, aja no resto

Operação irreversível (apagar, sobrescrever, enviar pra terceiro, gastar recurso) pede confirmação clara. O resto — edição de arquivo do projeto, leitura, build, commit local — flui sem pedir permissão a cada passo. Fricção proporcional ao risco, nunca uniforme.

### 2.12 Tom

Português brasileiro, conversacional, direto, sem floreio. Honestidade > complacência. Quando errar, reconheça e corrija sem auto-flagelação. (Ajuste fino por operador vive em `user-data/identity/PREFERENCES.md` — **isso é preferência do operador, não regra universal**; não confundir o tom-padrão com o gosto específico do operador.)

---

## Parte 3 — Módulo Web (esboço)

> A escrever quando um projeto web (ex: Flow) exigir auditoria de UX. Não escrever no vácuo. Eixos que vão entrar, derivados do núcleo: hierarquia visual e tipografia, responsividade (mobile-first quando aplicável), estados de loading/skeleton/erro em toda chamada externa, optimistic UI, formulários (validação inline, mensagens úteis), navegação e arquitetura de informação, contraste/acessibilidade WCAG. Referência cruzada: camada Frontend do manual global (seção 4) e `frontend-design` skill.

---

## Parte 4 — Módulo App Nativo (esboço)

> A escrever quando houver app de loja (iOS/Android). Eixos previstos: convenções da plataforma (não brigar com o SO), gestos, estados offline e sincronização, notificações que respeitam a atenção (núcleo 1.7), onboarding, performance percebida em transições. Não escrever antes de existir o projeto.

---

## Fontes

Núcleo ancorado em material consolidado de UX:

- [10 Usability Heuristics for User Interface Design — Nielsen Norman Group](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [Doherty Threshold — Laws of UX](https://lawsofux.com/doherty-threshold/)
- [Nine UX best practices for AI chatbots — Mind the Product](https://www.mindtheproduct.com/deep-dive-ux-best-practices-for-ai-chatbots/)
- [Best Practices for Conversational UI Design — Onething Design](https://www.onething.design/post/best-practices-for-conversational-ui-design)

O módulo mensageiro é, além disso, destilado de regras já pagas com erro no histórico do Kobe (CLAUDE.md global seções 1.11 e do projeto; memória do agente).
