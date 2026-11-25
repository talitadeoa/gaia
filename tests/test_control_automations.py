"""Testes da agenda declarada de automacoes (Fase A: so observacao)."""

from __future__ import annotations

from datetime import datetime

import pytest

from gaia.control.automations import (
    MISSED_TOLERANCE_MINUTES,
    describe,
    describe_automations,
    due_now,
    find,
    needs_attention,
    next_run,
    status,
)

# 23/11/2026 e uma segunda-feira (0 = segunda).
SEGUNDA = datetime(2026, 11, 23, 8, 0).astimezone()


def _item(cfg, automation_id):
    achado = find(cfg, automation_id)
    assert achado is not None
    return achado


def test_describe_traz_as_automacoes_do_config(cfg):
    ids = [item.id for item in describe(cfg)]
    assert ids == [
        "briefing:morning",
        "briefing:noon",
        "briefing:evening",
        "market:watchlist",
        "career:weekly",
        "memory:daily",
        "memory:dream",
    ]


def test_describe_usa_os_horarios_do_config(cfg):
    cfg.career.at = "07:30"
    cfg.career.days = [0, 3]
    item = _item(cfg, "career:weekly")
    assert item.at == "07:30"
    assert item.days == (0, 3)
    assert item.delivery == "telegram"


def test_claim_key_bate_com_a_rotina_real(cfg):
    assert _item(cfg, "career:weekly").claim_key(SEGUNDA) == "career:2026-11-23"
    assert (
        _item(cfg, "briefing:morning").claim_key(SEGUNDA)
        == "briefing:2026-11-23:morning"
    )
    assert (
        _item(cfg, "market:watchlist").claim_key(SEGUNDA)
        == "briefing:market:2026-11-23"
    )


def test_next_run_na_segunda_de_manha(cfg):
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    item = _item(cfg, "career:weekly")
    proxima = next_run(item, SEGUNDA)
    assert proxima is not None
    assert proxima.date() == SEGUNDA.date()
    assert proxima.strftime("%H:%M") == "09:00"


def test_next_run_pula_para_a_proxima_semana(cfg):
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    item = _item(cfg, "career:weekly")
    depois = datetime(2026, 11, 23, 10, 0).astimezone()
    proxima = next_run(item, depois)
    assert proxima is not None
    assert proxima.date() == datetime(2026, 11, 30).date()


def test_next_run_sem_dias(cfg):
    cfg.briefing.market_days = []
    item = _item(cfg, "market:watchlist")
    assert next_run(item, SEGUNDA) is None


def test_status_disabled(cfg, store):
    cfg.career.enabled = False
    item = _item(cfg, "career:weekly")
    assert status(item, cfg, store, SEGUNDA) == "disabled"


def test_status_scheduled_dentro_da_janela(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    assert status(item, cfg, store, SEGUNDA) == "scheduled"


def test_status_missed_quando_janela_fechou_sem_reserva(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    assert status(item, cfg, store, depois) == "missed"


def test_status_nao_e_missed_com_reserva_em_meta(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    store.set_meta("career:2026-11-23", "enviado")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    assert status(item, cfg, store, depois) != "missed"


def test_status_nao_e_missed_com_run_do_dia(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    run = store.begin_run(
        kind="career",
        automation="career:weekly",
        started_at="2026-11-23T08:05:00-03:00",
    )
    store.finish_run(run, status="failed", error="boom")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    # Rodou (mesmo falhando): nao e "perdida", e falha registrada.
    assert status(item, cfg, store, depois) != "missed"


def test_status_missed_respeita_tolerancia(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    logo_depois = datetime(2026, 11, 23, 9, 1).astimezone()
    assert status(item, cfg, store, logo_depois) != "missed"
    bem_depois = datetime(2026, 11, 23, 9, 0 + MISSED_TOLERANCE_MINUTES + 1).astimezone()
    assert status(item, cfg, store, bem_depois) == "missed"


def test_status_running_tem_prioridade(cfg, store):
    item = _item(cfg, "career:weekly")
    store.begin_run(kind="career", automation="career:weekly")
    assert status(item, cfg, store, SEGUNDA) == "running"


def test_status_reflete_ultima_execucao(cfg, store):
    item = _item(cfg, "career:weekly")
    run = store.begin_run(
        kind="career",
        automation="career:weekly",
        started_at="2026-11-23T09:02:00-03:00",
    )
    store.finish_run(run, status="completed")
    # segunda 10h: rodou hoje -> completed
    assert status(item, cfg, store, datetime(2026, 11, 23, 10, 0).astimezone()) == "completed"
    # terca: dia sem rotina e sem janela -> agendada para a proxima segunda
    assert status(item, cfg, store, datetime(2026, 11, 24, 12, 0).astimezone()) == "scheduled"


def test_describe_automations_achata_tudo(cfg, store):
    itens = {item["id"]: item for item in describe_automations(cfg, store, SEGUNDA)}
    assert itens["career:weekly"]["enabled"] is True
    assert itens["career:weekly"]["delivery"] == "telegram"
    assert itens["career:weekly"]["days_label"] == "seg"
    assert itens["career:weekly"]["last_run"] is None
    assert itens["briefing:morning"]["days_label"] == "todos os dias"


def test_describe_automations_traz_ultima_execucao(cfg, store):
    run = store.begin_run(kind="career", automation="career:weekly")
    store.finish_run(run, status="failed", error="502", error_kind="network")
    itens = {item["id"]: item for item in describe_automations(cfg, store, SEGUNDA)}
    assert itens["career:weekly"]["last_run"]["status"] == "failed"
    assert itens["career:weekly"]["last_run"]["error_kind"] == "network"


def test_due_now_usa_a_regra_do_worker(cfg):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    assert due_now(item, cfg, SEGUNDA) is True
    assert due_now(item, cfg, datetime(2026, 11, 22, 12, 0).astimezone()) is False


def test_due_now_desligada(cfg):
    cfg.career.enabled = False
    item = _item(cfg, "career:weekly")
    assert due_now(item, cfg, SEGUNDA) is False


def test_needs_attention_pega_missed_e_falha(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    itens = describe_automations(cfg, store, depois)
    ids = {item["id"] for item in needs_attention(itens)}
    assert "career:weekly" in ids


def test_done_key_da_memoria_usa_a_chave_real(cfg):
    """A rotina grava memory_consolidated:; o painel precisa olhar essa chave."""
    item = _item(cfg, "memory:daily")
    assert item.claim_key(SEGUNDA) == "memory:2026-11-23"
    assert item.done_key(SEGUNDA) == "memory_consolidated:2026-11-23"
    sonho = _item(cfg, "memory:dream")
    assert sonho.done_key(SEGUNDA) == "memory_dreamed:2026-11-23"


def test_done_key_vazia_quando_a_claim_ja_e_a_conclusao(cfg):
    assert _item(cfg, "career:weekly").done_key(SEGUNDA) == ""
    assert _item(cfg, "briefing:morning").done_key(SEGUNDA) == ""


def test_nao_e_missed_quando_a_chave_de_conclusao_existe(cfg, store):
    """Memory ja consolidada hoje (chave real), sem run: nao e 'perdida'."""
    cfg.memory.at = "23:00"
    store.set_meta("memory_consolidated:2026-11-23", "/SecondBrain/Memoria/x.md")
    item = _item(cfg, "memory:daily")
    depois = datetime(2026, 11, 24, 6, 0).astimezone()
    assert status(item, cfg, store, depois) != "missed"


def test_sonho_com_memoria_dreamed_gravada_nao_e_missed(cfg, store):
    store.set_meta("memory_dreamed:2026-11-23", "/SecondBrain/Sonhos/x.md")
    item = _item(cfg, "memory:dream")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    assert status(item, cfg, store, depois) != "missed"


def test_status_completed_dentro_da_janela_nao_mente(cfg, store):
    """Rodou 09:02 com sucesso; as 10:00 o status e completed, nao 'scheduled'."""
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    run = store.begin_run(
        kind="career",
        automation="career:weekly",
        started_at="2026-11-23T09:02:00-03:00",
    )
    store.finish_run(run, status="completed", detail="3 vagas")
    agora = datetime(2026, 11, 23, 10, 0).astimezone()
    assert status(item, cfg, store, agora) == "completed"


def test_status_failed_dentro_da_janela(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    run = store.begin_run(
        kind="career",
        automation="career:weekly",
        started_at="2026-11-23T09:02:00-03:00",
    )
    store.finish_run(run, status="failed", error="502", error_kind="network")
    agora = datetime(2026, 11, 23, 10, 0).astimezone()
    assert status(item, cfg, store, agora) == "failed"


def test_falha_de_ontem_nao_marca_hoje_como_failed(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    run = store.begin_run(
        kind="career",
        automation="career:weekly",
        started_at="2026-11-16T09:02:00-03:00",
    )
    store.finish_run(run, status="failed", error="502")
    # segunda seguinte, antes da janela: nao rodou hoje -> agendada, mesmo com
    # a falha antiga em last_run (que a tela mostra em separado)
    agora = datetime(2026, 11, 23, 8, 0).astimezone()
    assert status(item, cfg, store, agora) == "scheduled"
    itens = {i["id"]: i for i in describe_automations(cfg, store, agora)}
    assert itens["career:weekly"]["last_run"]["status"] == "failed"


# ---------------------------------------------------------------------------
# Fase C: a tabela e a fonte de verdade (seed -> load -> update)
# ---------------------------------------------------------------------------
def test_seed_copia_o_config_para_a_tabela(cfg, store):
    from gaia.control.automations import load, seed

    assert seed(cfg, store) == 7
    linhas = {linha["id"]: linha for linha in store.list_automations()}
    assert linhas["career:weekly"]["at"] == cfg.career.at
    assert linhas["career:weekly"]["days"] == ",".join(
        str(dia) for dia in cfg.career.days
    )
    assert linhas["briefing:morning"]["until"] == cfg.briefing.noon_at
    assert linhas["memory:dream"]["until"] == cfg.memory.dream_until
    # Seed e idempotente: rodar de novo nao duplica nem reescreve.
    assert seed(cfg, store) == 0
    assert len(load(cfg, store)) == 7


def test_seed_nao_desfaz_pausa_do_painel(cfg, store):
    """Um boot novo nao pode religar o que a pessoa pausou."""
    from gaia.control.automations import load, seed

    seed(cfg, store)
    store.update_automation("career:weekly", enabled=False)
    assert seed(cfg, store) == 0
    carreira = next(item for item in load(cfg, store) if item.id == "career:weekly")
    assert carreira.enabled is False


def test_load_sem_tabela_usa_o_config(cfg, store):
    from gaia.control.automations import load

    # Nada semeado: a agenda derivada continua valendo (comportamento anterior).
    itens = load(cfg, store)
    assert [item.id for item in itens] == [item.id for item in describe(cfg)]


def test_load_traz_horario_editado_na_tabela(cfg, store):
    from gaia.control.automations import load, seed

    seed(cfg, store)
    store.update_automation("career:weekly", at="07:30", days="1,3")
    carreira = next(item for item in load(cfg, store) if item.id == "career:weekly")
    assert carreira.at == "07:30"
    assert carreira.days == (1, 3)


def test_pausar_muda_o_status_para_paused(cfg, store):
    from gaia.control.automations import load, seed

    seed(cfg, store)
    store.update_automation("career:weekly", enabled=False)
    carreira = next(item for item in load(cfg, store) if item.id == "career:weekly")
    assert status(carreira, cfg, store, SEGUNDA) == "paused"


def test_validate_payload_recusa_horario_invalido():
    from gaia.control.automations import validate_payload

    with pytest.raises(ValueError, match="at invalido"):
        validate_payload({"at": "09:99"})
    with pytest.raises(ValueError, match="until invalido"):
        validate_payload({"until": "24:00"})
    with pytest.raises(ValueError, match=r"0\.\.6"):
        validate_payload({"days": [7]})
    with pytest.raises(ValueError, match="days"):
        validate_payload({"days": "0,2"})
    with pytest.raises(ValueError, match="retry_max"):
        validate_payload({"retry_max": 99})
    with pytest.raises(ValueError, match="delivery"):
        validate_payload({"delivery": "pombo"})
    with pytest.raises(ValueError, match="nada para alterar"):
        validate_payload({})


def test_validate_payload_normaliza_dias():
    from gaia.control.automations import validate_payload

    assert validate_payload({"days": [4, 0, 0]}) == {"days": "0,4"}
    assert validate_payload({"at": "7:05"}) == {"at": "07:05"}
    assert validate_payload({"enabled": False}) == {"enabled": False}


def test_update_grava_e_devolve_o_painel(cfg, store):
    from gaia.control.automations import seed, update

    seed(cfg, store)
    item = update(store, cfg, "briefing:evening", {"at": "19:00", "enabled": False})
    assert item["at"] == "19:00"
    assert item["paused"] is True
    assert item["status"] in {"paused", "disabled"}


def test_update_recusa_id_desconhecido(cfg, store):
    from gaia.control.automations import update

    with pytest.raises(KeyError):
        update(store, cfg, "nao:existe", {"at": "10:00"})


def test_due_e_claim_por_janela(cfg, store, monkeypatch):
    from gaia.control.automations import claim, due, load, release, seed

    seed(cfg, store)
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "18:00"
    store.update_automation("career:weekly", at="08:00", until="18:00", days="0")
    carreira = next(item for item in load(cfg, store) if item.id == "career:weekly")

    assert due(carreira, cfg, SEGUNDA, store) is True
    assert claim(carreira, store, SEGUNDA) is True
    # Reservada: o scheduler nao dispara duas vezes na mesma janela.
    assert due(carreira, cfg, SEGUNDA, store) is False
    release(carreira, store, SEGUNDA)
    assert due(carreira, cfg, SEGUNDA, store) is True


def test_retry_so_para_rede_e_desconhecido(cfg):
    from gaia.control.automations import backoff_seconds, retry_allowed

    item = _item(cfg, "career:weekly")
    item.retry_max = 2
    assert retry_allowed("network", 1, item) is True
    assert retry_allowed("unknown", 2, item) is True
    assert retry_allowed("network", 3, item) is False
    assert retry_allowed("auth", 1, item) is False
    assert retry_allowed("config", 1, item) is False
    item.retry_backoff_minutes = 5.0
    assert backoff_seconds(item, 1) == 300.0
    assert backoff_seconds(item, 2) == 600.0
