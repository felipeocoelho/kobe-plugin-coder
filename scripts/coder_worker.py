#!/usr/bin/env python3
"""coder_worker.py — wrapper de background pra sessão remota de Claude Code.

Roda como subprocess detached do `run_remote.py`. Lê o state.json, invoca
`claude -p` com a missão (start) ou input (resume), captura o stream-json
linha por linha pra atualizar `last_activity` e `last_text`, e fecha o
state.json com status final quando o claude sai.

NÃO é invocado diretamente pelo operador nem pelo agente — é lançado em
background por `run_remote.py`. Saída do próprio worker (Python) vai pra
arquivo de log junto com stdout/stderr do claude.

Uso (interno):
    python coder_worker.py --state-file <path> --mode <start|resume>

Convenção de envs (herdadas do bot do Kobe via cadeia de subprocess):
    KOBE_HOME, KOBE_TELEGRAM_BOT_TOKEN, KOBE_CHAT_ID, KOBE_THREAD_ID
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger("coder.worker")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_state(state_path: Path) -> dict:
    return json.loads(state_path.read_text(encoding="utf-8"))


def _write_state(state_path: Path, state: dict) -> None:
    # Escrita atômica: tmp + rename. Evita state vazio se o processo morrer
    # no meio da gravação (read concorrente do agente principal).
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def _patch_state(state_path: Path, **fields) -> dict:
    state = _read_state(state_path)
    state.update(fields)
    state["last_activity"] = _now_iso()
    _write_state(state_path, state)
    return state


def _notify_error(kobe_home: Path, msg: str) -> None:
    """Manda kobe-notify em caso de erro. Silencioso se envs ausentes."""
    notify_bin = kobe_home / "bot" / "bin" / "kobe-notify"
    if not notify_bin.is_file():
        return
    if not os.environ.get("KOBE_TELEGRAM_BOT_TOKEN") or not os.environ.get(
        "KOBE_CHAT_ID"
    ):
        return
    try:
        subprocess.run(
            [str(notify_bin), msg],
            timeout=15,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001 — best effort
        logger.exception("falha enviando kobe-notify de erro")


def _build_prompt(state: dict, mode: str, system_prompt: str) -> str:
    """Monta o prompt que vai pra stdin do claude.

    Em `start`, o prompt é a missão original + o system prompt como
    cabeçalho (porque o `--append-system-prompt` cobre isso). Em `resume`,
    é só a nova entrada do operador.
    """
    if mode == "start":
        return state["mission"]
    # resume
    return state.get("pending_input") or "(operador não passou conteúdo na retomada — continue de onde parou)"


def run_claude(
    *,
    state_path: Path,
    mode: str,
    kobe_home: Path,
) -> int:
    """Invoca o `claude` correto pro modo e atualiza o state ao longo.

    Retorna o exit code do claude (0 em sucesso, !=0 em falha).
    """
    state = _read_state(state_path)
    session_id = state["session_id"]
    cwd = Path(state["cwd"])
    log_path = Path(state["log_path"])

    # Append-system-prompt vai como string — leitura tem que estar pronta
    # antes do popen porque ARG_MAX comporta sem stress.
    system_prompt_file = Path(__file__).resolve().parent.parent / "prompts" / "remote-system.md"
    system_prompt = system_prompt_file.read_text(encoding="utf-8")

    if mode == "start":
        cmd = [
            "claude",
            "-p",
            "--session-id",
            session_id,
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--append-system-prompt",
            system_prompt,
        ]
    elif mode == "resume":
        cmd = [
            "claude",
            "-p",
            "--resume",
            session_id,
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--append-system-prompt",
            system_prompt,
        ]
    else:
        raise ValueError(f"modo inválido: {mode}")

    prompt = _build_prompt(state, mode, system_prompt)

    cwd.mkdir(parents=True, exist_ok=True)

    _patch_state(state_path, status="running", started_turn_at=_now_iso())

    # Abre log em modo append. stderr → stdout pra um único stream.
    log_fh = log_path.open("a", encoding="utf-8")
    log_fh.write(f"\n# --- turn @ {_now_iso()} mode={mode} ---\n")
    log_fh.flush()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
        )
    except FileNotFoundError:
        _patch_state(
            state_path,
            status="failed",
            exit_code=-1,
            last_text="claude CLI não encontrado no PATH do worker.",
        )
        _notify_error(
            kobe_home,
            "🔴 [coder] worker não conseguiu achar o CLI `claude`. "
            "Verifique o PATH do systemd ou a instalação.",
        )
        log_fh.close()
        return -1

    # Atualiza state com PID — pra crash detection futura.
    _patch_state(state_path, pid=proc.pid)

    # Manda o prompt via stdin e fecha.
    assert proc.stdin is not None
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except BrokenPipeError:
        # Claude pode ter morrido antes de ler. Vai cair no wait abaixo.
        pass

    # Consome stream-json linha por linha. Atualiza state periodicamente.
    last_text: str | None = state.get("last_text")
    last_persist_ts = 0.0
    PERSIST_EVERY = 5.0  # segundos
    assistant_texts: list[str] = []
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        log_fh.write(raw_line.decode("utf-8", errors="replace"))
        log_fh.flush()
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "assistant":
            msg = event.get("message") or {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        assistant_texts.append(txt)
                        last_text = txt
        elif etype == "result":
            txt = (event.get("result") or "").strip()
            if txt:
                last_text = txt
        # Persist barato e periódico — não cada linha pra evitar I/O excessivo
        now = time.time()
        if now - last_persist_ts >= PERSIST_EVERY:
            try:
                _patch_state(state_path, last_text=last_text)
            except Exception:  # noqa: BLE001
                logger.exception("falha atualizando state durante stream")
            last_persist_ts = now

    proc.wait()
    exit_code = proc.returncode
    log_fh.write(f"# --- turn end @ {_now_iso()} exit={exit_code} ---\n")
    log_fh.close()

    # Resultado final consolidado
    if last_text is None and assistant_texts:
        last_text = "\n".join(assistant_texts).strip() or None

    if exit_code == 0:
        new_state = _patch_state(
            state_path,
            status="idle",
            exit_code=exit_code,
            last_text=last_text,
            pending_input=None,  # consumido no turno
            turn_count=(state.get("turn_count") or 0) + 1,
        )
        # Heurística: se o último texto do assistant não contém kobe-notify
        # explícito (que ele rodou via Bash, não escreveu no texto), o
        # operador pode ter ficado sem feedback. Mandamos uma nota discreta.
        if last_text and (not _looks_like_kobe_notify_was_sent(new_state, kobe_home)):
            short_id = new_state["session_id"][:8]
            preview = (last_text or "").strip()
            if len(preview) > 350:
                preview = preview[:350].rstrip() + "…"
            _notify_error(
                kobe_home,
                (
                    f"ℹ️ [coder] turno encerrado (sessão `{short_id}`) — "
                    f"sem progresso explícito via kobe-notify. Última fala:\n\n"
                    f"{preview}"
                ),
            )
    else:
        _patch_state(
            state_path,
            status="failed",
            exit_code=exit_code,
            last_text=last_text,
            pending_input=None,
        )
        short_id = state["session_id"][:8]
        _notify_error(
            kobe_home,
            (
                f"🔴 [coder] sessão `{short_id}` saiu com erro "
                f"(exit={exit_code}). Veja `{state['log_path']}` pra detalhes."
            ),
        )

    return exit_code


def _looks_like_kobe_notify_was_sent(state: dict, kobe_home: Path) -> bool:
    """Heurística: o sub-claude rodou kobe-notify pelo menos uma vez neste turno?

    Lemos as últimas linhas do log e procuramos por chamada de Bash com
    `kobe-notify` ou `kobe-attach`. Não é perfeito, mas evita ruído quando
    a sessão remota fez o trabalho dela e mandou progresso.
    """
    log_path = Path(state["log_path"])
    if not log_path.is_file():
        return False
    try:
        # Últimos ~50KB são mais que suficientes pra cobrir um turno típico
        size = log_path.stat().st_size
        offset = max(0, size - 50_000)
        with log_path.open("rb") as fh:
            fh.seek(offset)
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    # Só conta marcadores DESTE turno (após o último "# --- turn @").
    last_turn_marker = tail.rfind("# --- turn @")
    if last_turn_marker >= 0:
        tail = tail[last_turn_marker:]
    return "kobe-notify" in tail or "kobe-attach" in tail


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker de sessão remota Claude Code")
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=["start", "resume"])
    args = parser.parse_args()

    kobe_home_raw = os.environ.get("KOBE_HOME") or ""
    if not kobe_home_raw:
        print("KOBE_HOME ausente no env do worker", file=sys.stderr)
        return 2
    kobe_home = Path(kobe_home_raw).expanduser().resolve()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s coder.worker: %(message)s",
        stream=sys.stderr,
    )

    state_path = args.state_file
    if not state_path.is_file():
        print(f"state file não existe: {state_path}", file=sys.stderr)
        return 2

    # Sinaliza no state que estamos rodando
    _patch_state(state_path, worker_started_at=_now_iso(), worker_pid=os.getpid())

    # Tratamento de SIGTERM (quando o supervisord/operador matar)
    def _on_term(signum, frame):  # noqa: ANN001 — handler
        try:
            _patch_state(state_path, status="terminated", exit_code=-15)
        except Exception:  # noqa: BLE001
            pass
        sys.exit(143)

    signal.signal(signal.SIGTERM, _on_term)

    try:
        return run_claude(state_path=state_path, mode=args.mode, kobe_home=kobe_home)
    except Exception as exc:  # noqa: BLE001 — captura tudo pra deixar state sano
        logger.exception("worker exception")
        try:
            _patch_state(
                state_path,
                status="crashed",
                exit_code=-99,
                last_text=f"worker exception: {exc!r}",
            )
            _notify_error(
                kobe_home,
                f"🔴 [coder] worker crashou: {exc!r}",
            )
        except Exception:  # noqa: BLE001
            pass
        return 99


if __name__ == "__main__":
    sys.exit(main())
