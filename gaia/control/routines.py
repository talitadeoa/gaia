"""Rotinas continuas: o que roda sem horario declarado na tabela ``automations``.

A distincao existe porque o driver e outro. Uma **automacao** tem janela
(``at``/``until``/``days``) e quem decide e o ``_scheduler_tick``; uma
**rotina** roda dentro de um laco que ja existe (``_proactive_loop``,
``_memory_loop``), no boot ou por evento. Sem horario nao ha "perdeu a hora":
o estado possivel e ``active``/``paused``/``disabled``/``error``.

O estado da pessoa mora na tabela ``routines`` (schema v9) como **override**:
linha ausente = ligada. Assim nao existe seed, nem sincronizacao entre registry
e banco, nem o risco de "boot novo desfaz a pausa" (que o seed de
``automations`` precisa resolver porque la as colunas sao a agenda inteira).

Nada aqui executa rotina: quem chama o handler e o ``RoutineMixin``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gaia.config import Config
from gaia.memory.store import MemoryStore

# Rotulo do gatilho na tela (o web nao traduz codigo).
TRIGGER_LABELS: dict[str, str] = {
    "boot": "no boot",
    "loop": "a cada ciclo",
    "event": "por evento",
    "watch": "varredura",
}


@dataclass(frozen=True)
class Routine:
    """Descritor de uma rotina continua.

    ``requires`` sao caminhos de config que precisam ser verdadeiros (o valor e
    avaliado com ``bool``, entao ``tasks_poll_seconds = 0`` desliga). ``worker``
    e o rotulo usado pelo ``ControlCenter._tasks()``: se o worker do motor
    estiver morto, a rotina nao roda e o painel mostra ``error``.
    ``runnable`` diz se existe handler seguro para "rodar agora".
    """

    id: str
    name: str
    skill: str
    description: str
    trigger: str  # boot | loop | event | watch
    module: str
    requires: tuple[str, ...] = ()
    worker: str = ""
    runnable: bool = False


ROUTINES: tuple[Routine, ...] = (
    Routine(
        id="initiative:greeting",
        name="Saudacao de abertura",
        skill="initiative",
        description="Cumprimenta ao subir o motor, com lembretes pendentes no fim.",
        trigger="boot",
        module="gaia/controller/orchestrator.py, gaia/controller/initiative.py",
        requires=("proactive.enabled", "proactive.startup_greeting"),
        worker="iniciativa",
        runnable=True,
    ),
    Routine(
        id="initiative:reminders",
        name="Lembretes vencidos",
        skill="initiative",
        description="Entrega no canal os lembretes cuja hora passou.",
        trigger="loop",
        module="gaia/controller/initiative.py",
        requires=("proactive.enabled", "proactive.reminders"),
        worker="iniciativa",
        runnable=True,
    ),
    Routine(
        id="initiative:agenda",
        name="Tarefas novas da Agenda",
        skill="agenda",
        description="Le as tarefas pendentes do dia e avisa o que e novo.",
        trigger="loop",
        module="gaia/controller/initiative.py, gaia/agenda/",
        requires=("proactive.enabled", "agenda.enabled"),
        worker="iniciativa",
        runnable=True,
    ),
    Routine(
        id="initiative:estudos",
        name="Cobranca de estudo",
        skill="estudos",
        description="Cobra o simulado quando a meta da semana esta atrasada.",
        trigger="loop",
        module="gaia/controller/initiative.py, gaia/estudos/",
        requires=("proactive.enabled", "estudos.remind_when_late"),
        worker="iniciativa",
        runnable=True,
    ),
    Routine(
        id="initiative:tasks",
        name="Sincronizacao da pagina de tarefas",
        skill="agenda",
        description="Le a pagina de tarefas da Agenda e planeja o que e novo.",
        trigger="loop",
        module="gaia/controller/initiative.py",
        requires=("proactive.enabled", "agenda.tasks_poll_seconds"),
        worker="iniciativa",
        runnable=True,
    ),
    Routine(
        id="initiative:focus",
        name="Foco e fase da lua",
        skill="initiative",
        description="Pergunta pelo foco nas fases principais da lua e quando ele vence.",
        trigger="loop",
        module="gaia/controller/initiative.py, gaia/focus.py",
        # Fora de `proactive.enabled` de proposito: roda no boot do mesmo jeito.
        requires=("focus.enabled",),
        worker="iniciativa",
        runnable=True,
    ),
    Routine(
        id="initiative:idle",
        name="Check-in de ociosidade",
        skill="initiative",
        description="Puxa conversa quando ninguem fala ha um tempo, com limite diario.",
        trigger="loop",
        module="gaia/controller/initiative.py",
        requires=("proactive.enabled", "proactive.idle_checkin"),
        worker="iniciativa",
    ),
    Routine(
        id="memory:previous_day",
        name="Consolidacao do dia anterior",
        skill="memory",
        description="Recupera o dia anterior se a Gaia estava desligada na hora certa.",
        trigger="boot",
        module="gaia/controller/memory.py, gaia/memory/consolidation.py",
        requires=("memory.enabled",),
        worker="memoria",
        runnable=True,
    ),
    Routine(
        id="memory:dream_catch_up",
        name="Sonho atrasado",
        skill="dream",
        description="Sonha de manha o dia que ficou sem sonho.",
        trigger="boot",
        module="gaia/controller/memory.py, gaia/memory/dream.py",
        requires=("memory.enabled", "memory.dream_enabled"),
        worker="memoria",
        runnable=True,
    ),
    Routine(
        id="memory:dream_report",
        name="Aviso do sonho",
        skill="dream",
        description="Conta no dia seguinte que houve sonho e resume a nota.",
        trigger="loop",
        module="gaia/controller/memory.py",
        requires=("memory.enabled", "memory.dream_notify"),
        worker="memoria",
        runnable=True,
    ),
    Routine(
        id="backlog:notify",
        name="Aviso de PR do backlog",
        skill="backlog",
        description="Avisa quando o pi abre o PR de um item (ou quando falha).",
        trigger="event",
        module="gaia/controller/backlog.py",
        requires=("backlog.enabled",),
    ),
    Routine(
        id="audiobook:watch",
        name="Varredura de audiobooks",
        skill="audiobook",
        description="Confere a pasta de entrada e narra o que chegou, de tempos em tempos.",
        trigger="watch",
        module="gaia/telegram/service.py, gaia/audiobook/",
        requires=("audiobook.enabled", "audiobook.watch_minutes"),
    ),
)


def find(routine_id: str) -> Routine | None:
    for item in ROUTINES:
        if item.id == routine_id:
            return item
    return None


def config_value(cfg: Config, dotted: str) -> Any:
    """Le ``"a.b.c"`` da config; ``None`` quando o caminho nao existe."""
    atual: Any = cfg
    for parte in dotted.split("."):
        atual = getattr(atual, parte, None)
        if atual is None:
            return None
    return atual


def missing_requirements(cfg: Config, item: Routine) -> list[str]:
    """Gates do config que estao desligados (vazio = tudo ligado)."""
    return [
        caminho
        for caminho in item.requires
        if not bool(config_value(cfg, caminho))
    ]


def config_enabled(cfg: Config, item: Routine) -> bool:
    return not missing_requirements(cfg, item)


def enabled_map(store: MemoryStore | None) -> dict[str, bool]:
    """Override da tabela, uma leitura por ciclo (nao uma query por rotina)."""
    if store is None:
        return {}
    return {
        str(linha["id"]): bool(linha["enabled"])
        for linha in store.list_routines()
    }


def is_enabled(
    store: MemoryStore | None,
    routine_id: str,
    overrides: dict[str, bool] | None = None,
) -> bool:
    """Decisao da pessoa sobre a rotina (ausente = ligada)."""
    mapa = overrides if overrides is not None else enabled_map(store)
    return mapa.get(routine_id, True)


def status(
    item: Routine,
    cfg: Config,
    store: MemoryStore | None,
    *,
    overrides: dict[str, bool] | None = None,
    workers: dict[str, bool] | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Devolve ``(status, motivo)``: active | paused | disabled | error."""
    faltando = missing_requirements(cfg, item)
    if faltando:
        return "disabled", "desligada no config: " + ", ".join(faltando)
    if not is_enabled(store, item.id, overrides):
        return "paused", "pausada no painel"
    if item.worker and workers is not None and not workers.get(item.worker, True):
        return "error", f"worker parado: {item.worker}"
    ultima = store.last_run(item.skill, item.id) if store is not None else None
    if ultima is not None and str(ultima.get("status")) == "failed":
        return "error", str(ultima.get("error") or "ultima execucao falhou")
    return "active", ""


def render(
    item: Routine,
    cfg: Config,
    store: MemoryStore | None = None,
    now: datetime | None = None,
    *,
    overrides: dict[str, bool] | None = None,
    workers: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Uma rotina pronta para o painel (mesmo espirito de ``automations.render``)."""
    estado, motivo = status(
        item, cfg, store, overrides=overrides, workers=workers, now=now
    )
    config_ok = config_enabled(cfg, item)
    pausada = not is_enabled(store, item.id, overrides)
    ultima = store.last_run(item.skill, item.id) if store is not None else None
    return {
        "id": item.id,
        "kind": "routine",
        "name": item.name,
        "skill": item.skill,
        "description": item.description,
        "trigger": item.trigger,
        "trigger_label": TRIGGER_LABELS.get(item.trigger, item.trigger),
        "module": item.module,
        "requires": list(item.requires),
        "runnable": item.runnable,
        # Duas coisas diferentes, como nas automacoes: `paused` e a decisao da
        # pessoa, `enabled` e o resultado final (pausada OU config desligada).
        "paused": pausada,
        "config_enabled": config_ok,
        "enabled": config_ok and not pausada,
        "status": estado,
        "reason": motivo,
        "last_run": _last_run_payload(ultima),
    }


def _last_run_payload(linha: dict[str, Any] | None) -> dict[str, Any] | None:
    if linha is None:
        return None
    return {
        "id": int(linha.get("id") or 0),
        "status": str(linha.get("status") or ""),
        "trigger": str(linha.get("trigger") or ""),
        "started_at": str(linha.get("started_at") or ""),
        "duration_ms": int(linha.get("duration_ms") or 0),
        "detail": str(linha.get("detail") or ""),
        "error": str(linha.get("error") or ""),
        "error_kind": str(linha.get("error_kind") or ""),
    }


def describe_routines(
    cfg: Config,
    store: MemoryStore | None = None,
    now: datetime | None = None,
    *,
    workers: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """Lista pronta para o painel (ordem do registry, nao a da tabela)."""
    momento = now or datetime.now().astimezone()
    overrides = enabled_map(store)
    return [
        render(
            item,
            cfg,
            store,
            momento,
            overrides=overrides,
            workers=workers,
        )
        for item in ROUTINES
    ]


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Corpo de ``POST /automations/set`` para uma rotina (so ``enabled``)."""
    if not isinstance(payload, dict):
        raise ValueError("corpo deve ser um objeto JSON")
    if "enabled" not in payload:
        raise ValueError("rotina aceita apenas enabled (nao tem horario)")
    for chave in payload:
        if chave != "enabled":
            raise ValueError(
                f"rotina nao tem horario: campo desconhecido '{chave}' "
                "(use enabled)"
            )
    valor = payload["enabled"]
    if not isinstance(valor, bool):
        raise ValueError("enabled deve ser true ou false")
    return {"enabled": valor}


def update(
    store: MemoryStore,
    cfg: Config,
    routine_id: str,
    payload: dict[str, Any],
    *,
    workers: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Valida e grava a pausa/retomada; devolve a rotina renderizada.

    ``KeyError`` para id desconhecido, ``ValueError`` para payload invalido —
    o transporte traduz em 404/422, igual ao caminho das automacoes.
    """
    item = find(routine_id)
    if item is None:
        raise KeyError(routine_id)
    campos = validate_payload(payload)
    store.set_routine(routine_id, enabled=campos["enabled"])
    return render(item, cfg, store, workers=workers)


def summarize(
    cfg: Config, store: MemoryStore | None, workers: dict[str, bool] | None = None
) -> dict[str, Any]:
    """Contagem para o Overview/cabecalho da tela."""
    itens = describe_routines(cfg, store, workers=workers)
    return {
        "total": len(itens),
        "active": sum(1 for item in itens if item["status"] == "active"),
        "paused": [item["id"] for item in itens if item["status"] == "paused"],
        "disabled": [item["id"] for item in itens if item["status"] == "disabled"],
        "error": [item["id"] for item in itens if item["status"] == "error"],
        "items": itens,
    }


__all__ = [
    "ROUTINES",
    "TRIGGER_LABELS",
    "Routine",
    "config_enabled",
    "config_value",
    "describe_routines",
    "enabled_map",
    "find",
    "is_enabled",
    "missing_requirements",
    "render",
    "status",
    "summarize",
    "update",
    "validate_payload",
]
