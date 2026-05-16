# Changelog

Todas as mudanças notáveis deste projeto ficam aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

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
