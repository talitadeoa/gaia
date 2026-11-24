"""Politica por sessao: allowlist, diretorio e escopo sobrevivem ao disco."""

from __future__ import annotations

from gaia.agent.session import AgentSession, SessionPolicy, SessionStore


def test_policy_default_e_none():
    session = AgentSession(id="ses_1")
    assert session.policy is None


def test_policy_roundtrip_no_json(tmp_path):
    """Uma sessao retomada depois do restart NAO volta a ter todas as tools."""
    store = SessionStore(tmp_path)
    policy = SessionPolicy(
        tools=("read", "grep"),
        directory=str(tmp_path / "ws"),
        scope=str(tmp_path / "ws"),
        bot="donna",
    )
    session = AgentSession(id="ses_1", policy=policy)
    store.save(session)

    loaded = store.load("ses_1")

    assert loaded is not None
    assert loaded.policy == policy
    assert loaded.policy is not None
    assert loaded.policy.tools == ("read", "grep")
    assert loaded.policy.bot == "donna"


def test_policy_vazia_nao_restringe():
    """Policy sem tools = todas (comportamento de hoje, preservado)."""
    policy = SessionPolicy()
    assert policy.tools is None
    assert policy.directory == ""
    assert policy.scope == ""


def test_sessao_antiga_sem_policy_carrega_como_none(tmp_path):
    """JSON gravado antes desta mudanca nao pode quebrar o load."""
    path = tmp_path / "ses_velha.json"
    path.write_text('{"id": "ses_velha", "messages": []}', encoding="utf-8")
    store = SessionStore(tmp_path)

    loaded = store.load("ses_velha")

    assert loaded is not None and loaded.policy is None
