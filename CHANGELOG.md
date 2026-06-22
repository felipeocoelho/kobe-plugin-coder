# Changelog

Todas as mudanças notáveis deste projeto ficam aqui.

> **A partir de v0.3.0** o changelog segue o **formato auditável** do harness do Coder (§6 do `harness/CONTRACT.md`): cada mudança registra *o que o operador pediu*, *por quê*, *o que foi feito*, *o que foi testado*, *os commits* e *como reverter*. É a trilha de auditoria da codificação — auditoria, reversibilidade e teste no mesmo lugar. Entradas anteriores seguem o [Keep a Changelog](https://keepachangelog.com/).

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
