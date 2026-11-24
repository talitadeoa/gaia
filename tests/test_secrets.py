"""Cofre de credenciais de terceiros (`gaia/secrets.py`).

Regra que estes testes cobrem: credencial de terceiro (GitHub, Google, X) fica
num arquivo proprio, lido sob demanda -- nunca exportada para `os.environ`,
porque o `bash` e o `pi` herdam o ambiente inteiro do processo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gaia.secrets import SecretFile, default_dir, open_service


def _arquivo(tmp_path: Path, texto: str = "") -> Path:
    caminho = tmp_path / "x.env"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_le_key_value_e_ignora_comentario(tmp_path):
    arquivo = SecretFile(
        _arquivo(
            tmp_path,
            "# comentario\nX_CLIENT_ID=abc\n\nX_CLIENT_SECRET='segredo'\n",
        )
    )
    assert arquivo.read() == {"X_CLIENT_ID": "abc", "X_CLIENT_SECRET": "segredo"}


def test_arquivo_ausente_le_vazio_e_nao_explode(tmp_path):
    arquivo = SecretFile(tmp_path / "nao-existe.env")
    assert arquivo.read() == {}
    assert arquivo.get("X_CLIENT_ID") == ""


def test_get_prefere_o_arquivo_ao_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "do-ambiente")
    arquivo = SecretFile(_arquivo(tmp_path, "X_CLIENT_ID=do-arquivo\n"))
    assert arquivo.get("X_CLIENT_ID") == "do-arquivo"


def test_get_cai_para_o_ambiente_quando_falta_no_arquivo(tmp_path, monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "do-ambiente")
    arquivo = SecretFile(_arquivo(tmp_path, "X_CLIENT_SECRET=segredo\n"))
    assert arquivo.get("X_CLIENT_ID") == "do-ambiente"


def test_get_aceita_varios_nomes_e_devolve_o_primeiro_preenchido(tmp_path):
    arquivo = SecretFile(_arquivo(tmp_path, "GH_TOKEN=token-1\n"))
    assert arquivo.get("GITHUB_TOKEN", "GH_TOKEN") == "token-1"


def test_leitura_nao_vaza_para_o_ambiente(tmp_path):
    nome = "GAIA_TESTE_SEGREDO_NAO_EXPORTAR"
    os.environ.pop(nome, None)
    arquivo = SecretFile(_arquivo(tmp_path, f"{nome}=valor\n"))
    assert arquivo.get(nome) == "valor"
    assert nome not in os.environ


def test_set_grava_600_preserva_outras_chaves_e_cria_dir_700(tmp_path):
    pasta = tmp_path / "cofre"
    caminho = pasta / "x.env"
    caminho.parent.mkdir(parents=True)
    caminho.write_text("X_CLIENT_ID=abc\n# fica\nX_REFRESH_TOKEN=velho\n", encoding="utf-8")
    caminho.chmod(0o644)

    arquivo = SecretFile(caminho)
    arquivo.set("X_REFRESH_TOKEN", "novo")

    texto = caminho.read_text(encoding="utf-8")
    assert "X_REFRESH_TOKEN=novo" in texto
    assert "X_CLIENT_ID=abc" in texto
    assert "# fica" in texto
    assert "velho" not in texto
    assert oct(caminho.stat().st_mode & 0o777) == "0o600"


def test_set_cria_arquivo_quando_nao_existe(tmp_path):
    caminho = tmp_path / "cofre" / "github.env"
    SecretFile(caminho).set("GH_TOKEN", "pat")
    assert caminho.read_text(encoding="utf-8").strip() == "GH_TOKEN=pat"
    assert oct(caminho.stat().st_mode & 0o777) == "0o600"
    assert oct(caminho.parent.stat().st_mode & 0o777) == "0o700"


def test_set_recusa_valor_com_quebra_de_linha(tmp_path):
    arquivo = SecretFile(tmp_path / "x.env")
    with pytest.raises(ValueError, match="quebra de linha"):
        arquivo.set("X_REFRESH_TOKEN", "um\ndois")


def test_set_recusa_valor_vazio(tmp_path):
    arquivo = SecretFile(tmp_path / "x.env")
    with pytest.raises(ValueError, match="vazio"):
        arquivo.set("X_REFRESH_TOKEN", "   ")


def test_nome_de_servico_com_barra_e_recusado(tmp_path):
    with pytest.raises(ValueError, match="servico invalido"):
        open_service("../etc", diretorio=tmp_path)
    with pytest.raises(ValueError, match="servico invalido"):
        open_service("x/y", diretorio=tmp_path)


def test_open_service_usa_o_diretorio_pedido(tmp_path):
    arquivo = open_service("github", diretorio=tmp_path)
    assert arquivo.path == tmp_path / "github.env"


def test_default_dir_respeita_gaia_secrets_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_SECRETS_DIR", str(tmp_path / "cofre"))
    assert default_dir() == tmp_path / "cofre"


def test_perms_aponta_arquivo_legivel_por_outros(tmp_path):
    caminho = _arquivo(tmp_path, "X_CLIENT_ID=abc\n")
    caminho.chmod(0o644)
    avisos = SecretFile(caminho).problems()
    assert any("644" in aviso or "permiss" in aviso for aviso in avisos)


def test_arquivo_600_nao_tem_aviso(tmp_path):
    caminho = _arquivo(tmp_path, "X_CLIENT_ID=abc\n")
    caminho.chmod(0o600)
    assert SecretFile(caminho).problems() == []


# --------------------------------------------------------------- config/integracao
def test_config_aponta_o_cofre_e_respeita_env(tmp_path, monkeypatch, isolated_env):
    from gaia.config import Config

    monkeypatch.setenv("GAIA_SECRETS_DIR", str(tmp_path / "cofre-env"))
    cfg = Config.load(config_path=isolated_env / "nao-existe.toml")
    assert cfg.secrets_dir() == tmp_path / "cofre-env"
    assert cfg.secrets.enabled is True


def test_config_usa_secrets_dir_do_toml(isolated_env):
    from gaia.config import Config

    cofre = isolated_env / "cofre-toml"
    toml = isolated_env / "config.toml"
    toml.write_text(f'[secrets]\ndir = "{cofre}"\n', encoding="utf-8")
    cfg = Config.load(config_path=toml, env={"GAIA_DATA_DIR": str(isolated_env / "data")})
    assert cfg.secrets_dir() == cofre.resolve()


def test_env_map_cobre_a_secao_secrets():
    from gaia.config import _ENV_MAP

    assert _ENV_MAP["GAIA_SECRETS_ENABLED"] == ("secrets", "enabled")
    assert _ENV_MAP["GAIA_SECRETS_DIR"] == ("secrets", "dir")


def test_template_tem_a_secao_secrets():
    from gaia.config import _DEFAULT_CONFIG_TEMPLATE

    assert "[secrets]" in _DEFAULT_CONFIG_TEMPLATE
