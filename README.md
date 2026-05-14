# kobe-plugin-coder

Plugin **público** do [Kobe](https://github.com/felipeocoelho/kobe) que permite ao operador disparar sessões de `claude -p` na VPS conversando pelo Telegram — em vez de precisar logar via SSH. As sessões rodam **em background**, com `--permission-mode bypassPermissions`, e se comunicam com o operador via `kobe-notify` (texto) e `kobe-attach` (arquivos).

> Útil quando você quer mandar uma missão de código por áudio/texto, deixar o agente trabalhar autônomo, e voltar pra responder quando ele te chamar.

## Arquitetura — "Modelo A" (sessão morre entre turnos)

```
Operador → Telegram → bot do Kobe → agente principal
                                  → Agent(subagent_type="coder", missão="...")
                                    → run_remote.py start
                                      → fork (detach):
                                        coder_worker.py
                                        ↳ claude -p --session-id X
                                          ↳ trabalha autônomo
                                          ↳ kobe-notify nos marcos
                                          ↳ sai quando turno termina
                                        ↳ atualiza state.json (status=idle)
                                    → retorna { session_id, short_id, ... }
                                  → resposta curta ao operador
```

Quando o operador volta com mensagem nova no mesmo tópico:

```
Telegram → bot do Kobe → agente principal
                       → Agent(coder, "operador respondeu Y na sessão X")
                         → run_remote.py resume --session X --input "Y"
                           → fork coder_worker.py modo=resume
                             → claude -p --resume X (com Y como prompt)
```

A memória entre turnos é preservada pelo próprio Claude Code via `--session-id` / `--resume`. Como cada turno é um processo novo, sobrevive a reboot da VPS — basta retomar.

## Comandos do operador

| Comando | Efeito |
|---|---|
| `/coder <missão>` | Dispara nova sessão. |
| `/coder-status` | Lista sessões do tópico atual com status e idade. |
| Texto livre tipo "continua o que estava fazendo no projeto X" | Subagente busca sessão idle do tópico e retoma. |

O subagente coder decide entre `start` e `resume` lendo o estado das sessões em `user-data/coder-sessions/<topic>/` e o contexto da mensagem.

## Estado

Cada sessão tem:
- `user-data/coder-sessions/<topic>/<uuid>.json` — metadados (status, cwd, missão, pid, log_path, ...)
- `user-data/coder-sessions/<topic>/<uuid>.log` — stream-json e stderr do claude

`<topic>` = `general` se sem thread, ou `KOBE_THREAD_ID` numérico.

Status possíveis:
- `starting` — state criado, worker disparando
- `running` — claude -p ativo
- `idle` — turno encerrou normalmente, esperando próximo input
- `failed` — claude saiu com exit != 0
- `crashed` — worker morreu sem fechar state (detectado em listagem via PID test)
- `terminated` — SIGTERM (raro, manual)

## CLI direto (debug)

Útil quando logado na VPS via SSH:

```bash
# nova sessão
KOBE_HOME=$PWD KOBE_THREAD_ID= python plugins/public/coder/scripts/run_remote.py \
  start --cwd /home/$USER/projetos/foo --mission "implementa feature X"

# listar sessões do tópico atual
KOBE_HOME=$PWD python plugins/public/coder/scripts/run_remote.py list

# listar todos os tópicos
KOBE_HOME=$PWD python plugins/public/coder/scripts/run_remote.py list --all

# resumir uma sessão (aceita short-id de 8 chars)
KOBE_HOME=$PWD python plugins/public/coder/scripts/run_remote.py resume \
  --session abc12345 --input "agora roda os testes"

# status detalhado
KOBE_HOME=$PWD python plugins/public/coder/scripts/run_remote.py status --session abc12345
```

Saída é sempre JSON no stdout.

## Convenções da sessão remota

O system prompt em `prompts/remote-system.md` instrui o claude remoto a:

- Trabalhar autônomo (sem perguntas interativas — `input()` não funciona).
- Sempre mandar `kobe-notify` antes de encerrar o turno (com prefixo 🟢, 🟡, ✅, 🔴, ℹ️).
- Honrar o CLAUDE.md global do operador (se existir em `~/.claude/CLAUDE.md`).
- Não tentar `claude` recursivo, não rodar destrutivos sem confirmar.

O system prompt é agnóstico — não menciona um operador específico. A identidade do operador é carregada pelo próprio Claude Code via `CLAUDE.md` global + `user-data/identity/USER.md` do Kobe (mesmo lugar que o agente principal lê).

## Mensagens "no meio" do desenvolvimento

**Responsabilidade do emissor:** se a mensagem é direcionada à sessão remota, deixe claro pelo contexto. Se é conversa com o agente principal, idem. O agente principal usa essa pista pra decidir entre repassar (`resume`) ou responder direto.

Não há detecção automática "tem sessão ativa → repassa". Em ambiguidade, o agente principal pergunta.

## Limites

- Cada turno é single-thread: o operador não interrompe um turno em andamento. A próxima msg vira input pro próximo turno.
- `--append-system-prompt` é passado via argv. Se o prompt do `prompts/remote-system.md` crescer demais (>~500KB), pode estourar ARG_MAX. Hoje tem ~3KB.
- Worker não escreve em `user-data/coder-sessions/` fora do `state.json` e do `.log` — não pode "consumir" sessões (arquivar é manual ou via comando futuro).

## Instalação

```bash
cd /caminho/pra/kobe
bash infra/install-plugin.sh https://github.com/felipeocoelho/kobe-plugin-coder.git
systemctl --user restart kobe
```

O Kobe escaneia `plugins/{public,private}/*` no startup e simlinka o subagente em `.claude/agents/coder.md`.

## Requisitos

- Kobe rodando na VPS, com bot Telegram ativo.
- CLI `claude` instalado e funcional no PATH.
- Python 3.10+ (já requerido pelo Kobe).

## Licença

MIT.
