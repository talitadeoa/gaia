"""Registry dos bots especialistas: descritor declarativo, nao processo.

Um bot e configuracao + estado (`conversations.mode` = `bot.id`), nunca uma
unidade de deploy: o `gaia serve` continua sendo o unico escritor do banco, do
Second Brain e dos workers proativos.

As quatro camadas de isolamento (design, §4.2) sao: prompt (`directive`),
allowlist (`tools`), workspace/escopo (`workspace` + `scope`) e conversa
(`mode`). Este modulo e so a primeira e a descricao das outras tres; quem
aplica a allowlist e o escopo e o `AgentClient` (via `SessionPolicy`).

Regra de validacao, em dois niveis (o codigo vence a doc quando divergem):

* **Erro duro** (`ValueError` no `load_bots`): o que o TOML **declarou** e
  esta errado -- bot com id desconhecido, ferramenta inexistente,
  `can_message` para id que nao existe, `scope` que nao e caminho
  absoluto/nao existe/nao contem o `workspace`, `workspace` inexistente.
  Configuracao explicita errada nao sobe "meio ligada".
* **Indisponivel** (`bot_problem` != ""): o default do codigo aponta para um
  caminho que esta maquina ainda nao tem (ex.: `~/dissertacao` antes do clone).
  O bot continua no registry (a tela mostra "indisponivel" e o motivo) e
  `select_bot` recusa com mensagem clara -- em vez de derrubar a Gaia inteira
  por causa de um bot opcional que ainda nao tem pasta.

Quando o passo operacional da fase 1 (clone da dissertacao) for feito, a Donna
sai de "indisponivel" sem mudar uma linha de codigo.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# Ferramentas que o motor sabe executar (guardado por tests/test_bots.py:
# se uma ferramenta nascer sem entrar aqui, o teste falha e a allowlist de um
# bot nunca aponta para o vazio).
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "edit",
        "list",
        "glob",
        "grep",
        "bash",
        "pi",
        "planilha",
        "audiobook",
        "post_x",
        "criar_automacao",
        "agenda_dia",
        "agenda_rotinas",
        "agenda_paginas",
        "agenda_nova_tarefa",
        "agenda_marcar_tarefa",
        "estudos_metas",
        "estudos_pendentes",
        "estudos_progresso",
        "falar_com_bot",
    }
)

# Ferramentas de leitura/escrita do repositorio, usadas por quase todo bot.
_FS = ("read", "list", "glob", "grep")


@dataclass(frozen=True)
class Bot:
    """Identidade, area de trabalho e capacidades de um bot.

    ``tools=None`` = todas as ferramentas registradas (o catalogo do processo);
    tupla vazia seria "nenhuma" e nao e um caso de uso. ``workspace`` e o cwd
    de ``bash``/``pi`` e a raiz dos caminhos relativos; ``scope`` e o teto de
    leitura/escrita do filesystem e pode ser mais largo que o workspace (a
    Donna trabalha num clone e le o Second Brain).
    """

    id: str
    name: str
    role: str
    directive: str
    workspace: str
    tools: tuple[str, ...] | None = None
    scope: str = ""
    model: str = ""
    aliases: tuple[str, ...] = ()
    can_message: tuple[str, ...] = ()


def normalize_key(value: str) -> str:
    """Chave de comparacao: minuscula, sem acento e sem espaco nas pontas."""
    text = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _default_bot(id: str, **kwargs: Any) -> Bot:
    return Bot(id=id, **kwargs)


def default_bots(project_dir: str = "", data_dir: str = "") -> dict[str, Bot]:
    """Os cinco bots do codigo (a semente; o TOML e a decisao).

    ``project_dir`` e o diretorio do repo da Gaia (workspace da Gaia) e
    ``data_dir`` o ``GAIA_DATA_DIR`` (workspace da Ariana). Vazios caem no
    diretorio atual / no caminho padrao de dados.
    """
    projeto = str(project_dir or Path.cwd())
    dados = str(data_dir or "~/.local/share/gaia")
    return {
        "gaia": _default_bot(
            "gaia",
            name="Gaia",
            role="agenda, rotinas, prioridades e conversa",
            directive=(
                "Você é a Gaia da Talita. Ajude a organizar agenda, rotinas, "
                "compromissos, lembretes e prioridades. Para consultar ou alterar a "
                "Agenda use as ferramentas agenda_* (agenda_dia, agenda_rotinas, "
                "agenda_paginas, agenda_nova_tarefa, agenda_marcar_tarefa) — não use "
                "bash nem leia arquivos de credencial para isso. Confirme antes de "
                "mudanças destrutivas. Converse de forma informal, acolhedora, "
                "carinhosa, espontânea e íntima, sem formalidade excessiva e sem soar "
                "professoral. Escute, faça perguntas genuínas, ofereça companhia e "
                "evite transformar tudo em tarefa. Respeite limites e não pressione "
                "por assuntos sensíveis."
            ),
            workspace=projeto,
            tools=None,
            # A orquestradora fala com todos: e ela que encaminha o assunto de
            # outra area para o bot certo (design §4.6).
            can_message=("donna", "ariana", "sabrina", "ada"),
            aliases=(
                "assistente",
                "assistant",
                "agenda",
                "conversa",
                "conversar",
                "informal",
            ),
        ),
        "donna": _default_bot(
            "donna",
            name="Donna Haraway",
            role="dissertação, ensaios, revisão e método",
            directive=(
                "Você é a Donna Haraway da Talita. Ajude com a pesquisa e o "
                "mestrado em Antropologia Social (PPGAS/UFRN), leitura e fichamento "
                "de textos, escrita, metodologia, revisão bibliográfica, organização "
                "de argumentos e preparação de apresentações. O repositório da "
                "dissertação fica em ~/dissertacao (git + LaTeX): trabalhe nos "
                "capítulos, cite fontes e faça commit das mudanças que a Talita "
                "aprovar. Priorize rigor conceitual, clareza e referências "
                "confiáveis; sinalize incertezas e nunca invente citações. Use o "
                "Second Brain para recuperar notas quando a leitura atravessar a "
                "pesquisa."
            ),
            workspace="~/dissertacao",
            # Teto do filesystem. O clone mora FORA do Second Brain (o reindex
            # varreria o .git e os builds de LaTeX); sem symlink, o escopo e o
            # proprio clone. Com symlink, troque para ~/SecondBrain no TOML.
            scope="~/dissertacao",
            tools=("read", "list", "glob", "grep", "write", "edit", "bash", "pi"),
            aliases=(
                "pesquisadora",
                "pesquisador",
                "pesquisa",
                "academica",
                "academico",
                "mestrado",
                "haraway",
            ),
            # A Donna responde a orquestradora (e so a ela).
            can_message=("gaia",),
        ),
        "ariana": _default_bot(
            "ariana",
            name="Ariana Grande",
            role="bolsa, carteira e alertas",
            directive=(
                "Você é a Ariana Grande da Talita. Acompanhe informações de "
                "mercado, analise ações e indicadores, explique risco e contexto, e "
                "ajude com orçamento e investimentos. Seja clara, objetiva e "
                "responsável: não prometa retorno, sinalize riscos e nunca trate a "
                "análise como recomendação definitiva de compra ou venda. Ensine o "
                "raciocínio e ajude a Talita a decidir com consciência. O resumo "
                "periódico da carteira é determinístico (sem modelo); aqui você só "
                "explica e conversa."
            ),
            workspace=dados,
            # Dados da Gaia (watchlist, planilhas): e o unico lugar que ela le.
            scope=dados,
            # Determinismo: o laco da carteira nao chama o modelo (design §4.5a).
            # `bolsa`/`carteira` sao comandos, nao ferramentas.
            tools=("read", "planilha"),
            aliases=(
                "investidora",
                "investidor",
                "financas",
                "financeiro",
                "financeira",
                "bolsa",
                "investimentos",
                "acoes",
            ),
        ),
        "sabrina": _default_bot(
            "sabrina",
            name="Sabrina Carpenter",
            role="vagas, currículo e carta",
            directive=(
                "Você é a Sabrina Carpenter da Talita. Ajude a conseguir trabalho "
                "em tecnologia, dados, automações ou qualidade de dados; melhore "
                "currículo e portfólio, encontre vagas, adapte candidaturas, prepare "
                "entrevistas e negocie propostas. Seja prática e direta, proponha "
                "próximos passos concretos e prazos. O CV vive em ~/SecondBrain/cv e é "
                "LaTeX: a base cv-pt.tex é intocável — para cada empresa crie "
                "cv-pt-<empresa>.tex a partir dela, compile com pdflatex pelo bash "
                "(aprovado a cada turno) e nunca edite a base nem invente experiência, "
                "projeto ou formação: use só o que está nos arquivos. "
                "Quando o assunto for buscar ou avaliar vaga, siga "
                "~/SecondBrain/cv/playbook-vagas.md (fontes, filtros e rubrica de "
                "score): leia o arquivo antes de opinar e mantenha as vagas em "
                "~/gaia/data/career/state.json."
            ),
            workspace="~/SecondBrain/cv",
            scope="~/SecondBrain/cv",
            # `career` NAO e ferramenta: a rotina semanal de vagas e deterministica
            # e roda pelo agendador (fase 3). No chat ela usa leitura + escrita do
            # CV e `bash` para gerar o PDF com pdflatex.
            tools=("read", "list", "glob", "grep", "write", "bash"),
            aliases=(
                "trabalhadora",
                "trabalhador",
                "trabalho",
                "carreira",
                "emprego",
                "carpenter",
            ),
        ),
        "ada": _default_bot(
            "ada",
            name="Ada Lovelace",
            role="backlog, código e pull request",
            directive=(
                "Você é a Ada Lovelace da Talita. Cuide do backlog e do código: "
                "entenda o item, investigue no repositório certo, explique o plano e "
                "acompanhe o PR. Você trabalha em clones sob ~/pr-workspace, NUNCA "
                "no repositório ~/gaia (o deploy faz reset --hard a cada cinco "
                "minutos e apagaria o trabalho). Delegue implementação longa à "
                "ferramenta pi quando ela estiver disponível. Nunca faça merge: o PR "
                "é aberto e a revisão é humana."
            ),
            workspace="~/pr-workspace",
            # Nunca ~/gaia: o deploy faz `reset --hard` a cada cinco minutos.
            scope="~/pr-workspace",
            tools=("read", "write", "edit", "bash", "grep", "glob", "pi"),
            aliases=("programadora", "backlog", "codigo", "code", "lovelace"),
        ),
    }


DEFAULT_BOTS: dict[str, Bot] = default_bots()


def _path(value: str) -> Path | None:
    texto = (value or "").strip()
    if not texto:
        return None
    try:
        return Path(texto).expanduser().resolve()
    except OSError:  # pragma: no cover - defensivo
        return None


def bot_problem(bot: Bot, registry: dict[str, Bot] | None = None) -> str:
    """Motivo pelo qual o bot nao pode rodar agora (``""`` = pode).

    Checa o que e caro descobrir em tempo de execucao: ferramenta que nao
    existe, destino de `can_message` desconhecido, workspace inexistente e
    escopo que nao contem o workspace (bot assim escreve e nao consegue ler o
    que escreveu).
    """
    conhecidos = registry if registry is not None else DEFAULT_BOTS
    problemas: list[str] = []
    if bot.tools is not None:
        desconhecidas = sorted({nome for nome in bot.tools if nome not in KNOWN_TOOLS})
        if desconhecidas:
            problemas.append(
                "ferramenta desconhecida: " + ", ".join(desconhecidas)
            )
    destinos = sorted({d for d in bot.can_message if d not in conhecidos})
    if destinos:
        problemas.append("can_message para bot desconhecido: " + ", ".join(destinos))
    if bot.id in bot.can_message:
        problemas.append("can_message para si mesmo")

    workspace = _path(bot.workspace)
    if not bot.workspace.strip():
        problemas.append("workspace vazio")
    elif workspace is None or not workspace.is_dir():
        problemas.append(f"workspace inexistente: {bot.workspace}")

    if bot.scope.strip():
        escopo = _path(bot.scope)
        bruto = Path(bot.scope).expanduser()
        if not bruto.is_absolute():
            problemas.append(f"scope precisa ser caminho absoluto: {bot.scope}")
        elif escopo is None or not escopo.is_dir():
            problemas.append(f"scope inexistente: {bot.scope}")
        elif workspace is not None:
            try:
                workspace.relative_to(escopo)
            except ValueError:
                problemas.append(
                    f"scope {bot.scope} nao contem o workspace {bot.workspace}"
                )
    return "; ".join(problemas)


def _apply_override(bot: Bot, override: Any) -> Bot:
    """Mescla um `BotConfig` do TOML sobre o bot do codigo (campo vazio = herda)."""
    dados: dict[str, Any] = {}
    for campo in ("name", "role", "directive", "workspace", "scope", "model"):
        valor = getattr(override, campo, "")
        if isinstance(valor, str) and valor.strip():
            dados[campo] = valor.strip()
    for campo in ("tools", "aliases", "can_message"):
        valor = getattr(override, campo, None)
        if valor is not None:
            dados[campo] = tuple(str(item).strip() for item in valor if str(item).strip())
    return replace(bot, **dados)


def load_bots(cfg: Any = None) -> dict[str, Bot]:
    """Registry efetivo: defaults do codigo + `[bots.<id>]` do TOML.

    Configuracao explicita invalida levanta ``ValueError`` (o boot/`select_bot`
    mostra a mensagem). O default que aponta para pasta inexistente **nao**
    derruba o processo: o bot fica marcado indisponivel por `bot_problem`.
    """
    projeto = ""
    dados = ""
    if cfg is not None:
        try:
            projeto = str(cfg.project_directory())
        except Exception:  # pragma: no cover - config parcial em teste
            projeto = ""
        resolvedor = getattr(cfg, "resolved_data_dir", None)
        if callable(resolvedor):
            try:
                dados = str(resolvedor())
            except Exception:  # pragma: no cover - config parcial em teste
                dados = ""

    bots = default_bots(projeto, dados)
    overrides = dict(getattr(cfg, "bots", None) or {})
    desconhecidos = sorted(
        chave for chave in overrides if normalize_key(str(chave)) not in bots
    )
    if desconhecidos:
        raise ValueError(
            "bot desconhecido no config: "
            + ", ".join(desconhecidos)
            + f" (ids: {', '.join(bots)})"
        )
    for chave, override in overrides.items():
        bot_id = normalize_key(str(chave))
        atualizado = _apply_override(bots[bot_id], override)
        problema = _override_problem(atualizado, override, bots)
        if problema:
            raise ValueError(f"bot '{bot_id}' mal configurado: {problema}")
        bots[bot_id] = atualizado
    return bots


def _declarado(override: Any, campo: str) -> bool:
    """O TOML disse algo para este campo? (vazio/ausente = herda o codigo)."""
    if campo in {"tools", "aliases", "can_message"}:
        return getattr(override, campo, None) is not None
    valor = getattr(override, campo, "")
    return isinstance(valor, str) and bool(valor.strip())


def _override_problem(
    bot: Bot, override: Any, registry: dict[str, Bot]
) -> str:
    """Valida o que o TOML **declarou** (o herdado do codigo nao derruba o boot).

    A distincao importa: configuracao explicita errada e contrato quebrado
    (erro duro no boot); o default do codigo apontando para uma pasta que esta
    maquina ainda nao tem e só indisponibilidade (`bot_problem`), porque ela
    pode aparecer depois (ex.: o clone da dissertacao) sem mexer no config.
    """
    problemas: list[str] = []
    if _declarado(override, "tools") and bot.tools is not None:
        desconhecidas = sorted({nome for nome in bot.tools if nome not in KNOWN_TOOLS})
        if desconhecidas:
            problemas.append("ferramenta desconhecida: " + ", ".join(desconhecidas))
    if _declarado(override, "can_message"):
        destinos = sorted({d for d in bot.can_message if d not in registry})
        if destinos:
            problemas.append(
                "can_message para bot desconhecido: " + ", ".join(destinos)
            )
        if bot.id in bot.can_message:
            problemas.append("can_message para si mesmo")

    workspace_declarado = _declarado(override, "workspace")
    if workspace_declarado:
        alvo = _path(bot.workspace)
        if alvo is None or not alvo.is_dir():
            problemas.append(f"workspace inexistente: {bot.workspace}")

    scope_declarado = _declarado(override, "scope")
    if scope_declarado:
        escopo = _path(bot.scope)
        if not Path(bot.scope).expanduser().is_absolute():
            problemas.append(f"scope precisa ser caminho absoluto: {bot.scope}")
        elif escopo is None or not escopo.is_dir():
            problemas.append(f"scope inexistente: {bot.scope}")
    if scope_declarado or workspace_declarado:
        # `scope` que nao contem o workspace deixa o bot sem ler o que ele
        # mesmo escreve; so da para checar quando os dois caminhos existem.
        escopo = _path(bot.scope) if bot.scope.strip() else None
        alvo = _path(bot.workspace) if bot.workspace.strip() else None
        if escopo is not None and alvo is not None and escopo.is_dir() and alvo.is_dir():
            try:
                alvo.relative_to(escopo)
            except ValueError:
                problemas.append(
                    f"scope {bot.scope} nao contem o workspace {bot.workspace}"
                )
    return "; ".join(problemas)


def bot_aliases(registry: dict[str, Bot] | None = None) -> dict[str, str]:
    """Mapa apelido normalizado -> id do bot (inclui o proprio id)."""
    bots = registry if registry is not None else DEFAULT_BOTS
    alias: dict[str, str] = {}
    for bot_id, bot in bots.items():
        alias[normalize_key(bot_id)] = bot_id
        alias[normalize_key(bot.name)] = bot_id
        for nome in bot.aliases:
            alias[normalize_key(nome)] = bot_id
    return alias


def get_bot(key: str | None, registry: dict[str, Bot] | None = None) -> Bot | None:
    """Bot pelo id, pelo nome ou por um apelido antigo (``None`` = desconhecido)."""
    if not key:
        return None
    bots = registry if registry is not None else DEFAULT_BOTS
    return bots.get(bot_aliases(bots).get(normalize_key(str(key)), ""))


def require_bot(key: str, registry: dict[str, Bot] | None = None) -> Bot:
    """Igual ao `get_bot`, mas levanta ``ValueError`` com os ids disponiveis."""
    bot = get_bot(key, registry)
    if bot is None:
        bots = registry if registry is not None else DEFAULT_BOTS
        raise ValueError(f"bot desconhecido: {key} (ids: {', '.join(bots)})")
    return bot


def persona_aliases(registry: dict[str, Bot] | None = None) -> dict[str, str]:
    """Alias antigos de modo -> id de bot (compatibilidade do disco).

    Tudo que `controller/modes.py` aceitava como chave de persona continua
    resolvendo: `assistente`->`gaia`, `pesquisadora`->`donna`,
    `trabalhadora`->`sabrina`, `investidora`->`ariana`.
    """
    return dict(bot_aliases(registry))


class BotIndisponivel(RuntimeError):
    """Bot bem configurado, mas dependente de algo que esta maquina nao tem.

    E o caso da Donna antes do clone de `~/dissertacao`: o registry recusa
    ativa-la, mas a Gaia continua de pe e os outros bots seguem funcionando.
    O transporte traduz em 409 (a configuracao esta certa; o ambiente nao).
    """
