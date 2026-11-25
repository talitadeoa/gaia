"""Caracteriza o parser e os módulos extraídos da CLI."""

from __future__ import annotations

from gaia.cli_parser import build_parser


def test_parser_ask_command():
    args = build_parser().parse_args(["ask", "resuma", "o", "dia", "--no-voice"])
    assert args.command == "ask"
    assert args.message == ["resuma", "o", "dia"]
    assert args.no_voice is True


def test_parser_whatsapp_serve_defaults():
    args = build_parser().parse_args(["whatsapp", "serve", "--port", "9000"])
    assert args.command == "whatsapp"
    assert args.whatsapp_command == "serve"
    assert args.port == 9000
    assert args.host is None


def test_parser_top_level_flags():
    args = build_parser().parse_args(["--ui", "textual", "--verbose"])
    assert args.ui == "textual"
    assert args.verbose is True
    assert args.command is None


def test_textual_modules_expose_public_api():
    from gaia.interface.textual_app import _HELP, GaiaTextualApp, TextualSink
    from gaia.interface.textual_render import _welcome_art, card
    from gaia.interface.textual_screens import CommandPalette, SessionPicker

    assert GaiaTextualApp and TextualSink and _HELP
    assert card and _welcome_art
    assert CommandPalette and SessionPicker


def test_welcome_art_wordmark_cabe_na_largura():
    from gaia.interface.textual_render import _welcome_art

    art = _welcome_art(78)
    assert art
    assert len(art) <= 8
    largura = max(sum(len(texto) for texto, _ in segments) for segments in art)
    assert largura <= 78


def test_parser_serve_defaults():
    args = build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.host is None
    assert args.port is None
    assert args.token is None
    assert args.no_telegram is False


def test_parser_serve_flags():
    args = build_parser().parse_args(
        ["serve", "--host", "203.0.113.10", "--port", "9000", "--no-telegram"]
    )
    assert args.host == "203.0.113.10"
    assert args.port == 9000
    assert args.no_telegram is True


def test_parser_remote_vale_antes_do_subcomando():
    args = build_parser().parse_args(["--remote", "http://gaia:8787", "ask", "oi"])
    assert args.remote == "http://gaia:8787"
    assert args.command == "ask"


def test_parser_remote_ausente_por_padrao():
    assert build_parser().parse_args([]).remote is None


def test_remote_ask_fala_com_servidor_e_nao_com_o_motor_local(monkeypatch):
    """Regressão: `ask` vinha antes do ramo remoto e rodava tudo no Mac.

    O cliente fino não pode abrir banco nem indexar o Second Brain: o pedido
    tem de ir por HTTP para o `gaia serve`.
    """
    import sys

    import pytest

    from gaia import cli

    visto: dict = {}

    class FakeClient:
        def __init__(self, base: str, token: str = "", *, ui=None) -> None:
            visto["base"] = base
            visto["token"] = token

        def pedir_um_turno(self, texto: str) -> str:
            visto["texto"] = texto
            return "ok"

        def run(self) -> None:  # pragma: no cover - não é o caminho deste teste
            visto["run"] = True

    class FakeUI:
        def info(self, *a, **k) -> None: ...
        def error(self, *a, **k) -> None: ...

    monkeypatch.setattr(cli, "RemoteClient", FakeClient)
    monkeypatch.setattr(cli, "TerminalUI", lambda *a, **k: FakeUI())
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(
        cli, "_ask_command", lambda *a, **k: pytest.fail("rodou o motor local")
    )
    monkeypatch.setattr(
        sys, "argv", ["gaia", "--remote", "http://gaia:8799", "ask", "oi"]
    )

    cli.main()

    assert visto["base"] == "http://gaia:8799"
    assert visto["texto"] == "oi"
