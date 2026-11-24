"""Registry dos bots: defaults, override do TOML e validacao dura.

A integracao com o motor (conversa `mode=donna`, politica aplicada na sessao)
tem um teste de ponta no fim; o resto aqui e o descritor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.bots import (
    DEFAULT_BOTS,
    KNOWN_TOOLS,
    bot_aliases,
    bot_problem,
    get_bot,
    load_bots,
    normalize_key,
    require_bot,
)
from gaia.config import BotConfig, Config


@pytest.fixture
def cfg(tmp_path, isolated_env):
    return Config.load(
        config_path=tmp_path / "missing.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )


def test_os_cinco_bots_com_ids_estaveis():
    assert list(DEFAULT_BOTS) == ["gaia", "donna", "ariana", "sabrina", "ada"]
    for bot_id, bot in DEFAULT_BOTS.items():
        assert bot.id == bot_id
        assert bot.name and bot.role and bot.directive
    # `tools=None` = catalogo inteiro; restringir a Gaia nao faz sentido.
    assert DEFAULT_BOTS["gaia"].tools is None
    for bot_id in ("donna", "ariana", "sabrina", "ada"):
        assert DEFAULT_BOTS[bot_id].tools, bot_id


def test_capacidades_que_sao_fronteira_de_seguranca():
    """Ada nunca no repo da Gaia; Ariana nunca com modelo no laco."""
    ada = DEFAULT_BOTS["ada"]
    assert "pr-workspace" in ada.workspace and "/gaia" not in ada.workspace
    assert {"bash", "pi"} <= set(ada.tools or ())

    ariana = DEFAULT_BOTS["ariana"]
    assert not {"bash", "pi", "write", "edit"} & set(ariana.tools or ())

    donna = DEFAULT_BOTS["donna"]
    assert {"bash", "pi", "read", "edit"} <= set(donna.tools or ())
    assert "agenda_dia" not in (donna.tools or ())

    assert DEFAULT_BOTS["sabrina"].workspace.endswith("SecondBrain/cv")


def test_known_tools_cobre_tudo_o_que_o_motor_registra(tmp_path):
    """Ferramenta nova sem entrada em KNOWN_TOOLS falha aqui, nao em producao."""
    from gaia.agent.tools import ToolContext, build_tools

    registradas = set(build_tools(ToolContext(directory=tmp_path)).names())
    # So entram com integracao/config ligada; o registry precisa conhece-las.
    opcionais = {
        "agenda_dia",
        "agenda_rotinas",
        "agenda_paginas",
        "agenda_nova_tarefa",
        "agenda_marcar_tarefa",
        "estudos_metas",
        "estudos_pendentes",
        "estudos_progresso",
        "planilha",
        "audiobook",
        "post_x",
        "criar_automacao",
        "pi",
    }
    assert registradas - KNOWN_TOOLS == set()
    assert KNOWN_TOOLS - opcionais - registradas == set()


def test_pasta_ausente_marca_indisponivel_sem_derrubar():
    """Bot apontando para pasta ausente e aviso, nao crash (Donna antes do clone)."""
    from dataclasses import replace

    bot = replace(DEFAULT_BOTS["donna"], workspace="/caminho/que/nao/existe")
    assert "workspace inexistente" in bot_problem(bot)


def test_get_bot_por_id_nome_e_apelidos_antigos():
    assert get_bot("donna").id == "donna"  # type: ignore[union-attr]
    assert get_bot("Donna Haraway").id == "donna"  # type: ignore[union-attr]
    assert get_bot("haraway").id == "donna"  # type: ignore[union-attr]
    assert get_bot("inexistente") is None
    assert normalize_key("Sabrina Carpenter") == "sabrina carpenter"
    # Compatibilidade: as quatro personas antigas viram bots.
    alias = bot_aliases()
    assert alias["assistente"] == "gaia"
    assert alias["pesquisadora"] == "donna"
    assert alias["trabalhadora"] == "sabrina"
    assert alias["investidora"] == "ariana"
    with pytest.raises(ValueError) as erro:
        require_bot("maria")
    assert "maria" in str(erro.value) and "donna" in str(erro.value)


def test_toml_sobrepoe_o_default_e_o_vazio_herda(tmp_path, cfg):
    (tmp_path / "tese").mkdir()
    cfg.bots = {
        "donna": BotConfig(
            name="Pesquisadora",
            workspace=str(tmp_path / "tese"),
            scope=str(tmp_path),
            tools=["read"],
            model="deepseek/deepseek-v4.1-flash",
        )
    }
    donna = load_bots(cfg)["donna"]
    assert donna.name == "Pesquisadora"
    assert donna.workspace == str(tmp_path / "tese")
    assert donna.tools == ("read",)
    assert donna.model == "deepseek/deepseek-v4.1-flash"
    assert donna.role == DEFAULT_BOTS["donna"].role
    # Lista vazia e decisao ("nenhuma ferramenta"), nao "herda".
    cfg.bots = {"ariana": BotConfig(tools=[])}
    assert load_bots(cfg)["ariana"].tools == ()


@pytest.mark.parametrize(
    ("bot_id", "override", "trecho"),
    [
        ("ada", {"tools": ["read", "teletransporte"]}, "teletransporte"),
        ("ada", {"can_message": ["maria"]}, "maria"),
        ("ada", {"workspace": "/caminho/que/nao/existe"}, "workspace"),
        ("maria", {}, "maria"),
    ],
)
def test_config_invalida_levanta_no_load(tmp_path, cfg, bot_id, override, trecho):
    (tmp_path / "ws").mkdir()
    dados = {"workspace": str(tmp_path / "ws"), **override}
    cfg.bots = {bot_id: BotConfig(**dados)}
    with pytest.raises(ValueError) as erro:
        load_bots(cfg)
    assert trecho in str(erro.value)


@pytest.mark.parametrize(
    ("scope", "trecho"),
    [
        ("segundo-cerebro", "absoluto"),
        ("fora", "nao contem o workspace"),
    ],
)
def test_scope_que_nao_cobre_o_workspace_levanta(tmp_path, cfg, scope, trecho, monkeypatch):
    (tmp_path / "ws").mkdir()
    (tmp_path / "fora").mkdir()
    monkeypatch.chdir(tmp_path)
    # `fora` e absoluto de proposito: o "nao contem" so vale para caminho
    # absoluto existente; relativo cai antes no "precisa ser absoluto".
    escolhido = str(tmp_path / "fora") if scope == "fora" else scope
    cfg.bots = {"ada": BotConfig(workspace=str(tmp_path / "ws"), scope=escolhido)}
    with pytest.raises(ValueError) as erro:
        load_bots(cfg)
    assert trecho in str(erro.value)


def test_default_com_pasta_ausente_nao_derruba_o_load(tmp_path, cfg):
    """O default do codigo pode apontar para pasta que esta maquina nao tem.

    E o caso da Donna antes do clone: `load_bots` aceita (o bot aparece
    indisponivel), mas o TOML declarando a mesma pasta errada levanta.
    """
    bots = load_bots(cfg)
    indisponiveis = {
        bot_id for bot_id, bot in bots.items() if bot_problem(bot, bots)
    }
    # Nenhum erro: o load passou. O que estiver ausente fica marcado.
    assert indisponiveis <= set(bots)
    cfg.bots = {"donna": BotConfig(workspace=str(tmp_path / "nada"))}
    with pytest.raises(ValueError):
        load_bots(cfg)


def test_scope_valido_passa(tmp_path, cfg):
    (tmp_path / "ws").mkdir()
    cfg.bots = {"ada": BotConfig(workspace=str(tmp_path / "ws"), scope=str(tmp_path))}
    assert load_bots(cfg)["ada"].scope == str(tmp_path)


# ------------------------------------------------------------- integracao ---
async def test_select_bot_aplica_a_politica_e_troca_a_conversa(tmp_path, isolated_env):
    """Ponta: conversa `mode=donna`, cwd e allowlist na sessao (inclusive retomada)."""
    import logging
    import types

    from gaia.agent import AgentClient
    from gaia.controller.orchestrator import Orchestrator
    from gaia.memory.store import MemoryStore

    (tmp_path / "tese").mkdir()
    cfg = Config.load(
        config_path=tmp_path / "missing.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.bots = {"donna": BotConfig(workspace=str(tmp_path / "tese"), scope=str(tmp_path))}
    store = MemoryStore(tmp_path / "gaia.db")
    client = AgentClient(cfg, tmp_path / "proj")

    class _UI:
        def __getattr__(self, name):
            return lambda *a, **k: None

    orch = Orchestrator(
        cfg=cfg,
        logger=logging.getLogger("test.bots"),
        ui=_UI(),
        store=store,
        index=None,
        brain=None,
        tts=None,
        transcriber=None,
        recorder=types.SimpleNamespace(),
        client=client,
    )
    try:
        bot = await orch.select_bot("donna")

        assert store.get_conversation(orch.conversation_id)["mode"] == "donna"
        assert orch.persona.key == "donna"
        sessao = client._session(orch.session_id)
        assert sessao is not None and sessao.policy is not None
        assert sessao.policy.bot == "donna"
        assert sessao.policy.tools == bot.tools
        assert sessao.policy.directory == str(Path(tmp_path / "tese"))
        assert sessao.policy.scope == str(tmp_path)
        # A conversa retomada reaplica a politica (nao volta com tudo).
        sessao.policy = None
        await orch.select_bot("donna")
        assert client._session(orch.session_id).policy.bot == "donna"  # type: ignore[union-attr]
    finally:
        store.close()
        await client.aclose()


async def test_bot_indisponivel_nao_e_ativado(tmp_path, isolated_env):
    import logging
    import types

    from gaia.agent import AgentClient
    from gaia.bots import BotIndisponivel
    from gaia.controller.orchestrator import Orchestrator
    from gaia.memory.store import MemoryStore

    cfg = Config.load(
        config_path=tmp_path / "missing.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    store = MemoryStore(tmp_path / "gaia.db")
    client = AgentClient(cfg, tmp_path / "proj")
    class _UI:
        def __getattr__(self, name):
            return lambda *a, **k: None

    orch = Orchestrator(
        cfg=cfg,
        logger=logging.getLogger("test.bots"),
        ui=_UI(),
        store=store,
        index=None,
        brain=None,
        tts=None,
        transcriber=None,
        recorder=types.SimpleNamespace(),
        client=client,
    )
    try:
        # Nesta maquina o clone pode existir: forca a indisponibilidade.
        orch.bots["donna"] = DEFAULT_BOTS["donna"].__class__(
            **{**DEFAULT_BOTS["donna"].__dict__, "workspace": str(tmp_path / "nada")}
        )
        with pytest.raises(BotIndisponivel, match="nao esta disponivel"):
            await orch.select_bot("donna")
        assert not store.list_conversations(
            str(orch.directory), "donna", orch.channel
        )
        with pytest.raises(ValueError):
            await orch.select_bot("maria")
    finally:
        store.close()
        await client.aclose()
