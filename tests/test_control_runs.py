"""Testes do registro de execucoes (Control Center, Fase A)."""

from __future__ import annotations

import asyncio

import pytest

from gaia.control.runs import (
    classify_error,
    record_run,
    render_run,
    summary_since,
)
from gaia.memory.store import MemoryStore


async def test_record_run_sucesso(store):
    async with record_run(
        store, kind="career", automation="career:weekly", delivery="telegram"
    ) as run:
        run.detail = "3 vagas"

    rows = store.list_runs()
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["duration_ms"] is not None
    assert rows[0]["duration_ms"] >= 0
    assert rows[0]["detail"] == "3 vagas"
    assert rows[0]["delivery"] == "telegram"
    assert rows[0]["automation"] == "career:weekly"
    assert rows[0]["finished_at"]


async def test_record_run_falha_registra_erro(store):
    with pytest.raises(RuntimeError):
        async with record_run(store, kind="market"):
            raise RuntimeError("boom")

    row = store.list_runs()[0]
    assert row["status"] == "failed"
    assert "boom" in row["error"]
    assert row["error_kind"] == "unknown"


async def test_record_run_cancelado(store):
    with pytest.raises(asyncio.CancelledError):
        async with record_run(store, kind="briefing"):
            raise asyncio.CancelledError()

    row = store.list_runs()[0]
    assert row["status"] == "cancelled"


def test_classifica_erro_de_credencial():
    assert classify_error("401 Unauthorized") == "auth"
    assert classify_error("402 Insufficient Balance") == "auth"
    assert classify_error("chave invalida") == "auth"


def test_classifica_erro_de_rede():
    assert classify_error("timeout ao falar com o provedor") == "network"
    assert classify_error("httpx.ConnectError") == "network"
    assert classify_error("503 Service Unavailable") == "network"


def test_classifica_erro_de_config():
    assert classify_error("career.enabled nao configurado") == "config"


def test_classifica_desconhecido():
    assert classify_error("qualquer outra coisa") == "unknown"
    assert classify_error("") == "unknown"


def test_filtros_e_limite(store):
    career_1 = store.begin_run(kind="career")
    store.finish_run(career_1, status="completed")
    store.begin_run(kind="market")
    career_2 = store.begin_run(kind="career")
    store.finish_run(career_2, status="failed", error="boom")
    assert len(store.list_runs(kind="career")) == 2
    assert len(store.list_runs(status="failed")) == 1
    assert len(store.list_runs(limit=1)) == 1
    assert [row["kind"] for row in store.list_runs(limit=1)] == ["career"]


def test_last_run_por_automacao(store):
    primeiro = store.begin_run(kind="briefing", automation="briefing:morning")
    store.finish_run(primeiro, status="completed")
    segundo = store.begin_run(kind="briefing", automation="briefing:noon")
    store.finish_run(segundo, status="failed", error="x")
    achado = store.last_run("briefing", "briefing:morning")
    assert achado is not None
    assert achado["status"] == "completed"
    assert store.last_run("nada") is None


def test_resumo_por_status_e_kind(store):
    a = store.begin_run(kind="career")
    store.finish_run(a, status="completed")
    b = store.begin_run(kind="career")
    store.finish_run(b, status="failed", error="x")
    resumo = summary_since(store, days=1)
    assert resumo["total"] == 2
    assert resumo["by_status"]["completed"] == 1
    assert resumo["by_kind"]["career"]["total"] == 2
    assert resumo["by_kind"]["career"]["failed"] == 1


def test_prune_remove_antigos(store):
    store.begin_run(kind="career")
    removidos = store.prune_runs("2999-01-01T00:00:00+00:00")
    assert removidos == 1
    assert store.list_runs() == []


def test_render_run_nao_vaza_campos_internos(store):
    store.begin_run(kind="career")
    dados = render_run(store.list_runs()[0])
    assert set(dados) == {
        "id",
        "kind",
        "automation",
        "trigger",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "delivery",
        "detail",
        "error",
        "error_kind",
        "attempt",
        # `bot` e novo (schema v12/fase 3): a atribuicao da execucao ao bot.
        "bot",
    }


async def test_banco_fechado_nao_derruba_a_rotina(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    store.close()
    executou = False
    async with record_run(store, kind="career") as run:
        executou = True
        run.detail = "ok"
    assert executou is True
    assert run.status == "completed"
