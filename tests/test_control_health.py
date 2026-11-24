"""Testes do System Health."""

from __future__ import annotations

import pytest

from gaia.control.health import (
    config_health,
    database_health,
    engine_health,
    overall_status,
    providers_health,
    scheduler_health,
    second_brain_health,
    system_health,
    telegram_health,
)


@pytest.fixture
def cfg(cfg, tmp_path):
    raiz = tmp_path / "brain"
    (raiz / "Inbox").mkdir(parents=True)
    cfg.secondbrain.path = str(raiz)
    return cfg


def _por_id(itens):
    return {item["id"]: item for item in itens}


def test_system_health_tem_os_componentes(cfg, store):
    itens = _por_id(system_health(cfg, store))
    assert {
        "engine",
        "database",
        "second_brain",
        "providers",
        "telegram",
        "scheduler",
        "config",
    } <= set(itens)
    for item in itens.values():
        assert item["status"] in {"ok", "warn", "error", "off"}
        assert item["detail"].strip()
        assert item["checked_at"]


def test_engine_sem_motor(cfg):
    item = engine_health(None)
    assert item["status"] == "warn"
    assert "nao ligado" in item["detail"]


def test_engine_com_motor(cfg):
    class FakeOrch:
        _busy = False
        channel = "serve"
        cfg = None

    fake = FakeOrch()
    fake.cfg = cfg
    item = engine_health(fake)
    assert item["status"] == "ok"
    assert "serve" in item["detail"]
    assert item["busy"] is False


def test_database_ok(cfg, store):
    item = database_health(cfg, store)
    assert item["status"] == "ok"
    assert item["schema_version"] >= 6
    assert "WAL" in item["detail"] or "wal" in item["detail"].lower()


def test_database_sem_store(cfg):
    item = database_health(cfg, None)
    assert item["status"] == "off"


def test_second_brain_sem_raiz(cfg):
    cfg.secondbrain.path = "/caminho/que/nao/existe"
    item = second_brain_health(cfg, None)
    assert item["status"] == "error"
    assert "nao encontrada" in item["detail"]


def test_second_brain_sem_notas(cfg):
    item = second_brain_health(cfg, None)
    assert item["status"] == "warn"
    assert "sem notas" in item["detail"]


def test_providers_com_chave(monkeypatch, cfg):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste-1234")
    item = providers_health(cfg, [{"name": "openai", "has_key": True}])
    assert item["status"] in {"ok", "error"}
    assert "sk-teste" not in item["detail"]


def test_providers_sem_chave_e_erro(cfg, monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_API_KEY", raising=False)
    cfg.agent.provider = "openai"
    cfg.agent.api_key = ""
    item = providers_health(cfg, [])
    assert item["status"] == "error"
    assert "sem credencial" in item["detail"]


def test_providers_locais_sem_chave_sao_ok(cfg):
    cfg.agent.provider = "ollama"
    cfg.agent.api_key = ""
    item = providers_health(cfg, [])
    assert item["status"] == "ok"


def test_telegram_desligado(cfg):
    cfg.telegram.enabled = False
    assert telegram_health(cfg)["status"] == "off"


def test_telegram_sem_token(cfg):
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = ""
    item = telegram_health(cfg)
    assert item["status"] == "error"
    assert "bot_token" in item["detail"]


def test_telegram_sem_chat_id(cfg):
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = "1:abc"
    cfg.telegram.allowed_chat_id = ""
    assert telegram_health(cfg)["status"] == "warn"


def test_telegram_ok_com_ultimo_erro(cfg):
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = "1:abc"
    cfg.telegram.allowed_chat_id = "42"
    assert telegram_health(cfg)["status"] == "ok"
    assert telegram_health(cfg, "409 conflict")["status"] == "warn"


def test_scheduler_worker_parado(cfg, store):
    item = scheduler_health(cfg, store, {"career": False, "briefing": True})
    assert item["status"] == "error"
    assert "career" in item["detail"]


def _segunda(hora=12, minuto=0):
    from datetime import datetime

    return datetime(2026, 11, 23, hora, minuto).astimezone()


def test_scheduler_aponta_automacao_perdida(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = scheduler_health(cfg, store, {}, now=_segunda(12))
    assert item["status"] == "warn"
    assert "career:weekly" in item["detail"]


def test_scheduler_ok_com_workers_vivos(cfg, store):
    # Segunda 08:30: tudo dentro da janela ou ainda por vir.
    item = scheduler_health(cfg, store, {"career": True, "briefing": True}, now=_segunda(8, 30))
    assert item["status"] == "ok"
    assert "automacao" in item["detail"]


def test_config_aponta_override_do_env(monkeypatch, cfg):
    monkeypatch.setenv("GAIA_PROACTIVE_IDLE_CHECKIN", "false")
    cfg.proactive.idle_checkin = True
    item = config_health(cfg)
    assert item["status"] == "warn"
    assert "GAIA_PROACTIVE_IDLE_CHECKIN" in item["detail"]


def test_config_sem_override(cfg):
    item = config_health(cfg, env={})
    assert item["status"] == "ok"


def test_overall_status_pega_o_pior():
    itens = [
        {"status": "ok"},
        {"status": "off"},
        {"status": "warn"},
        {"status": "error"},
    ]
    assert overall_status(itens) == "error"
    assert overall_status([{"status": "ok"}, {"status": "off"}]) == "ok"
    assert overall_status([]) == "ok"
