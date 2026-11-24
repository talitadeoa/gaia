"""Ferramenta ``falar_com_bot``: um bot pergunta a outro, uma vez.

A logica de permissao, profundidade e auditoria mora em ``gaia/bots/bus.py``;
aqui so ha a traducao argumento -> ``BotBus``. A ferramenta existe apenas nos
bots com ``can_message`` nao vazio (o catalogo do processo continua com todas as
ferramentas; a allowlist da sessao e que filtra).

``requires_approval=False`` de proposito: a chamada nao altera nada por si so (o
turno do destino continua pedindo aprovacao para `write`/`bash`) e pedir
aprovacao para "pergunta pra Donna" seria ruido. O custo e limitado pela
profundidade maxima e pelo orcamento do proprio turno.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gaia.agent.tools.base import Tool, ToolContext, ToolResult, schema, string

if TYPE_CHECKING:
    from gaia.bots.bus import BotBus


def _bus(ctx: ToolContext) -> BotBus:
    from gaia.bots.bus import BotBus as _BotBus

    bus = getattr(ctx, "bot_bus", None)
    if isinstance(bus, _BotBus):
        return bus
    raise RuntimeError("bus entre bots nao configurado neste processo")


def falar_com_bot(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Pergunta a outro bot e devolve a resposta dele (ou o motivo da recusa)."""
    destino = str(arguments.get("bot") or arguments.get("to") or "").strip()
    pergunta = str(arguments.get("pergunta") or arguments.get("question") or "").strip()
    if not destino:
        return ToolResult.failure("diga para qual bot falar (donna, ariana, sabrina, ada)")
    if not pergunta:
        return ToolResult.failure("pergunta vazia")
    origem = str(getattr(ctx, "bot", "") or "").strip()
    if not origem:
        return ToolResult.failure("esta sessao nao tem bot: falar_com_bot indisponivel")
    try:
        bus = _bus(ctx)
    except RuntimeError as exc:
        return ToolResult.failure(str(exc))
    from gaia.bots.bus import DEPTH

    resposta = bus.ask_sync(origem, destino, pergunta, depth=DEPTH.get())
    # Recusa volta como sucesso de ferramenta: o modelo deve explicar a pessoa
    # (nao e falha da ferramenta, e a resposta certa).
    return ToolResult.success(resposta)


BOT_TOOLS: tuple[dict[str, Any], ...] = (
    dict(
        name="falar_com_bot",
        description=(
            "Pergunta a outro bot especialista (uma unica resposta curta) e "
            "devolve o que ele disse. Use quando o assunto nao for seu: a Donna "
            "cuida da dissertacao, a Sabrina do curriculo/vagas, a Ariana da "
            "carteira, a Ada do backlog/codigo. A chamada nao troca o bot ativo "
            "nem a conversa; a resposta volta como resultado desta ferramenta."
        ),
        parameters=schema(
            {
                "bot": string("id do bot de destino (donna, ariana, sabrina, ada, gaia)."),
                "pergunta": string("Pergunta objetiva, com o contexto necessario."),
            },
            ["bot", "pergunta"],
        ),
        handler=falar_com_bot,
    ),
)


def build_bot_tools() -> tuple[Tool, ...]:
    return tuple(Tool(**spec) for spec in BOT_TOOLS)


__all__ = ["BOT_TOOLS", "build_bot_tools", "falar_com_bot"]
