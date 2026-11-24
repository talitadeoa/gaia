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
        routines_mod.find("initiative:agenda"),
        cfg,
        store,
        NOW,
        workers={"iniciativa": False},
    )
    assert item["status"] == "error"
    assert "worker" in item["reason"]


def test_update_valida_e_grava(cfg, store):
    resultado = routines_mod.update(
        store, cfg, "initiative:agenda", {"enabled": False}
    )
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
    assert [item["id"] for item in lista] == [
        item.id for item in routines_mod.ROUTINES
    ]
    assert all(item["kind"] == "routine" for item in lista)
    assert all(
        item["trigger"] in {"boot", "loop", "event", "watch"} for item in lista
    )
