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
| `/coder <missão>` | Dispara nova sessão. Sessão remota produz plano em anexo antes de codar (a partir de v0.2.0). |
| `/coder_status` (ou `/coder-status`) | Lista sessões do tópico **e** instâncias Claude Code ativas cross-tópico (presença global). |
| Texto livre tipo "continua o que estava fazendo no projeto X" | Subagente busca sessão idle do tópico e retoma. |

O subagente coder decide entre `start` e `resume` lendo o estado das sessões em `user-data/coder-sessions/<topic>/` e o contexto da mensagem.

### Plano antes de codar (v0.2.0+)

Em missão nova não-trivial, a sessão remota:

1. Lê o contexto e escreve `.local/plano-<slug>.md` na cwd do projeto.
2. Anexa o plano via Telegram (`kobe-attach`).
3. **Para** o turno aguardando aprovação do operador.

O operador lê pelo Telegram (ou baixa, ou abre no Claude Code local), aprova ou pede ajustes. Só depois a sessão executa, marcando o checklist `- [ ]` → `- [x]` conforme avança e mandando `kobe-notify` curto a cada marco.

Tarefas triviais (1-liner, typo, rename simples) podem pular o plano — a sessão decide e avisa.

### Aviso de pasta ocupada (v0.2.0+)

Antes de spawnar a sessão remota, o `run_remote.py start` consulta o registro de presença. Se outra instância Claude Code já está na mesma cwd, retorna `warning: presence_conflict` sem disparar nada. O subagente pergunta ao operador pelo Telegram; resposta afirmativa faz reinvocar com `--force`.

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

## Sistema de presença

Pasta: `$KOBE_HOME/user-data/claude-presence/<pid>.json`. Cada instância Claude Code grava ao iniciar, remove ao terminar. Formato:

```json
{
  "pid": 12345,
  "cwd": "/home/<user>/projetos/foo",
  "session_id": "uuid-ou-null",
  "source": "telegram-coder",
  "topic_key": "general",
  "started_at": "2026-05-16T12:34:56+00:00"
}
```

Cleanup é inline: toda função que LÊ a pasta faz `kill -0 <pid>` em cada arquivo e remove os órfãos. PID morto nunca confunde resultado.

A sessão remota disparada pelo plugin registra `source=telegram-coder` automaticamente — sem ação do operador.

### Hook opcional pra o Claude Code local

Pra o claude local do operador também aparecer no `/coder_status`, adicione no `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "KOBE_HOME=$HOME/kobe python3 $HOME/kobe/plugins/public/coder/scripts/presence.py register --source local-claude --cwd \"$PWD\""
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "KOBE_HOME=$HOME/kobe python3 $HOME/kobe/plugins/public/coder/scripts/presence.py unregister"
          }
        ]
      }
    ]
  }
}
```

Ajuste o `KOBE_HOME` e o path do plugin conforme sua instalação. Hook é opcional — sem ele, o claude local não aparece no status (o resto do plugin continua funcionando normalmente).

## Harness do Coder — as regras do jogo (v0.3.0+)

A partir de v0.3.0 o Coder carrega um **harness próprio, portável e autocontido** — `harness/CONTRACT.md` — no system prompt de toda sessão remota. É o "Contrato do Coder": reversibilidade absoluta, o rito de quatro etapas (Planejamento → Advogado do Diabo → Revisão → Testes), guardrails de autonomia, modelo aditivo de regras, changelog auditável, e o contrato de deploy (4 ambientes via git). Junto vão os **baselines de qualidade** (`harness/baselines/`), cópia self-contained usada como lentes do crivo de revisão.

O motor monta o prompt em três camadas, de forma determinística (`coder_worker.py::_build_system_prompt`):

1. **Base operacional** (`prompts/remote-system.md`) — protocolo de comunicação com o Telegram, fim de turno, regras destrutivas.
2. **Harness do Coder (B)** (`harness/CONTRACT.md`) — as regras do jogo, injetadas porque NÃO vêm da cwd.
3. **Contrato do projeto (C)** — o `CLAUDE.md` do projeto-alvo, carregado nativamente pelo Claude Code por a sessão rodar na cwd do projeto. O motor anexa uma nota determinística sobre a presença/ausência de C.

**O manual pessoal do operador (A) nunca é carregado pelo motor.** O harness é portável: uma sessão que tem só B + C tem tudo que precisa para rodar limpo — inclusive para um operador que não seja o original. Convenções que não estão em B nem em C são preferência do operador, e a sessão **pergunta** em vez de chutar a partir de um ambiente pessoal específico.

### Gates determinísticos (v0.4.0+)

A sessão remota roda sob travas de código reais — o hook `harness/hooks/guard.py` (PreToolUse) é injetado via `--settings` e **nega** ações mesmo sob `bypassPermissions`:

| Gate | Env (default) | O que faz |
|---|---|---|
| Deny-list | `KOBE_CODER_GATE_DENYLIST=on` | Bloqueia destrutivo duro (rm recursivo, force/mirror/delete push, reset --hard, DROP/TRUNCATE/DELETE, publish, systemctl stop/kill, etc.) e indireção (base64\|sh, eval). A sessão pede OK ao operador. |
| Changelog | `KOBE_CODER_GATE_CHANGELOG=on` | `git commit` exige um arquivo de changelog no staged diff. Escape auditável: `[wip]` na mensagem (commit-rede-de-segurança intermediário). |
| PARA-e-espera | `KOBE_CODER_GATE_PLAN=on` | Antes da aprovação do plano, nega edição de código de produção (rascunhos em `.local/` são livres). Liberado com `--approve-plan` no resume. |
| HALT | (estado) | Conflito de regras (§7.1) → nega ação mutante até `--clear-halt`. Comunicação (`kobe-notify`) segue permitida. |

O path do state vai no **argv do hook**, não no env da sessão — a sessão não alcança o próprio cadeado. Os gates desligam por env (reversibilidade) sem reverter código.

**Isolamento por worktree** (`KOBE_CODER_WORKTREE`, default **off**): cada sessão numa `git worktree` própria; merge de volta serializado por lock e conservador (`run_remote.py merge --session <id>`) — recusa árvore suja, detached HEAD ou branch errada, registra o sha pré-merge como caminho de volta, nunca força.

### Convenções da sessão remota

O system prompt (base operacional + harness) instrui o claude remoto a:

- Trabalhar autônomo (sem perguntas interativas — `input()` não funciona).
- Sempre mandar `kobe-notify` antes de encerrar o turno (com prefixo 🟢, 🟡, ✅, 🔴, ℹ️).
- Operar sob harness (B) + contrato do projeto (C); ler o `CLAUDE.md` do projeto quando incerto sobre convenção.
- Não tentar `claude` recursivo, não rodar destrutivos sem confirmar.

O system prompt é agnóstico — não menciona um operador específico.

## Mensagens "no meio" do desenvolvimento

**Responsabilidade do emissor:** se a mensagem é direcionada à sessão remota, deixe claro pelo contexto. Se é conversa com o agente principal, idem. O agente principal usa essa pista pra decidir entre repassar (`resume`) ou responder direto.

Não há detecção automática "tem sessão ativa → repassa". Em ambiguidade, o agente principal pergunta.

## Limites

- Cada turno é single-thread: o operador não interrompe um turno em andamento. A próxima msg vira input pro próximo turno.
- `--append-system-prompt` é passado via argv. O prompt montado (base operacional + harness `CONTRACT.md`) tem ~28KB — folgadíssimo dentro do ARG_MAX (limite prático ~500KB). O contrato do projeto (C) não é inlinado (o Claude Code já o carrega pela cwd), então não pesa aqui.
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
