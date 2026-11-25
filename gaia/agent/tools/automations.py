"""Ferramenta ``criar_automacao``: a frase vira linha na tabela automations.

Quem interpreta e decide a confianca e ``controller/automation_builder.py``
(regras, nao LLM); esta ferramenta so traduz o resultado para o modelo e o
texto de volta para a pessoa. Nada aqui edita ou apaga automacao existente:
pedido repetido devolve a que ja existe e o painel continua sendo o lugar de
pausar/mudar horario.

``requires_approval=True`` porque a acao muda o que a Gaia faz sozinha, igual
a ``bash``/``write``/``edit``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gaia.agent.tools.base import Tool, ToolContext, ToolResult, schema, string

if TYPE_CHECKING:
    from gaia.memory.store import MemoryStore


def _store(ctx: ToolContext) -> MemoryStore | None:
    store = getattr(ctx, "store", None)
    return store if store is not None else None


def criar_automacao(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Interpreta a frase, cria (se seguro) e devolve o resumo para a pessoa."""
    # Import tardio: ``gaia.controller`` importa o pacote do agente no
    # ``__init__``, entao carregar o builder no topo fecharia o ciclo
    # agent.tools -> controller -> agent.
    from gaia.controller.automation_builder import MAX_PER_DAY, propose_automation

    if ctx.cfg is None:
        return ToolResult.failure("sem configuracao: nao da para criar automacao")
    phrase = str(arguments.get("pedido") or arguments.get("frase") or "").strip()
    if not phrase:
        return ToolResult.failure("pedido vazio: descreva o que deve ser automatizado")
    store = _store(ctx)
    if store is None:
        return ToolResult.failure(
            "sem banco de dados neste processo: a automacao nao pode ser gravada"
        )
    resultado = propose_automation(phrase, ctx.cfg, store)
    if resultado.action == "refuse":
        # Recusa nao e falha da ferramenta: e a resposta certa (o modelo deve
        # repassar o motivo para a pessoa reformular).
        return ToolResult.success(resultado.message)
    return ToolResult.success(
        resultado.message
        + (
            f"\n\n[detalhe] status={resultado.status} confianca="
            f"{resultado.confidence}/10 skill={resultado.skill} "
            f"dias={','.join(str(dia) for dia in resultado.days) or '-'} "
            f"hora={resultado.at or '-'} entrega={resultado.delivery or '-'} "
            f"bot={resultado.params.get('bot') or '-'} "
            f"id={resultado.automation_id or '-'} (teto {MAX_PER_DAY}/dia)"
        )
    )


AUTOMATION_TOOLS: tuple[dict[str, Any], ...] = (
    dict(
        name="criar_automacao",
        description=(
            "Cria uma automacao recorrente a partir do pedido da pessoa. Use "
            "quando ela disser algo como 'toda sexta as 9h me lembre de "
            "estudar', 'todo dia as 8h me mande o resumo da agenda' ou 'toda "
            "quarta as 20h resumo dos estudos'. Tambem atende pedido a um bot "
            "('toda sexta as 10h pede pra Donna revisar o capitulo'): a "
            "automacao roda na sessao daquele bot e entrega no feed do painel. "
            "O parser extrai dia, horario, skill alvo e entrega; a confianca e "
            "medida por regras: alta cria ativa, media cria pausada para "
            "conferir e baixa pede reformular. Nunca edita nem apaga automacao "
            "existente."
        ),
        parameters=schema(
            {
                "pedido": string(
                    "A frase da pessoa, inteira e sem interpretar: o parser "
                    "deterministico precisa dos termos de dia e horario."
                ),
            },
            ["pedido"],
        ),
        handler=criar_automacao,
        requires_approval=True,
    ),
)


def build_automation_tools() -> tuple[Tool, ...]:
    return tuple(Tool(**spec) for spec in AUTOMATION_TOOLS)


__all__ = ["AUTOMATION_TOOLS", "build_automation_tools", "criar_automacao"]
