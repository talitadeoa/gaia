"""System health: estado dos componentes, em uma lista pronta para a tela.

Cada item tem ``status`` (ok|warn|error|off), ``detail`` legivel e
``checked_at``. Nada aqui levanta: se um componente nao responder, ele proprio
vira ``error`` com o motivo — um painel de saude que quebra quando algo quebra
nao serve para nada.

A maior parte das checagens e local e barata; as que dependem de rede recebem
o resultado pronto (``providers``, ``telegram``) para nao bloquear a rota.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from gaia.agent.providers import PRESETS, normalize_provider, resolve_api_key
from gaia.config import Config
from gaia.memory.store import MemoryStore
from gaia.secondbrain.index import SecondBrainIndex

STATUSES = ("ok", "warn", "error", "off")

# Overrides do .env que ja causaram incidente (proatividade desligada por cima
# do TOML, modelo trocado sem aviso). O painel mostra a contradicao.
_CONFLICT_KEYS = (
    "GAIA_PROACTIVE_IDLE_CHECKIN",
    "GAIA_PROACTIVE_STARTUP_GREETING",
    "GAIA_AGENT_MODEL",
    "GAIA_AGENT_PROVIDER",
    "GAIA_TELEGRAM_ENABLED",
    "GAIA_AGENT_AUTO_APPROVE",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _item(
    ident: str, label: str, status: str, detail: str, **extra: Any
) -> dict[str, Any]:
    if status not in STATUSES:
        status = "error"
    return {
        "id": ident,
        "label": label,
        "status": status,
        "detail": detail,
        "checked_at": _now(),
        **extra,
    }


def engine_health(orch: Any = None) -> dict[str, Any]:
    """Estado do motor: modelo ativo, ocupado, canal."""
    if orch is None:
        return _item(
            "engine", "Motor", "warn", "motor nao ligado neste processo"
        )
    modelo = ""
    try:
        modelo = orch.cfg.agent.model_slug()
    except Exception:
        modelo = ""
    ocupado = bool(getattr(orch, "_busy", False))
    canal = str(getattr(orch, "channel", ""))
    return _item(
        "engine",
        "Motor",
        "ok",
        f"modelo {modelo or '?'} · {'ocupado' if ocupado else 'ocioso'}"
        + (f" · canal {canal}" if canal else ""),
        busy=ocupado,
        model=modelo,
        channel=canal,
    )


def database_health(cfg: Config, store: MemoryStore | None) -> dict[str, Any]:
    if store is None:
        return _item("database", "Banco", "off", "sem MemoryStore neste processo")
    path = Path(store.db_path)
    try:
        tamanho = path.stat().st_size if path.is_file() else 0
        with store._lock:
            modo = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
            integridade = store._conn.execute("PRAGMA quick_check").fetchone()[0]
        versao = store.schema_version()
    except sqlite3.Error as exc:
        return _item("database", "Banco", "error", f"sqlite: {exc}", path=str(path))
    if str(integridade).lower() != "ok":
        return _item(
            "database",
            "Banco",
            "error",
            f"quick_check: {integridade}",
            path=str(path),
        )
    tamanho_mb = tamanho / (1024 * 1024)
    detalhe = f"{path.name} · {tamanho_mb:.1f} MB · schema v{versao} · {modo}"
    status = "ok" if versao >= 6 else "warn"
    return _item(
        "database",
        "Banco",
        status,
        detalhe,
        path=str(path),
        schema_version=versao,
        journal_mode=str(modo),
        size_bytes=tamanho,
    )


def second_brain_health(
    cfg: Config, index: SecondBrainIndex | None
) -> dict[str, Any]:
    raiz = cfg.second_brain_path()
    if not raiz.is_dir():
        return _item(
            "second_brain",
            "Second Brain",
            "error",
            f"raiz nao encontrada: {raiz}",
            path=str(raiz),
        )
    notas = 0
    if index is not None:
        try:
            notas = index.count()
        except Exception as exc:
            return _item(
                "second_brain",
                "Second Brain",
                "error",
                f"indice ilegivel: {exc}",
                path=str(raiz),
            )
    if notas == 0:
        return _item(
            "second_brain",
            "Second Brain",
            "warn",
            f"{raiz} sem notas indexadas",
            path=str(raiz),
            notes=0,
        )
    return _item(
        "second_brain",
        "Second Brain",
        "ok",
        f"{notas} nota(s) indexada(s)",
        path=str(raiz),
        notes=notas,
    )


def providers_health(
    cfg: Config, providers: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Credencial do provedor ativo. Nunca devolve o valor da chave."""
    nome = normalize_provider(cfg.agent.provider)
    chave = resolve_api_key(cfg.agent.provider, cfg.agent.api_key)
    preset = PRESETS.get(nome)
    sem_chave_ok = preset is not None and not preset.env_keys and not preset.auth_file
    if chave:
        origem = "ambiente/.env"
        if preset is not None:
            for env_key in preset.env_keys:
                if os.environ.get(env_key, "").strip():
                    origem = env_key
                    break
            else:
                if getattr(cfg.agent, "api_key", "").strip():
                    origem = "config"
        return _item(
            "providers",
            "Provedores",
            "ok",
            f"{nome} com chave ({origem}) · {cfg.agent.model}",
            provider=nome,
            key_source=origem,
            configured=[item["name"] for item in (providers or []) if item.get("has_key")],
        )
    if sem_chave_ok:
        return _item(
            "providers",
            "Provedores",
            "ok",
            f"{nome} sem chave propria (local)",
            provider=nome,
        )
    return _item(
        "providers",
        "Provedores",
        "error",
        f"{nome} sem credencial: defina a chave no .env",
        provider=nome,
    )


def telegram_health(cfg: Config, last_error: str = "") -> dict[str, Any]:
    if not cfg.telegram.enabled:
        return _item("telegram", "Telegram", "off", "desligado no config")
    if not cfg.telegram.bot_token.strip():
        return _item(
            "telegram", "Telegram", "error", "enabled = true, mas sem bot_token"
        )
    if not cfg.telegram.allowed_chat_id.strip():
        return _item(
            "telegram",
            "Telegram",
            "warn",
            "sem allowed_chat_id: o primeiro chat que falar assume o bot",
        )
    if last_error:
        return _item(
            "telegram", "Telegram", "warn", f"ultimo erro: {last_error}"
        )
    return _item(
        "telegram",
        "Telegram",
        "ok",
        f"chat autorizado {cfg.telegram.allowed_chat_id}",
    )


def scheduler_health(
    cfg: Config,
    store: MemoryStore | None,
    tasks: dict[str, bool] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Workers vivos + automacoes perdidas (o alerta mais importante)."""
    from gaia.control.automations import describe_automations

    tasks = tasks or {}
    mortos = [nome for nome, vivo in tasks.items() if not vivo]
    itens = describe_automations(cfg, store, now)
    perdidas = [item["id"] for item in itens if item["status"] == "missed"]
    falhas = [
        item["id"]
        for item in itens
        if (item["last_run"] or {}).get("status") == "failed"
    ]
    if mortos:
        return _item(
            "scheduler",
            "Agendador",
            "error",
            "worker parado: " + ", ".join(mortos),
            stopped=mortos,
            missed=perdidas,
        )
    if perdidas:
        return _item(
            "scheduler",
            "Agendador",
            "warn",
            "nao executou: " + ", ".join(perdidas),
            missed=perdidas,
            failed=falhas,
        )
    if falhas:
        return _item(
            "scheduler",
            "Agendador",
            "warn",
            "ultima execucao falhou: " + ", ".join(falhas),
            failed=falhas,
        )
    # Rotinas continuas com problema: e a falha silenciosa que a tela de
    # automacoes existe para matar (worker morto, ultima execucao manual ruim).
    from gaia.control.routines import describe_routines

    rotinas_erro = [
        item["id"]
        for item in describe_routines(cfg, store, now)
        if item["status"] == "error"
    ]
    if rotinas_erro:
        return _item(
            "scheduler",
            "Agendador",
            "error",
            "rotina com problema: " + ", ".join(rotinas_erro),
            routines=rotinas_erro,
            missed=perdidas,
            failed=falhas,
        )
    ativas = [item["id"] for item in itens if item["enabled"]]
    return _item(
        "scheduler",
        "Agendador",
        "ok",
        f"{len(ativas)} automacao(oes) ativa(s)",
        active=ativas,
        missed=[],
    )


def config_health(
    cfg: Config, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Acusa override do .env que contradiz o TOML (causa do incidente 22/11)."""
    fonte = dict(os.environ if env is None else env)
    conflitos: list[str] = []
    for chave in _CONFLICT_KEYS:
        valor = fonte.get(chave)
        if valor is None or valor == "":
            continue
        conflitos.append(f"{chave}={valor}")
    if conflitos:
        return _item(
            "config",
            "Configuracao",
            "warn",
            "override no ambiente/.env: " + ", ".join(conflitos),
            overrides=conflitos,
        )
    return _item(
        "config",
        "Configuracao",
        "ok",
        f"{cfg.config_file or 'defaults'} sem override no ambiente",
        config_file=cfg.config_file,
    )


def system_health(
    cfg: Config,
    store: MemoryStore | None = None,
    *,
    orch: Any = None,
    index: SecondBrainIndex | None = None,
    providers: list[dict[str, Any]] | None = None,
    tasks: dict[str, bool] | None = None,
    env: dict[str, str] | None = None,
    telegram_error: str = "",
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Todos os componentes, na ordem em que a tela mostra."""
    if index is None:
        index = getattr(orch, "index", None)
    return [
        engine_health(orch),
        database_health(cfg, store),
        second_brain_health(cfg, index),
        providers_health(cfg, providers),
        telegram_health(cfg, telegram_error),
        scheduler_health(cfg, store, tasks, now),
        config_health(cfg, env),
    ]


def overall_status(items: list[dict[str, Any]]) -> str:
    """Pior status da lista, ignorando componentes desligados."""
    ordem = {"ok": 0, "off": 0, "warn": 1, "error": 2}
    pior = "ok"
    for item in items:
        status = str(item.get("status") or "ok")
        if ordem.get(status, 0) > ordem.get(pior, 0):
            pior = status
    return pior


__all__ = [
    "config_health",
    "database_health",
    "engine_health",
    "overall_status",
    "providers_health",
    "scheduler_health",
    "second_brain_health",
    "system_health",
    "telegram_health",
]
