from __future__ import annotations

import os
import tomllib

import pytest

from gaia.config import (
    Config,
    ServeConfig,
    model_env_override,
    save_agent_model,
    write_default_config,
)


def test_defaults(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )
    assert cfg.agent.provider == "commandcode"
    assert cfg.agent.model == "deepseek/deepseek-v4.1-flash"
    assert cfg.agent.model_slug() == "commandcode/deepseek/deepseek-v4.1-flash"
    assert cfg.stt.language == "pt"
    assert cfg.stt.confirm_transcript is False
    assert cfg.audio.continuous is True
    assert cfg.audio.silence_ms == 5000
    assert cfg.audio.max_seconds == 0.0
    assert cfg.stt.partial_transcription is True
    assert cfg.tts.provider == "edge"
    assert cfg.secondbrain.confirm_create is False
    assert cfg.secondbrain.require_confirmation is True
    assert cfg.interface.concise is True
    assert cfg.interface.echo_language == "zh"
    assert cfg.proactive.enabled is True
    assert cfg.proactive.quiet_hours == "23:00-08:00"
    assert cfg.secondbrain.path.endswith("SecondBrain")
    assert cfg.preferences_path().name == "preferences.md"
    assert cfg.preferences_path().parent == cfg.second_brain_path()
    assert cfg.resolved_data_dir() == (isolated_env / "data").resolve()


def test_idioma_do_eco_vem_do_ambiente_e_do_toml(isolated_env):
    """`echo_language` aceita zh/en/fr; qualquer outra coisa e erro de config."""
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={"GAIA_ECHO_LANGUAGE": "EN", "GAIA_DATA_DIR": str(isolated_env / "data")},
    )
    assert cfg.interface.echo_language == "en"

    config_path = isolated_env / "config.toml"
    config_path.write_text('[interface]\necho_language = "fr"\n', encoding="utf-8")
    cfg = Config.load(
        config_path=config_path, env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )
    assert cfg.interface.echo_language == "fr"

    with pytest.raises(ValueError):
        Config.load(
            config_path=isolated_env / "missing.toml",
            env={"GAIA_ECHO_LANGUAGE": "de", "GAIA_DATA_DIR": str(isolated_env / "data")},
        )


def test_toml_and_env_precedence(isolated_env):
    config_path = isolated_env / "config.toml"
    config_path.write_text(
        """
[agent]
provider = "openai"
model = "gpt-x"
api_key = "segredo"

[tts]
provider = "say"
""",
        encoding="utf-8",
    )
    env = {
        "GAIA_AGENT_MODEL": "gpt-y",
        "GAIA_TTS_PROVIDER": "edge",
        "GAIA_SECOND_BRAIN_PATH": str(isolated_env / "sb"),
        "GAIA_SB_AUTO_CONTEXT": "false",
        "GAIA_DATA_DIR": str(isolated_env / "data2"),
    }
    cfg = Config.load(config_path=config_path, env=env)
    assert cfg.agent.provider == "openai"
    assert cfg.agent.model == "gpt-y"
    assert cfg.tts.provider == "edge"
    assert cfg.secondbrain.path == str(isolated_env / "sb")
    assert cfg.secondbrain.auto_context is False
    assert cfg.public_dict()["agent"]["api_key"] == "***"


def test_config_antiga_migra_para_agent(isolated_env):
    config_path = isolated_env / "legacy.toml"
    config_path.write_text(
        """
[opencode]
model = "deepseek/deepseek-flash"
auto_approve = true
directory = "/tmp/proj"
""",
        encoding="utf-8",
    )
    cfg = Config.load(config_path=config_path, env={"GAIA_DATA_DIR": str(isolated_env / "data")})
    assert cfg.agent.provider == "commandcode"
    assert cfg.agent.model == "deepseek/deepseek-v4.1-flash"
    assert cfg.agent.auto_approve is True
    assert cfg.agent.directory == "/tmp/proj"


def test_chave_do_ambiente_chega_ao_agente(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={
            "GAIA_DATA_DIR": str(isolated_env / "data"),
            "GAIA_AGENT_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "sk-teste",
        },
    )
    assert cfg.agent.api_key == "sk-teste"
    assert cfg.public_dict()["agent"]["api_key"] == "***"


def test_env_types_are_inferred_from_model(isolated_env):
    env = {
        "GAIA_UI": "terminal",  # string, antes era tratado como bool
        "GAIA_UI_DASHBOARD_COLLAPSED": "false",
        "GAIA_TELEGRAM_AUTO_APPROVE": "true",
        "GAIA_AUDIO_SILENCE_MS": "1500",
        "GAIA_AUDIO_VAD_THRESHOLD": "0.7",
        "GAIA_SB_AUTO_CONTEXT": "yes",
        "GAIA_FAST": "off",
        "GAIA_UI_MARKDOWN": "false",
        "GAIA_DATA_DIR": str(isolated_env / "data"),
    }
    cfg = Config.load(config_path=isolated_env / "missing.toml", env=env)
    assert cfg.interface.ui == "terminal"
    assert cfg.interface.dashboard_collapsed is False
    assert cfg.telegram.auto_approve is True
    assert cfg.audio.silence_ms == 1500
    assert cfg.audio.vad_threshold == 0.7
    assert cfg.secondbrain.auto_context is True
    assert cfg.interface.fast is False
    assert cfg.interface.markdown_render is False


def test_dotenv_fica_visivel_para_subprocessos(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "GAIA_TELEGRAM_BOT_TOKEN=abc123\nGAIA_TELEGRAM_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIA_DOTENV", str(dotenv))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAIA_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GAIA_TELEGRAM_ENABLED", raising=False)
    try:
        cfg = Config.load(config_path=tmp_path / "missing.toml")
        assert cfg.telegram.bot_token == "abc123"
        assert os.environ["GAIA_TELEGRAM_BOT_TOKEN"] == "abc123"
        assert os.environ["GAIA_TELEGRAM_ENABLED"] == "true"
    finally:
        os.environ.pop("GAIA_TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("GAIA_TELEGRAM_ENABLED", None)


def test_write_default_config(tmp_path):
    target = tmp_path / "cfg" / "config.toml"
    written = write_default_config(target)
    assert written == target
    text = written.read_text(encoding="utf-8")
    assert "[agent]" in text
    assert "deepseek-v4.1-flash" in text

    written.write_text("# alterado\n", encoding="utf-8")
    write_default_config(target)
    assert written.read_text(encoding="utf-8") == "# alterado\n"
    write_default_config(target, force=True)
    assert "deepseek-v4.1-flash" in written.read_text(encoding="utf-8")


def test_save_agent_model_preserva_comentarios_e_faz_backup(tmp_path):
    target = tmp_path / "config.toml"
    original = (
        "# topo\n"
        "[agent]\n"
        "# provedor em uso\n"
        'provider = "custom"  # comentario\n'
        'model = "antigo"\n'
        'api_key = "segredo"\n'
        'base_url = ""\n\n'
        "[tts]\n"
        'provider = "edge"\n'
    )
    target.write_text(original, encoding="utf-8")

    saved = save_agent_model("openai", "gpt-x", target)

    text = saved.read_text(encoding="utf-8")
    assert "# topo" in text
    assert "# provedor em uso" in text
    assert 'provider = "openai" # comentario' in text
    assert 'model = "gpt-x"' in text
    # nada alem das duas chaves muda: nem segredo, nem outras secoes.
    assert 'api_key = "segredo"' in text
    assert '[tts]\nprovider = "edge"' in text
    backup = target.with_name("config.toml.bak")
    assert backup.read_text(encoding="utf-8") == original

    cfg = Config.load(config_path=target, env={"GAIA_DATA_DIR": str(tmp_path / "data")})
    assert cfg.agent.provider == "openai"
    assert cfg.agent.model_slug() == "openai/gpt-x"


def test_save_agent_model_adiciona_secao_quando_falta(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[tts]\nprovider = "say"\n', encoding="utf-8")

    save_agent_model("deepseek", "deepseek-flash", target)

    text = target.read_text(encoding="utf-8")
    assert "[agent]" in text
    # a seção nova fica depois da existente, sem quebrar o TOML
    data = tomllib.loads(text)
    assert data["agent"] == {"provider": "deepseek", "model": "deepseek-flash"}
    assert data["tts"]["provider"] == "say"

    cfg = Config.load(config_path=target, env={"GAIA_DATA_DIR": str(tmp_path / "data")})
    # Ao carregar, o par antigo (API direta) migra para a Command Code.
    assert cfg.agent.model_slug() == "commandcode/deepseek/deepseek-v4.1-flash"


def test_save_agent_model_exige_provider_e_model(tmp_path):
    with pytest.raises(ValueError, match="obrigatórios"):
        save_agent_model("", "x", tmp_path / "config.toml")
    with pytest.raises(ValueError, match="obrigatórios"):
        save_agent_model("openai", "  ", tmp_path / "config.toml")


def test_model_env_override_lista_apenas_variaveis_preenchidas():
    assert model_env_override({}) == []
    assert model_env_override({"GAIA_AGENT_MODEL": "  "}) == []
    assert model_env_override({"GAIA_AGENT_MODEL": "gpt-x"}) == ["GAIA_AGENT_MODEL"]
    assert model_env_override(
        {"GAIA_AGENT_PROVIDER": "openai", "GAIA_AGENT_MODEL": "gpt-x"}
    ) == ["GAIA_AGENT_PROVIDER", "GAIA_AGENT_MODEL"]


def test_public_dict_redige_o_token_do_serve():
    cfg = Config()
    cfg.serve.token = "segredo-do-serve"
    cfg.agent.api_key = "chave-do-modelo"
    data = cfg.public_dict()
    assert data["serve"]["token"] == "***"
    assert data["agent"]["api_key"] == "***"


def test_serve_defaults_sao_loopback_e_sem_token():
    cfg = Config()
    assert cfg.serve.enabled is False
    assert cfg.serve.host == "127.0.0.1"
    assert cfg.serve.port == 8787
    assert cfg.serve.token == ""
    assert cfg.serve.telegram is True
    assert cfg.serve.voice_source == "browser"


def test_fonte_do_audio_do_modo_voz():
    """`voice_source` decide quem grava: o navegador (padrao) ou o motor."""
    assert ServeConfig().voice_source == "browser"
    assert ServeConfig(voice_source="server").voice_source == "server"
    assert ServeConfig(voice_source="SERVER").voice_source == "server"
    with pytest.raises(ValueError):
        ServeConfig(voice_source="microfone-do-vizinho")

    cfg = Config.load(env={"GAIA_SERVE_VOICE_SOURCE": "server"})
    assert cfg.serve.voice_source == "server"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", ""])
def test_serve_loopback_nunca_exige_token(host):
    assert ServeConfig(host=host).remote_bind_without_token() is False


def test_serve_recusa_escuta_na_rede_sem_token():
    assert ServeConfig(host="203.0.113.10").remote_bind_without_token() is True
    assert (
        ServeConfig(host="203.0.113.10", allow_remote=True).remote_bind_without_token()
        is True
    )
    assert (
        ServeConfig(
            host="203.0.113.10", allow_remote=True, token="segredo"
        ).remote_bind_without_token()
        is False
    )


def test_serve_vem_do_ambiente():
    cfg = Config.load(
        env={
            "GAIA_SERVE_HOST": "203.0.113.10",
            "GAIA_SERVE_PORT": "9000",
            "GAIA_SERVE_TOKEN": "abc",
            "GAIA_SERVE_ALLOW_REMOTE": "true",
            "GAIA_SERVE_INSTANCE_ID": "mini",
        }
    )
    assert cfg.serve.host == "203.0.113.10"
    assert cfg.serve.port == 9000
    assert cfg.serve.token == "abc"
    assert cfg.serve.allow_remote is True
    assert cfg.serve.instance_id == "mini"


def test_template_padrao_traz_a_secao_serve(tmp_path):
    target = tmp_path / "config.toml"
    write_default_config(target)
    cfg = Config.load(config_path=target, env={"GAIA_DATA_DIR": str(tmp_path)})
    assert cfg.serve.host == "127.0.0.1"
    assert cfg.serve.port == 8787
    assert cfg.serve.voice_source == "browser"
    assert "enabled = false" in target.read_text(encoding="utf-8")


def test_padrao_e_a_commandcode(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml", env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )
    assert cfg.agent.provider == "commandcode"
    assert cfg.agent.model == "deepseek/deepseek-v4.1-flash"
    assert cfg.agent.fast_model == "deepseek/deepseek-v4.1-flash-fast"
    assert cfg.agent.model_slug() == "commandcode/deepseek/deepseek-v4.1-flash"


def test_chave_de_outro_provedor_nao_vai_para_o_agente(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={
            "GAIA_DATA_DIR": str(isolated_env / "data"),
            "GAIA_AGENT_PROVIDER": "commandcode",
            "DEEPSEEK_API_KEY": "sk-da-deepseek",
        },
    )
    # Mandar a chave da DeepSeek no header da Command Code dava 401.
    assert cfg.agent.api_key == ""


def test_chave_do_provedor_configurado_chega_ao_agente(isolated_env):
    cfg = Config.load(
        config_path=isolated_env / "missing.toml",
        env={
            "GAIA_DATA_DIR": str(isolated_env / "data"),
            "GAIA_AGENT_PROVIDER": "commandcode",
            "COMMAND_CODE_API_KEY": "sk-da-commandcode",
            "DEEPSEEK_API_KEY": "sk-da-deepseek",
        },
    )
    assert cfg.agent.api_key == "sk-da-commandcode"


def test_escolha_da_deepseek_direta_sobrevive_ao_reload(isolated_env):
    """`/modelo ds` grava provider=deepseek; a migracao nao pode desfazer."""
    config_path = isolated_env / "config.toml"
    save_agent_model("deepseek", "deepseek-flash", config_path, fast_model="deepseek-flash")

    cfg = Config.load(
        config_path=config_path, env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )

    assert cfg.agent.provider == "deepseek"
    assert cfg.agent.model == "deepseek-flash"


def test_migracao_de_config_antiga_acontece_uma_vez(isolated_env):
    config_path = isolated_env / "config.toml"
    config_path.write_text('[agent]\nprovider = "deepseek"\nmodel = "deepseek-flash"\n')

    primeira = Config.load(
        config_path=config_path, env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )
    assert primeira.agent.provider == "commandcode"

    # Depois de escolher a direta, a config volta a valer como escrita.
    save_agent_model("deepseek", "deepseek-flash", config_path, fast_model="deepseek-flash")
    segunda = Config.load(
        config_path=config_path, env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )
    assert segunda.agent.provider == "deepseek"


def test_config_legada_aponta_para_a_commandcode(isolated_env):
    config_path = isolated_env / "legacy.toml"
    config_path.write_text(
        '[agent]\nmodel = "deepseek/deepseek-flash"\n',
        encoding="utf-8",
    )
    cfg = Config.load(
        config_path=config_path, env={"GAIA_DATA_DIR": str(isolated_env / "data")}
    )
    assert cfg.agent.provider == "commandcode"
    assert cfg.agent.model == "deepseek/deepseek-v4.1-flash"


def test_save_agent_model_grava_o_modelo_rapido(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(
        '[agent]\nprovider = "commandcode"\nmodel = "m"\nfast_model = "f"\n',
        encoding="utf-8",
    )

    saved = save_agent_model("deepseek", "deepseek-flash", target, fast_model="deepseek-flash")

    text = saved.read_text(encoding="utf-8")
    assert 'provider = "deepseek"' in text
    assert 'model = "deepseek-flash"' in text
    assert 'fast_model = "deepseek-flash"' in text
