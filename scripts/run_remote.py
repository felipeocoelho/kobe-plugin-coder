#!/usr/bin/env python3
"""run_remote.py — CLI do plugin coder pra disparar/retomar sessões remotas.

É o ponto de entrada chamado pelo subagente `coder`. Não roda o `claude`
diretamente — escreve o state.json e lança `coder_worker.py` em background
(detached do processo pai). Retorna JSON no stdout com os campos chave da
sessão.

Subcomandos:
    start --cwd <path> --task "<texto>"
    resume --session <uuid> --input "<texto>"
    list
    status --session <uuid>

Estado vive em $KOBE_HOME/user-data/coder-sessions/<topic-key>/<uuid>.json
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import local — `presence.py` mora no mesmo diretório que este script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import presence  # noqa: E402


# === Isolamento por git worktree + lock de merge (§13.1) ==================
# Feature-flag, default OFF: cada sessão roda numa worktree própria (cópia
# isolada, mesma origem), e os merges de volta são serializados por um lock —
# nunca duas sessões escrevendo a árvore principal ao mesmo tempo. Default off
# é decisão de reversibilidade: liga o isolamento sem mudar o comportamento
# padrão até validar em uso real. `KOBE_CODER_WORKTREE=true` ativa.


def _worktree_enabled() -> bool:
    raw = os.environ.get("KOBE_CODER_WORKTREE", "").strip().lower()
    return raw in ("1", "true", "on", "yes")


def _cleanup_stale_salas(kobe_home: Path) -> None:
    """Mata salas coder-* abandonadas: estado terminal (dead/failed/…) ou inativas
    há mais que o TTL (KOBE_CODER_SALA_TTL_HOURS, default 24h). Oportunista (no
    start), best-effort — nunca bloqueia o dispatch. Evita acúmulo de salas vivas.
    """
    try:
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return
        salas = [s for s in r.stdout.split() if s.startswith("coder-")]
        if not salas:
            return
        try:
            ttl_h = float(os.environ.get("KOBE_CODER_SALA_TTL_HOURS", "24") or 24)
        except ValueError:
            ttl_h = 24.0
        by_short: dict[str, dict] = {}
        for sp in (kobe_home / "user-data" / "coder-sessions").glob("*/*.json"):
            try:
                d = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if d.get("short_id"):
                by_short[d["short_id"]] = d
        terminal = {"dead", "failed", "terminated", "crashed", "merged"}
        now = datetime.now(timezone.utc)
        for sala in salas:
            # short_id é o ÚLTIMO segmento do nome (`coder-<slug>-<short>` OU
            # `coder-<short>`) — extrai por rsplit pra não quebrar com slug (Frente 3).
            st = by_short.get(sala.rsplit("-", 1)[-1])
            if st is None:
                continue  # sem state conhecido — não é nossa / não mexe
            kill = st.get("status") in terminal
            if not kill and st.get("last_activity"):
                try:
                    age_h = (now - datetime.fromisoformat(
                        st["last_activity"])).total_seconds() / 3600
                    kill = age_h > ttl_h
                except Exception:  # noqa: BLE001
                    pass
            if kill:
                subprocess.run(["tmux", "kill-session", "-t", sala],
                               capture_output=True, text=True)
    except Exception:  # noqa: BLE001 — limpeza é best-effort
        pass


def _git(args: list[str], cwd: str | Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _git_toplevel(cwd: Path) -> Optional[Path]:
    try:
        r = _git(["rev-parse", "--show-toplevel"], cwd)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    top = r.stdout.strip()
    return Path(top) if top else None


def _setup_worktree(origin_cwd: Path, short_id: str, kobe_home: Path) -> Optional[dict]:
    """Cria uma worktree isolada para a sessão. Retorna dict com os campos pro
    state, ou None se não for um repo git (cai no comportamento padrão).

    Mecânica 100% determinística (código): cria branch `coder/<short>` a partir
    do HEAD atual e adiciona uma worktree fora da árvore principal. Falha em
    qualquer passo → retorna None e a sessão roda na cwd original (degrada sem
    travar; reversibilidade > isolamento quando o isolamento não dá pra montar).
    """
    main_repo = _git_toplevel(origin_cwd)
    if main_repo is None:
        return None  # não é repo git — sem worktree
    # Registra a branch+sha de origem ANTES de criar a worktree — é o ponto
    # pra onde o merge deve voltar (§5.1: caminho de volta registrado antes de
    # agir). Se o repo estiver em detached HEAD, origin_branch fica None e o
    # merge depois recusa (não mescla cego).
    ob = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], main_repo)
    origin_branch = ob.stdout.strip() if ob.returncode == 0 else None
    os_ = _git(["rev-parse", "HEAD"], main_repo)
    origin_sha = os_.stdout.strip() if os_.returncode == 0 else None
    wt_base = kobe_home / "user-data" / "coder-worktrees"
    wt_path = wt_base / short_id
    branch = f"coder/{short_id}"
    try:
        wt_base.mkdir(parents=True, exist_ok=True)
        r = _git(["worktree", "add", "-b", branch, str(wt_path), "HEAD"], main_repo)
        if r.returncode != 0:
            return None
    except Exception:  # noqa: BLE001
        return None
    return {
        "main_repo": str(main_repo),
        "worktree_path": str(wt_path),
        "worktree_branch": branch,
        "origin_branch": origin_branch,
        "origin_sha": origin_sha,
    }


@contextlib.contextmanager
def _merge_lock(kobe_home: Path):
    """Serializa merges de volta à árvore principal — um de cada vez na fila.
    flock exclusivo num lockfile; libera ao sair do contexto (inclusive em erro).
    """
    lock_dir = kobe_home / "user-data" / "coder-worktrees"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".merge.lock"
    fh = lock_path.open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _slugify_task(task: str, max_len: int = 24) -> str:
    """Slug kebab-case ASCII a partir do texto da tarefa, pra o nome da sala
    aludir à missão (ex.: 'blindar-resume'). Determinístico (código): tira acento,
    baixa caixa, mantém só [a-z0-9-], colapsa hifens, corta no limite SEM partir
    palavra no meio. Retorna '' se não sobrar nada utilizável — aí o nome da sala
    cai no fallback `coder-<short>` (ver `_sala_name`)."""
    if not task:
        return ""
    norm = unicodedata.normalize("NFKD", task)
    norm = "".join(c for c in norm if not unicodedata.combining(c)).lower()
    out = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    if not out:
        return ""
    if len(out) > max_len:
        cut = out[:max_len]
        if "-" in cut:  # não corta a última palavra pela metade
            cut = cut.rsplit("-", 1)[0]
        out = cut.strip("-")
    return out


def _kobe_home() -> Path:
    raw = os.environ.get("KOBE_HOME")
    if not raw:
        sys.exit("KOBE_HOME ausente no env (rode este script via subagente do Kobe).")
    return Path(raw).expanduser().resolve()


def _warn_if_prod_cwd(origin_cwd: Path, kobe_home: Path) -> Optional[str]:
    """Rede de segurança (Frente 1): a sessão deveria trabalhar na ÁRVORE DE DEV,
    não em produção. Se `origin_cwd` cai sob `$KOBE_HOME` (a raiz de produção) e
    NÃO é a base de worktrees (que mora sob `user-data` por design e é legítima),
    retorna um aviso. NÃO bloqueia — só sinaliza (o operador pode ter mesmo querido
    apontar pra prod). O caminho certo é resolver o cwd sob `$KOBE_CODER_DEV_ROOT`
    no dispatch (ver claude/agents/coder.md)."""
    try:
        oc = str(origin_cwd.resolve())
        kh = str(kobe_home.resolve())
    except Exception:  # noqa: BLE001
        return None
    wt_base = str((kobe_home / "user-data").resolve())
    if oc == wt_base or oc.startswith(wt_base + os.sep):
        return None  # worktree/estado sob user-data não é "prod por engano"
    if oc == kh or oc.startswith(kh + os.sep):
        dev_root = os.environ.get("KOBE_CODER_DEV_ROOT", "").strip()
        hint = (
            f" A árvore de dev é `$KOBE_CODER_DEV_ROOT`"
            + (f" (`{dev_root}`)" if dev_root else " — que NÃO está setado")
            + "; o certo é resolver o cwd lá, não em produção."
        )
        return f"a cwd `{oc}` está sob a raiz de PRODUÇÃO (`{kh}`)." + hint
    return None


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
    (contagem, lista_de_dicts_com_short_id+topic+cwd+task) — útil pra
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
            if not data.get("session_id"):  # ignora .json que não seja state de sessão
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
                    "task": (data.get("task") or "")[:80],
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
        if not data.get("session_id"):  # ignora .json que não seja state de sessão
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

    # Força UTF-8 no worker. O worker monta o system prompt (~28KB, com §,
    # emojis, acentos) e o passa como argv pro `claude`. A codificação do argv
    # usa a filesystem encoding do worker; num locale POSIX/C puro ela cai pra
    # ASCII e o spawn levantaria UnicodeEncodeError. PYTHONUTF8=1 é lido no
    # startup do interpretador, então setar aqui (antes de lançar o worker)
    # garante UTF-8 independente do locale do host — fecha o único ponto onde
    # o conteúdo não-ASCII do harness cruza uma fronteira sensível a locale.
    worker_env = os.environ.copy()
    worker_env["PYTHONUTF8"] = "1"

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
        env=worker_env,
    )
    return proc.pid


def cmd_start(args: argparse.Namespace) -> int:
    kobe_home = _kobe_home()
    _cleanup_stale_salas(kobe_home)  # oportunista: mata salas abandonadas/velhas
    topic_key = _topic_key()
    cwd = Path(args.cwd).expanduser().resolve()
    task = args.task.strip()

    if not task:
        return _emit({"error": "tarefa vazia"}, error=True)

    # Rede de segurança (Frente 1): avisa (não bloqueia) se a sessão foi apontada
    # pra raiz de produção em vez da árvore de dev. Determinístico — emite direto
    # via kobe-notify (run_remote herda o env do bot) e devolve no payload.
    cwd_warning = _warn_if_prod_cwd(cwd, kobe_home)
    if cwd_warning:
        notify_bin = kobe_home / "bot" / "bin" / "kobe-notify"
        if notify_bin.is_file() and os.environ.get("KOBE_TELEGRAM_BOT_TOKEN") and os.environ.get("KOBE_CHAT_ID"):
            with contextlib.suppress(Exception):
                subprocess.run(
                    [str(notify_bin),
                     f"⚠️ [coder] atenção ao cwd: {cwd_warning} Disparando mesmo "
                     f"assim — confira se era essa a intenção."],
                    timeout=15, capture_output=True,
                )

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
    # Slug kebab-case da missão pro nome da sala (Frente 3) — gravado no estado
    # pra SOBREVIVER a resume (o nome da sala é derivado do state, não recalculado
    # do texto a cada turno; assim um resume nunca procura uma sala com nome novo).
    slug = _slugify_task(task)
    topic_dir = _sessions_dir(kobe_home, topic_key)
    topic_dir.mkdir(parents=True, exist_ok=True)
    state_path = topic_dir / f"{session_id}.json"
    log_path = topic_dir / f"{session_id}.log"

    # Isolamento por worktree (§13.1), se ligado e a cwd for repo git. A sessão
    # passa a rodar na worktree (run_cwd); origin_cwd guarda onde o operador
    # apontou; os campos de merge ficam no state pro `merge` depois.
    origin_cwd = cwd
    run_cwd = cwd
    worktree_fields: dict = {
        "origin_cwd": str(origin_cwd),
        "main_repo": None,
        "worktree_path": None,
        "worktree_branch": None,
        "merged": False,
    }
    if _worktree_enabled():
        wt = _setup_worktree(origin_cwd, short, kobe_home)
        if wt is not None:
            run_cwd = Path(wt["worktree_path"])
            worktree_fields.update(wt)

    # BUG 2 (integridade): registra o HEAD do início da sessão. Se ela morrer no
    # meio (cota/crash/OOM), o resumo de fechamento isola EXATAMENTE os commits
    # que ela criou (`head_sha_at_start..HEAD`) — verdade do git, sem garimpo.
    # None se a cwd não for repo git (ou ainda não existir, em projeto novo).
    head_sha_at_start: Optional[str] = None
    try:
        _hs = _git(["rev-parse", "HEAD"], run_cwd)
        if _hs.returncode == 0:
            head_sha_at_start = _hs.stdout.strip() or None
    except Exception:  # noqa: BLE001 — best-effort; ausência degrada no resumo
        head_sha_at_start = None

    state = {
        "session_id": session_id,
        "short_id": short,
        "slug": slug,
        "topic_key": topic_key,
        "cwd": str(run_cwd),
        "task": task,
        "created_at": _now_iso(),
        "last_activity": _now_iso(),
        "head_sha_at_start": head_sha_at_start,
        "status": "starting",
        "pid": None,
        "worker_pid": None,
        "log_path": str(log_path),
        "state_path": str(state_path),
        "exit_code": None,
        "last_text": None,
        "turn_count": 0,
        "pending_input": None,
        # Gate PARA-e-espera-OK (§10): edição de código de produção fica
        # bloqueada (hook guard) até o plano ser aprovado. `--approve-plan` no
        # start pré-aprova (operador mandou tarefa trivial / pulou o plano).
        "plan_approved": bool(getattr(args, "approve_plan", False)),
        # HALT (§7.1): conflito de regras irreconciliável trava a sessão.
        "halted": False,
        "halt_reason": None,
        # Gate de deploy (§10): push pro remote público exige OK. Liberado por
        # --approve-deploy (operador aprovou o passo final que toca o público).
        "deploy_approved": False,
        # Nível de esforço (§3/§4): "standard" (Procedimento 1, default) ou
        # "max" (Procedimento 2, crivo em agentes separados). Só vira "max" por
        # comando explícito do operador — nunca por auto-escalação.
        "effort": "max" if getattr(args, "effort_max", False) else "standard",
        # Modo sala (--remote-control, visível) — é O MODELO do Coder: toda sessão
        # abre numa sala tmux navegável. Não é opção do operador; é o caminho.
        "sala_mode": True,
        # Isolamento por worktree (§13.1) — campos nulos quando desligado.
        **worktree_fields,
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
            "cwd_warning": cwd_warning,
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
    # Modo sala: resume = `tmux send-keys` numa sala interativa que PERSISTE — não
    # depende do turno encerrar (diferente do claude -p, que morre por turno). A
    # checagem de "running" só vale pro caminho headless; no modo sala, run_sala
    # trata o caso de sala morta.
    if not state.get("sala_mode") and status in {"running", "starting"}:
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
    # Aprovação do plano (§10): o operador aprovou → libera o gate PARA-e-espera.
    # Detecção da aprovação ("ok/manda/pode") é do agente principal (LLM); a
    # trava é código. Sticky: uma vez aprovado, segue aprovado.
    if getattr(args, "approve_plan", False):
        state["plan_approved"] = True
    # Aprovação do deploy público (§10): libera o gate do push pro remote público.
    if getattr(args, "approve_deploy", False):
        state["deploy_approved"] = True
    # Arbitragem de conflito (§7.1): operador resolveu o HALT → destrava.
    if getattr(args, "clear_halt", False):
        state["halted"] = False
        state["halt_reason"] = None
    # Esforço máximo pedido no meio da sessão (§4): só sobe por comando explícito.
    if getattr(args, "effort_max", False):
        state["effort"] = "max"
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
        payload: dict = {"sessions": all_sessions, "topic_key": "<all>"}
    else:
        topic_dir = _sessions_dir(kobe_home, topic_key)
        sessions = _list_sessions(topic_dir)
        payload = {"sessions": sessions, "topic_key": topic_key}

    # Presenças globais (cross-tópico): operador quer visão tipo `v$session`,
    # não só do tópico atual. Cleanup inline na `list_active`. Flag
    # --no-presence existe pra suprimir quando o consumidor só quer sessões.
    if args.include_presence:
        try:
            payload["presences"] = presence.list_active(kobe_home=kobe_home)
        except Exception as exc:  # noqa: BLE001
            payload["presences"] = []
            payload["presences_error"] = str(exc)
    return _emit(payload)


def cmd_status(args: argparse.Namespace) -> int:
    kobe_home = _kobe_home()
    topic_key = _topic_key()
    topic_dir = _sessions_dir(kobe_home, topic_key)
    state_path = _resolve_session(topic_dir, args.session)
    if state_path is None:
        return _emit({"error": f"sessão {args.session!r} não encontrada"}, error=True)
    return _emit(json.loads(state_path.read_text(encoding="utf-8")))


def cmd_merge(args: argparse.Namespace) -> int:
    """Mescla de volta a worktree da sessão na árvore principal (§13.1).

    Operação TERMINAL e conservadora — pensada pra reversibilidade:
    - serializada por lock (nunca dois merges concorrentes);
    - aborta se a árvore principal estiver suja (não arrisca dado não-salvo);
    - `--no-ff` (preserva o histórico da sessão);
    - se houver conflito, faz `merge --abort` e reporta — NUNCA auto-resolve nem
      força. O operador (ou uma sessão) resolve o conflito à mão depois.
    Em sucesso, remove a worktree e a branch da sessão e marca `merged`.
    """
    kobe_home = _kobe_home()
    topic_key = _topic_key()
    topic_dir = _sessions_dir(kobe_home, topic_key)
    state_path = _resolve_session(topic_dir, args.session)
    if state_path is None:
        return _emit({"error": f"sessão {args.session!r} não encontrada"}, error=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    branch = state.get("worktree_branch")
    main_repo = state.get("main_repo")
    if not branch or not main_repo:
        return _emit(
            {"error": "sessão não tem worktree (isolamento desligado ou cwd não-git) — nada a mesclar.",
             "short_id": state.get("short_id")},
            error=True,
        )
    if state.get("merged"):
        return _emit({"ok": True, "action": "merge", "note": "já mesclada", "short_id": state.get("short_id")})
    if state.get("status") in {"running", "starting"}:
        return _emit({"error": "sessão ainda ativa — aguarde o turno encerrar antes de mesclar."}, error=True)

    main = Path(main_repo)
    wt_path = state.get("worktree_path")
    origin_branch = state.get("origin_branch")
    branch_kept = False
    with _merge_lock(kobe_home):
        # (a) A árvore principal NÃO pode estar em detached HEAD — senão o merge
        # vira commit flutuante e o cleanup orfaniza tudo (B3).
        cur = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], main)
        if cur.returncode != 0:
            return _emit({"error": "árvore principal está em detached HEAD — faça checkout da "
                                   "branch de destino antes de mesclar. Não mesclo cego."}, error=True)
        current_branch = cur.stdout.strip()
        # (b) E precisa ser a MESMA branch de onde a worktree saiu — senão
        # mesclaríamos o trabalho na branch errada (B3).
        if origin_branch and current_branch != origin_branch:
            return _emit({"error": f"árvore principal está na branch '{current_branch}', mas a sessão "
                                   f"saiu de '{origin_branch}'. Faça checkout de '{origin_branch}' antes "
                                   "de mesclar (ou confirme a intenção). Não mesclo na branch errada."},
                         error=True)
        # (c) Árvore principal limpa — não pisa mudança não-salva.
        dirty = _git(["status", "--porcelain"], main)
        if dirty.returncode != 0:
            return _emit({"error": f"não consegui checar a árvore principal: {dirty.stderr.strip()}"}, error=True)
        if dirty.stdout.strip():
            return _emit({"error": "árvore principal está suja (mudanças não-commitadas) — "
                                   "commit/stash antes de mesclar. Não arrisco sobrescrever.",
                          "dirty": dirty.stdout.strip()[:500]}, error=True)
        # (d) A worktree NÃO pode ter trabalho não-commitado/untracked — o merge
        # só leva COMMITS; remover a worktree com sujeira perderia esse trabalho (B4).
        if wt_path and Path(wt_path).is_dir():
            wt_dirty = _git(["status", "--porcelain"], wt_path)
            if wt_dirty.returncode == 0 and wt_dirty.stdout.strip():
                return _emit({"error": "a worktree da sessão tem mudanças não-commitadas/untracked — "
                                       "commite-as (ou descarte explicitamente) antes de mesclar. O merge "
                                       "só leva commits; não removo a worktree com trabalho solto.",
                              "wt_dirty": wt_dirty.stdout.strip()[:500]}, error=True)
        # (e) Caminho de volta registrado ANTES de agir (§5.1).
        pre = _git(["rev-parse", "HEAD"], main)
        pre_merge_sha = pre.stdout.strip() if pre.returncode == 0 else None

        # Há o que mesclar?
        ahead = _git(["rev-list", "--count", f"HEAD..{branch}"], main)
        if ahead.returncode != 0:
            return _emit({"error": f"não consegui comparar a branch da sessão ('{branch}') com a principal "
                                   f"— ref inválida? abortado sem mexer. {ahead.stderr.strip()}"}, error=True)
        if ahead.stdout.strip() == "0":
            note = "nada a mesclar (sessão não adicionou commits)."
        else:
            mr = _git(["merge", "--no-ff", "-m",
                       f"merge coder/{state.get('short_id')}: {state.get('task','')[:80]}", branch],
                      main, timeout=120)
            if mr.returncode != 0:
                _git(["merge", "--abort"], main)  # conflito → aborta, NÃO auto-resolve
                return _emit({"error": "merge falhou/conflitou — abortado, árvore principal intacta. "
                                       "Resolva o conflito manualmente.",
                              "git_stderr": (mr.stderr or mr.stdout).strip()[:800],
                              "branch": branch, "rollback_sha": pre_merge_sha}, error=True)
            note = "mesclado com --no-ff."

        # Cleanup conservador: worktree já validada limpa (d) → remove é seguro;
        # branch -d (não -D) só remove se de fato incorporada (sem perder commit).
        if wt_path:
            _git(["worktree", "remove", wt_path], main)
        bd = _git(["branch", "-d", branch], main)
        branch_kept = bd.returncode != 0  # -d recusou → tem commit não incorporado; preserva

    state["merged"] = True
    state["status"] = "merged"
    state["merged_into"] = origin_branch or current_branch
    state["pre_merge_sha"] = pre_merge_sha
    state["last_activity"] = _now_iso()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if note.startswith("mesclado"):
        note += f" Caminho de volta: `git -C {main} reset --hard {pre_merge_sha}` (ou revert do merge)."
    if branch_kept:
        note += f" (branch '{branch}' preservada — `git branch -d` recusou; pode ter commit não incorporado.)"
    return _emit({"ok": True, "action": "merge", "short_id": state.get("short_id"),
                  "branch": branch, "note": note})


def cmd_halt(args: argparse.Namespace) -> int:
    """Marca uma sessão como HALTED (§7.1) — o hook guard passa a negar toda
    ação mutante até o operador arbitrar (`resume --clear-halt`). Pode ser
    acionado pelo operador ou pela própria sessão (via helper) ao detectar um
    conflito de regras irreconciliável.
    """
    kobe_home = _kobe_home()
    topic_key = _topic_key()
    topic_dir = _sessions_dir(kobe_home, topic_key)
    state_path = _resolve_session(topic_dir, args.session)
    if state_path is None:
        return _emit({"error": f"sessão {args.session!r} não encontrada"}, error=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["halted"] = True
    state["halt_reason"] = (args.reason or "").strip() or "parada dura sinalizada"
    state["last_activity"] = _now_iso()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return _emit(
        {"ok": True, "action": "halt", "short_id": state.get("short_id"),
         "halt_reason": state["halt_reason"]}
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI do plugin coder do Kobe — sessões remotas de Claude Code"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="dispara nova sessão remota")
    s.add_argument("--cwd", required=True, help="diretório onde o claude vai rodar")
    s.add_argument("--task", required=True, help="texto da tarefa pra sessão")
    s.add_argument(
        "--force",
        action="store_true",
        help="pula aviso de pasta ocupada (use quando o operador confirmou).",
    )
    s.add_argument(
        "--approve-plan",
        dest="approve_plan",
        action="store_true",
        help="pré-aprova o plano (tarefa trivial / operador pediu pra pular o "
        "plano) — libera o gate PARA-e-espera desde o start.",
    )
    s.add_argument(
        "--effort-max",
        dest="effort_max",
        action="store_true",
        help="Procedimento 2 (§4): esforço máximo, crivo em agentes separados. "
        "Só quando o operador pediu explicitamente (esforço máximo/ultracode).",
    )
    s.set_defaults(func=cmd_start)

    r = sub.add_parser("resume", help="retoma sessão existente com novo input")
    r.add_argument("--session", required=True, help="uuid ou short-id da sessão")
    r.add_argument("--input", required=True, help="texto da nova mensagem do operador")
    r.add_argument(
        "--approve-plan",
        dest="approve_plan",
        action="store_true",
        help="o operador aprovou o plano nesta retomada — libera o gate de "
        "edição de código de produção (sticky).",
    )
    r.add_argument(
        "--clear-halt",
        dest="clear_halt",
        action="store_true",
        help="o operador arbitrou o conflito — destrava a sessão (limpa HALT).",
    )
    r.add_argument(
        "--approve-deploy",
        dest="approve_deploy",
        action="store_true",
        help="o operador aprovou o passo final de deploy (push pro remote "
        "público) — libera o gate de deploy.",
    )
    r.add_argument(
        "--effort-max",
        dest="effort_max",
        action="store_true",
        help="sobe a sessão pro Procedimento 2 (esforço máximo) a partir desta "
        "retomada — só por pedido explícito do operador.",
    )
    r.set_defaults(func=cmd_resume)

    l = sub.add_parser("list", help="lista sessões do tópico atual")
    l.add_argument("--all", action="store_true", help="lista todos os tópicos")
    l.add_argument(
        "--no-presence",
        dest="include_presence",
        action="store_false",
        help="omite a lista de presenças globais (default: incluir).",
    )
    l.set_defaults(func=cmd_list, include_presence=True)

    st = sub.add_parser("status", help="detalhe de uma sessão")
    st.add_argument("--session", required=True)
    st.set_defaults(func=cmd_status)

    h = sub.add_parser("halt", help="trava uma sessão (HALT §7.1) até arbitragem")
    h.add_argument("--session", required=True, help="uuid ou short-id da sessão")
    h.add_argument("--reason", default="", help="motivo do HALT (conflito de regras, etc.)")
    h.set_defaults(func=cmd_halt)

    m = sub.add_parser("merge", help="mescla a worktree da sessão na árvore principal (§13.1)")
    m.add_argument("--session", required=True, help="uuid ou short-id da sessão")
    m.set_defaults(func=cmd_merge)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
