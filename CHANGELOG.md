# Changelog

Todas as mudanças notáveis deste projeto ficam aqui.

> **A partir de v0.3.0** o changelog segue o **formato auditável** do harness do Coder (§6 do `harness/CONTRACT.md`): cada mudança registra *o que o operador pediu*, *por quê*, *o que foi feito*, *o que foi testado*, *os commits* e *como reverter*. É a trilha de auditoria da codificação — auditoria, reversibilidade e teste no mesmo lugar. Entradas anteriores seguem o [Keep a Changelog](https://keepachangelog.com/).
>
> **Régua de detalhe (changelog público).** Este repositório é público — então o changelog descreve a **mudança técnica**, nunca o **ambiente ou processo pessoal do operador**. Detalhe é bom, mas detalhe operacional-pessoal (caminhos absolutos, nomes próprios, topologia de deploy específica) é vazamento: descreva *o que o código faz*, não *onde/como o operador roda*. Nomes concretos de ambiente vivem na camada de usuário (D), fora do que é versionado.

## [Unreleased] — 2026-06-24 — Incidente: dispatch-sem-sala + integridade de sessão + deadlock de aprovação

> Em progresso. Uma sessão anterior do Coder rodou **invisível** (sem sala anexável) e morreu no meio por limite de gasto, deixando trabalho sem sinal claro de onde parou. Esta leva corrige a causa estrutural (BUG 1), a integridade de sessão interrompida (BUG 2), o deadlock de aprovação do plano (BUG 0) e reconcilia o trabalho órfão.

### BUG 0 — auto-report determinístico quando o gate de plano trava (mata o deadlock silencioso)

**Operador pediu:** uma sessão aprovada que não destrava é um deadlock; e descobrir isso não pode exigir garimpo — a sessão tem que avisar sozinha quando trava.

**Por quê:** o gate PARA-e-espera (§10) bloqueia edição de código de produção até `plan_approved`. A flag só é setada pelo **canal de controle** (a retomada externa com `--approve-plan`). Aprovação digitada **direto na sala** (interface remote-control, navegável) NÃO tem caminho até a flag — então o operador podia aprovar e a sessão seguir bloqueada, **em silêncio**, até alguém investigar. Isso é correto por design: a sessão **não pode** setar a própria flag — há duas camadas independentes impedindo auto-aprovação (o hook `guard` + o classificador do auto-mode). A aprovação tem que nascer **fora** da sessão. O que faltava era o **sinal** de que ela travou.

**Foi feito:**
- O hook `guard`, no exato `deny` do gate de plano, emite um **auto-report** ao operador (`kobe-notify`) explicando que a sessão travou porque a aprovação não chegou à flag, e guiando ao canal de controle correto. Determinístico (código — não depende do LLM lembrar de avisar).
- Blindado pra **nunca** quebrar o `deny`: desligável por env (`KOBE_CODER_GATE_NOTIFY`, default on — desligado nos testes pra não disparar mensagem real), silencioso se faltam envs/bin, e com **throttle** (1 aviso por janela, marcador em `/tmp` fora do control-plane) pra não floodar quando a sessão tenta editar várias vezes antes de encerrar o turno.

**Testes (ambiente de desenvolvimento):** suíte completa do guard (regressão — todos verdes) + casos novos (deny intacto com notify on/off; `.local` livre mesmo com notify on) + teste de caminho-feliz com `kobe-notify` falso: dispara, cita o id curto da sessão, guia pro canal de controle, e o throttle segura o 2º disparo.

**Commits:** ver `git log`. **NÃO publicado.**

**Reversão:** aditiva — `git revert` do commit. Sem auto-report, volta ao `deny` silencioso anterior. Nada fora do git tocado.

### BUG 1 — dispatch SEMPRE nasce sala (mata o fallback silencioso)

**Operador pediu:** disparo de sessão de código tem que SEMPRE abrir uma sala navegável com remote control — não existe sessão rodando invisível. Matar o ramo que rodava sem sala calado.

**Por quê:** o roteador do worker decidia `run_sala if state.get("sala_mode") else run_claude`. Um state escrito por uma versão anterior (sem a marca de sala) caía no ramo headless **silenciosamente** — a sessão rodava sem sala anexável, inalcançável durante toda a execução. Enquanto esse fallback mudo existisse, qualquer state "antigo" reabria a falha. (Causa reconstruída do histórico: a marca de sala só passou a ser escrita no dispatch depois que o state da sessão-incidente já tinha sido criado; o worker, já com o roteador novo, leu um state sem a marca.)

**Foi feito:**
- Roteador reescrito: ausência da marca de sala é tratada como **estado anômalo**, não tolerado — a sessão é **promovida a sala** e o operador é **avisado** (kobe-notify), em vez de rodar invisível. A sessão nunca roda fora de uma sala.
- O caminho headless (`run_claude`) vira **código dormente** (não-alcançável pelo roteador). Se a própria sala não puder subir (tmux ausente/falha), esse caminho já falha duro — não há degradação silenciosa pra invisível.

**Testes (ambiente de desenvolvimento):** `py_compile`; teste comportamental do roteador — caso A (state sem a marca → promove a sala, normaliza a marca, avisa loud, **nunca** chama o caminho headless) e caso B (state com a marca → sala direto, sem ruído). Ambos verdes.

**Commits:** ver `git log`. **NÃO publicado.**

**Reversão:** aditiva — `git revert` do commit. Volta ao roteador anterior (com o fallback). Nenhum dado fora do git tocado.

### BUG 2 — resumo de fechamento de sessão interrompida (integridade: "onde parou")

**Operador pediu:** quando uma sessão morre no meio (cota/crash/OOM) tendo commitado, ele tem que saber NA HORA onde o código parou e se é seguro — sem garimpo manual de state + git + `.local`.

**Por quê:** a sessão-incidente morreu por limite de gasto tendo feito 2 commits locais; descobrir o estado real exigiu garimpo. Pior: o checklist do plano (instrução LLM já existente — `remote-system.md` §"Checklist vivo") ficou **todo `[ ]`** apesar dos commits — ele **mentia**. Faltava um sinal **determinístico** de fechamento, ancorado na verdade do git e não no que a sessão *achava*.

**Foi feito:**
- `head_sha_at_start` gravado no dispatch (HEAD da cwd no início da sessão) — pra isolar **exatamente** os commits que a sessão criou (`head_sha_at_start..HEAD`).
- **Resumo de fechamento determinístico** no worker: commits da sessão, estado vs upstream (push pendente), working tree limpo/sujo (trabalho solto = risco de perda), **checklist DECLARADO vs verdade do git** (reconcilia a ressalva do operador — conta `[x]`/`[ ]`, mostra o próximo item declarado e avisa que os commits são a verdade), e artefatos `.local` recentes. Entregue via `kobe-notify` (curto) + `kobe-attach` (completo) + campo `closing_summary` no state. **Idempotente** (morte detectada 2x não duplica o aviso).
- Disparado nos caminhos de morte da sala: monitor detecta morte mid-turn; resume encontra a sala morta; crash do worker. Reforça **morte ≠ pronto**: nada é pushado sem auditoria + OK.

**Testes (ambiente de desenvolvimento):** repo git temporário — isola os 2 commits da sessão (exclui o anterior ao start), flagra working tree sujo, conta o checklist declarado e mostra o próximo item, avisa que o git é a verdade; degrada sem `head_sha_at_start`; entrega (notify+attach+`closing_summary` no state) e idempotência verificadas com bins falsos.

**Commits:** ver `git log`. **NÃO publicado.**

**Reversão:** aditiva — `git revert`. Sem o resumo, volta ao comportamento anterior (só `status=dead`/`failed` no state). `head_sha_at_start` é campo extra inócuo.

## [0.7.0] — 2026-06-23 — Faxina de privacidade: split do deploy (camada D) + despersonalização

**Operador pediu:** o plugin é público e vazava o ambiente pessoal do operador pro GitHub — os termos do deploy dele cravados no contrato, caminhos absolutos, o nome do operador e o nome do agente. Tirar tudo isso do que é público, sem perder a função.

**Por quê:** a infraestrutura de deploy (quantos ambientes, caminhos, estágios, ações entre estágios) **varia de operador** — não é constante de produto. Cravá-la no harness público (a) vaza dado pessoal e (b) presume que todo usuário tem a mesma topologia. O harness deve fixar só os **invariantes**; a topologia concreta é **dado de usuário**, que mora fora do repo público e é injetada no prompt da sessão.

**Foi feito (commit 1 — split + camada D):**
- **Nova camada de usuário (D)** — `$KOBE_HOME/user-data/coder/deploy-profile.md` (gitignored, nunca público): a topologia de deploy do operador. `_build_system_prompt` (`coder_worker.py`) a lê como 4ª camada determinística, com **fallback gracioso** se ausente (usuário 2 sem perfil degrada como C-ausente, nunca crasha). Redundância intencional com o manual global: o Coder roda remoto sem garantia de receber A, então precisa do dado no próprio mundo dele. D ≠ A (A o motor jamais lê; D ele injeta de propósito).
- **`CONTRACT.md` reescrito (§0, §9):** §0 ganha a camada D na tabela; §9 troca o diagrama concreto de 4 ambientes por **invariantes genéricos** (testa antes de publicar; passo público exige OK; git nunca rsync; marco por estágio; **todo repo de produção é tratado como potencialmente público**). A topologia concreta passa a vir de D/C. Os termos de ambiente cravados em §2.3/§2.4/§6.1/§10 viram papéis genéricos (o lab de desenvolvimento / a homologação do operador).
- **Template público** `harness/deploy-profile.example.md` (sem dados reais) — mostra a um usuário 2 como descrever a própria topologia.

**Foi feito (commit 2 — despersonalização):** README, baselines e `remote-system.md` reescritos sem o nome do operador, sem o nome do agente e sem termos de ambiente pessoal; entradas antigas do CHANGELOG higienizadas (ambiente → papéis genéricos); **régua de detalhe público** registrada no topo deste arquivo; teste de portabilidade `tests/portability_guard.sh` que faz grep do tree e falha se um termo pessoal reaparecer (regressão permanente contra re-vazamento).

**Testes (ambiente de desenvolvimento):** `py_compile` + suíte funcional de `_build_system_prompt` cobrindo camada D **presente** (header + conteúdo do perfil injetados) e **ausente** (nota graciosa, sem crash), e harness B íntegro no prompt montado. Todos passaram.

**Commits:** v0.7.0 (ver `git log`). **NÃO publicado** — aguarda OK do operador para o deploy (e para a reescrita de história, em passo dedicado).

**Reversão:** aditiva. Rollback = `git revert` dos commits de v0.7.0. A camada D ausente degrada graciosamente; nada fora do git foi migrado (o `deploy-profile.md` real é gitignored e não versionado).

## [0.6.1] — 2026-06-22 — Fix: esforço máximo agora chega ao boot do `claude -p`

**Operador pediu:** avaliar se o `--effort-max` do plugin, que hoje só troca o PROMPT (manda rodar o crivo em agentes separados), mas NÃO passa `--effort max` (nem override de modelo) pro `claude -p` disparado, é decisão de design ou furo — e, se furo, fechá-lo na mesma trilha estruturada, mantendo a trava anti-gatilho-fantasma.

**Por quê (veredito: FURO):** o Procedimento 2 do plano é "o **maior esforço disponível**". Orquestração (largura: perspectivas independentes) e esforço de raciocínio (profundidade: quão fundo cada processo pensa) são **ortogonais, não redundantes** — um orquestrador em esforço padrão ainda planeja, julga o que delegar e sintetiza o crivo em profundidade padrão, deixando o raciocínio de maior alavancagem no default. O CLI expõe o lever (`--effort max`, valores low/medium/high/xhigh/max — verificado que funciona headless), e o plano §14 já trata a escolha de modelo/esforço como parte do P2. Não usar o lever entregava só metade do "esforço máximo" que o operador pagou conscientemente.

**Foi feito:**
- `coder_worker.py::_effort_flags` — quando `state.effort == "max"`, o `claude -p` (start E resume) nasce com `--effort max`. Override de modelo **só** se `KOBE_CODER_EFFORT_MAX_MODEL` estiver setado (default OFF — a escolha Fable/Max é decisão parqueada do operador, §14); por padrão sobe só o esforço, sem trocar o modelo.
- Prompt do Procedimento 2 enriquecido: anuncia que o processo nasceu em `--effort max` e manda rodar os agentes de crivo **também em esforço elevado** (profundidade tanto na orquestração quanto em cada lente).
- **Trava anti-gatilho-fantasma intacta:** `effort=max` só é setado via `--effort-max`, que o agente principal só passa por comando inequívoco (mencionar/perguntar "ultracode" não aciona). O `state.effort` vive no state protegido — a sessão não auto-escala.

**Testes (ambiente de desenvolvimento):** `_effort_flags` (standard→sem flag; max→`--effort max`; model só com env); cmd montado inclui/omite o flag certo (start e resume pelo mesmo caminho); prompt P2 anuncia boot em max + crivo elevado; **integração real**: `claude -p --effort max` + `--settings` do guard coexistindo — sessão sobe em max esforço E a deny-list segue bloqueando (`git reset --hard` negado). Regressão completa (guard ~70 casos + worktree) verde.

**Commits:** v0.6.1 (ver `git log`). **Commit local — NÃO publicado** (repo dev/prod e restart aguardam OK explícito do operador, em passo único).

**Reversão:** aditiva e mínima. Rollback = `git revert` do commit de v0.6.1. Sem `--effort-max`, nada muda (Procedimento 1, comportamento já existente).

## [0.6.0] — 2026-06-22 — Filosofia formalizada + esforço máximo sob comando (Fases 3 e 4)

**Operador pediu:** implementar as Fases 3 (rito de quatro etapas formalizado) e 4 (modo esforço máximo / Procedimento 2) do plano-mestre V3.

**Por quê:** fechar o upgrade — o Coder passa a se auto-auditar (advogado do diabo + revisão + testes) antes de entregar, e a reconhecer o comando de esforço máximo do operador, subindo pro Procedimento 2 (crivo em agentes separados) só quando pedido.

**Foi feito:**
- **Rito de quatro etapas, por procedimento, injetado no prompt** (`coder_worker.py`): a sessão recebe uma seção "PROCEDIMENTO DESTA SESSÃO" que diz se está no **Procedimento 1** (default — rito inline, "não escale por conta própria") ou no **Procedimento 2** (esforço máximo — rodar Advogado do Diabo / Revisão multi-lente / Testes em **agentes separados**, via a ferramenta de subagente, pra matar o viés de autoconfirmação).
- **Caminho de comando pro esforço máximo** (§4): flag `--effort-max` no `start`/`resume` → estado `effort: "max"` (default `"standard"`). Nunca por auto-escalação — o estado fica no state protegido, fora do alcance da sessão.
- **Reconhecimento no agente principal** (`coder.md`): o agente passa `--effort-max` só quando o operador pede de forma **inequívoca**, com a trava anti-gatilho-fantasma (§4.2: mencionar/perguntar/projetar "ultracode" ≠ comando; na dúvida, Procedimento 1).
- O conteúdo conceitual do rito e dos dois procedimentos já estava no `CONTRACT.md` (§2-§4) desde a Fase 0 — estas fases **operacionalizam** por sessão (a nota de procedimento + o reconhecimento do comando).

**Testes (ambiente de desenvolvimento):** assembly do prompt para `standard` e `max` (P1 traz "não escale", P2 traz "agentes separados"); retrocompat (sessão sem `effort` → standard); regressão completa do guard. Todos passaram. Self-review: a sessão self-escalar é impossível (effort vem do dispatch, state protegido); o conteúdo do rito é LLM por design (§12).

**Commits:** v0.6.0 (ver `git log`).

**Reversão:** aditiva. Rollback = `git revert` do commit de v0.6.0. Sem flag, tudo roda no Procedimento 1 (comportamento já existente).

## [0.5.0] — 2026-06-22 — Ritual de execução + gate do deploy público (Fase 2 do upgrade)

**Operador pediu:** seguir o plano-mestre V3 implementando a Fase 2 — o ritual de execução e reporte que faz o Coder "voltar a ser usável" (MVP) — sob o rito de quatro etapas.

**Por quê:** a Fase 2 fecha o MVP: o operador fala "coda X" e o ritual inteiro (brief → plano → PARA-e-espera → executa marcando checklist → revisa/testa → changelog → entrega) acontece sozinho, com o passo final de deploy que toca usuário público atrás de um gate de aprovação.

**Foi feito:**
- **Gate do passo público de deploy** (`guard.py`, `KOBE_CODER_GATE_DEPLOY` default on): `git push` pro remote público (declarado em `KOBE_CODER_PUBLIC_REMOTES`, ex.: `prod`) é **negado** até o operador aprovar (§10). Liberado por `--approve-deploy` no resume (estado `deploy_approved`). Default sem config = gate inativo (zero falso-positivo). Os passos intermediários (push pro repo dev) rodam normal.
- **Agent def** atualizado: o agente principal passa `--approve-deploy` quando o operador autoriza publicar.
- **CONTRACT §8** atualizado: gate de deploy marcado ✅ Fase 2; marcos de deploy e quarentena de vocabulário marcados como obrigação (LLM).
- **Já estava no harness desde v0.2.0/Fase 0-1** (a Fase 2 formaliza, não reconstrói): brief automático (plano em anexo antes de codar), checklist vivo persistido, dois tipos de marco via `kobe-notify` (§10.1), mecânica de testes no ambiente de desenvolvimento (rito §2 + gate de changelog exigindo o campo Testes), modelo de deploy 4-ambientes via git (§9), quarentena de vocabulário (§5.3).

**Testes (ambiente de desenvolvimento):** suíte do deploy gate (push público sem/com aprovação, remote não-público, sem config → inativo) + regressão completa da suíte do guard (~70 casos) — todos passaram. Self-review (advogado do diabo): push por URL em vez de nome de remote contornaria o gate — limitação conhecida do modelo name-based, aceitável (sessões empurram por nome de remote).

**Commits:** v0.5.0 (ver `git log`).

**Reversão:** aditiva. Rollback = `git revert` do commit de v0.5.0. O gate desliga por env (`KOBE_CODER_GATE_DEPLOY=false`) e já nasce inativo sem `KOBE_CODER_PUBLIC_REMOTES`.

## [0.4.0] — 2026-06-22 — Gates determinísticos + isolamento por worktree (Fase 1 do upgrade)

**Operador pediu:** seguir o plano-mestre V3 implementando a Fase 1 — as travas de código (o "trilho antes do trem") — sob o rito de quatro etapas, com revisão adversarial multi-agente.

**Por quê:** a Fase 0 deu à sessão as *regras do jogo* (o harness), mas elas eram só prosa que o LLM honra. A Fase 1 transforma as regras críticas em **travas de código** que a sessão autônoma (que roda sob `bypassPermissions`) não consegue pular — porque o que tem resposta certa e não pode driftar é código, não julgamento (§12 do plano).

**Foi feito:**
- **Hook `guard.py` (PreToolUse)** — enforcement real: verificado empiricamente que um hook que devolve `permissionDecision:deny` bloqueia a ferramenta **mesmo sob bypassPermissions**. Gates: **deny-list** de destrutivos (rm recursivo em qualquer forma, force/mirror/delete push, reset --hard, clean, restore/checkout ., DROP/TRUNCATE/DELETE, publish, systemctl/service/pkg, chmod -R 777, indireção base64|sh/eval/pipe-pra-interpretador); **gate de changelog** (commit exige arquivo de changelog no staged; escape `[wip]`); **gate PARA-e-espera-OK** (edição de código de produção negada até aprovação do plano; `.local/` livre); **HALT** (conflito de regras → nega ação mutante, exceto comunicação).
- **Wiring no worker** via `--settings` gerado por sessão. O path do state vai no **argv do hook**, não no env da sessão — a sessão não conhece o path do próprio cadeado e não pode reescrever `plan_approved`/`halted` por Bash. Settings efêmero em subdir `.settings/` (não colide com a busca de sessão).
- **Estado novo** (`run_remote.py`): `plan_approved`, `halted`, `halt_reason`, campos de worktree. Comandos: `--approve-plan` (start/resume), `--clear-halt` (resume), `halt`, `merge`. Agent def atualizado: o agente principal passa `--approve-plan` ao detectar a aprovação do operador (detecção = LLM, liberação do gate = código).
- **Isolamento por worktree + lock de merge** (`KOBE_CODER_WORKTREE`, **default OFF** por reversibilidade): cada sessão roda numa `git worktree` própria; merge de volta serializado por `flock`, conservador — registra branch+sha de origem, recusa detached HEAD / branch errada / árvore suja / worktree suja, `branch -d` (não `-D`), grava o sha pré-merge como caminho de volta (§5.1). Nunca força, nunca auto-resolve conflito.

**Testes (ambiente de desenvolvimento):** suíte de ~70 casos do guard cobrindo cada bypass que a revisão adversarial encontrou (rm flags separadas/long, redirect no carve-out, DELETE com WHERE, push mirror/delete/refspec, systemctl kill, base64|sh, etc.) + proteção do state + plan gate + HALT comm-only + fail-closed; suíte de worktree (setup/merge/cleanup/dirty-safety/non-git); **2 testes de integração com `claude -p` real** confirmando deny-list e plan-gate bloqueando sob bypassPermissions com o settings real. Todos passaram. **Revisão adversarial de 4 agentes** (bypass de deny-list, fluxo dos gates, segurança da worktree, correção do código) achou 5 blockers reais (state auto-gravável, colisão de glob do settings, merge cego, force-remove, HALT mudo) + majors — **todos corrigidos e re-testados** antes de fechar. Resíduo honesto: indireção arbitrária (travessia cega do FS pra achar o state) não é 100% pegável por regex — mitigado (vetores diretos fechados, fail-closed em corrupção), e é em si violação de contrato tratável como HALT.

**Commits:** v0.4.0 (ver `git log`).

**Reversão:** aditiva e flag-gated. Rollback = `git revert` do commit de v0.4.0. Os gates podem ser desligados sem reverter código via env (`KOBE_CODER_GATE_DENYLIST/CHANGELOG/PLAN=false`); a worktree já nasce desligada (`KOBE_CODER_WORKTREE` default off). Nada fora do git foi tocado.

## [0.3.0] — 2026-06-22 — Fundação do harness do Coder (Fase 0 do upgrade)

**Operador pediu:** implementar o upgrade do Coder pelo plano-mestre V3 já validado, começando pela Fase 0 (fundação do harness), sob o rito de quatro etapas e com orquestração multi-agente (ultracode) autorizada.

**Por quê:** hoje o operador dita a metodologia na mão a cada sessão de código (abre sala, reporta marcos, sobe pelos ambientes via git, valida). O upgrade faz o Coder **absorver essa metodologia** num harness próprio, portável e autocontido — "regras do jogo" que viajam com o produto e funcionam para qualquer operador (o "usuário 2"), sem depender do manual pessoal de ninguém. A Fase 0 monta a fundação: o Coder passa a abrir sessão já carregando o contrato de uma fonte portável.

**Foi feito:**
- **Harness do Coder (camada B)** em `harness/CONTRACT.md` — Contrato v1.0 autocontido materializando §3–§11 do plano: reversibilidade absoluta, rito de quatro etapas (Planejamento → Advogado do Diabo → Revisão → Testes), dois procedimentos sem auto-seleção, guardrails de autonomia, modelo aditivo + conflito→HALT + exceção declarada + quarentena de vocabulário, changelog auditável, baselines, deploy 4-ambientes via git, ritual de execução e a régua código-vs-LLM.
- **Baselines self-contained** em `harness/baselines/` (6 arquivos: code-quality, security, performance, ux, engineering-tradeoffs, spr) — cópia própria do Coder, com as referências cruzadas internas reescritas para o novo lar (zero dependência de `~/.claude/*`).
- **Montagem determinística do contrato no prompt** (`coder_worker.py::_build_system_prompt`): o motor injeta a base operacional + o harness (B) via `--append-system-prompt`, e anexa uma nota determinística sobre o contrato do projeto (C, o `CLAUDE.md` da cwd, carregado nativamente). O **manual pessoal do operador (A) nunca é carregado pelo motor** — removidas as remissões a `$HOME/.claude/CLAUDE.md` no `remote-system.md` e nos baselines.
- **Honestidade de fase:** a tabela código-vs-LLM (§8 do contrato) e os rótulos de "trava" marcam explicitamente o que já é trava de código (✅) vs. o que ainda é obrigação do contrato a virar trava na Fase 1/2 (⏳) — a sessão não confia em paredes ainda não construídas.
- **Robustez:** fallback gracioso quando `remote-system.md` ou `CONTRACT.md` faltam (base mínima de emergência em vez de crash); `PYTHONUTF8=1` no spawn do worker para o prompt não-ASCII (~31KB) não estourar em locale não-UTF-8.
- README atualizado (seção "Harness do Coder"); manifest bump para 0.3.0.

**Testes (ambiente de desenvolvimento):** `py_compile` + import smoke dos dois scripts; suíte funcional de `_build_system_prompt` cobrindo C-presente, C-ausente, harness-ausente e base-ausente (degrada sem crashar); verificação de que o prompt montado não contém nenhuma instrução de dependência do manual pessoal (A) e que `~/.claude` só aparece na nota documental de resíduo; `_build_prompt` sem o parâmetro morto. Todos passaram. Revisão multi-lente independente (4 agentes: fidelidade ao plano, portabilidade, correção do código, coerência código-vs-LLM) + síntese — 5 majors e vários minors aplicados antes de fechar. Validação final de produto = operador, em uso real.

**Commits:** v0.3.0 (ver `git log`).

**Reversão:** mudança puramente aditiva (harness novo + assembly do prompt). Rollback = `git revert` do commit de v0.3.0 no repo do plugin; nada fora do git foi tocado, nenhum dado migrado. O comportamento anterior (lia só `remote-system.md`) volta com o revert.

## [0.2.0] — 2026-05-16

### Added

- **Plano obrigatório antes de codar** (system prompt da sessão remota). Em missão nova, a sessão escreve `.local/plano-<slug>.md` na cwd, anexa via `kobe-attach`, e para o turno aguardando aprovação do operador. Pula plano só pra tarefa trivial declarada.
- **Checklist vivo dentro do plano**. A sessão marca `- [x]` conforme avança e manda `kobe-notify` curto a cada item. Re-attach do plano completo só em marcos grandes. Plano vira fonte de verdade compartilhada entre Telegram e Claude Code local.
- **Sistema de presença** (`scripts/presence.py`). Cada instância Claude Code grava `$KOBE_HOME/user-data/claude-presence/<pid>.json` com pid, cwd, session_id, source e started_at. Cleanup inline (kill -0) em toda leitura remove órfãos. Não é lock — é informação.
- **`run_remote.py list` cross-tópico**. Payload ganha campo `presences` com todas instâncias ativas (default), não só sessões coder do tópico. Flag `--no-presence` omite quando o consumidor só quer sessões.
- **Aviso de pasta ocupada** em `run_remote.py start`. Antes de spawnar o worker, consulta a presença e se há instância ativa na mesma cwd retorna `warning: presence_conflict` sem disparar. Subagente coder pergunta ao operador pelo Telegram; resposta afirmativa reinvoca com `--force`.
- Hook opcional documentado no README pra o Claude Code local do operador também aparecer no `/coder_status`.

### Changed

- `claude/agents/coder.md` ganha seção sobre o protocolo de `warning: presence_conflict` (palavras-chave aceitas pra confirmação, fluxo de reinvocação com `--force`).
- `/coder_status` agora mostra dois blocos: sessões coder do tópico **e** presenças globais cross-tópico.
- `kobe-plugin.md` version bumped pra `0.2.0`.

### Notes

- `coder_worker.py` registra presença do sub-claude com `source=telegram-coder` automaticamente — operador não precisa configurar nada.
- Pra claude local do operador aparecer na presença, ele precisa adicionar o hook descrito no README ao `~/.claude/settings.json`. Opcional.
- Hooks/lock automático não foram introduzidos: presença é informação, decisão fica com o operador.

## [0.1.0] — 2026-05-14

### Added
- Estrutura inicial do plugin: manifest `kobe-plugin.md`, README, agent definition (`claude/agents/coder.md`), system prompt da sessão remota (`prompts/remote-system.md`), scripts `run_remote.py` (CLI) e `coder_worker.py` (worker em background).
- Modelo arquitetural: sessão remota é disparada e termina entre turnos; retomada via `claude --resume <session-id>`. Estado em `user-data/coder-sessions/<topic>/<uuid>.json`.
- CLI standalone pra debug via SSH: `start`, `resume`, `list`, `status`.
- Detecção de crash via PID test ao listar sessões.
- Heurística pra avisar quando o turno encerrou sem `kobe-notify` explícito.
