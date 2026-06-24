#!/usr/bin/env python3
"""coder_worker.py — wrapper de background pra sessão remota de Claude Code.

Roda como subprocess detached do `run_remote.py`. Lê o state.json, invoca
`claude -p` com a tarefa (start) ou input (resume), captura o stream-json
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
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Import local — `presence.py` mora no mesmo diretório que este worker.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import presence  # noqa: E402


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


# Intervalo do heartbeat em segundos. Sessão remota silenciosa por mais que
# isso recebe um "ainda trabalhando" no Telegram pra evitar UX de "morri ou
# tô vivo?". Override via env KOBE_CODER_HEARTBEAT_SECONDS.
_DEFAULT_HEARTBEAT_SECONDS = 600  # 10 min


def _heartbeat_interval() -> int:
    raw = os.environ.get("KOBE_CODER_HEARTBEAT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_HEARTBEAT_SECONDS
    try:
        return max(60, int(raw))  # mínimo 60s pra não floodar
    except ValueError:
        return _DEFAULT_HEARTBEAT_SECONDS


def _fmt_elapsed(secs: float) -> str:
    s = int(secs)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{ss:02d}s"
    return f"{ss}s"


def _heartbeat_loop(
    stop_event: threading.Event,
    interval: int,
    kobe_home: Path,
    state_path: Path,
    started: float,
    notify_marker: dict,
) -> None:
    """Thread daemon: a cada `interval` segundos, se nada de novo aconteceu
    no log E o sub-claude não emitiu kobe-notify neste turno, manda um
    'ainda trabalhando' ao operador. Evita silêncio prolongado em turnos
    longos sem progresso explícito.
    """
    notify_bin = kobe_home / "bot" / "bin" / "kobe-notify"
    if not notify_bin.is_file():
        return
    while not stop_event.wait(interval):
        # Releitura barata do state pra ter short_id e last_text recentes.
        try:
            state = _read_state(state_path)
        except Exception:  # noqa: BLE001
            continue
        short = state.get("short_id", "????")
        elapsed = _fmt_elapsed(time.monotonic() - started)
        # Se o sub-claude já mandou kobe-notify nesse turno, pula heartbeat
        # (evita ruído duplicado). O marcador é atualizado pelo run_claude
        # via heurística de leitura do log.
        if notify_marker.get("sub_claude_notified", False):
            continue
        last = (state.get("last_text") or "").strip()
        preview = last[:200] + "…" if len(last) > 200 else last
        msg = (
            f"⏳ [coder] sessão `{short}` em andamento há {elapsed} — "
            f"ainda sem progresso explícito.\n"
            + (f"Última fala interna:\n\n{preview}" if preview else "")
        )
        try:
            subprocess.run(
                [str(notify_bin), msg],
                timeout=15,
                capture_output=True,
            )
        except Exception:  # noqa: BLE001 — heartbeat é nice-to-have
            pass


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


# ───────────────────────── Resumo de fechamento (BUG 2) ─────────────────────
# Quando uma sessão MORRE com trabalho feito (cota/crash/OOM), o operador ficava
# sem saber "onde parou e é seguro?" — exigia garimpo manual (state + git +
# .local). Aqui montamos um resumo determinístico (verdade do git, não o que a
# sessão *achava*) e entregamos via kobe-notify/attach + campo no state.


def _git_out(cwd: str | Path, args: list[str]) -> str | None:
    """git read-only na cwd. Retorna stdout (strip) em sucesso, None em erro/exceção.
    Para checagens de existência (rc 0, stdout vazio) retorna "" — distinto de None."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _build_closing_summary(state: dict) -> str:
    """Resumo legível de 'onde a sessão parou' — commits que ela criou, push
    pendente, estado da árvore, checklist DECLARADO vs verdade do git, artefatos.
    A régua é o git (autoritativo); o checklist do plano é o que a sessão *achava*
    (a ressalva do operador: a faxina morreu com o checklist todo `[ ]` apesar de
    2 commits). O resumo sinaliza divergência."""
    cwd = state.get("cwd") or "."
    short = state.get("short_id", "?")
    start = state.get("head_sha_at_start")
    head = _git_out(cwd, ["rev-parse", "--short", "HEAD"])
    branch = _git_out(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])

    L = [f"📍 [coder] sessão `{short}` encerrou — resumo de fechamento (onde parou):", ""]
    L.append(f"• cwd: `{cwd}`")
    L.append(f"• branch: `{branch or '?'}`  ·  HEAD: `{head or '?'}`")

    # Commits criados DURANTE a sessão (head registrado no start → HEAD).
    if not start:
        L.append("• commits da sessão: head inicial não registrado — não dá pra isolar.")
    elif _git_out(cwd, ["cat-file", "-e", f"{start}^{{commit}}"]) is None:
        L.append(f"• commits da sessão: sha inicial `{start[:8]}` sumiu do repo (rebase/reset?).")
    else:
        log = _git_out(cwd, ["log", "--oneline", f"{start}..HEAD"])
        if log:
            L.append("• commits criados pela sessão (NÃO pushados sem teu OK):")
            L += [f"    {ln}" for ln in log.splitlines()]
        else:
            L.append("• commits da sessão: nenhum (não commitou).")

    # Estado vs upstream (ahead/behind) — sinal de push pendente.
    sb = _git_out(cwd, ["status", "-sb"])
    if sb:
        L.append(f"• vs upstream: {sb.splitlines()[0].lstrip('#').strip()}")

    # Working tree limpo ou com trabalho solto não-commitado.
    dirty = _git_out(cwd, ["status", "--porcelain"])
    if dirty is None:
        L.append("• working tree: (cwd não é repo git ou git indisponível)")
    elif dirty:
        L.append("• working tree: SUJO — trabalho não-commitado (risco de perda):")
        L += [f"    {ln}" for ln in dirty.splitlines()[:10]]
    else:
        L.append("• working tree: LIMPO (nada solto).")

    # Checklist DECLARADO vs git (ressalva do operador). Pega o plano mais recente.
    try:
        planos = sorted(Path(cwd).glob(".local/plano-*.md"), key=lambda p: p.stat().st_mtime)
    except Exception:  # noqa: BLE001
        planos = []
    if planos:
        p = planos[-1]
        try:
            txt = p.read_text(encoding="utf-8")
            done = len(re.findall(r"(?m)^\s*-\s*\[x\]", txt))
            todo = len(re.findall(r"(?m)^\s*-\s*\[ \]", txt))
            L.append(f"• checklist (`{p.name}`): {done} feito(s) declarado(s), {todo} pendente(s).")
            m = re.search(r"(?m)^\s*-\s*\[ \]\s*(.+)$", txt)
            if m:
                L.append(f"    próximo declarado: {m.group(1).strip()[:120]}")
            L.append("    ⚠️ checklist é o que a sessão DECLAROU — confira contra os commits acima (a verdade).")
        except Exception:  # noqa: BLE001
            pass

    # Artefatos recentes em .local (descartável, mas ponteiro útil).
    try:
        locdir = Path(cwd) / ".local"
        arts = sorted(locdir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]
        if arts:
            L.append("• artefatos recentes `.local/`: " + ", ".join(a.name for a in arts))
    except Exception:  # noqa: BLE001
        pass

    L.append("")
    L.append("⚠️ Morte ≠ pronto: nada é pushado sem auditoria + teu OK.")
    return "\n".join(L)


def _emit_closing_summary(state_path: Path, kobe_home: Path) -> None:
    """Monta o resumo de fechamento, grava no state (`closing_summary`) e entrega
    ao operador: notify curto + attach do arquivo completo. Best-effort e blindado
    — nunca propaga exceção pro caminho de morte que o chamou."""
    try:
        state = _read_state(state_path)
    except Exception:  # noqa: BLE001
        return
    # Idempotência: não reemite se já houver resumo (morte detectada 2x).
    if state.get("closing_summary"):
        return
    try:
        summary = _build_closing_summary(state)
    except Exception:  # noqa: BLE001
        logger.exception("falha montando resumo de fechamento")
        return
    try:
        _patch_state(state_path, closing_summary=summary)
    except Exception:  # noqa: BLE001
        pass
    short = state.get("short_id", "sess")
    try:
        cwd = Path(state.get("cwd") or ".")
        outdir = cwd / ".local"
        outdir.mkdir(parents=True, exist_ok=True)
        outf = outdir / f"closing-{short}.md"
        outf.write_text(summary, encoding="utf-8")
    except Exception:  # noqa: BLE001
        outf = None
    notify_bin = kobe_home / "bot" / "bin" / "kobe-notify"
    attach_bin = kobe_home / "bot" / "bin" / "kobe-attach"
    have_env = bool(os.environ.get("KOBE_TELEGRAM_BOT_TOKEN") and os.environ.get("KOBE_CHAT_ID"))
    if have_env and notify_bin.is_file():
        head = (
            f"📍 [coder] sessão `{short}` encerrou — resumo de fechamento "
            f"(onde parou) {'em anexo' if outf else 'abaixo'}. Morte ≠ pronto: "
            f"nada pushado sem teu OK."
        )
        try:
            subprocess.run([str(notify_bin), head if outf else summary],
                           timeout=15, capture_output=True)
        except Exception:  # noqa: BLE001
            pass
    if have_env and outf is not None and attach_bin.is_file():
        try:
            subprocess.run([str(attach_bin), str(outf), "Resumo de fechamento da sessão"],
                           timeout=20, capture_output=True)
        except Exception:  # noqa: BLE001
            pass


def _build_system_prompt(plugin_root: Path, cwd: Path, effort: str = "standard") -> str:
    """Monta o system prompt apenso (`--append-system-prompt`) da sessão remota.

    Carga determinística (código, não confiança no LLM) das camadas de regra:

      1. `prompts/remote-system.md` — base operacional do runtime da sessão
         (protocolo de comunicação, fim de turno, regras destrutivas).
      2. `harness/CONTRACT.md` — o **harness do Coder (B)**: as regras do jogo,
         portáveis e autocontidas. É a peça que NÃO vem da cwd, então tem que
         ser injetada aqui — sem isso a sessão não conhece o contrato.

    O **contrato do projeto (C)** é o `CLAUDE.md` da cwd, carregado nativamente
    pelo Claude Code por a sessão rodar no diretório do projeto. Não inlinamos C
    (evita duplicar o que o Claude Code já carrega e estourar o prompt); em vez
    disso, anexamos uma **nota determinística** sobre a presença/ausência de C,
    pra sessão saber o status sem adivinhar.

    A **camada de usuário do Coder (D)** é o `deploy-profile.md` em
    `$KOBE_HOME/user-data/coder/` (gitignored, fora do repo público): a topologia
    de deploy do operador, que VARIA por operador e não pode subir pro GitHub.
    É injetada aqui (com fallback gracioso se ausente) — é o complemento concreto
    dos invariantes de deploy que o harness (B) fixa de forma genérica.

    O **manual pessoal do operador (A)** NUNCA é carregado aqui — o harness é
    portável e não pode depender do ambiente de um operador específico. D ≠ A:
    A é o manual global que o motor jamais lê; D é dado de deploy que o motor
    injeta de propósito (redundância intencional com o CLAUDE.md global).
    """
    parts: list[str] = []

    remote_system_file = plugin_root / "prompts" / "remote-system.md"
    if remote_system_file.is_file():
        parts.append(remote_system_file.read_text(encoding="utf-8"))
    else:
        # Base operacional ausente é instalação quebrada. Em vez de crashar o
        # turno com erro genérico (a base é a peça mais crítica — carrega o
        # protocolo de kobe-notify), degradamos como no caso do harness: um
        # base mínimo inline garante que a sessão ainda saiba se comunicar e
        # saiba que está num estado degradado. Espelha o tratamento gracioso
        # do CONTRACT.md ausente abaixo (sem assimetria entre as duas peças).
        parts.append(
            "Você é uma **sessão remota de Claude Code** disparada pelo plugin "
            "`coder`, rodando em background (sem TTY). O operador fala com você "
            "pelo Telegram.\n\n"
            "⚠️ INSTALAÇÃO DEGRADADA: o arquivo de base operacional "
            f"`prompts/remote-system.md` não foi encontrado em `{plugin_root}`. "
            "Você está operando com um base mínimo de emergência.\n\n"
            "Regras essenciais de comunicação:\n"
            "- Use `$KOBE_HOME/bot/bin/kobe-notify \"<texto>\"` pra falar com o "
            "operador e `$KOBE_HOME/bot/bin/kobe-attach <path>` pra anexos.\n"
            "- Cada `claude -p` é UM turno: termine o turno (saia) quando "
            "concluir ou precisar de input — não tente loop interativo.\n"
            "- Não rode ações destrutivas (`rm -rf`, force push, `DROP`, etc.) "
            "sem confirmar com o operador via `kobe-notify`.\n"
            "- Avise o operador, no primeiro `kobe-notify`, que a instalação do "
            "Coder está degradada (base operacional ausente)."
        )

    contract_file = plugin_root / "harness" / "CONTRACT.md"
    if contract_file.is_file():
        parts.append(
            "\n\n---\n\n# === HARNESS DO CODER (B) — regras do jogo ===\n\n"
            + contract_file.read_text(encoding="utf-8")
        )
    else:
        # Harness ausente é estado anômalo (instalação quebrada). A sessão
        # ainda roda sob a base operacional, mas avisamos no prompt pra não
        # operar achando que tem o contrato completo quando não tem.
        parts.append(
            "\n\n---\n\n# === HARNESS DO CODER (B) — AUSENTE ===\n\n"
            "⚠️ O arquivo `harness/CONTRACT.md` não foi encontrado na instalação "
            "do Coder. Você está operando só sob a base operacional. Trate toda "
            "ação irreversível com cautela redobrada e, na dúvida, pergunte ao "
            "operador via `kobe-notify`."
        )

    # Nota determinística sobre o contrato do projeto (C). `is_file()` em cwd
    # potencialmente-inexistente (projeto novo, pasta ainda não criada) retorna
    # False sem levantar — cai graciosamente na nota "sem contrato", que é o
    # comportamento certo (projeto novo não tem CLAUDE.md mesmo). Por isso este
    # bloco não exige que o `cwd.mkdir` (lá no run_claude) já tenha rodado.
    project_contract = cwd / "CLAUDE.md"
    if project_contract.is_file():
        c_note = (
            f"O contrato deste projeto (C) é `{project_contract}` — já carregado "
            "automaticamente por você estar nesta cwd. Leia-o e some as regras "
            "dele ao harness (modelo aditivo, §5 do contrato)."
        )
    else:
        c_note = (
            f"Não há `CLAUDE.md` em `{cwd}` — este projeto não tem contrato "
            "próprio (C). Você opera só sob o harness do Coder (B). Convenções "
            "específicas que faltarem são preferência do operador: pergunte, "
            "não chute."
        )
    parts.append(
        "\n\n---\n\n# === CONTRATO DO PROJETO (C) ===\n\n" + c_note
    )

    # Camada de usuário do Coder (D) — dado específico do operador que o MOTOR
    # injeta (≠ camada A, que nunca é carregada). Mora FORA do repo público do
    # plugin, em `$KOBE_HOME/user-data/coder/deploy-profile.md` (gitignored): a
    # topologia de deploy do operador (ambientes, caminhos, estágios e as ações
    # entre estágios) VARIA por operador e NÃO pode subir pro GitHub. Por isso
    # não mora no CONTRACT.md (público). A redundância com o CLAUDE.md global é
    # intencional: o Coder roda como sessão remota sem garantia de receber A,
    # então precisa do dado no próprio mundo dele. Ausente → nota graciosa
    # (usuário 2 sem perfil), degradando como o caso C-ausente — nunca crasha.
    kobe_home_raw = os.environ.get("KOBE_HOME", "").strip()
    profile_text: str | None = None
    if kobe_home_raw:
        profile_file = (
            Path(kobe_home_raw).expanduser()
            / "user-data" / "coder" / "deploy-profile.md"
        )
        if profile_file.is_file():
            try:
                profile_text = profile_file.read_text(encoding="utf-8").strip() or None
            except OSError:
                profile_text = None
    if profile_text:
        parts.append(
            "\n\n---\n\n# === CAMADA DE USUÁRIO DO CODER (D) ===\n\n"
            "Dado específico do operador desta instalação — a topologia de deploy "
            "(ambientes, caminhos, estágios e o que fazer entre eles). Some ao "
            "harness pelo modelo aditivo (§5); em conflito com B/C, §5.1 (para e "
            "avisa). É a fonte concreta do deploy — o harness só fixa os "
            "invariantes (§9).\n\n" + profile_text
        )
    else:
        parts.append(
            "\n\n---\n\n# === CAMADA DE USUÁRIO DO CODER (D) — AUSENTE ===\n\n"
            "Não há perfil de deploy do operador em "
            "`$KOBE_HOME/user-data/coder/deploy-profile.md`. A topologia concreta "
            "de deploy (quantos ambientes, caminhos, estágios, ações entre eles) "
            "não está definida aqui — ela vem do contrato do projeto (C) ou, na "
            "falta, **pergunte ao operador** antes de qualquer passo de deploy. "
            "Não invente caminhos nem ambientes."
        )

    # Procedimento de esforço desta sessão (§3/§4 do contrato).
    if effort == "max":
        proc_note = (
            "**Procedimento 2 — ESFORÇO MÁXIMO.** O operador pediu esforço máximo "
            "explicitamente, e este processo já nasceu em `--effort max` (raciocínio "
            "no máximo). Rode o rito de quatro etapas com o crivo em **agentes "
            "separados** (§3.3, §4): use a ferramenta de subagente/Task pra rodar o "
            "Advogado do Diabo, a Revisão multi-lente (baselines como lentes) e os "
            "Testes em cabeças independentes da que planejou — pra matar o viés de "
            "autoconfirmação. **Rode esses agentes de crivo em esforço elevado "
            "também** (ex.: effort alto/máximo), pra a profundidade chegar tanto na "
            "orquestração quanto em cada lente. Vale o custo extra de token: o "
            "operador assumiu."
        )
    else:
        proc_note = (
            "**Procedimento 1 — turno padrão (default).** Rode o rito de quatro "
            "etapas (Planejamento → Advogado do Diabo → Revisão → Testes) **inline**, "
            "no mesmo turno (§3.3). NÃO escale pro esforço máximo por conta própria — "
            "só o operador comanda isso (§4)."
        )
    parts.append(
        "\n\n---\n\n# === PROCEDIMENTO DESTA SESSÃO ===\n\n" + proc_note
    )

    return "".join(parts)


def _build_prompt(state: dict, mode: str) -> str:
    """Monta o prompt que vai pra stdin do claude.

    O system prompt (base operacional + harness) vai EXCLUSIVAMENTE via
    `--append-system-prompt` (ver `_build_system_prompt`); o stdin carrega só
    a carga de trabalho: a tarefa (start) ou a nova entrada do operador
    (resume). Não há injeção dupla do system prompt.
    """
    if mode == "start":
        return state["task"]
    # resume
    return state.get("pending_input") or "(operador não passou conteúdo na retomada — continue de onde parou)"


def _effort_flags(effort: str) -> list[str]:
    """Flags de boot do `claude -p` pro nível de esforço da sessão (§3/§4).

    Procedimento 2 (esforço máximo) não é só prompt — o processo `claude`
    orquestrador tem que NASCER em esforço máximo (`--effort max`), porque a
    profundidade de raciocínio dele (planejar, julgar o que delegar, sintetizar
    o crivo dos agentes) é ortogonal à orquestração em subagentes, não redundante
    com ela. Modelo: override só se o operador configurou
    `KOBE_CODER_EFFORT_MAX_MODEL` — por padrão NÃO troca o modelo (a escolha
    Fable/Max é decisão parqueada do operador, §14 do plano); apenas sobe o
    esforço. Procedimento 1 (default) não passa flag — usa o default do CLI.
    """
    flags: list[str] = []
    if effort == "max":
        flags += ["--effort", "max"]
        model = os.environ.get("KOBE_CODER_EFFORT_MAX_MODEL", "").strip()
        if model:
            flags += ["--model", model]
    return flags


def _build_session_settings(
    plugin_root: Path, kobe_home: Path, state_path: Path, effort: str = "standard"
) -> Path | None:
    """Escreve o settings.json da sessão que liga o hook `guard` (PreToolUse).

    O guard é o enforcement de código dos gates do contrato (deny-list, gate de
    changelog, PARA-e-espera-OK, HALT). Verificado empiricamente: um hook
    PreToolUse que devolve `permissionDecision: deny` barra a ferramenta MESMO
    sob `bypassPermissions` — é a única forma de travar a sessão autônoma antes
    da ação, não depois.

    Retorna o path do settings, ou None se o guard não existir na instalação —
    nesse caso a sessão roda SEM gates (degrada em vez de quebrar; o operador é
    avisado pelo prompt de que está sem rede). Fail-open consciente: um gate que
    quebra a sessão por bug de instalação seria pior que a ausência do gate.
    """
    settings: dict = {}
    # Esforço máximo (§4) = ULTRACODE: liga xhigh + orquestração de agentes já no
    # BOOT da sessão. Provado (cobaia 2026-06-23): `--settings {"ultracode": true}`
    # liga o modo no lançamento — o gatilho no prompt NÃO funciona. Convive com o
    # hook do guard no mesmo objeto de settings (chaves distintas) — provado (T1).
    # Só o caminho-sala passa effort; o caller do `claude -p` mantém o default
    # "standard", então o caminho headless segue intocado (aditivo).
    if effort == "max":
        settings["ultracode"] = True
    # O guard é o enforcement de código dos gates do contrato (PreToolUse deny):
    # um hook que devolve `permissionDecision: deny` barra a ferramenta MESMO sob
    # `bypassPermissions` — única forma de travar a sessão autônoma antes da ação.
    guard = plugin_root / "harness" / "hooks" / "guard.py"
    if guard.is_file():
        venv_python = kobe_home / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.is_file() else "python3"
        # O path do state vai como ARGV do hook (controlado pelo worker), NÃO no env
        # da sessão — assim a sessão não conhece o path do próprio cadeado e não
        # pode reescrever plan_approved/halted via Bash (B1 da revisão).
        hook_cmd = (
            f"{shlex.quote(python)} {shlex.quote(str(guard))} "
            f"--state {shlex.quote(str(state_path))}"
        )
        settings["hooks"] = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command", "command": hook_cmd}],
                }
            ]
        }
    # Sem guard e sem ultracode → nada a injetar (sessão roda sem settings extra).
    if not settings:
        return None
    # Fora do diretório de estado e da extensão `*.json` — senão o settings
    # colidiria com o glob `<short_id>*.json` de _resolve_session e quebraria
    # resume/status/halt/merge por short-id (B2 da revisão). Subdir `.settings/`.
    settings_dir = state_path.parent / ".settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / f"{state_path.stem}.json"
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return settings_path


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
    # antes do popen porque ARG_MAX comporta sem stress. Monta as camadas de
    # regra de forma determinística: base operacional + harness do Coder (B) +
    # nota sobre o contrato do projeto (C). Nunca o manual pessoal (A).
    plugin_root = Path(__file__).resolve().parent.parent
    system_prompt = _build_system_prompt(plugin_root, cwd, state.get("effort", "standard"))

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

    # Esforço de boot do processo (§3/§4): no Procedimento 2, o orquestrador
    # nasce em `--effort max` (+ override de modelo se configurado). Vale pra
    # start e resume — cada turno reconstrói o cmd lendo o state.effort.
    cmd += _effort_flags(state.get("effort", "standard"))

    # Liga o hook `guard` (gates determinísticos) via --settings. Se o guard
    # não existir, settings_path é None e a sessão roda sem gates (degrada).
    settings_path = _build_session_settings(plugin_root, kobe_home, state_path)
    if settings_path is not None:
        cmd += ["--settings", str(settings_path)]

    prompt = _build_prompt(state, mode)

    cwd.mkdir(parents=True, exist_ok=True)

    _patch_state(state_path, status="running", started_turn_at=_now_iso())

    # Env da sessão = o que o worker já tem (KOBE_HOME, token, etc.). NÃO
    # injetamos o path do state aqui: o hook recebe o path via argv (no
    # settings), fora do alcance da sessão. Assim a sessão não pode descobrir e
    # reescrever o próprio cadeado (plan_approved/halted) por Bash.
    session_env = os.environ.copy()
    session_env.pop("KOBE_CODER_STATE_FILE", None)

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
            env=session_env,
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

    # Registra presença do sub-claude na pasta global de instâncias ativas.
    # PID registrado é o do `claude` (não do worker), pra `/coder_status` e
    # avisos de conflito apontarem o processo certo. Falha silenciosa: se
    # algo der ruim aqui, o turno continua — presença é metadado, não trava.
    try:
        presence.register(
            source="telegram-coder",
            cwd=state["cwd"],
            session_id=session_id,
            topic_key=state.get("topic_key"),
            pid=proc.pid,
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha registrando presença do sub-claude")

    # Manda o prompt via stdin e fecha.
    assert proc.stdin is not None
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except BrokenPipeError:
        # Claude pode ter morrido antes de ler. Vai cair no wait abaixo.
        pass

    # Heartbeat: thread daemon que avisa o operador quando o turno tá
    # silencioso há muito tempo. Compartilha `notify_marker` (dict mutável)
    # com o loop principal — quando detectamos que o sub-claude chamou
    # kobe-notify via Bash, marcamos a flag pra suprimir heartbeat duplicado.
    notify_marker = {"sub_claude_notified": False}
    turn_started = time.monotonic()
    hb_stop = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(
            hb_stop,
            _heartbeat_interval(),
            kobe_home,
            state_path,
            turn_started,
            notify_marker,
        ),
        daemon=True,
    )
    hb_thread.start()

    # `claude_pid` capturado pra desregistrar presença no fim do turno.
    # Se algo explodir no meio, cleanup inline da `presence.list_active()`
    # vai remover o órfão na próxima leitura (PID do claude já morreu junto).
    claude_pid = proc.pid

    # Consome stream-json linha por linha. Atualiza state periodicamente.
    last_text: str | None = state.get("last_text")
    last_persist_ts = 0.0
    PERSIST_EVERY = 5.0  # segundos
    assistant_texts: list[str] = []
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        decoded = raw_line.decode("utf-8", errors="replace")
        log_fh.write(decoded)
        log_fh.flush()
        # Detecta chamada do sub-claude a kobe-notify/attach via Bash —
        # quando aparece, suprime heartbeat (sub-claude já está se
        # comunicando). Heurística textual barata, sem regex pesado.
        if not notify_marker["sub_claude_notified"] and (
            "kobe-notify" in decoded or "kobe-attach" in decoded
        ):
            notify_marker["sub_claude_notified"] = True
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

    # Para o heartbeat (sub-claude terminou o turno).
    hb_stop.set()
    hb_thread.join(timeout=2)

    proc.wait()
    exit_code = proc.returncode
    log_fh.write(f"# --- turn end @ {_now_iso()} exit={exit_code} ---\n")
    log_fh.close()

    # Settings é efêmero (regenerado a cada turno) — limpa pra não acumular.
    if settings_path is not None:
        try:
            settings_path.unlink()
        except OSError:
            pass

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

    # Desregistra presença do sub-claude. Falha silenciosa — o PID já saiu,
    # então mesmo se o unlink falhar, cleanup inline futuro limpa.
    try:
        presence.unregister(pid=claude_pid)
    except Exception:  # noqa: BLE001
        logger.exception("falha desregistrando presença do sub-claude")

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


# ───────────────────────── Modo sala (--remote-control) ─────────────────────
# O MODELO de dispatch do Coder: em vez de `claude -p` headless, a sessão abre
# como sala tmux `--remote-control` — VISÍVEL/navegável no Claude Code Desktop —
# e, no esforço máximo, já em ULTRACODE (via --settings, provado 2026-06-23). O
# `claude -p` (run_claude) fica como código dormente/fallback interno, não como
# escolha do operador. O worker LANÇA a sala e fica MONITORANDO (status + watcher
# de morte); o "resume" injeta input na sala viva via `tmux send-keys` (T2).


def _sala_name(state: dict) -> str:
    return f"coder-{state['short_id']}"  # label que aparece no app


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def _tmux_has_session(name: str) -> bool:
    return _tmux("has-session", "-t", name).returncode == 0


def _claude_pid_for_sala(sala: str) -> int | None:
    r = subprocess.run(
        ["pgrep", "-f", f"remote-control {sala}"], capture_output=True, text=True
    )
    pids = [int(p) for p in r.stdout.split() if p.strip().isdigit()]
    return pids[0] if pids else None


def _write_sala_brief(state: dict, brief_path: Path) -> None:
    sala = _sala_name(state)
    task = (state.get("task") or "(tarefa não registrada)").strip()
    brief_path.write_text(
        f"# Sala Coder `{sala}` — sessão de desenvolvimento\n\n"
        f"> Você é uma sessão de código Claude rodando em tmux "
        f"(`--remote-control {sala}`), disparada pelo Coder. Este arquivo é a tua "
        f"fonte de verdade — leia inteiro antes de agir.\n\n"
        f"## Tarefa\n\n{task}\n\n"
        f"## Rito de codificação (obrigatório)\n"
        f"Para cada unidade de código: **Planejamento → Advogado do Diabo → "
        f"Revisão → Testes** (testes no ambiente de desenvolvimento, na medida do "
        f"possível). As regras "
        f"completas (reversibilidade, gates, deploy, changelog) estão no teu system "
        f"prompt (contrato do Coder).\n\n"
        f"## Sinais de vida (NÃO fique mudo)\n"
        f"Reporte cada marco pro operador via `bot/bin/kobe-notify`: início, "
        f"conclusão de cada fase, bloqueios que exijam decisão dele, e o FIM. "
        f"Entregue artefatos com `bot/bin/kobe-attach <path>`.\n\n"
        f"## Turnos\n"
        f"Quando terminar (ou precisar de input), **termine o turno** (pare e "
        f"aguarde). O operador te retoma mandando a próxima mensagem — ela chega "
        f"aqui na sala. Não fique em loop interativo.\n",
        encoding="utf-8",
    )


_SALA_POLL_SECONDS = 8
_SALA_MONITOR_MAX_SECONDS = 6 * 3600  # backstop: não deixa um worker imortal


def _pane_busy(pane: str) -> bool:
    # O claude mostra "esc to interrupt" na status bar enquanto processa um turno;
    # idle (esperando input) NÃO mostra. Sinal robusto observado nos spikes (T2).
    return "esc to interrupt" in pane


def _extract_pane_last(pane: str) -> str | None:
    # Best-effort: a última fala do claude no pane (linhas com "●"). A TUI é
    # ruidosa — isto é só um preview grosso pro /coder-status; a fonte real pro
    # operador é a própria sala (Desktop) + os kobe-notify dela.
    bullets = [l.strip() for l in pane.splitlines() if l.lstrip().startswith("●")]
    return (bullets[-1].lstrip("● ").strip() or None) if bullets else None


def _monitor_sala(state_path: Path, sala: str, kobe_home: Path) -> int:
    """Fica vivo observando a sala DURANTE o turno (o worker não morre no launch).

    - Atualiza status (running/idle) e last_text lendo o `capture-pane`.
    - Detecta MORTE silenciosa (a sala caiu) → marca dead + avisa (watcher).
    - Heartbeat: avisa se o turno passa muito tempo sem encerrar.
    - ENCERRA quando a sala fica idle (turno terminou) — a sala segue VIVA pro
      próximo resume. Morte-entre-turnos (sala cai sem worker olhando) é pega no
      próximo resume (has-session) e pela limpeza oportunista do start.
    """
    started = time.monotonic()
    last_hb = started
    hb_interval = _heartbeat_interval()
    saw_busy = False
    idle_streak = 0
    # Margem de boot: o claude leva alguns segundos pra subir; sem isso o monitor
    # poderia ver "idle" no boot e encerrar antes do turno começar.
    time.sleep(_SALA_POLL_SECONDS)
    while True:
        if not _tmux_has_session(sala):
            _patch_state(state_path, status="dead", exit_code=-1,
                         last_text="sala tmux caiu (morte detectada pelo monitor).")
            _notify_error(kobe_home,
                          f"🔴 [coder] a sala `{sala}` caiu durante o turno. "
                          f"Abra uma nova pra continuar.")
            # BUG 2: morte com trabalho feito → resumo de fechamento (onde parou).
            _emit_closing_summary(state_path, kobe_home)
            return 1
        pane = _tmux("capture-pane", "-t", sala, "-p").stdout
        last_text = _extract_pane_last(pane)
        elapsed = time.monotonic() - started
        if _pane_busy(pane):
            saw_busy = True
            idle_streak = 0
            _patch_state(state_path, status="running", last_text=last_text)
            if time.monotonic() - last_hb >= hb_interval:
                _notify_error(kobe_home,
                              f"⏳ [coder] sala `{sala}` trabalhando há "
                              f"{_fmt_elapsed(elapsed)} — ainda em andamento.")
                last_hb = time.monotonic()
        else:
            # Idle. Exige 2 leituras idle seguidas (evita a janela curta entre
            # duas tool calls). Confirma se: viu busy e agora 2x idle; OU nunca
            # viu busy mas já passou tempo (turno trivial que terminou rápido).
            idle_streak += 1
            if (saw_busy and idle_streak >= 2) or (not saw_busy and elapsed > 60):
                _patch_state(state_path, status="idle", last_text=last_text)
                return 0
        if elapsed > _SALA_MONITOR_MAX_SECONDS:
            # Backstop: solta o worker (a sala continua viva); status fica como está.
            return 0
        time.sleep(_SALA_POLL_SECONDS)


def run_sala(*, state_path: Path, mode: str, kobe_home: Path) -> int:
    """Dispatch em modo sala tmux `--remote-control` (lançador + monitor).

    start  → escreve o brief, monta settings (guard + ultracode-se-max), abre a
             sala tmux e sai (a sala vive sozinha; reporta por kobe-notify).
    resume → injeta o pending_input na sala viva via `tmux send-keys` (T2).
    """
    state = _read_state(state_path)
    cwd = Path(state["cwd"])
    sala = _sala_name(state)
    plugin_root = Path(__file__).resolve().parent.parent

    if mode == "resume":
        if not _tmux_has_session(sala):
            _patch_state(state_path, status="failed",
                         last_text="sala tmux não está mais viva.")
            _notify_error(
                kobe_home,
                f"🔴 [coder] sessão `{state['short_id']}` — a sala `{sala}` não "
                f"está mais viva; não dá pra retomar. Abra uma nova.",
            )
            # BUG 2: a sala morreu entre turnos — resumo de fechamento (onde parou).
            _emit_closing_summary(state_path, kobe_home)
            return 1
        pending = (state.get("pending_input") or "").strip()
        if pending:
            # send-keys em dois tempos: texto literal (-l), depois Enter. Provado (T2).
            _tmux("send-keys", "-t", sala, "-l", pending)
            _tmux("send-keys", "-t", sala, "Enter")
        _patch_state(state_path, status="running", pending_input=None,
                     last_activity=_now_iso(),
                     turn_count=(state.get("turn_count") or 0) + 1)
        return _monitor_sala(state_path, sala, kobe_home)

    # mode == "start"
    salas_dir = cwd / ".local" / "salas"
    salas_dir.mkdir(parents=True, exist_ok=True)
    brief_path = salas_dir / f"{sala}.md"
    _write_sala_brief(state, brief_path)

    effort = state.get("effort", "standard")
    settings_path = _build_session_settings(plugin_root, kobe_home, state_path, effort)
    # System prompt (~28KB) vai por ARQUIVO via `--append-system-prompt-file`
    # (Item 4, preferência do operador): o CLI lê o arquivo direto — a linha de
    # lançamento fica curta (nada de 28KB no `ps`), some o `$(cat ...)` (que
    # dependia de locale/PYTHONUTF8 e podia mangle trailing newline), e a injeção
    # do briefing fica mais fiel (conteúdo raw, não pós-bash).
    sysprompt_path = salas_dir / f"{sala}.sysprompt.txt"
    sysprompt_path.write_text(_build_system_prompt(plugin_root, cwd, effort),
                              encoding="utf-8")
    launch_prompt = (
        f"Leia .local/salas/{sala}.md — é o teu briefing completo desta sala. "
        f"Comece por aí, sob o rito de 4 etapas."
    )
    settings_arg = (
        f"--settings {shlex.quote(str(settings_path))} " if settings_path else ""
    )
    launcher = salas_dir / f"{sala}-launch.sh"
    launcher.write_text(
        "#!/bin/bash\n"
        f"cd {shlex.quote(str(cwd))}\n"
        f"exec claude --permission-mode bypassPermissions "
        f"--remote-control {shlex.quote(sala)} {settings_arg}"
        f"--append-system-prompt-file {shlex.quote(str(sysprompt_path))} "
        f"{shlex.quote(launch_prompt)}\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    # Envs do Telegram pra a sala (kobe-notify/attach) via tmux -e — a sala não
    # herda o env do worker de outra forma. Só passa o que existe.
    tmux_cmd = ["tmux", "new-session", "-d", "-s", sala, "-c", str(cwd)]
    for k in ("KOBE_CHAT_ID", "KOBE_THREAD_ID", "KOBE_TELEGRAM_BOT_TOKEN"):
        v = os.environ.get(k)
        if v:
            tmux_cmd += ["-e", f"{k}={v}"]
    tmux_cmd += [f"bash {shlex.quote(str(launcher))}"]

    proc = subprocess.run(tmux_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        _patch_state(state_path, status="failed", exit_code=proc.returncode,
                     last_text=f"falha abrindo sala tmux: {proc.stderr.strip()}")
        _notify_error(kobe_home,
                      f"🔴 [coder] não consegui abrir a sala `{sala}`: "
                      f"{proc.stderr.strip()}")
        return 1

    # PID do claude da sala (presença/status). Pode levar um instante pra subir.
    claude_pid = None
    for _ in range(10):
        claude_pid = _claude_pid_for_sala(sala)
        if claude_pid:
            break
        time.sleep(1)

    _patch_state(state_path, status="running", sala_name=sala, sala_mode=True,
                 pid=claude_pid, last_activity=_now_iso())
    if claude_pid:
        try:
            presence.register(source="telegram-coder-sala", cwd=state["cwd"],
                              session_id=state["session_id"],
                              topic_key=state.get("topic_key"), pid=claude_pid)
        except Exception:  # noqa: BLE001
            logger.exception("falha registrando presença da sala")

    _notify_error(
        kobe_home,
        f"🟢 [coder] sala `{sala}` aberta e visível no Claude Code Desktop"
        + (" (ultracode ligado)" if effort == "max" else "")
        + ". Trabalhando na tarefa; reporto os marcos por aqui.",
    )
    return _monitor_sala(state_path, sala, kobe_home)


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

    # Sinaliza no state que estamos rodando (e captura pra rotear o modo).
    state = _patch_state(state_path, worker_started_at=_now_iso(), worker_pid=os.getpid())

    # Tratamento de SIGTERM (quando o supervisord/operador matar)
    def _on_term(signum, frame):  # noqa: ANN001 — handler
        try:
            _patch_state(state_path, status="terminated", exit_code=-15)
        except Exception:  # noqa: BLE001
            pass
        sys.exit(143)

    signal.signal(signal.SIGTERM, _on_term)

    try:
        # BUG 1 (incidente 2026-06-23, sessão 1dfc1ed6): dispatch de Coder é
        # SEMPRE sala tmux com remote control — requisito inegociável do operador.
        # NÃO existe sessão de Coder sem sala. O antigo ternário
        # `run_sala if sala_mode else run_claude` era um FALLBACK SILENCIOSO: um
        # state sem `sala_mode` (escrito por código pré-sala, ou regressão futura)
        # caía no ramo headless e a sessão rodava INVISÍVEL — foi exatamente o que
        # aconteceu com 1dfc1ed6 (state de 15:16 sem a flag + worker novo às 21:18).
        #
        # Correção (A1 do plano): ausência de `sala_mode` é estado ANÔMALO, não um
        # caso tolerado. Em vez de rodar mudo, NORMALIZA pra sala e AVISA loud — a
        # sessão nunca roda invisível, e nunca se perde trabalho. run_claude segue
        # no arquivo como código DORMENTE (não-alcançável por este roteador); se a
        # própria sala não puder subir (tmux ausente/falha), run_sala já falha duro.
        if not state.get("sala_mode"):
            logger.warning(
                "state sem sala_mode — promovendo a sala (estado anômalo, BUG 1)"
            )
            state = _patch_state(state_path, sala_mode=True)
            _notify_error(
                kobe_home,
                f"⚠️ [coder] sessão `{state.get('short_id', '?')}` veio sem marca "
                f"de sala (state antigo/anômalo) — promovida a sala automaticamente. "
                f"Nenhuma sessão de Coder roda invisível.",
            )
        return run_sala(state_path=state_path, mode=args.mode, kobe_home=kobe_home)
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
            # BUG 2: crash com trabalho feito → resumo de fechamento (onde parou).
            _emit_closing_summary(state_path, kobe_home)
        except Exception:  # noqa: BLE001
            pass
        return 99


if __name__ == "__main__":
    sys.exit(main())
