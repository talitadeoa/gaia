"""Testes da configuracao efetiva com origem e da edicao do TOML."""

from __future__ import annotations

import os

import pytest

from gaia.config import Config
from gaia.control.settings import effective, remove_env_override, update


def _campos(cfg, env=None):
    return {
        (secao["section"], campo["key"]): campo
        for secao in effective(cfg, env or {})
        for campo in secao["fields"]
    }


def test_effective_traz_as_secoes(cfg):
    secoes = {secao["section"] for secao in effective(cfg, {})}
    assert {"agent", "proactive", "briefing", "career", "memory", "telegram"} <= secoes


def test_effective_marca_origem_default(cfg):
    campos = _campos(cfg, {})
    assert campos[("proactive", "idle_minutes")]["source"] == "default"
    assert campos[("proactive", "idle_minutes")]["editable"] is True


def test_effective_marca_origem_toml(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\nidle_minutes = 30.0\n")
    cfg = Config.load(config_path=toml, env={})
    campos = _campos(cfg, {})
    assert campos[("proactive", "idle_minutes")]["source"] == "toml"
    assert campos[("proactive", "idle_minutes")]["value"] == 30.0


def test_effective_marca_origem_env_e_bloqueia(cfg, monkeypatch):
    monkeypatch.setenv("GAIA_PROACTIVE_DAILY_MAX", "9")
    campos = _campos(cfg, os.environ)
    campo = campos[("proactive", "daily_max")]
    assert campo["source"] == "env"
    assert campo["env_key"] == "GAIA_PROACTIVE_DAILY_MAX"
    assert campo["editable"] is False


def test_effective_esconde_segredos(cfg):
    campos = _campos(cfg, {})
    assert ("agent", "api_key") not in campos
    assert ("telegram", "bot_token") not in campos
    assert ("serve", "token") not in campos


def test_effective_marca_tipos(cfg):
    campos = _campos(cfg, {})
    assert campos[("proactive", "enabled")]["type"] == "bool"
    assert campos[("proactive", "idle_minutes")]["type"] == "float"
    assert campos[("proactive", "daily_max")]["type"] == "int"
    assert campos[("briefing", "morning_at")]["type"] == "time"
    assert campos[("career", "days")]["type"] == "list"


def test_fonte_do_audio_vira_select_no_painel(cfg):
    """`serve.voice_source` e enum: o painel desenha <select> com os 2 valores."""
    campo = _campos(cfg, {}).get(("serve", "voice_source"))
    assert campo is not None
    assert campo["value"] == "browser"
    assert campo["options"] == ["browser", "server"]
    assert campo["editable"] is True


def test_update_grava_a_fonte_do_audio(tmp_path):
    toml = tmp_path / "config.toml"
    resultado = update(toml, "serve", "voice_source", "server")
    assert resultado["value"] == "server"
    assert 'voice_source = "server"' in toml.read_text(encoding="utf-8")


def test_update_recusa_fonte_de_audio_invalida(tmp_path):
    toml = tmp_path / "config.toml"
    with pytest.raises(ValueError):
        update(toml, "serve", "voice_source", "microfone-do-vizinho")
    assert not toml.is_file()


def test_update_preserva_comentarios(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("# comentario do topo\n[proactive]\nidle_minutes = 45.0  # padrao\n")
    update(toml, "proactive", "idle_minutes", 30.0)
    texto = toml.read_text()
    assert "# comentario do topo" in texto
    assert "# padrao" in texto
    assert "idle_minutes = 30.0" in texto


def test_update_float_inteiro_vira_ponto_zero(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\nidle_minutes = 45.0\n")
    update(toml, "proactive", "idle_minutes", 30)
    assert "idle_minutes = 30.0" in toml.read_text()


def test_update_bool_minusculo(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\nenabled = true\n")
    update(toml, "proactive", "enabled", False)
    assert "enabled = false" in toml.read_text()


def test_update_lista_em_uma_linha(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[career]\ndays = [0]\n")
    update(toml, "career", "days", [0, 2])
    assert "days = [0, 2]" in toml.read_text()


def test_update_str_com_aspas(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[career]\nat = "09:00"\n')
    update(toml, "career", "at", "07:30")
    assert 'at = "07:30"' in toml.read_text()


def test_update_invalido_restaura_backup(tmp_path):
    toml = tmp_path / "config.toml"
    original = '[agent]\nprovider = "openai"\n'
    toml.write_text(original)
    with pytest.raises(ValueError):
        update(toml, "proactive", "daily_max", "nao-e-numero")
    assert toml.read_text() == original


def test_update_campo_inexistente_falha(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[agent]\nprovider = \"openai\"\n")
    with pytest.raises(ValueError):
        update(toml, "proactive", "campo_que_nao_existe", 1)
    assert not (tmp_path / "config.toml.bak").exists()


def test_update_secao_inexistente_falha(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[agent]\nprovider = \"openai\"\n")
    with pytest.raises(ValueError):
        update(toml, "secao_que_nao_existe", "x", 1)


def test_update_cria_secao_ausente(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[agent]\nprovider = "openai"\n')
    update(toml, "estudos", "remind_at", "21:00")
    texto = toml.read_text()
    assert "[estudos]" in texto
    assert 'remind_at = "21:00"' in texto


def test_update_cria_chave_em_secao_existente(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\nenabled = true\n")
    update(toml, "proactive", "daily_max", 7)
    texto = toml.read_text()
    assert texto.count("[proactive]") == 1
    assert "daily_max = 7" in texto


def test_update_bloqueia_campo_sensivel(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[agent]\nprovider = \"openai\"\n")
    with pytest.raises(ValueError):
        update(toml, "agent", "api_key", "sk-nao-vai-para-o-toml")
    assert "sk-nao-vai-para-o-toml" not in toml.read_text()


def test_update_bloqueia_quando_env_vence(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\ndaily_max = 4\n")
    monkeypatch.setenv("GAIA_PROACTIVE_DAILY_MAX", "9")
    with pytest.raises(ValueError) as excinfo:
        update(toml, "proactive", "daily_max", 3)
    assert "GAIA_PROACTIVE_DAILY_MAX" in str(excinfo.value)
    assert "daily_max = 4" in toml.read_text()


def test_update_devolve_backup(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\ndaily_max = 4\n")
    resultado = update(toml, "proactive", "daily_max", 5)
    assert resultado["backup"].endswith("config.toml.bak")
    assert resultado["value"] == 5
    assert resultado["source"] == "toml"


def test_update_recusa_valor_fora_da_faixa(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[proactive]\ndaily_max = 4\n")
    with pytest.raises(ValueError):
        update(toml, "proactive", "daily_max", -5)
    assert "daily_max = 4" in toml.read_text()


def test_update_recusa_horario_invalido(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[career]\nat = "09:00"\n')
    with pytest.raises(ValueError):
        update(toml, "career", "at", "25:99")


def test_update_arquivo_novo(tmp_path):
    toml = tmp_path / "config.toml"
    resultado = update(toml, "proactive", "enabled", False)
    assert resultado["value"] is False
    assert toml.read_text().strip() == "[proactive]\nenabled = false"


def test_update_aceita_string_de_lista(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[career]\n")
    update(toml, "career", "days", "[1, 3]")
    assert "days = [1, 3]" in toml.read_text()


def test_remove_env_override_comenta_variavel(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GAIA_PROACTIVE_DAILY_MAX=9\nOUTRA=1\n")
    resultado = remove_env_override(env, "proactive", "daily_max")
    assert resultado["removed"] == ["GAIA_PROACTIVE_DAILY_MAX"]
    assert "# GAIA_PROACTIVE_DAILY_MAX=9" in env.read_text()
    assert "OUTRA=1" in env.read_text()


def test_remove_env_override_sem_variavel(tmp_path):
    with pytest.raises(ValueError):
        remove_env_override(tmp_path / ".env", "agent", "campo_inexistente")
