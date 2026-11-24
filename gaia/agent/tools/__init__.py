"""Ferramentas padrao do agente."""

from __future__ import annotations

from pathlib import Path

from gaia.agenda.client import AgendaClient
from gaia.agent.tools.agenda import build_agenda_tools
from gaia.agent.tools.audiobook import build_audiobook_tools
from gaia.agent.tools.automations import build_automation_tools
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, ToolSet
from gaia.agent.tools.estudos import build_estudos_tools
from gaia.agent.tools.filesystem import (
    EDIT_TOOL,
    GLOB_TOOL,
    GREP_TOOL,
    LIST_TOOL,
    READ_TOOL,
    WRITE_TOOL,
)
from gaia.agent.tools.pi import build_pi_tools
from gaia.agent.tools.planilhas import build_planilha_tools
from gaia.agent.tools.shell import BASH_TOOL
from gaia.agent.tools.x import build_x_tools
from gaia.estudos.client import EstudosClient


def build_tools(ctx: ToolContext) -> ToolSet:
    tools = ToolSet()
    tools.add(
        Tool(**READ_TOOL),
        Tool(**WRITE_TOOL),
        Tool(**EDIT_TOOL),
        Tool(**LIST_TOOL),
        Tool(**GLOB_TOOL),
        Tool(**GREP_TOOL),
        Tool(**BASH_TOOL),
    )
    # So aparecem quando ha credencial da Agenda: sem isso o modelo tentaria
    # usar uma ferramenta que nunca vai funcionar.
    if ctx.agenda is not None and AgendaClient(ctx.agenda).enabled:
        tools.add(*build_agenda_tools())
    # Estudos: idem, so com credenciais do Abin Suite (Supabase proprio).
    if ctx.estudos is not None and EstudosClient(ctx.estudos).enabled:
        tools.add(*build_estudos_tools())
    # Planilhas: so quando existe um SecondBrain (a ferramenta le de la).
    if str(ctx.secondbrain or "").strip() and Path(ctx.secondbrain).expanduser().is_dir():
        tools.add(*build_planilha_tools())
    # Audiobook: so com a secao configurada e ligada.
    if ctx.audiobook is not None and getattr(ctx.audiobook, "enabled", False):
        tools.add(*build_audiobook_tools())
    # Pi: harness de codigo externo, so quando ligado no config.
    if ctx.pi is not None and getattr(ctx.pi, "enabled", False):
        tools.add(*build_pi_tools())
    # X: publicar exige credencial no cofre; sem ela a ferramenta nem aparece.
    if ctx.x is not None and getattr(ctx.x, "enabled", False):
        tools.add(*build_x_tools())
    # Automacoes: so com config E banco, porque a ferramenta grava na tabela
    # `automations` (sem store ela nao teria onde criar nada).
    if ctx.cfg is not None and ctx.store is not None:
        tools.add(*build_automation_tools())
    return tools


__all__ = ["Tool", "ToolContext", "ToolResult", "ToolSet", "build_tools"]
