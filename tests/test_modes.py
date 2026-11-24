"""Modos como shim do registry de bots (fase 1).

`PERSONAS` e derivado de `gaia/bots/registry.py`: as quatro personas antigas
viraram cinco bots com id proprio. O que este teste protege:

* as cinco chaves novas (``BOT_KEYS``) existem e sao a fonte da verdade;
* as quatro chaves antigas (``COMPAT_KEYS``) continuam **resolvendo** via
  `ALIASES` -- ha conversa e config no disco com `mode="pesquisadora"` e
  `interface.default_mode = "conversa"`;
* o `/modo`, o `Tab` da TUI e o `parse_mode_switch` continuam funcionando com
  os nomes e apelidos antigos e novos.
"""

from __future__ import annotations

from gaia.controller.modes import (
    ALIASES,
    DEFAULT_MODE,
    PERSONAS,
    get_persona,
    normalize_mode_key,
    parse_mode_switch,
    persona_block,
    resolve_mode,
)

# Chaves do registry (o `mode` gravado no banco a partir da fase 1).
BOT_KEYS = {
    "gaia",
    "donna",
    "ariana",
    "sabrina",
    "ada",
}
# Chaves antigas: NAO sao mais chave de PERSONAS, mas `get_persona` resolve
# para o bot dono (compatibilidade obrigatoria com conversas e TOML no disco).
COMPAT_KEYS = {
    "assistente": "gaia",
    "pesquisadora": "donna",
    "trabalhadora": "sabrina",
    "investidora": "ariana",
}


def test_cinco_bots_no_registry():
    assert set(PERSONAS) == BOT_KEYS
    assert DEFAULT_MODE in PERSONAS


def test_chaves_antigas_resolvem_para_o_bot_dono():
    for antiga, nova in COMPAT_KEYS.items():
        assert ALIASES[antiga] == nova
        assert get_persona(antiga) is PERSONAS[nova]
        assert resolve_mode(antiga) is PERSONAS[nova]


def test_conversa_foi_fundida_em_gaia():
    assert get_persona("conversa") is PERSONAS["gaia"]
    assert get_persona("informal") is PERSONAS["gaia"]
    assert resolve_mode("conversa") is PERSONAS["gaia"]


def test_resolve_por_chave_e_acentos():
    assert get_persona("donna") is PERSONAS["donna"]
    assert get_persona("PESQUISADORA") is PERSONAS["donna"]
    assert get_persona("acadêmica") is PERSONAS["donna"]
    assert get_persona("Donna Haraway") is PERSONAS["donna"]
    assert normalize_mode_key("Investidora") == "investidora"


def test_resolve_desconhecido_cai_no_padrao():
    assert resolve_mode("inexistente") is PERSONAS[DEFAULT_MODE]
    assert resolve_mode(None) is PERSONAS[DEFAULT_MODE]
    assert get_persona("inexistente") is None


def test_persona_block_menciona_modo_e_idioma():
    block = persona_block(PERSONAS["sabrina"])
    assert "Sabrina Carpenter" in block
    assert "português do Brasil" in block
    assert "Parceira" not in block


def test_sabrina_aponta_para_o_playbook_de_vagas():
    directive = PERSONAS["sabrina"].directive
    assert "playbook-vagas.md" in directive
    assert "state.json" in directive


def test_parse_mode_switch_falas_naturais():
    # Chaves antigas continuam valendo...
    assert parse_mode_switch("muda para o modo trabalhadora") == "sabrina"
    assert parse_mode_switch("Gaia, entra no modo pesquisadora") == "donna"
    assert parse_mode_switch("modo assistente") == "gaia"
    assert parse_mode_switch("modo conversa") == "gaia"
    assert parse_mode_switch("vira a parceira de trabalhadora") == "sabrina"
    assert parse_mode_switch("vira pesquisadora") == "donna"
    assert parse_mode_switch("muda para o modo finanças") == "ariana"
    assert parse_mode_switch("modo bolsa") == "ariana"
    # ...e os nomes dos bots tambem.
    assert parse_mode_switch("vira Donna") == "donna"
    assert parse_mode_switch("muda para o modo Haraway") == "donna"
    assert parse_mode_switch("vira a Sabrina") == "sabrina"
    assert parse_mode_switch("modo Ada") == "ada"
    assert parse_mode_switch("modo Ariana") == "ariana"
    assert parse_mode_switch("como faço para mudar de emprego?") is None
    assert parse_mode_switch("quero entender o modo como funciona") is None
