"""Automacao a partir de linguagem natural: parser, confianca e criacao.

A confianca NAO vem do modelo: vem das regras de ``automation_builder``. Os
testes abaixo cobrem as tres faixas (alta/media/baixa), as guardas de seguranca
(skill desligada, horario invalido, teto diario) e o caminho completo ate o
agendador executar a automacao ``user:*``.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from gaia.agent.tools import ToolContext, build_tools
from gaia.config import Config
from gaia.control import automations as automations_mod
from gaia.controller.automation_builder import (
    MAX_PER_DAY,
    SKILL_AGENDA,
    SKILL_ESTUDOS,
    SKILL_REMINDER,
    creations_today,
    parse_days,
    parse_delivery,
    parse_label,
    parse_target,
    parse_time,
    propose_automation,
    score,
    window_for,
)
from gaia.memory.store import MemoryStore

# 23/11/2026 e uma segunda-feira. 27/11/2026 e sexta.
SEGUNDA = datetime(2026, 11, 23, 8, 0).astimezone()
SEXTA_9H = datetime(2026, 11, 27, 9, 5).astimezone()


@pytest.fixture
def store(tmp_path):
    instance = MemoryStore(tmp_path / "gaia.db")
    yield instance
    instance.close()


@pytest.fixture
def cfg(tmp_path, isolated_env):
    instance = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    instance.telegram.enabled = True
    instance.telegram.bot_token = "123:abc"
    return instance


# ------------------------------------------------------------------ parser
def test_parse_days_nome_completo_e_abreviado():
    assert parse_days("toda sexta as 9h") == (4,)
    assert parse_days("toda sexta-feira as 9h") == (4,)
    assert parse_days("seg, qua e sex as 7h") == (0, 2, 4)
    assert parse_days("aos sabados") == (5,)


def test_parse_days_todos_os_dias_e_dias_uteis():
    assert parse_days("todos os dias as 8h") == (0, 1, 2, 3, 4, 5, 6)
    assert parse_days("todo dia as 8h") == (0, 1, 2, 3, 4, 5, 6)
    assert parse_days("de segunda a sexta") == (0, 1, 2, 3, 4)
    assert parse_days("nos dias uteis as 8h") == (0, 1, 2, 3, 4)
    assert parse_days("no fim de semana") == (5, 6)


def test_parse_days_sem_dia():
    assert parse_days("as 9h me lembre de estudar") == ()


def test_parse_time_formatos():
    assert parse_time("toda sexta as 9h") == "09:00"
    assert parse_time("as 9:30") == "09:30"
    assert parse_time("pelas 18 horas") == "18:00"
    assert parse_time("meio-dia") == "12:00"
    assert parse_time("meia-noite") == "00:00"


def test_parse_time_tarde_e_noite():
    assert parse_time("as 9h da noite") == "21:00"
    assert parse_time("as 7h da tarde") == "19:00"
    # 21h da noite nao vira 33h
    assert parse_time("as 21h da noite") == "21:00"


def test_parse_time_sem_hora_ou_invalida():
    assert parse_time("toda sexta me lembre de estudar") == ""
    assert parse_time("as 25h") == ""


def test_parse_time_relativo_nao_e_horario_do_dia():
    """"em 2 horas" e prazo de lembrete, nao horario de automacao."""
    assert parse_time("me lembre de X em 2 horas") == ""


def test_parse_target():
    assert parse_target("me lembre de estudar") == SKILL_REMINDER
    assert parse_target("me mande o resumo da agenda") == SKILL_AGENDA
    assert parse_target("resumo dos estudos") == SKILL_ESTUDOS
    assert parse_target("quero algo legal") == ""


def test_parse_label_limpa_os_slots():
    assert parse_label("toda sexta as 9h me lembre de estudar") == "Estudar"
    assert (
        parse_label("me lembre de ligar para a mae toda sexta as 18h")
        == "Ligar para a mae"
    )
    # Palavra de ligacao no meio NAO pode ser comda.
    assert parse_label("aos sabados as 10h me lembre de limpar a casa") == (
        "Limpar a casa"
    )
    assert parse_label("toda sexta as 21h me lembre de revisar as metas") == (
        "Revisar as metas"
    )


def test_parse_label_mantem_acento_do_original():
    assert parse_label("toda sexta as 9h me lembre de estudar japonês") == (
        "Estudar japonês"
    )


def test_parse_delivery(cfg):
    assert parse_delivery("toda sexta 9h no telegram", cfg) == ("telegram", True)
    assert parse_delivery("toda sexta 9h aqui", cfg) == ("local", True)
    # Sem dizer nada: telegram quando ha bot configurado.
    assert parse_delivery("toda sexta 9h", cfg) == ("telegram", False)
    cfg.telegram.enabled = False
    assert parse_delivery("toda sexta 9h", cfg) == ("local", False)


def test_window_for_uma_hora():
    assert window_for("09:00") == "10:00"
    assert window_for("23:30") == "00:00"


# ------------------------------------------------------------- confianca
def test_confianca_alta_com_todos_os_slots(cfg):
    total, nivel = score("toda sexta as 9h me lembre de estudar", cfg)
    assert nivel == "alta"
    assert total >= 7


def test_confianca_alta_quando_os_slots_estao_todos(cfg):
    """Sem "no telegram/aqui" a entrega usa o padrao: nao derruba a faixa."""
    total, nivel = score("toda sexta as 9h resumo da agenda", cfg)
    assert total == 10
    assert nivel == "alta"
    # Citar a area sem verbo de pedido ainda e alta (o alvo e claro? nao:).
    total, nivel = score("sexta as 9h agenda", cfg)
    assert nivel == "alta"


def test_confianca_media_quando_falta_dia(cfg):
    total, nivel = score("as 9h me lembre de estudar", cfg)
    assert total == 6
    assert nivel == "media"


def test_confianca_baixa_sem_skill(cfg):
    total, nivel = score("as 9h toda sexta", cfg)
    assert nivel == "baixa"
    assert total <= 3


# ------------------------------------------------------ criacao automatica
def test_alta_cria_ativada(cfg, store):
    resultado = propose_automation(
        "toda sexta 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    assert resultado.status == "created"
    assert resultado.action == "create"
    assert resultado.skill == SKILL_REMINDER
    assert resultado.days == (4,)
    assert resultado.at == "09:00"
    linha = store.get_automation(resultado.automation_id)
    assert linha is not None
    assert linha["enabled"] == 1
    assert linha["skill"] == SKILL_REMINDER
    assert linha["days"] == "4"
    assert json.loads(linha["params"]) == {"text": "Estudar"}
    assert linha["name"].startswith("Lembrete")


def test_media_cria_pausada(cfg, store):
    resultado = propose_automation(
        "as 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    assert resultado.status == "paused"
    assert resultado.level == "media"
    linha = store.get_automation(resultado.automation_id)
    assert linha is not None
    assert linha["enabled"] == 0
    assert "PAUSADA" in resultado.message


def test_baixa_nao_cria(cfg, store):
    resultado = propose_automation("as 9h toda sexta", cfg, store, now=SEGUNDA)
    assert resultado.status == "reformulate"
    assert resultado.action == "ask"
    assert store.list_automations() == []


def test_sem_horario_nao_cria(cfg, store):
    resultado = propose_automation(
        "toda sexta me lembre de estudar", cfg, store, now=SEGUNDA
    )
    assert resultado.status == "refused"
    assert "horario" in resultado.message.lower()
    assert store.list_automations() == []


def test_horario_invalido_nao_cria(cfg, store):
    resultado = propose_automation(
        "toda sexta as 25h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    assert resultado.status == "refused"
    assert store.list_automations() == []


def test_skill_desligada_recusa(cfg, store):
    cfg.estudos.enabled = False
    resultado = propose_automation(
        "toda quarta as 20h me mande o resumo dos estudos", cfg, store, now=SEGUNDA
    )
    assert resultado.status == "refused"
    assert "estudos.enabled" in resultado.message
    assert store.list_automations() == []


def test_lembrete_desligado_recusa(cfg, store):
    cfg.proactive.reminders = False
    resultado = propose_automation(
        "toda sexta as 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    assert resultado.status == "refused"
    assert "proactive.reminders" in resultado.message


def test_entrega_dita_no_pedido(cfg, store):
    resultado = propose_automation(
        "toda sexta as 9h me lembre de estudar no telegram", cfg, store, now=SEGUNDA
    )
    assert resultado.delivery == "telegram"
    assert store.get_automation(resultado.automation_id)["delivery"] == "telegram"


def test_resumo_da_agenda_e_de_estudos(cfg, store):
    agenda = propose_automation(
        "todo dia as 8h me mande o resumo da agenda", cfg, store, now=SEGUNDA
    )
    assert agenda.skill == SKILL_AGENDA
    assert agenda.days == (0, 1, 2, 3, 4, 5, 6)
    estudos = propose_automation(
        "toda quarta as 20h me mande o resumo dos estudos", cfg, store, now=SEGUNDA
    )
    assert estudos.skill == SKILL_ESTUDOS


def test_pedido_repetido_devolve_a_existente(cfg, store):
    primeiro = propose_automation(
        "toda sexta 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    segundo = propose_automation(
        "toda sexta as 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    assert segundo.status == "existing"
    assert segundo.automation_id == primeiro.automation_id
    assert len(store.list_automations()) == 1


def test_teto_diario(cfg, store):
    for indice in range(MAX_PER_DAY):
        resultado = propose_automation(
            f"toda sexta as 9h me lembre de tarefa {indice}",
            cfg,
            store,
            now=SEGUNDA,
        )
        assert resultado.status == "created"
    assert creations_today(store, SEGUNDA) == MAX_PER_DAY
    excedente = propose_automation(
        "toda sexta as 9h me lembre de mais uma", cfg, store, now=SEGUNDA
    )
    assert excedente.status == "refused"
    assert str(MAX_PER_DAY) in excedente.message
    assert len(store.list_automations()) == MAX_PER_DAY


def test_teto_diario_zera_no_dia_seguinte(cfg, store):
    for indice in range(MAX_PER_DAY):
        propose_automation(
            f"toda sexta as 9h me lembre de tarefa {indice}", cfg, store, now=SEGUNDA
        )
    amanha = datetime(2026, 11, 24, 8, 0).astimezone()
    assert creations_today(store, amanha) == 0
    resultado = propose_automation(
        "toda sexta as 9h me lembre de outra", cfg, store, now=amanha
    )
    assert resultado.status == "created"


def test_nunca_edita_automacao_existente_do_registry(cfg, store):
    cfg.career.at = "09:00"
    automations_mod.seed(cfg, store)
    antes = store.get_automation("career:weekly")
    propose_automation("toda sexta 9h me lembre de estudar", cfg, store, now=SEGUNDA)
    assert store.get_automation("career:weekly") == antes


# ------------------------------------------------- automacao no agendador
def test_automacao_criada_aparece_no_painel(cfg, store):
    resultado = propose_automation(
        "toda sexta 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    itens = {item["id"]: item for item in automations_mod.describe_automations(
        cfg, store, SEGUNDA
    )}
    assert resultado.automation_id in itens
    item = itens[resultado.automation_id]
    assert item["skill"] == SKILL_REMINDER
    assert item["enabled"] is True
    assert item["next_run"].startswith("2026-11-27T09:00")
    assert item["status"] == "scheduled"


def test_agendador_considera_devida_no_horario(cfg, store):
    resultado = propose_automation(
        "toda sexta 9h me lembre de estudar", cfg, store, now=SEGUNDA
    )
    item = automations_mod.find_loaded(cfg, store, resultado.automation_id)
    assert item is not None
    assert item.params == {"text": "Estudar"}
    assert automations_mod.due(item, cfg, SEXTA_9H, store) is True
    # Fora do dia/horario nao dispara.
    assert automations_mod.due(item, cfg, SEGUNDA, store) is False


def test_automacao_pausada_nao_e_devida(cfg, store):
    resultado = propose_automation("as 9h me lembre de estudar", cfg, store, now=SEGUNDA)
    item = automations_mod.find_loaded(cfg, store, resultado.automation_id)
    assert item is not None
    assert automations_mod.due(item, cfg, SEXTA_9H, store) is False


def test_painel_marca_pausada_e_nao_desliga_skill(cfg, store):
    resultado = propose_automation("as 9h me lembre de estudar", cfg, store, now=SEGUNDA)
    itens = {item["id"]: item for item in automations_mod.describe_automations(
        cfg, store, SEGUNDA
    )}
    item = itens[resultado.automation_id]
    assert item["paused"] is True
    assert item["config_enabled"] is True
    assert item["status"] == "paused"
    # Editar pelo painel funciona para o id dinamico.
    atualizado = automations_mod.update(
        store, cfg, resultado.automation_id, {"enabled": True}
    )
    assert atualizado["paused"] is False


# --------------------------------------------------------------- ferramenta
def test_ferramenta_so_existe_com_config_e_banco(tmp_path, cfg, store):
    sem = build_tools(ToolContext(tmp_path))
    assert "criar_automacao" not in sem.names()
    com = build_tools(ToolContext(tmp_path, cfg=cfg, store=store))
    ferramenta = com.get("criar_automacao")
    assert ferramenta is not None
    assert ferramenta.requires_approval is True


def test_ferramenta_cria_e_devolve_resumo(tmp_path, cfg, store):
    com = build_tools(ToolContext(tmp_path, cfg=cfg, store=store))
    ferramenta = com.get("criar_automacao")
    assert ferramenta is not None
    resultado = ferramenta.handler(
        {"pedido": "toda sexta 9h me lembre de estudar"},
        ToolContext(tmp_path, cfg=cfg, store=store),
    )
    assert resultado.ok
    assert "Automacao criada" in resultado.content
    assert "confianca=10/10" in resultado.content
    assert len(store.list_automations()) == 1


def test_ferramenta_pede_reformulacao(tmp_path, cfg, store):
    com = build_tools(ToolContext(tmp_path, cfg=cfg, store=store))
    ferramenta = com.get("criar_automacao")
    assert ferramenta is not None
    resultado = ferramenta.handler(
        {"pedido": "toda sexta as 9h"},
        ToolContext(tmp_path, cfg=cfg, store=store),
    )
    assert resultado.ok
    assert "Ainda nao sei" in resultado.content
    assert store.list_automations() == []


def test_ferramenta_sem_pedido_falha(tmp_path, cfg, store):
    com = build_tools(ToolContext(tmp_path, cfg=cfg, store=store))
    ferramenta = com.get("criar_automacao")
    assert ferramenta is not None
    resultado = ferramenta.handler({}, ToolContext(tmp_path, cfg=cfg, store=store))
    assert resultado.ok is False
