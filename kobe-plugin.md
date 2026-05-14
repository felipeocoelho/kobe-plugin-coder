---
name: coder
visibility: public
version: 0.1.0
description: "Dispara sessões remotas de Claude Code em background na VPS — modo dev assíncrono via Telegram. O subagente recebe a missão, lança um `claude -p` em modo bypassPermissions no diretório do projeto, e devolve o controle imediatamente. A sessão remota trabalha sozinha, manda updates via kobe-notify e encerra o turno quando precisa de input ou conclui. Mensagens novas do operador no mesmo tópico podem ser repassadas pra sessão via `claude --resume <session-id>`, preservando a memória. Estado em `user-data/coder-sessions/<topic>/<session>.json`."
triggers:
  - "operador pede pra implementar/codar/refatorar/fazer fix em algum projeto na VPS"
  - "operador pede pra continuar trabalho de dev iniciado antes (resume da sessão coder ativa)"
  - "operador pede status de sessão remota em andamento"
  - "comando textual `/coder <missão>` (dispara nova sessão) ou `/coder-status` (lista sessões do tópico)"
agent_definition: claude/agents/coder.md
dependencies:
  python: []
  system:
    - claude  # CLI do Claude Code; já presente no Kobe-base
env:
  required: []
  optional: []
---

# Coder — sessões remotas de Claude Code

Plugin público do Kobe. Permite ao operador disparar sessões de `claude -p` na VPS conversando pelo Telegram, sem precisar logar via SSH. A sessão trabalha em background com `bypassPermissions`, emite progresso pelo Telegram via `kobe-notify`, e encerra o turno quando concluiu ou precisa de input.

Mensagens posteriores do operador no mesmo tópico podem ser repassadas pra sessão via `claude --resume <session-id>` — a memória da sessão remota é preservada pelo próprio Claude Code.

## Arquitetura

```
Telegram → Kobe → agente principal
                → Agent(subagent_type="coder", missão="...")
                  → run_remote.py start ou resume
                    → fork: coder_worker.py (background, detached)
                      → claude -p --session-id X --output-format stream-json
                        ↳ trabalha autônomo (bypassPermissions)
                        ↳ emite kobe-notify nos marcos
                        ↳ sai quando turno termina
                      → atualiza state.json (status=idle)
                      → manda kobe-notify final
                  → retorna ao agente principal: "sessão X disparada"
```

Cada sessão tem um `state.json` em `$KOBE_HOME/user-data/coder-sessions/<topic-key>/<session-id>.json` com cwd, status, missão, pid e tempos. O agente principal lê essa pasta antes de decidir entre `start` e `resume`.

## Estado e onde os arquivos ficam

- Estado: `user-data/coder-sessions/<topic>/<uuid>.json`
- Log do stream-json: `user-data/coder-sessions/<topic>/<uuid>.log`
- `<topic>` = `general` ou o `KOBE_THREAD_ID` numérico.

`user-data/` é gitignored pelo Kobe-base — nada do estado das sessões sobe pra repo.

## Comandos do operador

| Comando | Efeito |
|---|---|
| `/coder <missão>` | Dispara nova sessão. Subagente decide cwd a partir da missão (pergunta se ambíguo). |
| `/coder-status` | Lista sessões ativas/idle do tópico atual. |
| Texto livre como "continua o que tava fazendo no projeto X" | Subagente busca sessão idle do tópico e resume. |

## Como a sessão remota se comunica

A sessão remota é instruída (via `--append-system-prompt`) a:
- Usar `$KOBE_HOME/bot/bin/kobe-notify` pra progresso e perguntas.
- Usar `$KOBE_HOME/bot/bin/kobe-attach` pra entregar artefatos.
- Encerrar o turno (sair) quando precisar de input — em vez de tentar ficar interativo.
- Prefixar mensagens com 🟢 (concluído), 🟡 (bloqueado / aguardando), ✅ (marco), 🔴 (erro), ℹ️ (info).

A próxima resposta do operador chega via `claude --resume <session-id>` injetando o texto novo.

## Limites conhecidos

- Sessão remota é single-thread: enquanto ela roda um turno, o operador não pode "interromper" pelo Telegram. A próxima msg vira input pro próximo turno.
- Concurrent sessions: cada tópico pode ter N sessões idle/running, mas o agente principal precisa desambiguar via missão ou perguntando ao operador.
- Crash recovery: se a VPS reiniciar com sessão `running`, o state fica como running mas o processo morreu. Próximo `/coder-status` detecta (PID inexistente) e marca como `crashed`.
