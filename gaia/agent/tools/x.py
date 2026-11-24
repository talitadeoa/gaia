"""Ferramenta `post_x`: publicar no X em nome da conta da Gaia.

Mesmo motivo das ferramentas da Agenda: no Telegram o agente nao deve depender
de `bash` para falar com a API. Publicar e escrita publica, entao a ferramenta
pede aprovacao sempre (`requires_approval=True`).
"""

from __future__ import annotations

from typing import Any

from gaia.agent.errors import ToolError
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, schema, string
from gaia.x.client import LIMITE, XClient, XError


def _client(ctx: ToolContext) -> XClient:
    cfg = getattr(ctx, "x", None)
    if cfg is None:
        raise ToolError("X nao configurado")
    client = XClient(cfg)
    if not client.enabled:
        raise ToolError(
            "X desabilitado: falta X_CLIENT_ID/X_REFRESH_TOKEN no cofre "
            "(~/.config/gaia/secrets/x.env)"
        )
    return client


def post_x(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    client = _client(ctx)
    texto = str(arguments.get("texto") or "").strip()
    try:
        dados = client.post(texto)
    except XError as exc:
        return ToolResult.failure(str(exc))
    identificador = str(dados.get("id") or "")
    corpo = texto if len(texto) <= 120 else texto[:120] + "..."
    if identificador:
        return ToolResult.success(
            f"Post publicado no X (id {identificador}): {corpo}\n"
            f"https://x.com/i/web/status/{identificador}"
        )
    return ToolResult.success(f"Post publicado no X: {corpo}")


X_TOOLS: tuple[dict[str, Any], ...] = (
    dict(
        name="post_x",
        description=(
            "Publica um post (unico, texto puro) na conta do X da Gaia. "
            "Escreve em nome publico: use so quando a pessoa pedir o post. "
            "Devolve o id e o link."
        ),
        parameters=schema(
            {"texto": string(f"Texto do post (limite {LIMITE} caracteres).")},
            ["texto"],
        ),
        handler=post_x,
        requires_approval=True,
    ),
)


def build_x_tools() -> tuple[Tool, ...]:
    return tuple(Tool(**spec) for spec in X_TOOLS)


__all__ = ["X_TOOLS", "build_x_tools", "post_x"]
