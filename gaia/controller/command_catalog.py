"""Catalogo dos comandos de barra, numa fonte so.

A ajuda do terminal, a lista que a pessoa ve ao digitar ``/`` no web e a rota
``GET /commands`` leem daqui. O despacho continua em
``controller/commands.py``; este modulo e so a descricao publica.

Regra: todo ``names`` daqui precisa aparecer no ``_handle_command``
(``tests/test_command_catalog.py`` compara com o codigo via AST). Comando novo
entra nos dois lugares na mesma mudanca.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    usage: str
    summary: str
    names: tuple[str, ...]
    # Comando que aceita argumento: a paleta do web deixa um espaco depois do
    # nome para a pessoa digitar.
    takes_arg: bool = False


COMMANDS: tuple[Command, ...] = (
    Command("/ajuda", "mostra esta ajuda", ("/ajuda", "/help")),
    Command("/status", "servidor, sessão, modelo, memória e voz", ("/status",)),
    Command(
        "/tokens",
        "consumo de tokens por turno e total da sessão",
        ("/tokens", "/consumo"),
    ),
    Command("/buscar <termo>", "pesquisa no Second Brain", ("/buscar",), True),
    Command("/nota <título>", "lê uma nota do Second Brain", ("/nota",), True),
    Command("/indexar", "reindexa o Second Brain", ("/indexar",)),
    Command(
        "/novo · /nova",
        "inicia nova conversa e sessão do agente",
        ("/novo", "/nova"),
    ),
    Command(
        "/modelo [número|provider/model|atalho]",
        "lista/troca o modelo; atalhos cc, ds, glm, turbo, zen (F6 no app)",
        ("/modelo", "/modelos", "/model"),
        True,
    ),
    Command(
        "/bot [nome]",
        "lista ou troca o bot ativo (gaia, donna, ariana, sabrina, ada)",
        ("/bot", "/bots"),
        True,
    ),
    Command(
        "/modo [nome]",
        "alias antigo de /bot (mesmos bots, apelidos e chaves antigas)",
        ("/modo", "/perfil", "/persona"),
        True,
    ),
    Command(
        "/bolsa <ticker>",
        "cotação rápida (ex.: PETR4, AAPL)",
        ("/bolsa", "/acao", "/ações"),
        True,
    ),
    Command(
        "/carteira [add|remover]",
        "lista/acompanha seus ativos",
        ("/carteira", "/watchlist", "/watch"),
        True,
    ),
    Command(
        "/metas",
        "painel visual com foco atual e metas de longo prazo",
        ("/metas", "/objetivos"),
    ),
    Command("/painel", "mesmo painel de metas", ("/painel",)),
    Command(
        "/foco [definir|limpar]",
        "define/limpa o foco do ciclo",
        ("/foco", "/focos"),
        True,
    ),
    Command(
        "/contexto on|off",
        "liga/desliga contexto automático do Second Brain",
        ("/contexto",),
        True,
    ),
    Command(
        "/rapido on|off",
        "modo rápido (sem contexto, respostas curtas)",
        ("/rapido", "/rápido", "/fast"),
        True,
    ),
    Command(
        "/chines on|off|en|fr",
        "eco de tradução embaixo de cada mensagem (chinês, inglês ou francês)",
        ("/chines", "/chinês", "/中文"),
        True,
    ),
    Command(
        "/pensando on|off",
        "mostra (ou esconde) o raciocínio do modelo na tela",
        ("/pensando", "/pensar", "/thinking", "/raciocinio", "/raciocínio"),
        True,
    ),
    Command(
        "/tarefas [sync|limpar]",
        "lista/sincroniza/limpa tarefas concluídas",
        ("/tarefas", "/tarefa"),
        True,
    ),
    Command(
        "/backlog [sync|add|run|repo|remover]",
        "card do backlog: sincroniza a Agenda, adiciona, define o repositorio e despacha itens ao pi",
        ("/backlog",),
        True,
    ),
    Command("/voz on|off|edge|say|none", "controla a voz", ("/voz",), True),
    Command(
        "/audiobook <arquivo> [título]",
        "narra um PDF/TXT em MP3",
        ("/audiobook", "/audiolivro"),
        True,
    ),
    Command(
        "/audiobooks",
        "narra todos os PDFs da pasta de audiobooks",
        ("/audiobooks", "/audiolivros"),
    ),
    Command(
        "/preferencia <texto>",
        "aprende uma preferência (taste)",
        ("/preferencia", "/preferência"),
        True,
    ),
    Command(
        "/preferencias",
        "mostra as preferências aprendidas",
        ("/preferencias", "/preferências"),
    ),
    Command(
        "/consolidar [hoje|ontem|AAAA-MM-DD]",
        "salva a memória do dia no Second Brain",
        ("/consolidar", "/memoria", "/memória"),
        True,
    ),
    Command(
        "/lembrar <texto> em <tempo>",
        "cria um lembrete falado",
        ("/lembrar", "/lembrete"),
        True,
    ),
    Command("/lembretes", "lista lembretes pendentes", ("/lembretes",)),
    Command("/cancelar <id>", "cancela um lembrete", ("/cancelar",), True),
    Command(
        "/proativo on|off",
        "liga/desliga iniciativa da Gaia",
        ("/proativo",),
        True,
    ),
    Command(
        "/continuo on|off",
        "escuta contínua (sem Enter a cada fala)",
        ("/continuo", "/contínuo"),
        True,
    ),
    Command("/fala", "volta para a escuta contínua por voz", ("/fala", "/ouvir")),
    Command("/texto", "volta para o modo texto (digitar)", ("/texto", "/digitar")),
    Command(
        "/confirmar on|off",
        "pede (ou não) confirmação ao criar nota",
        ("/confirmar",),
        True,
    ),
    Command(
        "/autoaprovar on|off",
        "auto-aprova ferramentas do agente (perigoso)",
        ("/autoaprovar", "/autoapprove"),
        True,
    ),
    Command("/interromper", "aborta a execução atual do agente", ("/interromper",)),
    Command("/sonhar", "gera o sonho do dia", ("/sonhar", "/sonho")),
    Command("/historico", "mostra os últimos turnos da conversa", ("/historico",)),
    Command("/sessao", "mostra o ID da sessão do agente", ("/sessao",)),
    Command("/reiniciar", "reinicia a Gaia com o código novo", ("/reiniciar", "/restart")),
    Command("/sair", "encerra a Gaia", ("/sair", "/quit", "/exit")),
)


def as_rows() -> list[tuple[str, str]]:
    """Pares ``(sintaxe, descricao)`` para a tabela da ajuda do terminal."""
    return [(command.usage, command.summary) for command in COMMANDS]


def as_dicts() -> list[dict[str, object]]:
    """JSON de ``GET /commands``: a paleta do web filtra por ``names``."""
    return [
        {
            "usage": command.usage,
            "summary": command.summary,
            "names": list(command.names),
            "argument": command.takes_arg,
        }
        for command in COMMANDS
    ]
