"""Cliente e ferramenta do X (Twitter): OAuth2 PKCE + `POST /2/tweets`.

Nada de rede: o transporte do httpx e substituido por um `MockTransport`, entao
o teste exercita o codigo real (refresh, rotacao do refresh token, header e
corpo da requisicao) sem tocar a internet.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from gaia.agent.errors import ToolError
from gaia.agent.tools import ToolContext, build_tools
from gaia.agent.tools.x import build_x_tools, post_x
from gaia.config import XConfig
from gaia.secrets import open_service
from gaia.x.client import XClient, XError

TOKEN_URL = "https://api.x.com/2/oauth2/token"
TWEET_URL = "https://api.x.com/2/tweets"


def _cofre(tmp_path: Path, texto: str = "") -> Path:
    pasta = tmp_path / "secrets"
    pasta.mkdir(exist_ok=True)
    arquivo = pasta / "x.env"
    arquivo.write_text(
        texto
        or "X_CLIENT_ID=id-do-app\n"
        "X_CLIENT_SECRET=segredo-do-app\n"
        "X_REFRESH_TOKEN=refresh-velho\n",
        encoding="utf-8",
    )
    arquivo.chmod(0o600)
    return pasta


def _cfg(tmp_path: Path, **overrides) -> XConfig:
    dados = {"enabled": True, "secrets_dir": str(_cofre(tmp_path))}
    dados.update(overrides)
    return XConfig(**dados)


def _transport(monkeypatch, handler) -> list[httpx.Request]:
    vistos: list[httpx.Request] = []

    def envolver(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return handler(request)

    def cliente(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(envolver)
        return _CLIENTE_REAL(*args, **kwargs)

    _CLIENTE_REAL = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", cliente)
    return vistos


def _ok(monkeypatch, tweet_id: str = "123", novo_refresh: str = "refresh-novo"):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "access-novo",
                    "refresh_token": novo_refresh,
                    "token_type": "bearer",
                    "expires_in": 7200,
                },
            )
        return httpx.Response(201, json={"data": {"id": tweet_id, "text": "oi"}})

    return _transport(monkeypatch, handler)


# ------------------------------------------------------------------- credencial
def test_sem_credencial_fica_desabilitado(tmp_path):
    cfg = XConfig(enabled=True, secrets_dir=str(tmp_path / "vazio"))
    assert XClient(cfg).enabled is False


def test_desabilitado_por_config(tmp_path):
    cliente = XClient(_cfg(tmp_path, enabled=False))
    assert cliente.enabled is False


def test_cliente_habilitado_com_credencial_no_cofre(tmp_path):
    assert XClient(_cfg(tmp_path)).enabled is True


def test_credencial_do_ambiente_nao_vaza_para_subprocesso(tmp_path, monkeypatch):
    """O cofre e lido sob demanda: nada entra em os.environ."""
    monkeypatch.delenv("X_REFRESH_TOKEN", raising=False)
    cliente = XClient(_cfg(tmp_path))
    assert cliente.enabled is True
    assert "X_REFRESH_TOKEN" not in __import__("os").environ


# -------------------------------------------------------------------- validacao
def test_texto_vazio_e_recusado(tmp_path):
    with pytest.raises(XError, match="vazio"):
        XClient(_cfg(tmp_path)).post("   ")


def test_texto_acima_do_limite_e_recusado(tmp_path):
    with pytest.raises(XError, match="280"):
        XClient(_cfg(tmp_path)).post("a" * 281)


# ------------------------------------------------------------------------ post
def test_post_troca_refresh_por_access_e_publica(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    vistos = _ok(monkeypatch)

    resultado = XClient(cfg).post("primeiro post da Gaia")

    assert resultado["id"] == "123"
    assert [str(r.url) for r in vistos] == [TOKEN_URL, TWEET_URL]

    # Basic auth com client_id:client_secret e grant_type=refresh_token.
    esperado = base64.b64encode(b"id-do-app:segredo-do-app").decode()
    assert vistos[0].headers["authorization"] == f"Basic {esperado}"
    corpo_token = dict(httpx.QueryParams(vistos[0].content.decode()))
    assert corpo_token["grant_type"] == "refresh_token"
    assert corpo_token["refresh_token"] == "refresh-velho"

    assert vistos[1].headers["authorization"] == "Bearer access-novo"
    assert json.loads(vistos[1].content) == {"text": "primeiro post da Gaia"}


def test_refresh_novo_e_gravado_no_cofre(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _ok(monkeypatch, novo_refresh="refresh-rotacionado")

    XClient(cfg).post("oi")

    dados = open_service("x", diretorio=Path(cfg.secrets_dir)).read()
    assert dados["X_REFRESH_TOKEN"] == "refresh-rotacionado"
    # O resto do arquivo continua intacto.
    assert dados["X_CLIENT_ID"] == "id-do-app"
    arquivo = open_service("x", diretorio=Path(cfg.secrets_dir))
    assert oct(arquivo.path.stat().st_mode & 0o777) == "0o600"


def test_resposta_de_erro_da_api_vira_xerror(tmp_path, monkeypatch):
    def handler(request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(201, json={"data": {"id": "1"}})

    _transport(monkeypatch, handler)
    with pytest.raises(XError, match="autorizacao"):
        XClient(_cfg(tmp_path)).post("oi")


def test_erro_ao_publicar_vira_xerror(tmp_path, monkeypatch):
    def handler(request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "b"})
        return httpx.Response(403, json={"title": "Forbidden", "detail": "no scope"})

    _transport(monkeypatch, handler)
    with pytest.raises(XError, match="403"):
        XClient(_cfg(tmp_path)).post("oi")


def test_falha_de_rede_vira_xerror(tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("sem rede")

    _transport(monkeypatch, handler)
    with pytest.raises(XError, match="conectar"):
        XClient(_cfg(tmp_path)).post("oi")


# --------------------------------------------------------------------- tools
@pytest.fixture
def tools_x(tmp_path):
    cfg = _cfg(tmp_path)
    return {tool.name: tool for tool in build_x_tools()}, ToolContext(tmp_path, True, x=cfg)


def test_ferramenta_so_existe_com_config_ligada(tmp_path):
    sem = build_tools(ToolContext(Path(".")))
    assert "post_x" not in sem.names()

    cfg = XConfig(enabled=True, secrets_dir=str(_cofre(tmp_path)))
    com = build_tools(ToolContext(Path("."), True, x=cfg))
    assert "post_x" in com.names()


def test_post_x_exige_aprovacao(tools_x):
    tools, _ = tools_x
    assert tools["post_x"].requires_approval is True


def test_post_x_publica_e_devolve_id(tools_x, monkeypatch):
    tools, ctx = tools_x
    _ok(monkeypatch, tweet_id="987")
    saida = tools["post_x"].handler({"texto": "oi mundo"}, ctx)
    assert saida.ok
    assert "987" in saida.content


def test_post_x_sem_credencial_avisa(tools_x, tmp_path):
    _, _ = tools_x
    ctx = ToolContext(tmp_path, True, x=XConfig(enabled=True, secrets_dir=str(tmp_path / "vazio")))
    with pytest.raises(ToolError, match="X"):
        post_x({"texto": "oi"}, ctx)
