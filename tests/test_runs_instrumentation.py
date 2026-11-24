"""As rotinas existentes registram execucao real (e so quando executam)."""

from __future__ import annotations

from datetime import date

import pytest

from gaia.controller.briefing import BriefingMixin
from gaia.controller.career import CareerMixin
from gaia.memory.store import MemoryStore


class FakeTelegram:
    enabled = True

    def __init__(self, falhar: bool = False) -> None:
        self.enviados: list[str] = []
        self.falhar = falhar

    async def send_message(self, text: str) -> None:
        if self.falhar:
            raise RuntimeError("502 Bad Gateway")
        self.enviados.append(text)


class FakeStore(MemoryStore):
    """MemoryStore real: o comportamento de meta e o que importa."""


@pytest.fixture
def cfg(cfg):
    cfg.career.enabled = True
    cfg.briefing.enabled = True
    return cfg


def _concrete(cls):
    """Versao concreta do mixin: os metodos cruzados viram no-ops."""
    stubs = {nome: (lambda *a, **k: None) for nome in cls.__abstractmethods__}
    return type(f"Fake{cls.__name__}", (cls,), stubs)


def _mixin_instance(cls, cfg, store, telegram, **extra):
    """Instancia um mixin sem passar pelo contrato abstrato."""
    instance = object.__new__(_concrete(cls))
    instance.cfg = cfg
    instance.store = store
    instance.telegram = telegram
    instance.logger = __import__("logging").getLogger("test")
    instance._career_last_attempt = 0.0
    instance._briefing_last_attempt = 0.0
    instance._market_last_attempt = 0.0
    for chave, valor in extra.items():
        setattr(instance, chave, valor)
    return instance


def OrchCareer(cfg, store, telegram, **extra):
    instancia = _mixin_instance(CareerMixin, cfg, store, telegram, **extra)
    import tempfile
    from pathlib import Path

    instancia._career_state_path = (  # type: ignore[method-assign]
        lambda: Path(tempfile.mkdtemp()) / "state.json"
    )
    return instancia


def OrchBriefing(cfg, store, telegram, texto="ola", **extra):
    instancia = _mixin_instance(
        BriefingMixin, cfg, store, telegram, _texto=texto, **extra
    )

    async def _compose(slot, day):
        return instancia._texto

    instancia._compose_briefing = _compose  # type: ignore[method-assign]
    return instancia


async def test_career_registra_run_quando_ha_vagas(monkeypatch, cfg, store):
    from gaia.controller import career as modulo

    class Resultado:
        bruto = 3
        itens: tuple = ()

    monkeypatch.setattr(modulo, "collect", lambda state: _fake_await(Resultado()))
    monkeypatch.setattr(modulo, "load_state", lambda path: {})
    monkeypatch.setattr(modulo, "record_results", lambda state, result: None)
    monkeypatch.setattr(modulo, "save_state", lambda path, state: None)
    monkeypatch.setattr(
        modulo,
        "pending_jobs",
        lambda state: [{"id": "1"}, {"id": "2"}],
    )
    monkeypatch.setattr(modulo, "compose_message", lambda *a, **k: "vagas!")
    monkeypatch.setattr(modulo, "mark_delivered", lambda state, ids: None)

    orch = OrchCareer(cfg, store, FakeTelegram())
    assert await orch._send_career(date(2026, 11, 23)) is True

    row = store.list_runs(kind="career")[0]
    assert row["status"] == "completed"
    assert row["automation"] == "career:weekly"
    assert row["delivery"] == "telegram"
    assert row["duration_ms"] is not None
    assert "entregues" in row["detail"]


async def test_career_nao_registra_quando_ja_entregue(cfg, store):
    store.set_meta("career:2026-11-23", "enviado")
    orch = OrchCareer(cfg, store, FakeTelegram())
    assert await orch._send_career(date(2026, 11, 23)) is False
    assert store.list_runs(kind="career") == []


async def test_career_falha_no_telegram_vira_run_failed(monkeypatch, cfg, store):
    from gaia.controller import career as modulo

    class Resultado:
        bruto = 1
        itens: tuple = ()

    monkeypatch.setattr(modulo, "collect", lambda state: _fake_await(Resultado()))
    monkeypatch.setattr(modulo, "load_state", lambda path: {})
    monkeypatch.setattr(modulo, "record_results", lambda state, result: None)
    monkeypatch.setattr(modulo, "save_state", lambda path, state: None)
    monkeypatch.setattr(modulo, "pending_jobs", lambda state: [{"id": "1"}])
    monkeypatch.setattr(modulo, "compose_message", lambda *a, **k: "vagas!")

    orch = OrchCareer(cfg, store, FakeTelegram(falhar=True))
    assert await orch._send_career(date(2026, 11, 23)) is False

    row = store.list_runs(kind="career")[0]
    assert row["status"] == "failed"
    assert row["error_kind"] == "network"
    assert "502" in row["error"]


async def test_briefing_registra_execucao(cfg, store):
    orch = OrchBriefing(cfg, store, FakeTelegram())
    assert await orch._send_briefing("morning", date(2026, 11, 23)) is True
    row = store.list_runs(kind="briefing")[0]
    assert row["status"] == "completed"
    assert row["automation"] == "briefing:morning"
    assert "morning" in row["detail"]


async def test_briefing_sem_conteudo_nao_e_sucesso(cfg, store):
    orch = OrchBriefing(cfg, store, FakeTelegram(), texto="")
    assert await orch._send_briefing("noon", date(2026, 11, 23)) is False
    row = store.list_runs(kind="briefing")[0]
    assert row["status"] == "completed"
    assert row["detail"] == "sem conteudo"


async def test_briefing_falha_registra_erro(cfg, store):
    orch = OrchBriefing(cfg, store, FakeTelegram(falhar=True))
    assert await orch._send_briefing("morning", date(2026, 11, 23)) is False
    row = store.list_runs(kind="briefing")[0]
    assert row["status"] == "failed"
    assert row["error_kind"] == "network"


async def test_briefing_nao_registra_quando_ja_enviado(cfg, store):
    store.set_meta("briefing:2026-11-23:morning", "enviado")
    orch = OrchBriefing(cfg, store, FakeTelegram())
    assert await orch._send_briefing("morning", date(2026, 11, 23)) is False
    assert store.list_runs(kind="briefing") == []


async def test_nada_executou_nada_registrado(cfg, store):
    """O gate de horario nao registra nada: 'aguardando' nao e execucao."""
    orchs = [
        OrchCareer(cfg, store, FakeTelegram()),
        OrchBriefing(cfg, store, FakeTelegram()),
    ]
    assert store.list_runs() == []
    assert len(orchs) == 2


def _fake_await(valor):
    async def _coro():
        return valor

    return _coro()
