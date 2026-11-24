"""Catalogo de skills: descritores estaticos + status derivado da config.

Nao existe "objeto skill executavel" aqui de proposito. A skill real e o codigo
que ja existe (``controller/career.py``, ``briefing.py``, ``finance/``,
``agenda/``, ``estudos/``); este modulo apenas a descreve para o painel.

``enabled`` NAO e campo do descritor: e calculado em ``describe_skills`` a
partir do ``Config`` e das credenciais. Duas fontes de verdade para "esta
ativa?" foi exatamente o problema do incidente de 22/11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gaia.config import Config
from gaia.memory.store import MemoryStore


@dataclass(frozen=True)
class Skill:
    """Descritor de uma capacidade do agente."""

    id: str
    name: str
    description: str
    kind: str  # routine | tool_group
    # Caminhos de config que precisam ser verdadeiros: "career.enabled".
    requires: tuple[str, ...] = ()
    # Nomes de variavel de ambiente exigidos (segredo nunca e lido aqui).
    needs_env: tuple[str, ...] = ()
    # Ferramentas expostas ao modelo (kind=tool_group).
    provides: tuple[str, ...] = ()
    # Quando roda, em linguagem para a tela.
    schedule_hint: str = ""
    # Area do repo onde vive a implementacao (rastreabilidade).
    module: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


SKILLS: tuple[Skill, ...] = (
    Skill(
        id="career",
        name="Pesquisa de vagas",
        description=(
            "Busca vagas nas fontes abertas, filtra pelos criterios do playbook, "
            "pontua e entrega o relatorio no Telegram."
        ),
        kind="routine",
        requires=("career.enabled", "telegram.enabled"),
        schedule_hint="semanal (seg por padrao), dentro da janela configurada",
        module="gaia/career/",
        tags=("carreira", "notificacao"),
    ),
    Skill(
        id="market",
        name="Monitoramento da bolsa",
        description=(
            "Le a carteira (watchlist), busca cotacoes e envia o resumo nos dias "
            "configurados, sem usar o modelo."
        ),
        kind="routine",
        requires=("briefing.enabled", "briefing.market_enabled", "telegram.enabled"),
        schedule_hint="nos dias da carteira, dentro da janela configurada",
        module="gaia/briefing/market.py, gaia/finance/",
        tags=("financas", "notificacao"),
    ),
    Skill(
        id="briefing",
        name="Briefings do dia",
        description=(
            "Bom dia com tarefas e habitos, resumo do meio-dia e fecho da noite "
            "com o que foi feito e o que ficou."
        ),
        kind="routine",
        requires=("briefing.enabled", "telegram.enabled"),
        schedule_hint="manha, meio-dia e noite (horarios no config)",
        module="gaia/controller/briefing.py, gaia/briefing/compose.py",
        tags=("rotina", "notificacao"),
    ),
    Skill(
        id="agenda",
        name="Assistente de agenda",
        description=(
            "Le a Agenda (Supabase), avisa sobre tarefas pendentes e sincroniza a "
            "pagina de tarefas para o agente."
        ),
        kind="routine",
        requires=("agenda.enabled",),
        provides=("agenda_*",),
        schedule_hint="polling continuo (intervalo configurado)",
        module="gaia/agenda/, gaia/agent/tools/agenda.py",
        tags=("agenda", "ferramentas"),
    ),
    Skill(
        id="estudos",
        name="Concursos e ABIN Suite",
        description=(
            "Acompanha o progresso dos simulados no Abin Suite e cobra o estudo "
            "quando a meta da semana esta atrasada."
        ),
        kind="routine",
        requires=("estudos.enabled", "estudos.remind_when_late"),
        provides=("estudos_*",),
        schedule_hint="nos dias de estudo, a partir do horario de cobranca",
        module="gaia/estudos/, gaia/agent/tools/estudos.py",
        tags=("estudos", "concursos", "ferramentas"),
    ),
    Skill(
        id="memory",
        name="Memoria consolidada",
        description=(
            "Consolida os turnos do dia numa nota do Second Brain, com resumo "
            "local ou pelo modelo."
        ),
        kind="routine",
        requires=("memory.enabled",),
        schedule_hint="diaria, no horario configurado",
        module="gaia/controller/memory.py, gaia/memory/consolidation.py",
        tags=("memoria",),
    ),
    Skill(
        id="dream",
        name="Sonho noturno",
        description=(
            "Cruza a memoria do dia com notas antigas e escreve uma nota de sonho "
            "na madrugada; avisa no dia seguinte."
        ),
        kind="routine",
        requires=("memory.enabled", "memory.dream_enabled"),
        schedule_hint="janela da madrugada (horario configuravel)",
        module="gaia/memory/dream.py",
        tags=("memoria",),
    ),
    Skill(
        id="initiative",
        name="Iniciativa proativa",
        description=(
            "Saudacao, lembretes, check-in de ociosidade e foco: o agente fala "
            "sem ser chamado, respeitando quiet hours e limites diarios."
        ),
        kind="routine",
        requires=("proactive.enabled",),
        schedule_hint="ciclo continuo (intervalo configurado)",
        module="gaia/controller/initiative.py",
        tags=("proatividade",),
    ),
    Skill(
        id="audiobook",
        name="Audiobook",
        description=(
            "Converte PDF/texto da pasta inbox em MP3 com capitulos, retomando "
            "trechos ja narrados apos uma queda."
        ),
        kind="tool_group",
        requires=("audiobook.enabled",),
        provides=("audiobook",),
        module="gaia/audiobook/",
        tags=("audio", "ferramentas"),
    ),
    Skill(
        id="automations",
        name="Automacoes criadas pelo chat",
        description=(
            "Cria automacao recorrente (lembrete, resumo da agenda, resumo de "
            "estudos) a partir de uma frase, com confianca medida por regras. "
            "O agendador executa pelo id `user:*` da tabela `automations`."
        ),
        kind="tool_group",
        # Sem estes gates a automacao nasce mas nunca executa: o handler checa
        # os mesmos caminhos em `_user_automation_*`.
        requires=("agent.tools",),
        provides=("criar_automacao",),
        module="gaia/controller/automation_builder.py, gaia/agent/tools/automations.py",
        tags=("automacao", "ferramentas"),
    ),
    Skill(
        id="planilhas",
        name="Planilhas",
        description="Le e escreve planilhas dentro do Second Brain.",
        kind="tool_group",
        requires=(),
        provides=("planilha_*",),
        module="gaia/agent/tools/planilhas.py",
        tags=("ferramentas",),
    ),
    Skill(
        id="tools",
        name="Ferramentas do sistema",
        description=(
            "Leitura, escrita, edicao, busca e shell. write/edit/bash exigem "
            "aprovacao, salvo auto_approve explicito."
        ),
        kind="tool_group",
        requires=("agent.tools",),
        provides=("read", "write", "edit", "list", "glob", "grep", "bash"),
        module="gaia/agent/tools/",
        tags=("sistema", "ferramentas"),
    ),
)


def _get(cfg: Config, dotted: str) -> Any:
    atual: Any = cfg
    for parte in dotted.split("."):
        atual = getattr(atual, parte, None)
        if atual is None:
            return None
    return atual


def _env_present(names: tuple[str, ...]) -> bool:
    import os

    return any(os.environ.get(name, "").strip() for name in names)


def skill_status(cfg: Config, skill: Skill) -> tuple[str, str]:
    """Devolve ``(status, motivo)``: active | disabled | error."""
    for caminho in skill.requires:
        valor = _get(cfg, caminho)
        if valor is False:
            return "disabled", f"{caminho} = false"
    if skill.needs_env and not _env_present(skill.needs_env):
        return "error", f"credencial ausente: {', '.join(skill.needs_env)}"
    # Rotinas que entregam no Telegram sem bot configurado nao funcionam, mas
    # estao ligadas: e erro de configuracao, nao desligamento.
    if skill.kind == "routine" and "notificacao" in skill.tags:
        if not cfg.telegram.enabled or not cfg.telegram.bot_token.strip():
            return "error", "telegram desligado ou sem bot_token"
    return "active", ""


def describe_skills(
    cfg: Config, store: MemoryStore | None = None
) -> list[dict[str, Any]]:
    """Catalogo pronto para o painel, com status e ultima execucao."""
    itens: list[dict[str, Any]] = []
    for skill in SKILLS:
        status, motivo = skill_status(cfg, skill)
        ultima = None
        if store is not None and skill.kind == "routine":
            linha = store.last_run(skill.id)
            if linha is not None:
                ultima = {
                    "id": int(linha.get("id") or 0),
                    "status": str(linha.get("status") or ""),
                    "started_at": str(linha.get("started_at") or ""),
                    "duration_ms": int(linha.get("duration_ms") or 0),
                    "detail": str(linha.get("detail") or ""),
                    "error": str(linha.get("error") or ""),
                    "error_kind": str(linha.get("error_kind") or ""),
                }
        itens.append(
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "kind": skill.kind,
                "status": status,
                "reason": motivo,
                "requires": list(skill.requires),
                "needs_env": list(skill.needs_env),
                "provides": list(skill.provides),
                "schedule_hint": skill.schedule_hint,
                "module": skill.module,
                "tags": list(skill.tags),
                "last_run": ultima,
            }
        )
    return itens


def find_skill(skill_id: str) -> Skill | None:
    for skill in SKILLS:
        if skill.id == skill_id:
            return skill
    return None
