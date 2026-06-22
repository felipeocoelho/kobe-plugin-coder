# Changelog

Todas as mudanças notáveis deste projeto ficam aqui.

> **A partir de v0.3.0** o changelog segue o **formato auditável** do harness do Coder (§6 do `harness/CONTRACT.md`): cada mudança registra *o que o operador pediu*, *por quê*, *o que foi feito*, *o que foi testado*, *os commits* e *como reverter*. É a trilha de auditoria da codificação — auditoria, reversibilidade e teste no mesmo lugar. Entradas anteriores seguem o [Keep a Changelog](https://keepachangelog.com/).

## [0.6.0] — 2026-06-22 — Filosofia formalizada + esforço máximo sob comando (Fases 3 e 4)

**Operador pediu:** implementar as Fases 3 (rito de quatro etapas formalizado) e 4 (modo esforço máximo / Procedimento 2) do plano-mestre V3.

**Por quê:** fechar o upgrade — o Coder passa a se auto-auditar (advogado do diabo + revisão + testes) antes de entregar, e a reconhecer o comando de esforço máximo do operador, subindo pro Procedimento 2 (crivo em agentes separados) só quando pedido.

**Foi feito:**
- **Rito de quatro etapas, por procedimento, injetado no prompt** (`coder_worker.py`): a sessão recebe uma seção "PROCEDIMENTO DESTA SESSÃO" que diz se está no **Procedimento 1** (default — rito inline, "não escale por conta própria") ou no **Procedimento 2** (esforço máximo — rodar Advogado do Diabo / Revisão multi-lente / Testes em **agentes separados**, via a ferramenta de subagente, pra matar o viés de autoconfirmação).
- **Caminho de comando pro esforço máximo** (§4): flag `--effort-max` no `start`/`resume` → estado `effort: "max"` (default `"standard"`). Nunca por auto-escalação — o estado fica no state protegido, fora do alcance da sessão.
- **Reconhecimento no Hal** (`coder.md`): o agente passa `--effort-max` só quando o operador pede de forma **inequívoca**, com a trava anti-gatilho-fantasma (§4.2: mencionar/perguntar/projetar "ultracode" ≠ comando; na dúvida, Procedimento 1).
- O conteúdo conceitual do rito e dos dois procedimentos já estava no `CONTRACT.md` (§2-§4) desde a Fase 0 — estas fases **operacionalizam** por sessão (a nota de procedimento + o reconhecimento do comando).

**Testes (dev VPS):** assembly do prompt para `standard` e `max` (P1 traz "não escale", P2 traz "agentes separados"); retrocompat (sessão sem `effort` → standard); regressão completa do guard. Todos passaram. Self-review: a sessão self-escalar é impossível (effort vem do dispatch, state protegido); o conteúdo do rito é LLM por design (§12).

**Commits:** v0.6.0 (ver `git log`).

**Reversão:** aditiva. Rollback = `git revert` do commit de v0.6.0. Sem flag, tudo roda no Procedimento 1 (comportamento já existente).

## [0.5.0] — 2026-06-22 — Ritual de execução + gate do deploy público (Fase 2 do upgrade)

**Operador pediu:** seguir o plano-mestre V3 implementando a Fase 2 — o ritual de execução e reporte que faz o Coder "voltar a ser usável" (MVP) — sob o rito de quatro etapas.

**Por quê:** a Fase 2 fecha o MVP: o operador fala "coda X" e o ritual inteiro (brief → plano → PARA-e-espera → executa marcando checklist → revisa/testa → changelog → entrega) acontece sozinho, com o passo final de deploy que toca usuário público atrás de um gate de aprovação.

**Foi feito:**
- **Gate do passo público de deploy** (`guard.py`, `KOBE_CODER_GATE_DEPLOY` default on): `git push` pro remote público (declarado em `KOBE_CODER_PUBLIC_REMOTES`, ex.: `prod`) é **negado** até o operador aprovar (§10). Liberado por `--approve-deploy` no resume (estado `deploy_approved`). Default sem config = gate inativo (zero falso-positivo). Os passos intermediários (push pro repo dev) rodam normal.
- **Agent def** atualizado: o Hal passa `--approve-deploy` quando o operador autoriza publicar.
- **CONTRACT §8** atualizado: gate de deploy marcado ✅ Fase 2; marcos de deploy e quarentena de vocabulário marcados como obrigação (LLM).
- **Já estava no harness desde v0.2.0/Fase 0-1** (a Fase 2 formaliza, não reconstrói): brief automático (plano em anexo antes de codar), checklist vivo persistido, dois tipos de marco via `kobe-notify` (§10.1), mecânica de testes em dev VPS (rito §2 + gate de changelog exigindo o campo Testes), modelo de deploy 4-ambientes via git (§9), quarentena de vocabulário (§5.3).

**Testes (dev VPS):** suíte do deploy gate (push público sem/com aprovação, remote não-público, sem config → inativo) + regressão completa da suíte do guard (~70 casos) — todos passaram. Self-review (advogado do diabo): push por URL em vez de nome de remote contornaria o gate — limitação conhecida do modelo name-based, aceitável (sessões empurram por nome de remote).

**Commits:** v0.5.0 (ver `git log`).

**Reversão:** aditiva. Rollback = `git revert` do commit de v0.5.0. O gate desliga por env (`KOBE_CODER_GATE_DEPLOY=false`) e já nasce inativo sem `KOBE_CODER_PUBLIC_REMOTES`.

## [0.4.0] — 2026-06-22 — Gates determinísticos + isolamento por worktree (Fase 1 do upgrade)

**Operador pediu:** seguir o plano-mestre V3 implementando a Fase 1 — as travas de código (o "trilho antes do trem") — sob o rito de quatro etapas, com revisão adversarial multi-agente.

**Por quê:** a Fase 0 deu à sessão as *regras do jogo* (o harness), mas elas eram só prosa que o LLM honra. A Fase 1 transforma as regras críticas em **travas de código** que a sessão autônoma (que roda sob `bypassPermissions`) não consegue pular — porque o que tem resposta certa e não pode driftar é código, não julgamento (§12 do plano).

**Foi feito:**
- **Hook `guard.py` (PreToolUse)** — enforcement real: verificado empiricamente que um hook que devolve `permissionDecision:deny` bloqueia a ferramenta **mesmo sob bypassPermissions**. Gates: **deny-list** de destrutivos (rm recursivo em qualquer forma, force/mirror/delete push, reset --hard, clean, restore/checkout ., DROP/TRUNCATE/DELETE, publish, systemctl/service/pkg, chmod -R 777, indireção base64|sh/eval/pipe-pra-interpretador); **gate de changelog** (commit exige arquivo de changelog no staged; escape `[wip]`); **gate PARA-e-espera-OK** (edição de código de produção negada até aprovação do plano; `.local/` livre); **HALT** (conflito de regras → nega ação mutante, exceto comunicação).
- **Wiring no worker** via `--settings` gerado por sessão. O path do state vai no **argv do hook**, não no env da sessão — a sessão não conhece o path do próprio cadeado e não pode reescrever `plan_approved`/`halted` por Bash. Settings efêmero em subdir `.settings/` (não colide com a busca de sessão).
- **Estado novo** (`run_remote.py`): `plan_approved`, `halted`, `halt_reason`, campos de worktree. Comandos: `--approve-plan` (start/resume), `--clear-halt` (resume), `halt`, `merge`. Agent def atualizado: o Hal passa `--approve-plan` ao detectar a aprovação do operador (detecção = LLM, liberação do gate = código).
- **Isolamento por worktree + lock de merge** (`KOBE_CODER_WORKTREE`, **default OFF** por reversibilidade): cada sessão roda numa `git worktree` própria; merge de volta serializado por `flock`, conservador — registra branch+sha de origem, recusa detached HEAD / branch errada / árvore suja / worktree suja, `branch -d` (não `-D`), grava o sha pré-merge como caminho de volta (§5.1). Nunca força, nunca auto-resolve conflito.

**Testes (dev VPS):** suíte de ~70 casos do guard cobrindo cada bypass que a revisão adversarial encontrou (rm flags separadas/long, redirect no carve-out, DELETE com WHERE, push mirror/delete/refspec, systemctl kill, base64|sh, etc.) + proteção do state + plan gate + HALT comm-only + fail-closed; suíte de worktree (setup/merge/cleanup/dirty-safety/non-git); **2 testes de integração com `claude -p` real** confirmando deny-list e plan-gate bloqueando sob bypassPermissions com o settings real. Todos passaram. **Revisão adversarial de 4 agentes** (bypass de deny-list, fluxo dos gates, segurança da worktree, correção do código) achou 5 blockers reais (state auto-gravável, colisão de glob do settings, merge cego, force-remove, HALT mudo) + majors — **todos corrigidos e re-testados** antes de fechar. Resíduo honesto: indireção arbitrária (travessia cega do FS pra achar o state) não é 100% pegável por regex — mitigado (vetores diretos fechados, fail-closed em corrupção), e é em si violação de contrato tratável como HALT.

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

**Testes (dev VPS):** `py_compile` + import smoke dos dois scripts; suíte funcional de `_build_system_prompt` cobrindo C-presente, C-ausente, harness-ausente e base-ausente (degrada sem crashar); verificação de que o prompt montado não contém nenhuma instrução de dependência do manual pessoal (A) e que `~/.claude` só aparece na nota documental de resíduo; `_build_prompt` sem o parâmetro morto. Todos passaram. Revisão multi-lente independente (4 agentes: fidelidade ao plano, portabilidade, correção do código, coerência código-vs-LLM) + síntese — 5 majors e vários minors aplicados antes de fechar. Validação final de produto = operador, em uso real.

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
