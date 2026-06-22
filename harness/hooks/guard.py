#!/usr/bin/env python3
"""guard.py — hook PreToolUse do harness do Coder: as travas determinísticas.

Enforcement de código dos gates do contrato (§4, §5, §6, §7). Roda como hook
`PreToolUse` do Claude Code antes de cada chamada de ferramenta da sessão remota
e pode **negar** a ação — mesmo sob `--permission-mode bypassPermissions`
(verificado: o hook tem precedência sobre o bypass). O wrapper só lê o stream
DEPOIS que a ação rodou; o hook intercepta ANTES — por isso a interceptação de
`rm -rf`/commit-sem-changelog tem que ser hook.

Contrato do hook (Claude Code):
- stdin: JSON com `hook_event_name`, `tool_name`, `tool_input`, `cwd`, ...
- stdout p/ negar: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": "<por quê>"}}
- exit 0 sem output = deixa o fluxo normal seguir.

**Caminho do state via argv (`--state <path>`), NÃO via env da sessão.** O sinal
de aprovação/HALT vive no state.json; se a sessão (que roda Bash sob bypass)
soubesse o path, poderia reescrever o próprio cadeado. Então o worker passa o
path no comando do hook (no settings.json), fora do alcance da sessão, e a
deny-list ainda barra qualquer Bash que toque `coder-sessions`.

Gates (liga/desliga por env — reversibilidade):
- **Deny-list** (`KOBE_CODER_GATE_DENYLIST`, default on) — destrutivo duro (§4/§6)
  + proteção do próprio state. A sessão é instruída a pedir OK ao operador.
- **Gate de changelog** (`KOBE_CODER_GATE_CHANGELOG`, default on) — `git commit`
  exige arquivo de changelog no staged diff (§6.2). Escape auditável: `[wip]`.
  Best-effort: timing de `-a`/`add && commit` inline pode escapar (ver docstring
  do _check_changelog_gate).
- **Gate PARA-e-espera-OK** (`KOBE_CODER_GATE_PLAN`, default on) — antes da
  aprovação, bloqueia Edit/Write de código de produção (fora de `.local/`), §10.
- **HALT** — `halted: true` no state → nega toda ação mutante até arbitragem
  (§7.1). Exceção: helpers de comunicação puros (a sessão precisa poder pedir
  socorro mesmo travada).

Fail-CLOSED quando `--state` é dado mas o arquivo não parseia (suspeita de
adulteração): nega Edit de código e ações mutantes. Fail-OPEN só quando não há
state algum (sessão antiga / instalação sem o campo).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _env_on(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


_ASK_OPERATOR = (
    " Esta é uma ação da lista dura do contrato (§4/§6): exige OK explícito do "
    "operador. Pare, mande um `kobe-notify` nomeando exatamente o que precisa "
    "rodar e por quê, e encerre o turno aguardando a decisão dele. Não contorne "
    "o gate — proponha um caminho reversível ou peça pro operador executar."
)


# === Tokenização e helpers de flag ========================================
def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


_SHELL_META = ("&", ";", "|", "$", "`", "(", ")", ">", "<", "\n")


def _has_meta(command: str) -> bool:
    return any(m in command for m in _SHELL_META)


def _flag_has(tokens: list[str], short_letters: str, long_names: tuple[str, ...]) -> bool:
    """True se algum token for um short-flag contendo qualquer letra de
    `short_letters` (ex.: 'r' em '-rf') ou um long-flag em `long_names`."""
    for t in tokens:
        if t.startswith("--"):
            name = t[2:].split("=", 1)[0]
            if name in long_names:
                return True
        elif t.startswith("-") and len(t) > 1 and not t.startswith("--"):
            body = t[1:]
            if any(c in body for c in short_letters):
                return True
    return False


def _word(cmd: str, *words: str) -> bool:
    """Algum dos `words` aparece como palavra isolada (\\b...\\b), case-insensitive."""
    return any(re.search(rf"(?<![\w./-]){re.escape(w)}(?![\w./-])", cmd, re.IGNORECASE) for w in words)


# === Deny-list ============================================================
def _denylist_reason(command: str, tokens: list[str]) -> str | None:
    """Retorna o rótulo do motivo se o comando é destrutivo/proibido, senão None.
    Robusto a ordem/agrupamento de flags e a long-flags (lições da revisão)."""
    c = command

    # rm recursivo (qualquer forma: -rf, -r -f, -R, --recursive). Remoção
    # recursiva sempre pede OK — independente de -f.
    if _word(c, "rm") and _flag_has(tokens, "rR", ("recursive",)):
        return "rm recursivo (deleção recursiva)"
    # find que apaga / executa rm
    if _word(c, "find") and (re.search(r"-(exec|execdir|ok)\s+rm\b", c) or re.search(r"\s-delete\b", c)):
        return "find -delete / -exec rm"
    if _word(c, "find") and re.search(r"\|\s*xargs\b[^|]*\brm\b", c):
        return "find | xargs rm"
    if re.search(r"\b(shred|wipe|mkfs(\.\w+)?)\b", c, re.IGNORECASE):
        return "shred/wipe/mkfs"
    if re.search(r"\bdd\b[^|]*\bof=/dev/", c):
        return "dd of=/dev/ (sobrescrita de device)"
    if re.search(r">\s*/dev/(sd|nvme|vd|mmcblk|hd)\w", c):
        return "escrita direta em device de bloco"
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}", c):
        return "fork bomb"

    # Git destrutivo / reescrita de história
    if _word(c, "git"):
        if _word(c, "push") and (
            _flag_has(tokens, "f", ("force", "force-with-lease", "mirror", "delete", "prune"))
            or re.search(r"\bpush\b[^\n]*\s\+\S", c)        # refspec +ref força
            or re.search(r"\bpush\b[^\n]*\s:\S", c)          # delete via :ref
        ):
            return "git push force/mirror/delete/refspec destrutivo"
        if _word(c, "reset") and _flag_has(tokens, "", ("hard",)):
            return "git reset --hard"
        if _word(c, "clean") and _flag_has(tokens, "f", ("force",)):
            return "git clean -f (apaga não-rastreados)"
        if _word(c, "branch") and (
            _flag_has(tokens, "D", ()) or (_flag_has(tokens, "d", ("delete",)) and _flag_has(tokens, "f", ("force",)))
        ):
            return "git branch -D (deleta branch não-mergeada)"
        if re.search(r"\bcheckout\b\s+(--\s+)?\.(\s|$)", c) or re.search(r"\brestore\b[^\n]*(--worktree|\s\.)(\s|$)", c):
            return "git checkout/restore . (descarta árvore)"
        if _word(c, "filter-branch") or re.search(r"\bfilter-repo\b", c):
            return "git filter-branch/filter-repo (reescreve história)"

    # Banco destrutivo — "em massa" não é detectável por sintaxe; bloqueia amplo.
    if re.search(r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|FUNCTION|ROLE|TYPE|TRIGGER|SEQUENCE)\b", c, re.IGNORECASE):
        return "DROP destrutivo"
    if re.search(r"\bTRUNCATE\s+(TABLE\s+)?\w", c, re.IGNORECASE):
        return "TRUNCATE TABLE"
    if re.search(r"\bDELETE\s+FROM\s+\w", c, re.IGNORECASE):
        return "DELETE FROM (deleção de dados — pede OK)"
    if re.search(r"\bALTER\s+TABLE\s+\w+[^\n]*\bDROP\s+COLUMN\b", c, re.IGNORECASE):
        return "ALTER TABLE ... DROP COLUMN"

    # Publicação irreversível (toca terceiros / usuário público)
    if re.search(r"\b(npm|yarn|pnpm)\s+publish\b", c):
        return "publish de pacote npm/yarn/pnpm"
    if re.search(r"\b(twine\s+upload|pip\s+upload)\b", c):
        return "publish PyPI"
    if _word(c, "cargo") and _word(c, "publish"):
        return "cargo publish"
    if re.search(r"\bdocker\b[^\n]*\bpush\b", c):
        return "docker push"
    if re.search(r"\bgh\s+release\s+create\b", c):
        return "gh release create (lançamento público)"

    # Sistema / infra
    if re.search(r"\bsystemctl\b[^\n]*\b(stop|disable|mask|kill)\b", c):
        return "systemctl stop/disable/mask/kill"
    if re.search(r"\bservice\s+\S+\s+stop\b", c):
        return "service ... stop"
    if _word(c, "crontab") and _flag_has(tokens, "r", ()):
        return "crontab -r"
    if re.search(r"\b(apt|apt-get|dnf|yum)\b[^\n]*\b(remove|purge|autoremove)\b", c):
        return "remoção de pacote (apt/dnf/yum)"
    if re.search(r"\bpacman\b[^\n]*\s-R", c) or re.search(r"\b(snap|flatpak)\b[^\n]*\b(remove|uninstall)\b", c):
        return "remoção de pacote (pacman/snap/flatpak)"
    if re.search(r">\s*/etc/(passwd|shadow|sudoers)", c):
        return "escrita em /etc/passwd|shadow|sudoers"
    if _word(c, "chmod") and _flag_has(tokens, "R", ("recursive",)) and re.search(r"\b0?777\b", c):
        return "chmod recursivo 777"

    # Indireção que contorna a análise léxica (mitigação dos vetores triviais)
    if re.search(r"\|\s*(sh|bash|zsh|dash|python\d?|perl|ruby|node)\b", c):
        return "pipe pra interpretador (sh/bash/python/...) — indireção"
    if re.search(r"\bbase64\b\s+(-d|--decode)\b", c) or re.search(r"\beval\b", c):
        return "base64 -d / eval — indireção"

    return None


# Helpers de comunicação só MANDAM texto; uma msg que MENCIONA `rm -rf` não pode
# ser barrada. Mas o carve-out só vale pra invocação PURA, sem NENHUM
# metacaractere de shell — senão um redirect (`kobe-notify x > /etc/passwd`) ou
# encadeamento esconderia ação destrutiva (lição da revisão: M2).
_COMM_RX = re.compile(r"^\s*(\S*bot/bin/)?kobe-(notify|attach)\b.*$")


def _is_comm_only(command: str) -> bool:
    if _has_meta(command):
        return False
    return bool(_COMM_RX.match(command))


# === Proteção do próprio state (B1) =======================================
def _touches_control_plane(command: str, state_path: str | None) -> bool:
    """A sessão não tem o que fazer em `coder-sessions` (seu plano de controle)
    nem no próprio state. Qualquer referência (read OU write) é negada — fecha o
    vetor de auto-aprovação/auto-destravamento de HALT."""
    # Referência direta ao plano de controle do Coder. Preciso (não pega
    # indireção por travessia cega — ver nota de resíduo no CONTRACT §10.2 —,
    # mas isso já seria violação do contrato, tratável como HALT, fora do escopo
    # de defesa por regex; over-bloquear `user-data` daria falso-positivo em
    # projetos-alvo legítimos que têm essa pasta).
    if "coder-sessions" in command or "coder-worktrees" in command:
        return True
    if state_path and (state_path in command or "KOBE_CODER_STATE_FILE" in command):
        return True
    return False


# === Gate de changelog ====================================================
_CHANGELOG_RX = re.compile(r"(^|/)(change\s?log|history)(\.\w+)?$", re.IGNORECASE)
_GIT_COMMIT_RX = re.compile(r"\bgit\b[^\n|&;]*\bcommit\b")
_WIP_RX = re.compile(r"\[wip\]", re.IGNORECASE)


def _is_git_commit(command: str) -> bool:
    if not _GIT_COMMIT_RX.search(command):
        return False
    if re.search(r"\bcommit\b[^\n]*--(help|dry-run)\b", command):
        return False
    return True


def _effective_git_cwd(command: str, cwd: str) -> str:
    """Extrai o dir efetivo do commit: `git -C X commit` ou `cd X && git commit`.
    Best-effort — fecha o bypass de `cd ../outro-repo` (M5)."""
    m = re.search(r"\bgit\b\s+-C\s+(\S+)", command)
    if m:
        cand = m.group(1).strip("'\"")
        return cand if os.path.isabs(cand) else os.path.join(cwd, cand)
    m = re.search(r"\bcd\s+(\S+)\s*&&", command)
    if m:
        cand = m.group(1).strip("'\"")
        return cand if os.path.isabs(cand) else os.path.join(cwd, cand)
    return cwd


def _staged_files(cwd: str, include_modified: bool) -> list[str]:
    files: list[str] = []
    try:
        out = subprocess.run(["git", "-C", cwd, "diff", "--cached", "--name-only"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            files += [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        if include_modified:  # git commit -a/-am: tracked modificados também entram
            out2 = subprocess.run(["git", "-C", cwd, "diff", "--name-only"],
                                  capture_output=True, text=True, timeout=10)
            if out2.returncode == 0:
                files += [ln.strip() for ln in out2.stdout.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return files
    return files


def _check_changelog_gate(command: str, cwd: str, tokens: list[str]) -> None:
    if not _is_git_commit(command):
        return
    if _WIP_RX.search(command):
        return
    gcwd = _effective_git_cwd(command, cwd)
    dash_a = _flag_has(tokens, "a", ("all",))
    staged = _staged_files(gcwd, include_modified=dash_a)
    if not staged:
        # Nada a inspecionar (commit vazio falha sozinho, ou staging inline que
        # não conseguimos ver). Não bloqueamos — best-effort, documentado.
        return
    if any(_CHANGELOG_RX.search(f) for f in staged):
        return
    _deny(
        "[guard:changelog] Commit bloqueado: nenhum arquivo de changelog entre as "
        "mudanças que vão pro commit. O contrato (§6.2) exige que todo commit "
        "carregue a entrada de changelog (o quê/por quê/feito/testes/reversão). "
        "Atualize o CHANGELOG e dê `git add` antes. Commit-rede-de-segurança "
        "intermediário: inclua `[wip]` na mensagem (fica auditável)."
    )


# === HALT + gate PARA-e-espera (dependem do state) ========================
_MUTATING_TOOLS = {"Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def _load_state(state_path: str | None) -> tuple[dict | None, bool]:
    """Retorna (state, corrupted). corrupted=True quando o path foi dado mas o
    arquivo não parseia → fail-CLOSED (suspeita de adulteração)."""
    if not state_path:
        return None, False
    p = Path(state_path)
    if not p.is_file():
        return None, False
    try:
        return json.loads(p.read_text(encoding="utf-8")), False
    except Exception:  # noqa: BLE001
        return None, True


def _edited_path(tool_name: str, tool_input: dict) -> str | None:
    if tool_name in ("Edit", "Write", "MultiEdit"):
        return tool_input.get("file_path") or tool_input.get("path")
    if tool_name == "NotebookEdit":
        return tool_input.get("notebook_path")
    return None


def _is_local_or_plan(path: str) -> bool:
    try:
        return ".local" in Path(path).parts
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None, help="path do state.json da sessão (fora do env da sessão)")
    args, _ = ap.parse_known_args()

    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    command = tool_input.get("command") or "" if tool_name == "Bash" else ""
    tokens = _tokens(command) if command else []

    state, corrupted = _load_state(args.state)

    # Fail-CLOSED: state setado mas ilegível = suspeita de adulteração.
    if corrupted and tool_name in _MUTATING_TOOLS:
        if not (tool_name == "Bash" and _is_comm_only(command)):
            _deny(
                "[guard:fail-closed] O state da sessão está ilegível (possível "
                "adulteração). Por segurança, ações mutantes estão bloqueadas. "
                "Avise o operador via kobe-notify e encerre o turno."
            )

    # HALT: nada mutante passa — EXCETO comunicação pura (pedir socorro).
    if state is not None and state.get("halted") and tool_name in _MUTATING_TOOLS:
        if not (tool_name == "Bash" and _is_comm_only(command)):
            reason = state.get("halt_reason") or "parada dura sinalizada"
            _deny(
                f"[guard:HALT] Sessão em HALT ({reason}). Nenhuma ação mutante até "
                "o operador arbitrar (§7.1). Você ainda pode usar kobe-notify pra "
                "explicar; depois encerre o turno aguardando a decisão dele."
            )

    if tool_name == "Bash":
        # Proteção do plano de controle (state) — antes da deny-list geral.
        if _touches_control_plane(command, args.state):
            _deny(
                "[guard:control-plane] Comando bloqueado: a sessão não toca em "
                "`user-data/coder-sessions` nem no próprio state (é o plano de "
                "controle do Coder). Se precisa de algo daí, peça ao operador."
            )
        if _env_on("KOBE_CODER_GATE_DENYLIST") and not _is_comm_only(command):
            label = _denylist_reason(command, tokens)
            if label:
                _deny(f"[guard:deny-list] Comando bloqueado — {label}." + _ASK_OPERATOR)
        if _env_on("KOBE_CODER_GATE_CHANGELOG"):
            _check_changelog_gate(command, cwd, tokens)

    # Gate PARA-e-espera-OK.
    if state is not None and _env_on("KOBE_CODER_GATE_PLAN") and not state.get("plan_approved"):
        if "plan_approved" in state:  # sessão antiga sem o campo: não-gated
            path = _edited_path(tool_name, tool_input)
            if path is not None and not _is_local_or_plan(path):
                _deny(
                    "[guard:plan] Edição de código de produção bloqueada: o plano "
                    "ainda não foi aprovado pelo operador (§10 — PARA e espera OK). "
                    "Escreva o plano em `.local/plano-<slug>.md`, anexe via "
                    "kobe-attach e encerre o turno aguardando o OK. Rascunhos em "
                    "`.local/` são livres."
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
