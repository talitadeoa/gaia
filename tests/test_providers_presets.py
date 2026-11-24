from __future__ import annotations

import json

import pytest

from gaia.agent import catalog
from gaia.agent.providers import (
    PRESETS,
    describe_provider,
    normalize_provider,
    preset_api_key,
    resolve_api_key,
)


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    for preset in PRESETS.values():
        for key in preset.env_keys:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("GAIA_AGENT_API_KEY", raising=False)


def test_aliases_apontam_para_presets():
    assert normalize_provider("cc") == "commandcode"
    assert normalize_provider("goat") == "commandcode"
    assert normalize_provider("cc-claude") == "commandcode-anthropic"
    assert normalize_provider("od") == "opendesign"
    assert normalize_provider("open-design") == "opendesign"
    assert normalize_provider("zen") == "opencode"
    assert normalize_provider("Claude") == "anthropic"


def test_preset_do_opendesign():
    info = describe_provider("opendesign")
    assert info["api"] == "openai"
    assert info["base_url"] == "https://amr-link.open-design.ai/v1"
    assert normalize_provider("opendesign") == "opendesign"


def test_chave_do_opendesign_vem_do_pi(monkeypatch, tmp_path):
    """A chave do pi (`auth.json`) serve ao preset, sem colar nada no .env."""
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"opendesign": {"key": "sk-amr-abc"}}), encoding="utf-8")
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {**PRESETS, "opendesign": _with_auth(PRESETS["opendesign"], auth)},
    )

    assert preset_api_key("opendesign") == "sk-amr-abc"


def test_chave_do_opendesign_por_ambiente(monkeypatch):
    monkeypatch.setenv("OPENDESIGN_API_KEY", "sk-amr-env")
    assert preset_api_key("opendesign") == "sk-amr-env"


def test_opendesign_declara_o_modelo_rapido_barato():
    # "eco" e a variante barata do mesmo modelo no gateway; vazio faria o
    # pedido curto cair no principal (mais caro).
    assert PRESETS["opendesign"].fast_model == "deepseek-v4.1-flash-eco"


def test_base_urls_da_command_code():
    assert describe_provider("commandcode")["base_url"] == (
        "https://api.commandcode.ai/provider/v1"
    )
    assert describe_provider("commandcode-anthropic")["api"] == "anthropic"
    assert describe_provider("commandcode-anthropic")["base_url"] == (
        "https://api.commandcode.ai/provider"
    )


def test_chave_lida_do_auth_file(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"apiKey": "user_abc"}), encoding="utf-8")
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {**PRESETS, "commandcode": _with_auth(PRESETS["commandcode"], auth)},
    )

    assert preset_api_key("commandcode") == "user_abc"
    assert resolve_api_key("commandcode") == "user_abc"


def test_env_vence_o_auth_file(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"apiKey": "user_arquivo"}), encoding="utf-8")
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {**PRESETS, "commandcode": _with_auth(PRESETS["commandcode"], auth)},
    )
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "user_env")

    assert preset_api_key("commandcode") == "user_env"


def test_auth_file_ausente_nao_quebra(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {
            **PRESETS,
            "commandcode": _with_auth(PRESETS["commandcode"], tmp_path / "sumiu.json"),
        },
    )
    assert preset_api_key("commandcode") == ""


def test_commandcode_entra_no_catalogo_mesmo_sem_env(monkeypatch):
    monkeypatch.setattr(
        "gaia.agent.catalog._own_key", lambda name: ""
    )
    assert catalog._has_own_key("commandcode") is True
    assert catalog._has_own_key("openrouter") is False


def _with_auth(preset, path):
    from dataclasses import replace

    return replace(preset, auth_file=(str(path),))


# ------------------------------------------------------------------ atalhos
def test_atalhos_nao_repetem_destino():
    from gaia.agent.providers import SHORTCUTS

    destinos = list(SHORTCUTS.values())
    assert len(set(destinos)) == len(destinos)


def test_atalho_cc_usa_o_ultimo_modelo_da_commandcode():
    from gaia.agent.providers import resolve_shortcut

    assert resolve_shortcut("cc", {"commandcode": "z-ai/glm-5.3-flash"}) == (
        "commandcode",
        "z-ai/glm-5.3-flash",
    )


def test_atalho_cc_sem_historico_cai_no_padrao_do_provedor():
    from gaia.agent.providers import resolve_shortcut

    assert resolve_shortcut("cc", {}) == ("commandcode", PRESETS["commandcode"].default_model)


def test_atalho_ds_aponta_para_a_deepseek_direta():
    from gaia.agent.providers import resolve_shortcut

    assert resolve_shortcut("ds", {"commandcode": "qualquer"}) == ("deepseek", "deepseek-flash")


def test_atalho_de_modelo_vence_o_provedor():
    from gaia.agent.providers import resolve_shortcut

    assert resolve_shortcut("glm", {"commandcode": "deepseek/deepseek-v4.1-flash"}) == (
        "commandcode",
        "z-ai/glm-5.3-flash",
    )
    assert resolve_shortcut("turbo", {}) == (
        "commandcode",
        "deepseek/deepseek-v4.1-flash-fast",
    )


def test_atalho_desconhecido_nao_resolve():
    from gaia.agent.providers import resolve_shortcut

    assert resolve_shortcut("nada", {}) is None
    assert resolve_shortcut("  ", {}) is None


# ------------------------------------------------- modelo rapido por provedor
def test_cada_preset_declara_seu_modelo_rapido():
    assert PRESETS["commandcode"].fast_model == "deepseek/deepseek-v4.1-flash-fast"
    # A API direta da DeepSeek so tem deepseek-flash (V4.1 Flash) e v4-pro:
    # nao ha variante "fast" la, entao o rapido e o proprio V4.1 Flash.
    assert PRESETS["deepseek"].fast_model == "deepseek-flash"


def test_modelo_rapido_de_outro_provedor_e_ignorado():
    from gaia.agent.providers import fast_model_ref

    # Estale do provedor anterior: volta o rapido do proprio preset, nunca o id
    # de outro provedor (a API direta da DeepSeek nao conhece esse nome).
    assert (
        fast_model_ref("deepseek", "commandcode/deepseek/deepseek-v4.1-flash-fast").model_id
        == "deepseek-flash"
    )
    assert (
        fast_model_ref("commandcode", "deepseek/deepseek-flash").model_id
        == "deepseek/deepseek-v4.1-flash-fast"
    )


def test_modelo_rapido_do_provedor_atual_vale():
    from gaia.agent.providers import fast_model_ref

    assert fast_model_ref("deepseek", "deepseek-flash").model_id == "deepseek-flash"
    assert (
        fast_model_ref("commandcode", "deepseek/deepseek-v4.1-flash-fast").model_id
        == "deepseek/deepseek-v4.1-flash-fast"
    )
    # O prefixo do proprio provedor e removido: o request leva so o id do modelo.
    assert fast_model_ref("deepseek", "deepseek/deepseek-flash").model_id == "deepseek-flash"
    # Gateway aceita o id inteiro do modelo vendido por ele.
    assert (
        fast_model_ref("commandcode", "commandcode/z-ai/glm-5.3-flash").model_id
        == "z-ai/glm-5.3-flash"
    )
    # Sem valor explicito, vale o rapido do preset (V4.1 Flash da direta).
    assert fast_model_ref("deepseek", "").model_id == "deepseek-flash"
