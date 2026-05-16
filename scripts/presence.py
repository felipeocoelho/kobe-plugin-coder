#!/usr/bin/env python3
"""presence.py — registro de instâncias Claude Code ativas.

Cada Claude Code rodando (sessão remota do plugin Coder, claude local do
operador, etc.) escreve um `<pid>.json` em `$KOBE_HOME/user-data/claude-presence/`
ao iniciar e remove ao terminar. Outros consumidores listam a pasta pra saber
quem está em qual cwd.

Não é lock — é informação. PID inexistente é limpo inline em toda leitura
(kill -0 falhou), então arquivo órfão de processo morto não polui resultado.

Uso programático:
    from presence import register, unregister, list_active, cleanup

    register(source="telegram-coder", cwd="/path", session_id="uuid")
    try:
        ...
    finally:
        unregister()

Uso CLI (pra hooks do Claude Code local em settings.json do operador):
    python presence.py register --source local-claude --cwd "$PWD"
    python presence.py unregister
    python presence.py list
    python presence.py cleanup
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _kobe_home() -> Path:
    """Resolve $KOBE_HOME. Erro fatal se ausente — sem fallback silencioso.

    Razão: presença escrita no lugar errado é pior que não escrever.
    """
    raw = os.environ.get("KOBE_HOME")
    if not raw:
        raise RuntimeError(
            "KOBE_HOME ausente no env — necessário pra localizar user-data/claude-presence/"
        )
    return Path(raw).expanduser().resolve()


def _presence_dir(kobe_home: Optional[Path] = None) -> Path:
    home = kobe_home or _kobe_home()
    return home / "user-data" / "claude-presence"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    """`kill -0` — sinaliza se o PID existe sem efeito colateral."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID existe mas é de outro user — improvável aqui (VPS pessoal),
        # mas tratamos como vivo pra não apagar registro alheio.
        return True
    except OSError:
        return False
    return True


def _record_path(pid: int, *, kobe_home: Optional[Path] = None) -> Path:
    return _presence_dir(kobe_home) / f"{pid}.json"


def register(
    *,
    source: str,
    cwd: Optional[str] = None,
    session_id: Optional[str] = None,
    topic_key: Optional[str] = None,
    pid: Optional[int] = None,
    kobe_home: Optional[Path] = None,
) -> Path:
    """Escreve `<pid>.json` na pasta de presença. Retorna o path criado.

    Idempotente: chamar duas vezes com o mesmo PID sobrescreve o registro
    (útil quando session_id ainda não estava conhecido na primeira chamada).
    """
    home = kobe_home or _kobe_home()
    presence_dir = _presence_dir(home)
    presence_dir.mkdir(parents=True, exist_ok=True)

    effective_pid = pid or os.getpid()
    record = {
        "pid": effective_pid,
        "cwd": str(Path(cwd).expanduser().resolve()) if cwd else os.getcwd(),
        "session_id": session_id,
        "source": source,
        "topic_key": topic_key,
        "started_at": _now_iso(),
    }
    path = _record_path(effective_pid, kobe_home=home)
    # Escrita atômica: tmp + rename. Leitura concorrente nunca pega arquivo
    # parcial. Convenção igual à de `coder_worker.py`.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def unregister(
    *,
    pid: Optional[int] = None,
    kobe_home: Optional[Path] = None,
) -> bool:
    """Remove o registro do PID. Retorna True se removeu, False se não existia."""
    home = kobe_home or _kobe_home()
    effective_pid = pid or os.getpid()
    path = _record_path(effective_pid, kobe_home=home)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def cleanup(*, kobe_home: Optional[Path] = None) -> list[int]:
    """Remove arquivos cujo PID não existe mais. Retorna lista de PIDs limpos."""
    home = kobe_home or _kobe_home()
    presence_dir = _presence_dir(home)
    if not presence_dir.is_dir():
        return []
    removed: list[int] = []
    for record_path in presence_dir.glob("*.json"):
        # Nome do arquivo é o PID — usa isso pra detectar morto sem precisar
        # ler/parsear JSON (mais barato e robusto a arquivo corrompido).
        try:
            pid = int(record_path.stem)
        except ValueError:
            # Arquivo com nome não-numérico no diretório — não é nosso, ignora.
            continue
        if not _pid_alive(pid):
            try:
                record_path.unlink()
                removed.append(pid)
            except FileNotFoundError:
                pass
    return removed


def list_active(*, kobe_home: Optional[Path] = None) -> list[dict]:
    """Lista presenças ativas. Faz cleanup inline antes de ler.

    Ordenação: mais recente primeiro (started_at desc).
    """
    home = kobe_home or _kobe_home()
    cleanup(kobe_home=home)
    presence_dir = _presence_dir(home)
    if not presence_dir.is_dir():
        return []
    records: list[dict] = []
    for record_path in presence_dir.glob("*.json"):
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        records.append(data)
    records.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return records


def find_by_cwd(
    cwd: str,
    *,
    kobe_home: Optional[Path] = None,
) -> list[dict]:
    """Retorna registros ativos cuja `cwd` resolvida bate com o argumento."""
    home = kobe_home or _kobe_home()
    target = str(Path(cwd).expanduser().resolve())
    return [r for r in list_active(kobe_home=home) if r.get("cwd") == target]


def _cmd_register(args: argparse.Namespace) -> int:
    path = register(
        source=args.source,
        cwd=args.cwd,
        session_id=args.session_id,
        topic_key=args.topic_key,
        pid=args.pid,
    )
    print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
    return 0


def _cmd_unregister(args: argparse.Namespace) -> int:
    removed = unregister(pid=args.pid)
    print(json.dumps({"ok": True, "removed": removed}, ensure_ascii=False))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    records = list_active()
    print(json.dumps({"presences": records}, ensure_ascii=False, indent=2))
    return 0


def _cmd_cleanup(args: argparse.Namespace) -> int:
    removed = cleanup()
    print(json.dumps({"ok": True, "removed_pids": removed}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Registro de presença de instâncias Claude Code"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register", help="registra esta instância (ou um PID dado)")
    reg.add_argument(
        "--source",
        required=True,
        help="origem da instância (ex: telegram-coder, local-claude, ssh-claude)",
    )
    reg.add_argument("--cwd", help="cwd da instância (default: cwd atual do shell)")
    reg.add_argument("--session-id", help="session_id do Claude Code (se conhecido)")
    reg.add_argument("--topic-key", help="topic_key do Kobe (se aplicável)")
    reg.add_argument("--pid", type=int, help="PID a registrar (default: PID atual)")
    reg.set_defaults(func=_cmd_register)

    unreg = sub.add_parser("unregister", help="remove o registro")
    unreg.add_argument("--pid", type=int, help="PID a remover (default: PID atual)")
    unreg.set_defaults(func=_cmd_unregister)

    lst = sub.add_parser("list", help="lista presenças ativas (faz cleanup antes)")
    lst.set_defaults(func=_cmd_list)

    cln = sub.add_parser("cleanup", help="apaga registros de PIDs mortos")
    cln.set_defaults(func=_cmd_cleanup)

    args = parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
