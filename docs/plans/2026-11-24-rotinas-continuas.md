# Rotinas contínuas no painel — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** tornar visíveis, pausáveis e executáveis no painel as 12 rotinas que
rodam **sem linha na tabela `automations`** (gatilho de boot, laço, evento ou
varredura), com um registry declarativo que as futuras usem de graça.

**Architecture:** um registry estático (`gaia/control/routines.py`) descreve
cada rotina (id, gatilho, gates de config, módulo, se é executável à mão). O
estado da pessoa mora na tabela `routines` (schema v9), que é **tabela de
override**: linha ausente = ligada, então não existe seed nem sincronização com
o registry. O `Orchestrator` ganha `RoutineMixin` com um ponto único de gate
(`_routine_on`); os laços existentes (`_proactive_loop`, `_memory_loop`,
`_audiobook_watcher`, `_backlog_notify`) passam a consultar esse gate. O painel
mostra as duas famílias no **mesmo** `GET /automations` e escreve pelas **mesmas**
rotas `/automations/set` e `/automations/run`, com despacho por id (os
namespaces são disjuntos: `briefing:*`/`career:*`/`memory:daily`/`memory:dream`
são automações; `initiative:*`/`memory:previous_day`/`memory:dream_catch_up`/
`memory:dream_report`/`backlog:*`/`audiobook:*` são rotinas).

**Tech Stack:** Python 3.12, pydantic, SQLite (WAL), asyncio, HTTP handler
caseiro do `gaia serve`, JS sem build (`index.html`).

---

## Contexto para quem nunca viu este repo

- O motor é o `Orchestrator` (`gaia/controller/orchestrator.py`), composto por
  mixins que herdam de `OrchestratorContract` (`gaia/controller/contract.py`).
  **Todo método novo de mixin precisa ser declarado no contrato** (`@abstractmethod`),
  senão o mypy acusa e `test_mixins_share_the_explicit_contract` quebra.
- Automações com horário vivem em `gaia/control/automations.py` + tabela
  `automations` (schema v7). Quem as dispara é `_scheduler_tick`
  (`gaia/controller/scheduler.py`).
- O painel de administração é `/control` (token `GAIA_CONTROL_TOKEN`); a **tela
  de automações do chat** (`#automacoes` em `gaia/serve/static/index.html`) usa
  o token do chat (`GAIA_SERVE_TOKEN`) e as rotas `/automations*`.
- Comentários e mensagens em português sem acento ("sessao", "execucao").
- Verificação obrigatória antes de qualquer commit:
  `.venv/bin/python -m pytest -q && .venv/bin/ruff check gaia tests && .venv/bin/mypy`

## O que é "rotina contínua" (e o que NÃO é)

| | `Automation` | `Routine` |
|---|---|---|
| driver | janela `at`/`until`/`days` | gate de config + laço que já existe |
| estado | `scheduled/running/completed/missed/failed/paused/disabled` | `active/paused/disabled/error` |
| reserva | `claim_meta` por janela | não tem (não há janela) |
| retry | backoff por `error_kind` | não tem (o laço tenta de novo no próximo ciclo) |
| run | todo disparo grava `runs` | **só o disparo manual** grava `runs` (senão inunda a tabela) |
| tabela | `automations` (agenda completa) | `routines` (só `id` + `enabled` + timestamps) |

As 12 rotinas mapeadas (ids estáveis, viram contrato de teste):

| id | onde vive | gatilho | gates (`requires`) | roda à mão? |
|---|---|---|---|---|
| `initiative:greeting` | `start_services` | boot | `proactive.enabled`, `proactive.startup_greeting` | sim |
| `initiative:reminders` | `_proactive_loop` | laço | `proactive.enabled`, `proactive.reminders` | sim |
| `initiative:agenda` | `_proactive_loop` | laço | `proactive.enabled`, `agenda.enabled` | sim |
| `initiative:estudos` | `_proactive_loop` | laço | `proactive.enabled`, `estudos.remind_when_late` | sim |
| `initiative:tasks` | `_proactive_loop` | laço | `proactive.enabled`, `agenda.tasks_poll_seconds` | sim |
| `initiative:focus` | laço **e** boot | laço+boot | `proactive.enabled`? não — `focus.enabled` | sim |
| `initiative:idle` | `_proactive_loop` | laço | `proactive.enabled`, `proactive.idle_checkin` | **não** |
| `memory:previous_day` | `_memory_loop` (boot) | boot | `memory.enabled` | sim |
| `memory:dream_catch_up` | `_memory_loop` (boot) | boot | `memory.enabled`, `memory.dream_enabled` | sim |
| `memory:dream_report` | `_memory_loop` (60 s) | laço | `memory.enabled`, `memory.dream_notify` | sim |
| `backlog:notify` | `_backlog_notify` | evento | `backlog.enabled` | **não** |
| `audiobook:watch` | `TelegramService` | varredura | `audiobook.enabled`, `audiobook.watch_minutes` | **não** |

`initiative:focus` fica fora de `proactive.enabled` de propósito: hoje
`_check_focus` roda no boot mesmo com a proatividade desligada
(`orchestrator.py::start_services`), e o plano **não muda comportamento** — só
torna o gate explícito e governável.

As 3 não-executáveis à mão não têm handler que faça sentido fora do seu
contexto: `initiative:idle` precisa do gerador de mensagem e do contador diário,
`backlog:notify` é callback do worker (recebe `status`/`item`/`resultado`),
`audiobook:watch` é uma varredura de disco com notificação no Telegram. Elas
**têm** checkbox (o pedido), só não têm "rodar agora".

---

### Task 1: Tabela `routines` (schema v9)

**Files:**
- Modify: `gaia/memory/store.py` (constante `SCHEMA_VERSION`, `_init_schema`,
  métodos novos ao lado dos de `automations`)
- Test: `tests/test_memory.py`

**Step 1: escreva o teste que falha**

Em `tests/test_memory.py`, acrescente no fim:

```python
def test_schema_v9_tem_a_tabela_routines(tmp_path):
    """Rotinas continuas: tabela de override (linha ausente = ligada)."""
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.schema_version() >= 9
        assert store.list_routines() == []
        # Sem linha = ligada (o registry e o padrao; a tabela so guarda excecao).
        assert store.routine_enabled("initiative:agenda") is True

        store.set_routine("initiative:agenda", enabled=False)
        assert store.routine_enabled("initiative:agenda") is False
        linha = store.get_routine("initiative:agenda")
        assert linha is not None and linha["enabled"] == 0

        # Idempotente: pausar duas vezes nao duplica nem re-cria.
        store.set_routine("initiative:agenda", enabled=False)
        assert len(store.list_routines()) == 1

        store.set_routine("initiative:agenda", enabled=True)
        assert store.routine_enabled("initiative:agenda") is True
    finally:
        store.close()
```

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_memory.py::test_schema_v9_tem_a_tabela_routines -q`
Expected: FAIL — `AttributeError: 'MemoryStore' object has no attribute 'list_routines'`

**Step 3: implemente**

Em `gaia/memory/store.py`:

1. `SCHEMA_VERSION = 9`.
2. No `executescript` de `_init_schema`, depois do bloco `backlog_items`:

```sql
                -- v9: rotinas continuas (boot/loop/evento/varredura). Diferente
                -- de `automations`, aqui NAO ha agenda: a tabela e so o override
                -- da decisao da pessoa (`enabled`). Linha ausente = ligada, entao
                -- nao existe seed nem sincronizacao com o registry.
                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
```

3. Ao lado de `update_automation`, os quatro métodos:

```python
    # ---------------------------------------------------------------- routines
    def list_routines(self) -> list[dict[str, Any]]:
        """Overrides de rotina continua, em ordem estavel de ``id``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routines ORDER BY id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_routine(self, routine_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM routines WHERE id = ?", (routine_id,)
            ).fetchone()
        return dict(row) if row else None

    def routine_enabled(self, routine_id: str) -> bool:
        """Ligada? Sem linha o padrao e ligada (a tabela so guarda excecao)."""
        linha = self.get_routine(routine_id)
        return True if linha is None else bool(linha["enabled"])

    def set_routine(self, routine_id: str, *, enabled: bool) -> None:
        """Grava a decisao da pessoa (upsert: uma linha por rotina)."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO routines (id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (routine_id, int(bool(enabled)), _now()),
            )
```

**Step 4: rode e veja passar**

Run: `.venv/bin/python -m pytest tests/test_memory.py -q`
Expected: PASS (todos)

**Step 5: commit**

```bash
git add gaia/memory/store.py tests/test_memory.py
git commit -m "feat(memory): schema v9 com a tabela routines (override das rotinas continuas)"
```

---

### Task 2: Registry de rotinas (`gaia/control/routines.py`)

**Files:**
- Create: `gaia/control/routines.py`
- Test: `tests/test_control_routines.py` (novo)

**Step 1: escreva o teste que falha**

`tests/test_control_routines.py`:

```python
"""Registry das rotinas continuas: descritor, override e render para o painel."""

from __future__ import annotations

from datetime import datetime

import pytest

from gaia.config import Config
from gaia.control import routines as routines_mod
from gaia.memory.store import MemoryStore

NOW = datetime(2026, 11, 24, 10, 0).astimezone()


@pytest.fixture
def cfg(tmp_path, isolated_env):
    return Config.load(
        config_path=tmp_path / "nao-existe.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )


@pytest.fixture
def store(tmp_path):
    banco = MemoryStore(tmp_path / "gaia.db")
    yield banco
    banco.close()


def test_registry_tem_as_rotinas_documentadas():
    """Os ids sao contrato: tela, testes e AGENTS.md falam deles."""
    ids = {item.id for item in routines_mod.ROUTINES}
    assert ids == {
        "initiative:greeting",
        "initiative:reminders",
        "initiative:agenda",
        "initiative:estudos",
        "initiative:tasks",
        "initiative:focus",
        "initiative:idle",
        "memory:previous_day",
        "memory:dream_catch_up",
        "memory:dream_report",
        "backlog:notify",
        "audiobook:watch",
    }


def test_sem_linha_na_tabela_a_rotina_esta_ligada(cfg, store):
    item = routines_mod.render(
        routines_mod.find("initiative:agenda"), cfg, store, NOW
    )
    assert item["enabled"] is True
    assert item["paused"] is False
    assert item["status"] == "active"


def test_pausar_pelo_painel_muda_o_status(cfg, store):
    store.set_routine("initiative:agenda", enabled=False)
    item = routines_mod.render(
        routines_mod.find("initiative:agenda"), cfg, store, NOW
    )
    assert item["paused"] is True
    assert item["enabled"] is False
    assert item["status"] == "paused"


def test_gate_do_config_vence_o_checkbox(cfg, store):
    """`paused` e a decisao da pessoa; `disabled` e o TOML desligando a skill."""
    cfg.proactive.enabled = False
    item = routines_mod.render(
        routines_mod.find("initiative:agenda"), cfg, store, NOW
    )
    assert item["paused"] is False
    assert item["config_enabled"] is False
    assert item["enabled"] is False
    assert item["status"] == "disabled"
    assert "proactive.enabled" in item["reason"]


def test_gate_numerico_zero_desliga(cfg, store):
    """`agenda.tasks_poll_seconds == 0` e o jeito de desligar a pagina de tarefas."""
    cfg.agenda.tasks_poll_seconds = 0.0
    item = routines_mod.render(
        routines_mod.find("initiative:tasks"), cfg, store, NOW
    )
    assert item["status"] == "disabled"

    cfg.agenda.tasks_poll_seconds = 60.0
    item = routines_mod.render(
        routines_mod.find("initiative:tasks"), cfg, store, NOW
    )
    assert item["status"] == "active"


def test_so_as_rotinas_com_handler_rodam_a_mao():
    podem = {item.id for item in routines_mod.ROUTINES if item.runnable}
    assert podem == {
        "initiative:greeting",
        "initiative:reminders",
        "initiative:agenda",
        "initiative:estudos",
        "initiative:tasks",
        "initiative:focus",
        "memory:previous_day",
        "memory:dream_catch_up",
        "memory:dream_report",
    }


def test_worker_morto_vira_erro(cfg, store):
    """Rotina cujo worker nao esta vivo e falha silenciosa: o painel acusa."""
    item = routines_mod.render(
        routines_mod.find("initiative:agenda"), cfg, store, NOW, workers={"iniciativa": False}
    )
    assert item["status"] == "error"
    assert "worker" in item["reason"]


def test_update_valida_e_grava(cfg, store):
    resultado = routines_mod.update(store, cfg, "initiative:agenda", {"enabled": False})
    assert resultado["paused"] is True
    assert store.routine_enabled("initiative:agenda") is False

    with pytest.raises(KeyError):
        routines_mod.update(store, cfg, "nao:existe", {"enabled": False})
    with pytest.raises(ValueError):
        routines_mod.update(store, cfg, "initiative:agenda", {"enabled": "sim"})
    with pytest.raises(ValueError):
        routines_mod.update(store, cfg, "initiative:agenda", {"at": "09:00"})


def test_describe_routines_tem_12_itens_e_ordem_do_registry(cfg, store):
    lista = routines_mod.describe_routines(cfg, store, NOW)
    assert [item["id"] for item in lista] == [item.id for item in routines_mod.ROUTINES]
    assert all(item["kind"] == "routine" for item in lista)
    assert all(item["trigger"] in {"boot", "loop", "event", "watch"} for item in lista)
```

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_control_routines.py -q`
Expected: FAIL — `ModuleNotFoundError: gaia.control.routines`

**Step 3: implemente `gaia/control/routines.py`**

```python
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
    store: MemoryStore | None, routine_id: str, overrides: dict[str, bool] | None = None
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
```

**Step 4: rode e veja passar**

Run: `.venv/bin/python -m pytest tests/test_control_routines.py -q`
Expected: PASS (9 testes)

**Step 5: commit**

```bash
git add gaia/control/routines.py tests/test_control_routines.py
git commit -m "feat(control): registry declarativo das rotinas continuas"
```

---

### Task 3: `RoutineMixin` e o gate único no motor

**Files:**
- Create: `gaia/controller/routines.py`
- Modify: `gaia/controller/orchestrator.py` (import, herança, `_reset_routine_clock`)
- Modify: `gaia/controller/contract.py` (declarar os métodos novos)
- Modify: `gaia/controller/initiative.py` (`_proactive_tick`, gates)
- Modify: `gaia/controller/memory.py` (gates no boot e no laço)
- Modify: `gaia/controller/backlog.py` (gate do aviso)
- Test: `tests/test_routines_gate.py` (novo)

**Step 1: escreva o teste que falha**

`tests/test_routines_gate.py`:

```python
"""O gate das rotinas continuas: pausada nao roda, config vence, rodar a mao grava."""

from __future__ import annotations

import logging
import types
from datetime import datetime

import pytest

from gaia.config import Config
from gaia.controller.initiative import InitiativeMixin
from gaia.controller.memory import MemoryMixin
from gaia.controller.routines import RoutineMixin
from gaia.memory.store import MemoryStore

NOW = datetime(2026, 11, 24, 10, 0).astimezone()


class _Gateway(RoutineMixin):
    """Mixin isolado: o gate nao depende do Orchestrator inteiro."""

    def __init__(self, store: MemoryStore, cfg: Config) -> None:
        self.store = store
        self.cfg = cfg
        self.logger = logging.getLogger("test.routines")
        self.chamadas: list[str] = []
        self._agenda_next_check = 0.0
        self._estudos_next_check = 0.0
        self._tasks_next_check = 0.0

    async def _check_agenda(self, now):
        self.chamadas.append("agenda")

    async def _check_estudos(self, now):
        self.chamadas.append("estudos")

    async def _check_task_page(self, now):
        self.chamadas.append("tasks")

    async def _check_focus(self, now):
        self.chamadas.append("focus")


def test_rotina_pausada_nao_roda(store, cfg):
    store.set_routine("initiative:agenda", enabled=False)
    gateway = _Gateway(store, cfg)
    assert gateway._routine_on("initiative:agenda") is False
    assert gateway._routine_on("initiative:estudos") is True


def test_rotina_sem_linha_roda(store, cfg):
    gateway = _Gateway(store, cfg)
    assert gateway._routine_on("initiative:agenda") is True


def test_config_desligada_nao_roda(store, cfg):
    cfg.agenda.enabled = False
    gateway = _Gateway(store, cfg)
    assert gateway._routine_on("initiative:agenda") is False


def test_ids_desconhecidos_nao_explodem(store, cfg):
    """Rotina removida do registry num deploy nao pode derrubar o laco."""
    gateway = _Gateway(store, cfg)
    assert gateway._routine_on("initiative:que-nao-existe") is True


@pytest.mark.asyncio
async def test_tick_do_laco_proativo_respeita_a_pausa(store, cfg):
    store.set_routine("initiative:agenda", enabled=False)
    gateway = _Gateway(store, cfg)
    await gateway._run_routine("initiative:agenda", gateway._check_agenda, NOW)
    assert gateway.chamadas == []

    await gateway._run_routine("initiative:estudos", gateway._check_estudos, NOW)
    assert gateway.chamadas == ["estudos"]


@pytest.mark.asyncio
async def test_rodar_a_mao_grava_run_e_ignora_a_pausa(store, cfg):
    """Rodar a mao e pedido explicito: vale mesmo com a rotina pausada."""
    store.set_routine("initiative:agenda", enabled=False)
    gateway = _Gateway(store, cfg)
    ok = await gateway.run_routine_now("initiative:agenda")
    assert ok is True
    assert gateway.chamadas == ["agenda"]
    ultima = store.last_run("agenda", "initiative:agenda")
    assert ultima is not None
    assert ultima["trigger"] == "manual"
    assert ultima["status"] == "completed"


@pytest.mark.asyncio
async def test_rodar_rotina_sem_handler_e_recusado(store, cfg):
    gateway = _Gateway(store, cfg)
    with pytest.raises(KeyError):
        await gateway.run_routine_now("initiative:idle")


@pytest.mark.asyncio
async def test_rodar_id_desconhecido_e_keyerror(store, cfg):
    gateway = _Gateway(store, cfg)
    with pytest.raises(KeyError):
        await gateway.run_routine_now("nao:existe")
```

E os fixtures, em `tests/conftest.py` (para os dois arquivos novos usarem):

```python
@pytest.fixture
def store(tmp_path):
    from gaia.memory.store import MemoryStore

    banco = MemoryStore(tmp_path / "gaia.db")
    yield banco
    banco.close()
```

> O `_Stub` liga os helpers do mixin na classe
> (`_routine_on = RoutineMixin._routine_on`, `_run_routine = ...`, etc.). É o
> padrão do repo para mixin não-ligado (`tests/test_initiative.py` faz igual com
> `InitiativeMixin._check_agenda`); chamar `RoutineMixin.run_routine_now(stub, ...)`
> sem isso não encontra `self._routine_handler`.

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_routines_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: gaia.controller.routines`

**Step 3: implemente `gaia/controller/routines.py`**

```python
"""Gate e disparo das rotinas continuas (o que roda sem horario declarado).

Duas responsabilidades so:

* ``_routine_on`` — o ponto unico que responde "esta rotina deve rodar agora?".
  Cruza a pausa da pessoa (tabela ``routines``) com os gates de config do
  registry (``gaia/control/routines.py``). Sem ele cada laco repetiria o mesmo
  ``if`` e um deles ficaria de fora — foi assim que a fase C nasceu.
* ``run_routine_now`` — "rodar agora" da tela, com ``trigger=manual`` e run
  registrado (o disparo automatico **nao** grava run: o laco roda a cada 30 s e
  inundaria a tabela).

Os laços continuam nos mixins donos (``InitiativeMixin``, ``MemoryMixin``): o
que muda aqui e que a chamada passa por ``_run_routine``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from gaia.control import routines as routines_mod
from gaia.control import runs as runs_mod
from gaia.controller.contract import OrchestratorContract

# id da rotina -> metodo que executa o corpo (so os `runnable` do registry).
_ROUTINE_HANDLERS: dict[str, str] = {
    "initiative:greeting": "_startup_greeting",
    "initiative:reminders": "_fire_due_reminders",
    "initiative:agenda": "_check_agenda",
    "initiative:estudos": "_check_estudos",
    "initiative:tasks": "_check_task_page",
    "initiative:focus": "_check_focus",
    "memory:previous_day": "_consolidate_previous_day",
    "memory:dream_catch_up": "_dream_catch_up",
    "memory:dream_report": "_maybe_dream_report",
}

# Relogios internos que o disparo manual precisa zerar: sem isso "rodar agora"
# nao faz nada quando o throttle do laco acabou de rodar (mesma ideia do
# ``_reset_attempt_clock`` das automacoes).
_ROUTINE_CLOCKS: dict[str, tuple[str, ...]] = {
    "initiative:agenda": ("_agenda_next_check",),
    "initiative:estudos": ("_estudos_next_check",),
    "initiative:tasks": ("_tasks_next_check",),
    "memory:dream_report": ("_dream_report_last_attempt",),
}


class RoutineMixin(OrchestratorContract):
    # ------------------------------------------------------------------ gate
    def _routine_on(self, routine_id: str, overrides: dict[str, bool] | None = None) -> bool:
        """A rotina deve rodar? (pausa do painel + gates de config)

        Id desconhecido roda: uma rotina removida do registry num deploy nao
        pode congelar em silencio um laco que ainda a chama.
        """
        item = routines_mod.find(routine_id)
        if item is None:
            return True
        if not routines_mod.config_enabled(self.cfg, item):
            return False
        return routines_mod.is_enabled(self.store, routine_id, overrides)

    def _routine_overrides(self) -> dict[str, bool]:
        """Leitura unica por ciclo do laco (nao uma query por rotina)."""
        return routines_mod.enabled_map(self.store)

    async def _run_routine(
        self,
        routine_id: str,
        handler: Callable[..., Awaitable[Any]],
        now: datetime,
        *,
        overrides: dict[str, bool] | None = None,
    ) -> None:
        """Executa a rotina se o gate permitir (disparo automatico, sem run)."""
        if not self._routine_on(routine_id, overrides):
            return
        await self._call_routine_handler(handler, now)

    async def _call_routine_handler(self, handler: Any, now: datetime) -> None:
        """Chama o handler com ``now`` so quando ele aceita.

        Metade das rotinas recebe o instante (``_check_agenda(now)``) e metade
        nao (``_fire_due_reminders()``, ``_consolidate_previous_day()``).
        Uniformizar as assinaturas quebraria os testes e os chamadores que ja
        existem; a inspecao resolve sem inventar ``lambda`` para cada uma (e sem
        perder o rastro do handler no "rodar agora").
        """
        parametros = inspect.signature(handler).parameters
        if parametros:
            await handler(now)
        else:
            await handler()

    # --------------------------------------------------------- disparo manual
    def _routine_handler(self, routine_id: str):
        nome = _ROUTINE_HANDLERS.get(routine_id)
        if nome is None:
            return None
        return getattr(self, nome, None)

    def _reset_routine_clock(self, routine_id: str) -> None:
        for atributo in _ROUTINE_CLOCKS.get(routine_id, ()):
            setattr(self, atributo, 0.0)

    async def run_routine_now(self, routine_id: str) -> dict[str, Any]:
        """Roda a rotina agora (``trigger=manual``) e devolve o run registrado.

        Levanta ``KeyError`` para id desconhecido ou rotina sem handler (o
        transporte traduz em 404) e ``RuntimeError`` quando o motor esta num
        turno (409). A pausa do painel **nao** bloqueia: o clique e explicito.
        """
        item = routines_mod.find(routine_id)
        handler = self._routine_handler(routine_id)
        if item is None or handler is None:
            raise KeyError(routine_id)
        if self._busy or self._recording or self._listening:
            raise RuntimeError("motor ocupado; tente quando o turno terminar")
        self._reset_routine_clock(routine_id)
        now = datetime.now().astimezone()
        async with runs_mod.record_run(
            self.store,
            kind=item.skill,
            automation=item.id,
            trigger="manual",
            delivery="local",
        ) as run:
            await self._call_routine_handler(handler, now)
            run.detail = f"{item.name} executada a mao"
        return {
            "id": item.id,
            "trigger": "manual",
            "status": run.status,
            "detail": run.detail,
            "error": run.error,
            "error_kind": run.error_kind,
            "attempt": run.attempt,
        }


__all__ = ["RoutineMixin"]
```

Em `gaia/controller/contract.py`, declare os métodos novos (o mypy exige):

```python
    # --- rotinas continuas (mixin proprio, chamado pelos lacos) ---
    @abstractmethod
    def _routine_on(
        self, routine_id: str, overrides: dict[str, bool] | None = None
    ) -> bool:
        ...

    @abstractmethod
    def _routine_overrides(self) -> dict[str, bool]:
        ...

    @abstractmethod
    async def _run_routine(
        self,
        routine_id: str,
        handler: Callable[..., Awaitable[Any]],
        now: datetime,
        *,
        overrides: dict[str, bool] | None = None,
    ) -> None:
        ...

    @abstractmethod
    async def _call_routine_handler(self, handler: Any, now: datetime) -> None:
        ...

    @abstractmethod
    async def run_routine_now(self, routine_id: str) -> dict[str, Any]:
        ...
```

(e acrescente `from collections.abc import Awaitable, Callable` no topo do
contrato; `datetime` e `Any` já estão lá.)

**Step 4: ligue o gate nos laços existentes**

`gaia/controller/initiative.py` — extraia o tick e passe cada chamada pelo
`_run_routine`:

```python
    async def _proactive_loop(self, greet: bool = True) -> None:
        cfg = self.cfg.proactive
        if not cfg.enabled:
            return
        await asyncio.sleep(1.0)
        if greet and cfg.startup_greeting:
            await self._startup_greeting()
        while True:
            await asyncio.sleep(max(5.0, cfg.check_interval_seconds))
            if not self.cfg.proactive.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            now = datetime.now().astimezone()
            if in_quiet_hours(now, cfg.quiet_hours):
                continue
            try:
                await self._proactive_tick(now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha na rotina proativa: %s", exc, exc_info=True)

    async def _proactive_tick(self, now: datetime) -> None:
        """Um ciclo da iniciativa: cada rotina passa pelo gate do painel.

        Separado do laco para o gate ser testavel sem ``sleep`` (e para a tela
        poder listar exatamente o que roda aqui).
        """
        cfg = self.cfg.proactive
        overrides = self._routine_overrides()
        if cfg.reminders:
            await self._run_routine(
                "initiative:reminders", self._fire_due_reminders,
                now, overrides=overrides,
            )
        await self._run_routine(
            "initiative:agenda", self._check_agenda, now, overrides=overrides
        )
        await self._run_routine(
            "initiative:estudos", self._check_estudos, now, overrides=overrides
        )
        await self._run_routine(
            "initiative:tasks", self._check_task_page, now, overrides=overrides
        )
        await self._run_routine(
            "initiative:focus", self._check_focus, now, overrides=overrides
        )
        if cfg.idle_checkin:
            await self._run_routine(
                "initiative:idle", lambda _now: self._idle_checkin(now),
                now, overrides=overrides,
            )

    async def _idle_checkin(self, now: datetime) -> None:
        """Check-in de ociosidade (o corpo que estava inline no laco)."""
        idle_for = (time.monotonic() - self._last_activity) / 60.0
        if idle_for >= self.cfg.proactive.idle_minutes and self._can_idle_checkin(now):
            await self._say_proactive(await self._generate_initiative_message())
            self._last_activity = time.monotonic()
```

> **Atenção:** `_run_routine` aceita handler de qualquer assinatura
> (`_call_routine_handler` inspeciona os parâmetros). Não mude a assinatura de
> `_fire_due_reminders`/`_consolidate_previous_day` para `(now)`: outros testes
> e chamadores já os usam sem argumento.

`gaia/controller/memory.py` — gate no boot e no laço:

```python
    async def _memory_loop(self) -> None:
        try:
            if self._routine_on("memory:previous_day"):
                await self._consolidate_previous_day()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("consolidação do dia anterior falhou: %s", exc)
        try:
            if self._routine_on("memory:dream_catch_up"):
                await self._dream_catch_up()
        except Exception as exc:  # pragma: no cover - depende do provedor
            self.logger.warning("sonho atrasado falhou: %s", exc)
        while True:
            await asyncio.sleep(60.0)
            if not self.cfg.memory.enabled:
                continue
            if self._busy or self._recording or self._listening:
                continue
            try:
                now = datetime.now().astimezone()
                # Consolidacao, sonho e briefings saem pelo agendador unico;
                # aqui so o aviso do sonho (sem horario declarado na tabela).
                await self._run_routine(
                    "memory:dream_report", self._maybe_dream_report, now
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - depende do provedor
                self.logger.warning("falha na memória diária: %s", exc)
```

`gaia/controller/orchestrator.py` — gate da saudação e do foco no boot:

```python
        if self.cfg.proactive.enabled and self.cfg.proactive.startup_greeting:
            if self._routine_on("initiative:greeting"):
                try:
                    await self._startup_greeting()
                except Exception as exc:  # pragma: no cover
                    self.logger.warning(
                        "falha na saudação inicial: %s", exc, exc_info=True
                    )
```

```python
        if self._routine_on("initiative:focus"):
            try:
                await self._check_focus(datetime.now().astimezone())
            except Exception as exc:  # pragma: no cover
                self.logger.warning("falha ao checar foco: %s", exc, exc_info=True)
```

E na declaração da classe, adicione o mixin **antes** de `InitiativeMixin`
(quem consome o gate vem depois de quem o define):

```python
from gaia.controller.routines import RoutineMixin
...
class Orchestrator(
    SpeechMixin,
    TurnMixin,
    VoiceMixin,
    CommandsMixin,
    MemoryMixin,
    BriefingMixin,
    CareerMixin,
    RegistrationMixin,
    RoutineMixin,
    InitiativeMixin,
    SchedulerMixin,
    BacklogMixin,
):
```

`gaia/controller/backlog.py` — gate do aviso:

```python
    async def _backlog_notify(
        self, status: str, item: dict[str, Any], resultado: BacklogResult
    ) -> None:
        """Avisa quando o PR fica pronto (ou quando o item falha)."""
        if not self._routine_on("backlog:notify"):
            logger.debug("backlog: aviso desligado no painel")
            return
        titulo = str(item.get("title") or "item do backlog")
        ...
```

**Step 5: rode os testes**

Run: `.venv/bin/python -m pytest tests/test_routines_gate.py tests/test_orchestrator.py tests/test_initiative.py tests/test_estudos.py tests/test_backlog.py -q`
Expected: PASS em todos. Se `test_mixins_share_the_explicit_contract` falhar,
inclua `RoutineMixin` na tupla do teste (ele é mixin do contrato).

**Step 6: commit**

```bash
git add gaia/controller/routines.py gaia/controller/contract.py \
        gaia/controller/orchestrator.py gaia/controller/initiative.py \
        gaia/controller/memory.py gaia/controller/backlog.py \
        tests/test_routines_gate.py tests/test_orchestrator.py tests/conftest.py
git commit -m "feat(controller): gate unico das rotinas continuas nos lacos do motor"
```

---

### Task 4: Gate na varredura de audiobooks

**Files:**
- Modify: `gaia/telegram/service.py` (`_audiobook_watcher`)
- Test: `tests/test_audiobook.py` (novo caso) ou `tests/test_telegram.py`

**Step 1: escreva o teste que falha**

Em `tests/test_telegram.py` (perto dos testes de audiobook, se houver; senão
no fim do arquivo):

```python
@pytest.mark.asyncio
async def test_varredura_de_audiobook_respeita_a_pausa(monkeypatch, tmp_path):
    """A rotina `audiobook:watch` pode ser desligada no painel."""
    chamadas: list[str] = []

    async def fake_process(*args, **kwargs):
        chamadas.append("varreu")
        return types.SimpleNamespace(empty=True, summary=lambda: "")

    monkeypatch.setattr(
        "gaia.audiobook.service.process_inbox", fake_process
    )
    servicio = _servico_de_audiobook(tmp_path)  # helper do arquivo
    servicio.orchestrator.store.set_routine("audiobook:watch", enabled=False)
    await servicio._audiobook_scan_once()
    assert chamadas == []

    servicio.orchestrator.store.set_routine("audiobook:watch", enabled=True)
    await servicio._audiobook_scan_once()
    assert chamadas == ["varreu"]
```

> Se `tests/test_telegram.py` não tiver um helper para montar o
> `TelegramService`, monte um mínimo no próprio teste: `TelegramService` só usa
> `self.cfg`, `self.orchestrator`, `self.client`, `self.logger` em
> `_audiobook_scan_once`. Um `types.SimpleNamespace` com `cfg` real,
> `orchestrator` com `store`, `client` com `send_message` async e um logger
> basta — não suba poller.

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_telegram.py -k audiobook -q`
Expected: FAIL — `AttributeError: 'TelegramService' object has no attribute '_audiobook_scan_once'`

**Step 3: implemente**

Em `gaia/telegram/service.py`, troque o corpo do laço por uma varredura
extraída (mesmo motivo do `_proactive_tick`: testável sem `sleep`):

```python
    async def _audiobook_watcher(self) -> None:
        """audiobook.watch_minutes > 0: confere a pasta sozinho de tempos em tempos."""
        cfg = self.cfg.audiobook
        interval = max(1.0, cfg.watch_minutes) * 60
        while True:
            await asyncio.sleep(interval)
            try:
                await self._audiobook_scan_once()
            except Exception:
                self.logger.exception("varredura de audiobooks falhou")

    async def _audiobook_scan_once(self) -> None:
        """Uma varredura: gate do painel, processa a inbox e avisa no chat."""
        from gaia.control import routines as routines_mod
        from gaia.audiobook.service import process_inbox

        cfg = self.cfg.audiobook
        store = getattr(self.orchestrator, "store", None)
        # Rotina `audiobook:watch` pausada no painel: nao varre nem avisa.
        if not routines_mod.is_enabled(store, "audiobook:watch"):
            return
        inbox = self.cfg.audiobook_inbox()
        outbox = self.cfg.audiobook_outbox()
        tmp_dir = cfg.tmp_path(self.cfg.resolved_data_dir())
        result = await process_inbox(cfg, inbox, outbox, tmp_dir, self.logger)
        chat_id = self.cfg.telegram.allowed_chat_id or self._chat_id
        if result.empty or not cfg.notify or not chat_id:
            return
        try:
            await self.client.send_message(
                "📚 audiobooks prontos em " + str(outbox) + "\n" + result.summary(),
                chat_id=chat_id,
            )
        except TelegramError as exc:
            self.logger.debug("aviso de audiobook falhou: %s", exc)
```

**Step 4: rode e veja passar**

Run: `.venv/bin/python -m pytest tests/test_telegram.py tests/test_audiobook.py -q`
Expected: PASS

**Step 5: commit**

```bash
git add gaia/telegram/service.py tests/test_telegram.py
git commit -m "feat(telegram): varredura de audiobook respeita a pausa da rotina"
```

---

### Task 5: Control Center — rotinas nas rotas do chat

**Files:**
- Modify: `gaia/control/center.py` (`automations`, `update_routine`,
  `run_routine`, `_tasks` passado como `workers`)
- Modify: `gaia/serve/server.py` (`_mudar_automacao`, `_rodar_automacao`)
- Test: `tests/test_control_api.py`

**Step 1: escreva o teste que falha**

Em `tests/test_control_api.py`, depois dos testes de automação do chat:

```python
def test_automations_traz_as_rotinas_continuas(setup):
    base, *_ = setup
    status, corpo = pedir(base, "GET", "/automations", token="tok-chat")
    assert status == 200
    rotinas = corpo["routines"]
    assert len(rotinas) == 12
    agenda = next(r for r in rotinas if r["id"] == "initiative:agenda")
    assert agenda["kind"] == "routine"
    assert agenda["trigger"] == "loop"
    assert agenda["enabled"] is True
    assert agenda["runnable"] is True
    # Sem motor ligado no teste, o worker nao e avaliado: a rotina nao pode
    # aparecer como erro so porque o ControlCenter nao tem `orch`.
    assert agenda["status"] in {"active", "paused", "disabled"}


def test_pausar_rotina_pelo_chat(setup):
    base, _, store, _ = setup
    status, corpo = pedir(
        base,
        "POST",
        "/automations/set",
        token="tok-chat",
        corpo={"id": "initiative:agenda", "enabled": False},
    )
    assert status == 200
    assert corpo["routine"]["paused"] is True
    assert store.routine_enabled("initiative:agenda") is False

    _, corpo = pedir(base, "GET", "/automations", token="tok-chat")
    agenda = next(r for r in corpo["routines"] if r["id"] == "initiative:agenda")
    assert agenda["status"] == "paused"


def test_rotina_aceita_so_enabled(setup):
    """Rotina nao tem horario: `at`/`days` sao 422, nao silencio."""
    base, *_ = setup
    status, corpo = pedir(
        base,
        "POST",
        "/automations/set",
        token="tok-chat",
        corpo={"id": "initiative:agenda", "at": "09:00"},
    )
    assert status == 422
    assert "enabled" in corpo["message"]


def test_rodar_rotina_sem_handler_e_404(setup):
    base, *_ = setup
    status, corpo = pedir(
        base,
        "POST",
        "/automations/run",
        token="tok-chat",
        corpo={"id": "initiative:idle"},
    )
    assert status == 404
    assert corpo["error"] == "unknown_automation"
```

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_control_api.py -k rotina -q`
Expected: FAIL — `KeyError: 'routines'`

**Step 3: implemente no `ControlCenter`**

`gaia/control/center.py` — no topo, `from gaia.control import routines as routines_mod`.

`automations()` passa a devolver as rotinas (aditivo: quem lê `automations`
continua igual) e usa o mesmo `_tasks()` como `workers`:

```python
    def automations(self) -> dict[str, Any]:
        """Agenda + rotinas continuas + estado da proatividade.

        `routines` e aditivo: a tela de automacoes mostra as duas familias e
        nao paga uma rota extra por isso. `workers` vem do mesmo `_tasks()` que
        o health usa — rotina com worker morto aparece como `error` em vez de
        mentir "ativa".
        """
        cfg = self.cfg.proactive
        workers = self._tasks()
        return {
            "automations": automations_mod.describe_automations(self.cfg, self.store),
            "routines": routines_mod.describe_routines(
                self.cfg, self.store, workers=workers or None
            ),
            "routines_summary": routines_mod.summarize(
                self.cfg, self.store, workers=workers or None
            ),
            "proactive": {
                "enabled": bool(cfg.enabled),
                "reminders": bool(cfg.reminders),
                "idle_checkin": bool(cfg.idle_checkin),
                "quiet_hours": str(cfg.quiet_hours),
                "daily_max": int(cfg.daily_max),
            },
        }
```

E os dois métodos novos, ao lado de `update_automation`/`run_automation`:

```python
    def update_routine(self, routine_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Pausa/retoma uma rotina continua (tabela `routines`, schema v9).

        ``KeyError`` = id desconhecido (404), ``ValueError`` = payload invalido
        (422). Nao aceita horario: rotina nao tem janela.
        """
        if self.store is None:
            raise RuntimeError("motor sem banco: rotinas indisponiveis")
        return routines_mod.update(
            self.store, self.cfg, routine_id, payload, workers=self._tasks() or None
        )

    async def run_routine(self, routine_id: str) -> dict[str, Any]:
        """Executa a rotina agora (``trigger=manual``) e devolve o run.

        O `KeyError` vem **antes** de tocar o motor: id desconhecido ou rotina
        sem handler e 404 sempre, nunca 409 de "motor ocupado" (o teste de rota
        que guarda isso e `test_rodar_rotina_sem_handler_e_404`).
        """
        item = routines_mod.find(routine_id)
        if item is None or not item.runnable:
            raise KeyError(routine_id)
        executar = getattr(self.orch, "run_routine_now", None)
        if not callable(executar):
            raise RuntimeError("este motor nao executa rotina sob demanda")
        return dict(await executar(routine_id))
```

> `_tasks()` devolve `{}` quando `self.orch is None` — nesse caso passamos
> `None` para o registry, que só avalia worker quando a chave existe (`render`
> usa `workers.get(item.worker, True)`). Assim um `ControlCenter` de teste não
> inventa "worker parado".

**Step 4: implemente o despacho no transporte**

`gaia/serve/server.py`, em `_mudar_automacao`, antes de chamar
`centro.update_automation`:

```python
    def _mudar_automacao(self, corpo: dict[str, Any]) -> None:
        """Liga/desliga uma automacao (tabela `automations`) ou rotina (`routines`).

        Os ids sao disjuntos (`briefing:*`/`career:*`/`memory:daily`/
        `memory:dream` vs `initiative:*`/`memory:previous_day`/...), entao o
        despacho e por lookup no registry. A tela usa a MESMA rota para as duas
        familias.
        """
        centro = self.app.center
        if centro is None:
            self._erro(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no_center",
                "automações exigem GAIA_CONTROL_TOKEN configurado",
            )
            return
        automation_id = str(corpo.get("id") or "").strip()
        if not automation_id:
            self._erro(HTTPStatus.BAD_REQUEST, "missing_id", "informe id")
            return
        corpo_sem_id = {chave: valor for chave, valor in corpo.items() if chave != "id"}
        if not corpo_sem_id:
            self._erro(
                HTTPStatus.BAD_REQUEST, "missing_value", "informe enabled (ou outro campo)"
            )
            return
        eh_rotina = routines_mod.find(automation_id) is not None
        try:
            if eh_rotina:
                atual = centro.update_routine(automation_id, corpo_sem_id)
                self._json(HTTPStatus.OK, {"ok": True, "routine": atual})
                return
            self._json(
                HTTPStatus.OK,
                {"ok": True, "automation": centro.update_automation(automation_id, corpo_sem_id)},
            )
        except KeyError:
            self._erro(
                HTTPStatus.NOT_FOUND, "unknown_automation",
                f"automação desconhecida: {automation_id}",
            )
        except ValueError as exc:
            self._erro(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_automation", str(exc))
        except Exception as exc:
            logger.warning("mudar automacao falhou: %s", exc)
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "automation_failed", str(exc))
```

Em `_rodar_automacao`, mesmo despacho (rotina não-executável cai no
`KeyError` → 404, que o teste acima cobre):

```python
        eh_rotina = routines_mod.find(automation_id) is not None
        try:
            if eh_rotina:
                resultado = _aguardar(self, centro.run_routine(automation_id))
            else:
                resultado = _aguardar(self, centro.run_automation(automation_id))
        except KeyError:
            ...
```

> Importe no topo de `gaia/serve/server.py`:
> `from gaia.control import routines as routines_mod`.

**Step 5: rode e veja passar**

Run: `.venv/bin/python -m pytest tests/test_control_api.py -q`
Expected: PASS (os testes antigos de automação continuam verdes — a rota só
ganhou um ramo).

**Step 6: commit**

```bash
git add gaia/control/center.py gaia/serve/server.py tests/test_control_api.py
git commit -m "feat(control): rotinas continuas nas rotas do chat (/automations*)"
```

---

### Task 6: Tela `#automacoes` com a seção de rotinas contínuas

**Files:**
- Modify: `gaia/serve/static/index.html` (CSS, `AUTO_STATUS`, `importarRotinas`,
  `linhaRotina`, `carregarTelaAutomacoes`, `alternarRotina`, `rodarRotina`)
- Test: `tests/test_serve_web.py`

**Step 1: escreva o teste que falha**

Em `tests/test_serve_web.py`, logo depois de `test_tela_de_automacoes_no_html`:

```python
def test_rotinas_continuas_na_tela_de_automacoes():
    """A mesma tela mostra as duas familias: automacao (janela) e rotina (gatilho).

    O contrato: a secao existe, cada rotina tem checkbox (ativar/desativar) e,
    quando `runnable`, o "rodar agora"; e as duas familias escrevem pelas MESMAS
    rotas do motor (`/automations/set` e `/automations/run`).
    """
    texto, coletor = _html()
    assert 'id="auto-rotinas"' in texto
    assert "auto-rotinas" in coletor.ids
    # Uma linha por rotina, com o gatilho vindo do servidor (nao do JS).
    assert "function linhaRotina(item)" in texto
    assert "trigger_label" in texto
    # Checkbox de ativar/desativar para TODAS as rotinas (o pedido).
    assert "onchange: (evento) => alternarRotina(item, evento.target)" in texto
    # "rodar agora" so nas que tem handler (o resto nao tem botao).
    assert "item.runnable" in texto
    assert "onclick: (evento) => rodarRotina(item, evento.currentTarget)" in texto
    # Mesmas rotas: rotina e automacao compartilham a escrita.
    assert 'api("/automations/set"' in texto
    assert 'api("/automations/run"' in texto
    # Status novos existem no mapa da tela (sem "undefined" na etiqueta).
    for estado in ("active", "paused", "disabled", "error"):
        assert estado in texto
    # O motivo do servidor aparece (config desligada / worker parado).
    assert "item.reason" in texto
    # O resumo do cabecalho conta as duas familias.
    assert "routines_summary" in texto
```

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_serve_web.py::test_rotinas_continuas_na_tela_de_automacoes -q`
Expected: FAIL — `assert 'id="auto-rotinas"' in texto`

**Step 3: implemente o HTML**

Em `gaia/serve/static/index.html`, no `<section id="view-automacoes">`, logo
abaixo de `#auto-lista`:

```html
  <div id="auto-lista"></div>
  <!-- Rotinas continuas: sem horario declarado (boot/laco/evento/varredura).
       Mesmo cartao das automacoes; o gatilho vem do servidor. -->
  <div id="auto-rotinas"></div>
```

No CSS (junto do bloco `.auto-card`), para o agrupamento:

```css
  #auto-rotinas { display: flex; flex-direction: column; gap: 10px; }
  #auto-rotinas > h3 { font-size: 12px; margin: 6px 0 0; color: var(--dim);
                       text-transform: uppercase; letter-spacing: .06em; }
```

**Step 4: implemente o JS**

Logo depois de `AUTO_STATUS`, acrescente os estados das rotinas:

```javascript
const AUTO_STATUS = {
  scheduled: ["agendada", ""],
  running: ["rodando", "warn"],
  completed: ["deu certo", "ok"],
  failed: ["falhou", "err"],
  missed: ["não rodou", "err"],
  disabled: ["desligada", ""],
  paused: ["pausada", "warn"],
  // Rotinas continuas: nao existe "perdeu a hora" (nao ha janela).
  active: ["ativa", "ok"],
  error: ["com erro", "err"],
};
```

Depois de `linhaAutomacao` (mesmo `.auto-card`, mesmo checkbox):

```javascript
/**
 * Uma rotina continua (boot/laco/evento/varredura). Mesmo cartao da automacao,
 * com tres diferencas: o "quando" e o gatilho (nao uma janela), o "rodar agora"
 * so existe quando o servidor diz `runnable`, e o motivo (`reason`) explica
 * pausa no painel vs. config desligada vs. worker parado.
 */
function linhaRotina(item) {
  const ultima = item.last_run || null;
  const meta = [
    item.trigger_label || item.trigger || "",
    item.skill || "",
  ].filter(Boolean).join(" · ");
  const falhou = Boolean(ultima && ultima.status === "failed");
  const ligada = !item.paused;
  return window.Gaia.el("div", { class: "auto-card" + (falhou ? " falhou" : "") },
    window.Gaia.el("div", { class: "topo" },
      window.Gaia.el("span", { class: "nome" }, item.name || item.id),
      window.Gaia.el("label", { class: "check", title: "ativar/desativar esta rotina" },
        window.Gaia.el("input", {
          type: "checkbox",
          ...(ligada ? { checked: "checked" } : {}),
          onchange: (evento) => alternarRotina(item, evento.target),
        }),
        window.Gaia.el("span", { class: ligada ? "ligado" : "" },
          ligada ? "ativa" : "desativada")),
      autoTag(item.status),
      item.runnable
        ? window.Gaia.el("button", {
            class: "pill rodar-agora",
            type: "button",
            title: "executar esta rotina agora",
            onclick: (evento) => rodarRotina(item, evento.currentTarget),
          }, "rodar agora")
        : null),
    window.Gaia.el("div", { class: "linha-meta" }, meta),
    item.description ? window.Gaia.el("div", { class: "dica" }, item.description) : null,
    item.reason ? window.Gaia.el("div", { class: "erro" }, item.reason) : null,
    ultima ? window.Gaia.el("div",
      { class: "ultima " + (falhou ? "falhou" : (ultima.status === "completed" ? "ok" : "")) },
      "última (manual): " + ultimaEmTexto(ultima)) : null,
  );
}
```

`agendaAutomacoes()` passa a guardar as rotinas também:

```javascript
async function agendaAutomacoes() {
  try {
    const dados = await api("/automations");
    estado.automacoesProativo = dados && dados.proactive ? dados.proactive : null;
    estado.rotinas = Array.isArray(dados.routines) ? dados.routines : [];
    estado.rotinasResumo = dados && dados.routines_summary ? dados.routines_summary : null;
    return Array.isArray(dados.automations) ? dados.automations : [];
  } catch (erro) {
    if (erro.status === 401) tratar401(erro);
    estado.automacoesProativo = null;
    estado.rotinas = [];
    estado.rotinasResumo = null;
    return null;
  }
}
```

Em `estado` (perto de `automacoesProativo`), adicione:

```javascript
  rotinas: [],            // `routines` de `GET /automations`
  rotinasResumo: null,    // contagem por status das rotinas
```

E em `carregarTelaAutomacoes()`, depois de pintar `#auto-lista`:

```javascript
  const rotinas = estado.rotinas || [];
  $("#auto-rotinas").replaceChildren(
    ...(rotinas.length
      ? [
          window.Gaia.el("h3", {}, "Rotinas contínuas"),
          ...rotinas.map(linhaRotina),
        ]
      : []),
  );
```

No resumo do cabeçalho, inclua as rotinas:

```javascript
  const resumoRotinas = estado.rotinasResumo || { active: 0, total: 0 };
  $("#auto-resumo").textContent = [
    ligadas + "/" + lista.length + " agendadas",
    rotinas.length ? resumoRotinas.active + "/" + resumoRotinas.total + " rotinas ativas" : "",
    atrasadas ? atrasadas + " não rodaram" : "",
    falhas ? falhas + " falharam" : "",
  ].filter(Boolean).join(" · ");
```

As duas funções de escrita, ao lado de `alternarAutomacao`/`rodarAutomacao`:

```javascript
/** Pausa/retoma uma rotina (tabela `routines`; o laco respeita no proximo ciclo). */
async function alternarRotina(item, caixa) {
  caixa.disabled = true;
  try {
    await api("/automations/set", {
      method: "POST",
      body: JSON.stringify({ id: item.id, enabled: caixa.checked }),
    });
    await carregarTelaAutomacoes();
  } catch (erro) {
    caixa.checked = !caixa.checked; // desfaz na tela: o servidor recusou
    if (erro.status === 401) tratar401(erro);
    aviso("error", "não foi possível mudar a rotina: " + texto(erro.message));
  } finally {
    caixa.disabled = false;
  }
}

/**
 * Executa a rotina de verdade agora (`trigger=manual`).
 *
 * Vale mesmo com a rotina desativada: o clique e explicito. Avisa no canal em
 * uso (`_say_proactive`), por isso a confirmacao antes.
 */
async function rodarRotina(item, botao) {
  const ok = window.confirm(
    'Rodar "' + (item.name || item.id) + '" agora?'
    + "\n\nIsso executa de verdade e pode enviar mensagem no canal.",
  );
  if (!ok) return;
  const rotulo = botao.textContent;
  botao.disabled = true;
  botao.textContent = "rodando…";
  try {
    const dados = await api("/automations/run", {
      method: "POST",
      body: JSON.stringify({ id: item.id }),
    });
    const run = (dados && dados.run) || {};
    aviso(
      run.status === "completed" ? "success" : "error",
      (item.name || item.id) + ": " + (run.status || "sem resultado")
      + (run.detail ? " — " + run.detail : "")
      + (run.error ? " — " + run.error : ""),
    );
  } catch (erro) {
    if (erro.status === 401) tratar401(erro);
    const motivo = erro.status === 409
      ? "o motor está num turno; tente quando ele terminar"
      : texto(erro.message);
    aviso("error", "não foi possível rodar " + (item.name || item.id) + ": " + motivo);
  } finally {
    botao.disabled = false;
    botao.textContent = rotulo;
    await carregarTelaAutomacoes();
  }
}
```

**Step 5: rode e veja passar**

Run: `.venv/bin/python -m pytest tests/test_serve_web.py -q`
Expected: PASS (inclusive `test_nenhum_id_repetido_no_index` e
`test_todo_seletor_de_id_existe_no_html`).

**Step 6: commit**

```bash
git add gaia/serve/static/index.html tests/test_serve_web.py
git commit -m "feat(web): secao de rotinas continuas na tela de automacoes"
```

---

### Task 7: Painel `/control` mostra as rotinas

**Files:**
- Modify: `gaia/serve/static/control.js` (`desenharAutomations`)
- Test: `tests/test_serve_web.py` (contrato do `control.js`)

**Step 1: escreva o teste que falha**

```python
def test_painel_control_lista_as_rotinas():
    from gaia.serve import web

    bruto, _ = web.control_asset("control.js")  # type: ignore[misc]
    js = bruto.decode("utf-8")
    assert "Rotinas contínuas" in js
    assert "dados.routines" in js
    assert "trigger_label" in js
```

**Step 2: rode e veja falhar**

Run: `.venv/bin/python -m pytest tests/test_serve_web.py::test_painel_control_lista_as_rotinas -q`
Expected: FAIL

**Step 3: implemente**

Em `control.js`, no fim de `desenharAutomations(alvo, dados)`, acrescente um
painel de leitura (o painel `/control` administra; pausar continua sendo na
tela do chat, que usa o token do chat):

```javascript
    const rotinas = dados.routines || [];
    if (rotinas.length) {
      alvo.append(
        el("div", { class: "panel" },
          el("header", {}, el("h2", {}, "Rotinas contínuas"),
            el("span", { class: "dim" }, "boot · laço · evento · varredura")),
          el("div", { class: "panel-body" },
            el("table", {},
              el("thead", {}, el("tr", {},
                el("th", {}, "rotina"), el("th", {}, "gatilho"), el("th", {}, "status"),
                el("th", {}, "motivo"), el("th", {}, "última (manual)"))),
              el("tbody", {}, rotinas.map((r) =>
                el("tr", {},
                  el("td", {}, r.name, el("div", { class: "mono dim" }, r.id)),
                  el("td", { class: "dim" }, r.trigger_label || r.trigger),
                  el("td", {}, tag(r.status, classeStatus(r.status))),
                  el("td", { class: "dim" }, r.reason || r.description || "—"),
                  el("td", { class: "dim" }, r.last_run
                    ? `${r.last_run.status} · ${data(r.last_run.started_at)}`
                    : "nunca"),
                ))),
            ),
          ),
        ),
      );
    }
```

**Step 4: rode e veja passar**

Run: `.venv/bin/python -m pytest tests/test_serve_web.py tests/test_control_api.py -q`
Expected: PASS

**Step 5: commit**

```bash
git add gaia/serve/static/control.js tests/test_serve_web.py
git commit -m "feat(web): painel /control lista as rotinas continuas"
```

---

### Task 8: Documentação e verificação final

**Files:**
- Modify: `AGENTS.md` (mapa do código + receita nova + status)
- Modify: `docs/README.md` (status do plano)
- Modify: `gaia/control/health.py` (rotina em `error` entra nos alertas)

**Step 1: `AGENTS.md` — mapa do código**

Na tabela do mapa, na linha de `gaia/control/`, acrescente `routines`:

```markdown
| `gaia/control/` | Control Center: `runs` (histórico), `skills`, `automations` (agenda + seed/load/retry), `routines` (rotinas contínuas: boot/laço/evento), `health`, `secrets`, `settings`, `providers`, `center` |
```

E na linha de `gaia/controller/`:

```markdown
| `gaia/controller/scheduler.py` | agendador único: lê `automations`, calcula `due()`, chama o handler da skill, faz retry e notifica falha |
| `gaia/controller/routines.py` | gate único (`_routine_on`) e "rodar agora" das rotinas contínuas; os laços dos mixins chamam por aqui |
```

**Step 2: `AGENTS.md` — receita nova**

Depois da receita "Nova automação":

```markdown
- **Nova rotina contínua:** rotina é o que roda **sem horário declarado** — boot,
  laço, evento ou varredura. São três passos, e só três:
  1. o `Routine(...)` em `gaia/control/routines.py` (id namespaced, `trigger`,
     `requires` com caminhos de config, `module`, `worker` e `runnable`);
  2. o `id -> método` em `gaia/controller/routines.py::_ROUTINE_HANDLERS`
     **só** se ela puder rodar à mão (o resto não aparece com botão na tela);
  3. a chamada no laço dono passa por `_run_routine("id", handler, now)` (e o
     boot por `_routine_on("id")`). **Nunca** `if` solto no laço: o gate tem de
     ser um só, senão uma rotina nasce sem checkbox no painel.
  A pausa mora na tabela `routines` (schema v9) como **override**: linha ausente
  = ligada, então não existe seed nem sincronização com o registry (ao contrário
  de `automations`, onde a tabela guarda a agenda inteira). Disparo automático
  **não** grava `runs` (o laço roda a cada 30 s); só `trigger=manual` grava.
  A tela é a MESMA de automacoes (`#automacoes`, `/automations/set` e
  `/automations/run` com despacho por id). Testes: `tests/test_control_routines.py`
  (registry/render), `tests/test_routines_gate.py` (gate e rodar à mão),
  `tests/test_control_api.py` (rotas) e `tests/test_serve_web.py` (tela).
```

**Step 3: `AGENTS.md` — armadilha**

No bloco "Armadilhas já descobertas":

```markdown
- **Rotina com gate duplicado escapa do painel.** Se o laço fizer
  `if self.cfg.x.enabled:` antes de chamar a rotina, o checkbox do painel não
  tem efeito nenhum (o `if` ganha primeiro). O gate de config mora em
  `Routine.requires`; no laço só pode existir `_run_routine`/`_routine_on`.
- **`_memory_loop` e `_proactive_loop` gravam `runs` só no manual.** Registrar
  todo ciclo encheria a tabela `runs` (12 rotinas × 2/min) e o Error Center
  passaria a mostrar "falha" de rotina que simplesmente não tinha nada a fazer.
```

**Step 4: `health.py` — rotina em erro vira alerta**

Em `scheduler_health`, depois do bloco de `perdidas`/`falhas`, acrescente as
rotinas com problema (o alerta já existe para automação; rotina em `error` é a
falha silenciosa que este plano existe para matar):

```python
    from gaia.control.routines import describe_routines

    rotinas = describe_routines(cfg, store, now)
    rotinas_erro = [
        item["id"] for item in rotinas if item["status"] == "error"
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
```

> A ordem importa: workers mortos e `missed` continuam tendo prioridade sobre
> "rotina com problema" (o alerta mais grave primeiro).

**Step 5: `docs/README.md`**

Na tabela de planos:

```markdown
| [`plans/2026-11-24-rotinas-continuas.md`](plans/2026-11-24-rotinas-continuas.md) | **implementado**: rotinas contínuas (boot/laço/evento/varredura) no painel, com tabela `routines` (schema v9) e gate único; as futuras entram com um `Routine(...)` |
```

**Step 6: verificação completa (o CI inteiro)**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

Expected: os três verdes. Se o `mypy` reclamar do `Callable[[datetime], Awaitable[Any]]`
nos `lambda` do `_proactive_tick`, anote o tipo no `_run_routine` com
`Callable[[datetime], Awaitable[None]]` e deixe os `lambda` devolverem `None`
(`lambda _now: self._idle_checkin(now)` já devolve — o corpo é `async def`).

**Step 7: commit**

```bash
git add AGENTS.md docs/README.md gaia/control/health.py
git commit -m "docs: receita e armadilhas das rotinas continuas; health acusa rotina em erro"
```

---

## Verificação manual (depois do merge, no mini)

```bash
# 1. a tabela existe e o motor subiu com o gate ligado
gmin shell 'sqlite3 ~/.local/share/gaia/gaia.db "select * from routines;"'

# 2. a tela responde as duas familias
curl -s -H "Authorization: Bearer $GAIA_SERVE_TOKEN" \
  http://127.0.0.1:8787/automations | jq '.routines | length, .[0]'

# 3. pausar e ver o status virar "paused"
curl -s -X POST -H "Authorization: Bearer $GAIA_SERVE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"initiative:agenda","enabled":false}' \
  http://127.0.0.1:8787/automations/set | jq '.routine.status'

# 4. rodar a mao grava run com trigger=manual
curl -s -X POST -H "Authorization: Bearer $GAIA_SERVE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"initiative:focus"}' \
  http://127.0.0.1:8787/automations/run | jq '.run'
```

## Riscos e como o plano os cobre

| Risco | Cobertura |
|---|---|
| comportamento observado mudar (fase C proibia) | gates novos **ligados por padrão**; o único efeito de subir o plano é a tabela vazia = tudo ligado. `test_orchestrator`/`test_initiative`/`test_estudos` rodam sem tocar em `routines` e continuam verdes |
| painel mentir "ativa" com worker morto | `Routine.worker` + `workers` do `_tasks()`; status `error` e motivo na linha |
| "rodar agora" mandar mensagem sem querer | `confirm()` na tela (igual à automação) e só 9 rotinas com botão |
| inundar `runs` | disparo automático não grava nada; só `trigger=manual` |
| rotina removida do registry quebrar o laço | `_routine_on` devolve `True` para id desconhecido, e o `find` do registry devolve `None` sem exceção |
