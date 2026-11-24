"""Testes do catalogo de skills."""

from __future__ import annotations

import pytest

from gaia.control.skills import SKILLS, describe_skills, find_skill, skill_status


@pytest.fixture
def cfg(cfg):
    """Config isolada (conftest), com Telegram ligado e token presente."""
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = "123:abc"
    return cfg


def test_registry_tem_as_skills_iniciais():
    ids = {skill.id for skill in SKILLS}
    assert {
        "career",
        "market",
        "agenda",
        "estudos",
        "briefing",
        "memory",
        "dream",
        "audiobook",
        "planilhas",
        "tools",
    } <= ids


def test_ids_sao_unicos():
    ids = [skill.id for skill in SKILLS]
    assert len(ids) == len(set(ids))


def test_toda_skill_tem_descricao_e_modulo():
    for skill in SKILLS:
        assert skill.description.strip()
        assert skill.name.strip()
        assert skill.kind in {"routine", "tool_group"}


def test_status_reflete_config(cfg, store):
    lista = {item["id"]: item for item in describe_skills(cfg, store)}
    assert lista["career"]["status"] == "active"
    cfg.career.enabled = False
    lista = {item["id"]: item for item in describe_skills(cfg, store)}
    assert lista["career"]["status"] == "disabled"
    assert "career.enabled" in lista["career"]["reason"]


def test_skill_com_telegram_desligado_fica_desabilitada(cfg, store):
    cfg.telegram.enabled = False
    lista = {item["id"]: item for item in describe_skills(cfg, store)}
    assert lista["career"]["status"] == "disabled"


def test_rotina_sem_bot_token_fica_err(cfg, store):
    cfg.telegram.bot_token = ""
    lista = {item["id"]: item for item in describe_skills(cfg, store)}
    assert lista["career"]["status"] == "error"
    assert "telegram" in lista["career"]["reason"].lower()


def test_tool_group_tambem_respeita_config(cfg, store):
    cfg.agent.tools = False
    lista = {item["id"]: item for item in describe_skills(cfg, store)}
    assert lista["tools"]["status"] == "disabled"


def test_ultima_execucao_aparece(cfg, store):
    antes = {item["id"]: item for item in describe_skills(cfg, store)}
    assert antes["career"]["last_run"] is None

    run = store.begin_run(kind="career", automation="career:weekly")
    store.finish_run(run, status="completed", detail="3 vagas")

    depois = {item["id"]: item for item in describe_skills(cfg, store)}
    assert depois["career"]["last_run"]["status"] == "completed"
    assert depois["career"]["last_run"]["detail"] == "3 vagas"


def test_tool_group_nao_busca_ultima_execucao(cfg, store):
    store.begin_run(kind="tools")
    lista = {item["id"]: item for item in describe_skills(cfg, store)}
    assert lista["tools"]["last_run"] is None


def test_find_skill():
    assert find_skill("career") is not None
    assert find_skill("nao-existe") is None


def test_skill_status_isolado(cfg):
    skill = find_skill("memory")
    assert skill is not None
    status, motivo = skill_status(cfg, skill)
    assert status == "active"
    assert motivo == ""
