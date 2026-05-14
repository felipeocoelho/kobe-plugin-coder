# Changelog

Todas as mudanças notáveis deste projeto ficam aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Estrutura inicial do plugin: manifest `kobe-plugin.md`, README, agent definition (`claude/agents/coder.md`), system prompt da sessão remota (`prompts/remote-system.md`), scripts `run_remote.py` (CLI) e `coder_worker.py` (worker em background).
- Modelo arquitetural: sessão remota é disparada e termina entre turnos; retomada via `claude --resume <session-id>`. Estado em `user-data/coder-sessions/<topic>/<uuid>.json`.
- CLI standalone pra debug via SSH: `start`, `resume`, `list`, `status`.
- Detecção de crash via PID test ao listar sessões.
- Heurística pra avisar quando o turno encerrou sem `kobe-notify` explícito.
