#!/usr/bin/env python3
"""run_remote.py — CLI do plugin coder pra disparar/retomar sessões remotas.

É o ponto de entrada chamado pelo subagente `coder`. Não roda o `claude`
diretamente — escreve o state.json e lança `coder_worker.py` em background
(detached do processo pai). Retorna JSON no stdout com os campos chave da
sessão.

Subcomandos:
    start --cwd <path> --mission "<texto>"
    resume --session <uuid> --input "<texto>"
    list
    status --session <uuid>

Estado vive em $KOBE_HOME/user-data/coder-sessions/<topic-key>/<uuid>.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import local — `presence.py` mora no mesmo diretório que este script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import presence  # noqa: E402


def _kobe_home() -> Path:
    raw = os.environ.get("KOBE_HOME")
    if not raw:
        sys.exit("KOBE_HOME ausente no env (rode este script via subagente do Kobe).")
    return Path(raw).expanduser().resolve()


def _topic_key() -> str:
    """Pasta-chave da sessão. `general` se sem thread, senão o thread_id."""
    raw = os.environ.get("KOBE_THREAD_ID", "").strip()
    if not raw or raw == "0":
        return "general"
    return raw


def _sessions_dir(kobe_home: Path, topic_key: Optional[str] = None) -> Path:
    base = kobe_home / "user-data" / "coder-sessions"
    if topic_key is None:
        return base
    return base / topic_key


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _emit(payload: dict, *, error: bool = False) -> int:
    """Saída padronizada do CLI: JSON único no stdout (ou stderr se erro)."""
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if error:
        print(out, file=sys.stderr)
        return 1
    print(out)
    return 0


# Limite global default de sessões coder simultâneas (somatório de todos os
# tópicos). Cada sessão = processo `claude -p` extra + tokens Anthropic por
# turno. Limite protege contra explosão de custo em rajada de pedidos.
# Override via env `KOBE_CODER_MAX_CONCURRENT=N`. N=0 desativa o limite.
_DEFAULT_MAX_CONCURRENT = 3


def _max_concurrent() -> int:
    raw = os.environ.get("KOBE_CODER_MAX_CONCURRENT", "").strip()
    if not raw:
        return _DEFAULT_MAX_CONCURRENT
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_CONCURRENT


def _count_active_sessions_global(kobe_home: Path) -> tuple[int, list[dict]]:
    """Conta sessões em status `starting` ou `running` em TODOS os tópicos.

    Faz crash detection inline (PID inexistente = não conta). Retorna
    (contagem, lista_de_dicts_com_short_id+topic+cwd+mission) — útil pra
    o caller construir mensagem de erro informativa.
    """
    base = _sessions_dir(kobe_home)
    active: list[dict] = []
    if not base.is_dir():
        return 0, active
    for tdir in base.iterdir():
        if not tdir.is_dir():
            continue
        for state_file in tdir.glob("*.json"):
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            status = data.get("status")
            if status not in ("starting", "running"):
                continue
            pid = data.get("pid") or data.get("worker_pid")
            if pid:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    # processo sumiu — não conta como ativo
                    continue
                except OSError:
                    continue
            active.append(
                {
                    "short_id": data.get("short_id", ""),
                    "topic_key": data.get("topic_key", tdir.name),
                    "cwd": data.get("cwd", ""),
                    "mission": (data.get("mission") or "")[:80],
                }
            )
    return len(active), active


def _list_sessions(topic_dir: Path) -> list[dict]:
    if not topic_dir.is_dir():
        return []
    sessions: list[dict] = []
    for state_file in sorted(topic_dir.glob("*.json")):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Crash detection: status="running" mas PID não existe → marca crashed
        status = data.get("status")
        pid = data.get("pid")
        if status == "running" and pid:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                data["status"] = "crashed"
                try:
                    state_file.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
        sessions.append(data)
    sessions.sort(key=lambda s: s.get("last_activity") or "", reverse=True)
    return sessions


def _spawn_worker(state_path: Path, mode: str, log_path: Path) -> int:
    """Lança coder_worker.py em background detached. Retorna PID."""
    worker = Path(__file__).resolve().parent / "coder_worker.py"
    kobe_home = _kobe_home()
    # Python do venv do Kobe, se existir; senão python3 do sistema.
    venv_python = kobe_home / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.is_file() else "python3"

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("a", encoding="utf-8")
    log_fh.write(f"\n# --- worker spawn @ {_now_iso()} mode={mode} ---\n")
    log_fh.flush()

    proc = subprocess.Popen(
        [
            python,
            str(worker),
            "--state-file", str(state_path),
            "--mode", mode,
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # detach do parent — não morre ao sair daqui
        env=os.environ.copy(),
    )
    return proc.pid


def cmd_start(args: argparse.Namespace) -> int:
    kobe_home = _kobe_home()
    topic_key = _topic_key()
    cwd = Path(args.cwd).expanduser().resolve()
    mission = args.mission.strip()

    if not mission:
        return _emit({"error": "missão vazia"}, error=True)

    # Aviso (não bloqueio) de pasta ocupada — outra instância Claude Code já
    # está mexendo nessa cwd? Retorna `warning: presence_conflict` e não
    # dispara. Subagente coder propaga via kobe-notify pro operador decidir.
    # Bypass: --force (operador confirmou explicitamente).
    if not args.force:
        try:
            conflicts = presence.find_by_cwd(str(cwd))
        except Exception:  # noqa: BLE001 — presença é nice-to-have
            conflicts = []
        if conflicts:
            return _emit(
                {
                    "warning": "presence_conflict",
                    "cwd": str(cwd),
                    "active": [
                        {
                            "pid": c.get("pid"),
                            "source": c.get("source"),
                            "session_id": c.get("session_id"),
                            "started_at": c.get("started_at"),
                            "topic_key": c.get("topic_key"),
                        }
                        for c in conflicts
                    ],
                    "message": (
                        "Já existe instância Claude Code ativa nessa cwd. "
                        "Confirme com o operador antes de disparar. "
                        "Reinvoque com --force pra prosseguir."
                    ),
                }
            )

    # Limite global de sessões concorrentes — protege contra explosão de
    # custo Anthropic em rajada. Override via env KOBE_CODER_MAX_CONCURRENT.
    max_concurrent = _max_concurrent()
    if max_concurrent > 0:
        count, active = _count_active_sessions_global(kobe_home)
        if count >= max_concurrent:
            return _emit(
                {
                    "error": (
                        f"limite de {max_concurrent} sessão(ões) coder ativas "
                        f"atingido. Sessões em execução: "
                        + ", ".join(
                            f"{s['short_id']}@{s['topic_key']}" for s in active
                        )
                        + ". Aguarde uma terminar ou ajuste KOBE_CODER_MAX_CONCURRENT."
                    ),
                    "active_count": count,
                    "max_concurrent": max_concurrent,
                    "active_sessions": active,
                },
                error=True,
            )

    session_id = str(uuid.uuid4())
    short = session_id[:8]
    topic_dir = _sessions_dir(kobe_home, topic_key)
    topic_dir.mkdir(parents=True, exist_ok=True)
    state_path = topic_dir / f"{session_id}.json"
    log_path = topic_dir / f"{session_id}.log"

    state = {
        "session_id": session_id,
        "short_id": short,
        "topic_key": topic_key,
        "cwd": str(cwd),
        "mission": mission,
        "created_at": _now_iso(),
        "last_activity": _now_iso(),
        "status": "starting",
        "pid": None,
        "worker_pid": None,
        "log_path": str(log_path),
        "state_path": str(state_path),
        "exit_code": None,
        "last_text": None,
        "turn_count": 0,
        "pending_input": None,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    worker_pid = _spawn_worker(state_path, "start", log_path)

    state["worker_pid"] = worker_pid
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return _emit(
        {
            "ok": True,
            "action": "start",
            "session_id": session_id,
            "short_id": short,
            "cwd": str(cwd),
            "topic_key": topic_key,
            "state_path": str(state_path),
            "log_path": str(log_path),
            "worker_pid": worker_pid,
        }
    )


def cmd_resume(args: argparse.Namespace) -> int:
    kobe_home = _kobe_home()
    topic_key = _topic_key()
    target = args.session.strip()
    new_input = args.input.strip()

    if not new_input:
        return _emit({"error": "input vazio na retomada"}, error=True)

    topic_dir = _sessions_dir(kobe_home, topic_key)
    state_path = _resolve_session(topic_dir, target)
    if state_path is None:
        return _emit(
            {"error": f"sessão {target!r} não encontrada no tópico {topic_key!r}"},
            error=True,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    status = state.get("status")
    if status in {"running", "starting"}:
        return _emit(
            {
                "error": (
                    f"sessão {state.get('short_id')} ainda está em status={status} — "
                    "aguarde o turno encerrar antes de resumir."
                ),
                "session": state,
            },
            error=True,
        )

    state["pending_input"] = new_input
    state["last_activity"] = _now_iso()
    state["status"] = "starting"
    state["exit_code"] = None
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    log_path = Path(state["log_path"])
    worker_pid = _spawn_worker(state_path, "resume", log_path)
    state["worker_pid"] = worker_pid
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return _emit(
        {
            "ok": True,
            "action": "resume",
            "session_id": state["session_id"],
            "short_id": state["short_id"],
            "cwd": state["cwd"],
            "state_path": str(state_path),
            "log_path": str(log_path),
            "worker_pid": worker_pid,
        }
    )


def _resolve_session(topic_dir: Path, target: str) -> Optional[Path]:
    """Resolve <target> pra um state file. Aceita uuid cheio ou short id (8 chars)."""
    if not topic_dir.is_dir():
        return None
    # Match exato primeiro
    exact = topic_dir / f"{target}.json"
    if exact.is_file():
        return exact
    # Prefixo
    candidates = list(topic_dir.glob(f"{target}*.json"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return None
    return None


def cmd_list(args: argparse.Namespace) -> int:
    kobe_home = _kobe_home()
    topic_key = _topic_key() if not args.all else None
    if args.all:
        base = _sessions_dir(kobe_home)
        all_sessions: list[dict] = []
        if base.is_dir():
            for tdir in sorted(base.iterdir()):
                if tdir.is_dir():
                    all_sessions.extend(_list_sessions(tdir))
        return _emit({"sessions": all_sessions, "topic_key": "<all>"})
    topic_dir = _sessions_dir(kobe_home, topic_key)
    sessions = _list_sessions(topic_dir)
    return _emit({"sessions": sessions, "topic_key": topic_key})


def cmd_status(args: argparse.Namespace) -> int:
    kobe_home = _kobe_home()
    topic_key = _topic_key()
    topic_dir = _sessions_dir(kobe_home, topic_key)
    state_path = _resolve_session(topic_dir, args.session)
    if state_path is None:
        return _emit({"error": f"sessão {args.session!r} não encontrada"}, error=True)
    return _emit(json.loads(state_path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI do plugin coder do Kobe — sessões remotas de Claude Code"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="dispara nova sessão remota")
    s.add_argument("--cwd", required=True, help="diretório onde o claude vai rodar")
    s.add_argument("--mission", required=True, help="texto da missão pra sessão")
    s.add_argument(
        "--force",
        action="store_true",
        help="pula aviso de pasta ocupada (use quando o operador confirmou).",
    )
    s.set_defaults(func=cmd_start)

    r = sub.add_parser("resume", help="retoma sessão existente com novo input")
    r.add_argument("--session", required=True, help="uuid ou short-id da sessão")
    r.add_argument("--input", required=True, help="texto da nova mensagem do operador")
    r.set_defaults(func=cmd_resume)

    l = sub.add_parser("list", help="lista sessões do tópico atual")
    l.add_argument("--all", action="store_true", help="lista todos os tópicos")
    l.set_defaults(func=cmd_list)

    st = sub.add_parser("status", help="detalhe de uma sessão")
    st.add_argument("--session", required=True)
    st.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
