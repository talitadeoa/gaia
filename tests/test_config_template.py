"""Template TOML (`gaia config --init`) contra os modelos pydantic.

O template e escrito a mao (os comentarios sao o valor dele), entao ele pode
divergir do modelo sem ninguem notar. Este teste cobra o que quebra de fato:

- chave do template que nao existe no modelo (erro na validacao);
- secao do template sem campo correspondente;
- campo do modelo sem default e sem entrada no template (a chave fica
  invisivel para quem edita o arquivo a mao).

Campo COM default pode faltar no template de proposito: segredo (nao vai para
o TOML) ou ajuste fino (fica implicito).
"""

from __future__ import annotations

import tomllib

import pytest

from gaia.config import _DEFAULT_CONFIG_TEMPLATE, Config


@pytest.fixture(scope="module")
def template() -> dict:
    return tomllib.loads(_DEFAULT_CONFIG_TEMPLATE)


def _subcampos(secao: str) -> dict:
    campo = Config.model_fields[secao]
    return getattr(campo.annotation, "model_fields", {}) or {}


def test_template_nao_tem_chave_que_o_modelo_nao_conhece(template):
    extra = []
    for secao, valores in template.items():
        if secao not in Config.model_fields:
            extra.append(f"[{secao}]")
            continue
        conhecidos = _subcampos(secao)
        if not conhecidos:
            continue
        extra += [f"{secao}.{chave}" for chave in valores if chave not in conhecidos]
    assert extra == []


def test_campo_obrigatorio_aparece_no_template(template):
    faltando = []
    for secao, valores in template.items():
        for chave, info in _subcampos(secao).items():
            if info.is_required() and chave not in valores:
                faltando.append(f"{secao}.{chave}")
    assert faltando == []


def test_toda_secao_do_template_existe_no_modelo(template):
    secoes = {secao for secao in template}
    assert secoes <= set(Config.model_fields)


def test_template_e_toml_valido_e_gera_config_completo(template):
    cfg = Config.model_validate(template)
    # Carregar o template nao pode estourar a validacao nem perder secao.
    for secao in template:
        assert getattr(cfg, secao) is not None
