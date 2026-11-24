"""Monta uma automacao a partir de uma frase, com confianca medida por regras.

O pedido chega em linguagem natural ("toda sexta as 9h me lembre de estudar") e
sai como uma linha da tabela ``automations`` (id ``user:*``). Quem decide se o
entendimento foi bom **nao** e o modelo: e o mesmo parser deterministico que
extrai os slots (dias, hora, skill alvo, entrega, parametros) e conta quantos
deles vieram explicitos.

Tres faixas de confianca (0 a 10):

* alta (>= 7): cria ativada -- a frase disse tudo o que o agendador precisa;
* media (4-6): cria **pausada** e pede confirmacao (o checkbox do painel liga);
* baixa (< 4): nao cria nada e pede para reformular (falta skill alvo ou hora).

Seguranca (v1):

* a skill alvo tem de estar ligada na config;
* horario tem de ser HH:MM valido (00:00-23:59);
* no maximo ``MAX_PER_DAY`` criacoes automaticas por dia (chaves em ``meta``);
* nunca edita nem apaga automacao existente: pedido repetido devolve a que ja
  existe e o painel e o unico lugar de pausar/editar.

Nada aqui fala com o agente, com a rede ou com relogio que nao seja o de
``now``: ``propose_automation`` e testavel sem provedor.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gaia.config import Config
from gaia.memory.store import MemoryStore

# Teto diario de criacoes automaticas. Contar por chaves em ``meta`` (uma por
# automacao criada) em vez de um contador evita contar duas vezes o mesmo
# pedido e sobrevive a um crash no meio.
MAX_PER_DAY = 10

# ---- skills alvo (v1) ------------------------------------------------------
SKILL_REMINDER = "reminder"
SKILL_AGENDA = "agenda_summary"
SKILL_ESTUDOS = "estudos_summary"

# skill -> caminho de config que precisa estar ligado (``requires`` da skill).
SKILL_REQUIRES: dict[str, str] = {
    SKILL_REMINDER: "proactive.reminders",
    SKILL_AGENDA: "agenda.enabled",
    SKILL_ESTUDOS: "estudos.enabled",
}

SKILL_NAMES: dict[str, str] = {
    SKILL_REMINDER: "Lembrete",
    SKILL_AGENDA: "Resumo da agenda",
    SKILL_ESTUDOS: "Resumo de estudos",
}

# ---- dias ------------------------------------------------------------------
DAY_NAMES = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
DAY_SHORT = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")

# Do mais longo para o mais curto: sem isso "domingo" casaria "dom".
_WD_WORD = (
    r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo"
    r"|seg|ter|qua|qui|sex|sab|dom)"
)
_WD_RE = re.compile(r"\b" + _WD_WORD + r"s?\b")
_WD_INDEX = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
    "seg": 0,
    "ter": 1,
    "qua": 2,
    "qui": 3,
    "sex": 4,
    "sab": 5,
    "dom": 6,
}
_ALL_DAYS_RE = re.compile(
    r"\b(?:(?:todos\s+os\s+dias|todo\s+dia|diariamente|diario)(?:\s+(?:as|pela(s)?))?)\b"
)
_WEEKDAYS_RE = re.compile(
    r"\b(?:dias\s+uteis|dia\s+de\s+semana|durante\s+a\s+semana)\b"
)
_WEEKEND_RE = re.compile(r"\b(?:fim|final)\s+de\s+semana\b")
# "de segunda a sexta" e um intervalo: so juntar os dias citados deixaria
# segunda e sexta (buraco no meio).
_DAY_RANGE_RE = re.compile(
    r"\b(?:de|da)\s+(" + _WD_WORD + r")\s+a(?:te|s)?\s+(" + _WD_WORD + r")\b"
)

# ---- hora ------------------------------------------------------------------
_COLON_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_HOUR_RE = re.compile(r"\b([01]?\d|2[0-3])\s*h(?:oras?)?\b(?:\s*([0-5]\d))?")
_AS_HOUR_RE = re.compile(
    r"\b(?:as|pelas|por\s+volta\s+das)\s+([01]?\d|2[0-3])\b(?:\s*(?:horas?|h))?"
)
_NOON_RE = re.compile(r"\bmeio[\s-]?dia\b")
_MIDNIGHT_RE = re.compile(r"\bmeia[\s-]?noite\b")
_PM_HINT_RE = re.compile(r"\b(?:da|a|de)\s+(?:tarde|noite)\b")
# A expressao "9h da noite" e um bloco so: a limpeza do rotulo precisa levar
# junto o qualificador, senao o lembrete comeca com "noite de sexta...".
_PM_BLOCK_RE = re.compile(
    r"\b(?:as|pelas|por\s+volta\s+das)\s+([01]?\d|2[0-3])\s*h?(?:\s*:\s*[0-5]\d)?"
    r"\s*(?:da|a|de)\s+(?:tarde|noite)\b"
)

# ---- alvo ------------------------------------------------------------------
_REMINDER_RE = re.compile(r"\b(?:lembr(?:e|ar|ete|ete-se)|lembrete|avis(?:e|ar)|avisa)\b")
_AGENDA_RE = re.compile(r"\bagend(?:a|as|inha)\b")
_ESTUDOS_RE = re.compile(r"\b(?:estud(?:o|ar|os)|simulad(?:o|os)|abin)\b")
_SUMMARY_HINT_RE = re.compile(r"\b(?:resum(?:o|e|a|ir)|manda(?:r)?|envia(?:r)?|manda)\b")

# ---- limpeza do rotulo -----------------------------------------------------
_REMINDER_PREFIX_RE = re.compile(
    r"\b(?:me\s+)?(?:lembr(?:e|ar|ete)|lembrete|avis(?:e|ar)|avisa)\b"
    r"(?:\s+(?:de|que|para|pra|sobre))?"
)
_DELIVERY_TELEGRAM_RE = re.compile(r"\b(?:no|pelo|via|por)\s+telegram\b|\btelegram\b")
_DELIVERY_LOCAL_RE = re.compile(
    r"\b(?:por\s+)?aqui\b|\bno\s+chat\b|\bnesta\s+conversa\b|\bsem\s+enviar\b"
)
_FILLER_WORDS = frozenset(
    {
        "a",
        "ao",
        "aos",
        "as",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "me",
        "my",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "para",
        "pelas",
        "pelo",
        "por",
        "pra",
        "que",
        "sobre",
        "toda",
        "todas",
        "todo",
        "todos",
        "por favor",
    }
)


def _normalize(text: str) -> str:
    """Minusculas sem acento, mesma quantidade de caracteres do original.

    MANTER o comprimento e o que permite recortar o rotulo no texto original a
    partir dos indices encontrados no normalizado ("estudar" continua na mesma
    posicao depois de tirar o acento de "estudá-lo").
    """
    decomposto = unicodedata.normalize("NFKD", text)
    sem_acento = "".join(ch for ch in decomposto if not unicodedata.combining(ch))
    return sem_acento.lower()


def _slice(original: str, normalizado: str, inicio: int, fim: int) -> str:
    if len(original) == len(normalizado):
        return original[inicio:fim]
    return normalizado[inicio:fim]


@dataclass(frozen=True)
class ProposalResult:
    """Resultado de ``propose_automation`` (nunca levanta para o chamador)."""

    # created | paused | reformulate | refused | existing
    status: str
    # alta | media | baixa
    level: str
    confidence: int
    # create | ask | refuse
    action: str
    skill: str
    message: str
    reason: str = ""
    automation_id: str = ""
    automation: dict[str, Any] | None = None
    days: tuple[int, ...] = ()
    at: str = ""
    until: str = ""
    delivery: str = ""
    label: str = ""
    params: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- slots
def parse_days(text: str) -> tuple[int, ...]:
    """Dias da semana (0 = segunda) citados na frase; ``()`` = nao disse."""
    norm = _normalize(text)
    dias: set[int] = set()
    if _ALL_DAYS_RE.search(norm):
        dias.update(range(7))
    if _WEEKDAYS_RE.search(norm):
        dias.update(range(5))
    if _WEEKEND_RE.search(norm):
        dias.update({5, 6})
    for match in _DAY_RANGE_RE.finditer(norm):
        inicio = _WD_INDEX.get(match.group(1))
        fim = _WD_INDEX.get(match.group(2))
        if inicio is None or fim is None:
            continue
        if inicio <= fim:
            dias.update(range(inicio, fim + 1))
        else:  # intervalo que vira a semana (sex -> seg)
            dias.update(range(inicio, 7))
            dias.update(range(0, fim + 1))
    for match in _WD_RE.finditer(norm):
        indice = _WD_INDEX.get(match.group(0).rstrip("s"))
        if indice is not None:
            dias.add(indice)
    return tuple(sorted(dias))


def _time_match(text: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """``(hora, minuto)`` e o span usado para limpar o rotulo."""
    norm = _normalize(text)
    # Bloco completo primeiro: "as 9h da noite" vale 21:00 e o rotulo perde
    # tudo, nao so "as 9h".
    for match in _PM_BLOCK_RE.finditer(norm):
        hora = int(match.group(1))
        # "21h da noite" ja e 21: pela convencao da lingua, o qualificador so
        # soma quando a hora declarada e menor que 12.
        if hora < 12:
            hora += 12
        return (hora, 0), match.span()
    for regex in (_COLON_RE, _HOUR_RE, _AS_HOUR_RE):
        for match in regex.finditer(norm):
            antes = norm[max(0, match.start() - 4) : match.start()]
            if regex is _HOUR_RE and antes.rstrip().endswith("em"):
                # "em 2 horas" e prazo relativo, nao horario do dia.
                continue
            hora = int(match.group(1))
            minuto = int(match.group(2) or 0) if match.lastindex and match.lastindex >= 2 else 0
            if minuto > 59:
                continue
            # "9h da noite" -> 21:00 (o padrao brasileiro de falar a hora).
            if hora < 12 and _PM_HINT_RE.search(norm):
                hora += 12
            return (hora, minuto), match.span()
    for regex, hora in ((_NOON_RE, 12), (_MIDNIGHT_RE, 0)):
        achado = regex.search(norm)
        if achado:
            return (hora, 0), achado.span()
    return None, None


def parse_time(text: str) -> str:
    """Horario normalizado ``HH:MM`` ou ``""`` quando a frase nao disse."""
    value, _ = _time_match(text)
    if value is None:
        return ""
    return f"{value[0]:02d}:{value[1]:02d}"


def parse_target(text: str) -> str:
    """Skill alvo: reminder | agenda_summary | estudos_summary | ""."""
    norm = _normalize(text)
    if _REMINDER_RE.search(norm):
        return SKILL_REMINDER
    if _ESTUDOS_RE.search(norm):
        return SKILL_ESTUDOS
    if _AGENDA_RE.search(norm):
        return SKILL_AGENDA
    return ""


def parse_delivery(text: str, cfg: Config) -> tuple[str, bool]:
    """``(entrega, foi_explicita)``; padrao depende do Telegram configurado."""
    norm = _normalize(text)
    if _DELIVERY_TELEGRAM_RE.search(norm):
        return "telegram", True
    if _DELIVERY_LOCAL_RE.search(norm):
        return "local", True
    return ("telegram" if cfg.telegram.enabled else "local"), False


def parse_label(text: str) -> str:
    """Texto do lembrete sem as palavras de horario/dia/entrega/verbo.

    Remove o que e slot (dia, hora, "no telegram", o verbo "me lembre de")
    e depois apara palavras de ligacao **so nas pontas**: cortar as do meio
    destruiria o proprio lembrete ("limpar a casa" viraria "limpar casa").
    O recorte sai do texto ORIGINAL para manter acentos e maiusculas.
    """
    original = text.strip()
    norm = _normalize(original)
    if not norm:
        return ""
    spans: list[tuple[int, int]] = []
    for regex in (
        _PM_BLOCK_RE,
        _DAY_RANGE_RE,
        _ALL_DAYS_RE,
        _WEEKDAYS_RE,
        _WEEKEND_RE,
        _WD_RE,
        _DELIVERY_TELEGRAM_RE,
        _DELIVERY_LOCAL_RE,
        _NOON_RE,
        _MIDNIGHT_RE,
    ):
        spans.extend(match.span() for match in regex.finditer(norm))
    _, time_span = _time_match(original)
    if time_span is not None:
        spans.append(time_span)
    prefixo = _REMINDER_PREFIX_RE.search(norm)
    if prefixo is not None:
        spans.append(prefixo.span())

    removido = [False] * len(norm)
    for inicio, fim in spans:
        for indice in range(max(0, inicio), min(len(norm), fim)):
            removido[indice] = True
    tokens: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\S+", norm):
        if all(removido[indice] for indice in range(match.start(), match.end())):
            continue
        tokens.append((match.start(), match.end(), match.group(0)))
    while tokens and _is_filler(tokens[0][2]):
        tokens.pop(0)
    while tokens and _is_filler(tokens[-1][2]):
        tokens.pop()
    if not tokens:
        return ""
    label = _slice(original, norm, tokens[0][0], tokens[-1][1])
    label = " ".join(label.split()).strip(" ,.;:-!?")
    # Corte final do token de educacao: "revisar as metas, por favor" ->
    # "revisar as metas".
    label = re.sub(r"[,\s]+por\s+favor\s*$", "", label, flags=re.IGNORECASE)
    return label[:1].upper() + label[1:] if label else ""


def _is_filler(token: str) -> bool:
    limpo = token.strip(" ,.;:-!?").casefold()
    return not limpo or limpo in _FILLER_WORDS


def _summary_label(skill: str) -> str:
    return SKILL_NAMES.get(skill, "")


def build_params(skill: str, text: str) -> dict[str, Any]:
    """Parametros que o handler da automacao vai consumir."""
    if skill == SKILL_REMINDER:
        return {"text": parse_label(text)}
    return {}


# ---------------------------------------------------------------- confianca
# Pesos das regras (soma maxima 11, limitada a 10). Cada slot que a frase
# entrega explicitamente vale ponto; o que falta vira teto, nao desconto:
#
# * skill alvo reconhecida ......... +3
# * verbo claro do que fazer ....... +2  ("me lembre de X", "mande o resumo")
# * horario valido ................. +3
# * dias citados ................... +2
# * entrega explicita .............. +1
#
# Teto por slot essencial ausente:
# * sem skill ou sem horario -> <= 3 (baixa: nao da para agendar sem saber
#   o que ou quando);
# * sem dias -> <= 6 (media: cria pausada e pede os dias no painel, porque
#   sem recorrencia a automacao nunca dispara sozinha).
_WEIGHTS = {"skill": 3, "verb": 2, "hour": 3, "days": 2, "delivery": 1}


def _clear_verb(text: str, skill: str) -> bool:
    """O pedido diz claramente o que fazer (nao so cita a area)?"""
    norm = _normalize(text)
    if skill == SKILL_REMINDER:
        return bool(parse_label(text))
    if skill in (SKILL_AGENDA, SKILL_ESTUDOS):
        return bool(_SUMMARY_HINT_RE.search(norm))
    return False


def score(text: str, cfg: Config) -> tuple[int, str]:
    """Confianca 0-10 e nivel (alta|media|baixa), medidos por regra.

    Alta (>= 7) cria ativada; media (4-6) cria pausada e pergunta; baixa (< 4)
    nao cria nada. Ver ``_WEIGHTS`` para a conta e os tetos.
    """
    alvo = parse_target(text)
    hora = parse_time(text)
    dias = parse_days(text)
    _, entrega_explicita = parse_delivery(text, cfg)
    total = 0
    if alvo:
        total += _WEIGHTS["skill"]
        if _clear_verb(text, alvo):
            total += _WEIGHTS["verb"]
    if hora:
        total += _WEIGHTS["hour"]
    if dias:
        total += _WEIGHTS["days"]
    if entrega_explicita:
        total += _WEIGHTS["delivery"]
    total = max(0, min(10, total))
    if not alvo or not hora:
        total = min(total, 3)
    elif not dias:
        total = min(total, 6)
    if total >= 7:
        return total, "alta"
    if total >= 4:
        return total, "media"
    return total, "baixa"


# ------------------------------------------------------------------ horarios
def _plus_minutes(at: str, minutes: int) -> str:
    hora, _, minuto = at.partition(":")
    base = datetime(2000, 1, 1, int(hora), int(minuto or 0)) + timedelta(minutes=minutes)
    return base.strftime("%H:%M")


def window_for(at: str, minutes: int = 60) -> str:
    """Fim da janela: uma hora depois, sem virar o dia de forma ambigua.

    A janela existe porque o agendador so dispara algo que esteja aberto; uma
    hora e folga suficiente para o tick de 30 s e para a linha nao repetir no
    mesmo dia (``claim`` por janela). Se a hora for perto da meia-noite, o fim
    vira ``00:00`` e ``in_window`` trata a virada.
    """
    fim = _plus_minutes(at, minutes)
    hora, _, minuto = at.partition(":")
    minutos_at = int(hora) * 60 + int(minuto or 0)
    fim_hora, _, fim_minuto = fim.partition(":")
    if int(fim_hora) * 60 + int(fim_minuto or 0) <= minutos_at:
        return "00:00"
    return fim


# ------------------------------------------------------------------- limites
def _creation_prefix(day: datetime) -> str:
    return f"automation_created:{day.date().isoformat()}:"


def creations_today(store: MemoryStore, now: datetime) -> int:
    return store.count_meta(_creation_prefix(now))


def _slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", _normalize(text)).strip("-")
    return base[:40].strip("-") or "automacao"


def _same_automation(
    skill: str,
    days: tuple[int, ...],
    at: str,
    params: dict[str, Any],
    store: MemoryStore,
) -> dict[str, Any] | None:
    """Automacao ``user:*`` equivalente ja criada (nao cria duplicata)."""
    for linha in store.list_automations():
        if not str(linha["id"]).startswith("user:"):
            continue
        if str(linha.get("skill")) != skill:
            continue
        if str(linha.get("days") or "") != ",".join(str(dia) for dia in days):
            continue
        if str(linha.get("at") or "") != at:
            continue
        guardados = _params_de(linha)
        if skill == SKILL_REMINDER and guardados.get("text") != params.get("text"):
            continue
        return dict(linha)
    return None


def _params_de(linha: dict[str, Any]) -> dict[str, Any]:
    raw = linha.get("params")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        valor = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return dict(valor) if isinstance(valor, dict) else {}


# --------------------------------------------------------------- mensagens
def _days_label(days: tuple[int, ...]) -> str:
    if not days:
        return "nunca"
    if set(days) == set(range(7)):
        return "todos os dias"
    return ", ".join(DAY_NAMES[dia] for dia in days)


def _describe(
    skill: str, days: tuple[int, ...], at: str, delivery: str, label: str
) -> str:
    quando = f"{_days_label(days)} as {at}"
    entrega = "no Telegram" if delivery == "telegram" else "aqui"
    if skill == SKILL_REMINDER:
        return f"Lembrete \"{label}\" {quando}, {entrega}"
    return f"{SKILL_NAMES.get(skill, skill)} {quando}, {entrega}"


def _skill_enabled(cfg: Config, skill: str) -> tuple[bool, str]:
    caminho = SKILL_REQUIRES.get(skill, "")
    if not caminho:
        return True, ""
    atual: Any = cfg
    for parte in caminho.split("."):
        atual = getattr(atual, parte, None)
        if atual is None:
            return False, caminho
    if not bool(atual):
        return False, caminho
    return True, ""


def _definitive_id(skill: str, label: str, store: MemoryStore) -> str:
    base = f"user:{_slug(label or SKILL_NAMES.get(skill, skill))}"
    existentes = {linha["id"] for linha in store.list_automations()}
    if base not in existentes:
        return base
    for indice in range(2, 100):
        candidato = f"{base}-{indice}"
        if candidato not in existentes:
            return candidato
    return f"{base}-{datetime.now().strftime('%H%M%S')}"


# ---------------------------------------------------------------- proposta
def propose_automation(
    text: str,
    cfg: Config,
    store: MemoryStore | None,
    *,
    now: datetime | None = None,
) -> ProposalResult:
    """Interpreta a frase e, conforme a confianca, cria (ou nao) a automacao.

    Nunca levanta excecao de regra de negocio: skill desligada, horario
    invalido, pedido repetido e teto diario viram ``status`` + ``message``
    (o agente repassa o texto e a pessoa entende o que corrigir).
    """
    original = " ".join(str(text or "").split())
    momento = now or datetime.now().astimezone()
    if not original:
        return ProposalResult(
            status="reformulate",
            level="baixa",
            confidence=0,
            action="ask",
            skill="",
            message="Me diga o que voce quer automatizar, com dia e horario.",
            reason="texto vazio",
        )

    skill = parse_target(original)
    days = parse_days(original)
    at = parse_time(original)
    delivery, _ = parse_delivery(original, cfg)
    confidence, _ = score(original, cfg)
    params = build_params(skill, original)
    label = str(params.get("text") or "") or _summary_label(skill)

    def recusar(reason: str, *, status: str = "refused", action: str = "refuse") -> ProposalResult:
        return ProposalResult(
            status=status,
            level="baixa",
            confidence=confidence,
            action=action,
            skill=skill,
            message=reason,
            reason=reason,
            days=days,
            at=at,
            delivery=delivery,
            label=label,
            params=params,
        )

    # 1) slot essencial que falta -> pede para reformular (nao e recusa:
    # a pessoa so nao disse tudo o que o parser precisa).
    if not skill:
        return recusar(
            "Ainda nao sei o que a automacao deve fazer. Diga se e um lembrete "
            "(\"me lembre de X\"), um resumo da agenda ou um resumo de estudos.",
            status="reformulate",
            action="ask",
        )
    if not at:
        return recusar(
            "Falta o horario. Diga algo como \"toda sexta as 9h\".",
            status="reformulate",
            action="ask",
        )
    if not 0 <= int(at[:2]) <= 23 or not 0 <= int(at[3:]) <= 59:
        return recusar(f"Horario invalido: {at}. Use HH:MM entre 00:00 e 23:59.")
    if skill == SKILL_REMINDER and not params.get("text"):
        return recusar(
            "Entendi que e um lembrete, mas nao o assunto. Diga \"me lembre de X\".",
            status="reformulate",
            action="ask",
        )
    enabled, caminho = _skill_enabled(cfg, skill)
    if not enabled:
        return recusar(
            f"A skill alvo ({caminho}) esta desligada na configuracao; "
            "ligue antes de criar a automacao."
        )
    if store is None:
        return recusar("Sem banco de dados nao da para agendar.", status="refused")

    # 2) baixa: nao cria, pede para reformular.
    if confidence < 4:
        return ProposalResult(
            status="reformulate",
            level="baixa",
            confidence=confidence,
            action="ask",
            skill=skill,
            message=(
                "Nao consegui montar isso com seguranca. Diga o dia e o horario "
                "junto com o que deve acontecer (ex.: \"toda sexta as 9h me "
                "lembre de estudar\")."
            ),
            reason="confianca baixa",
            days=days,
            at=at,
            delivery=delivery,
            label=label,
            params=params,
        )

    # 3) teto diario antes de qualquer escrita.
    if creations_today(store, momento) >= MAX_PER_DAY:
        return recusar(
            f"Ja criei {MAX_PER_DAY} automacoes hoje; esse e o limite diario. "
            "Amanha eu volto a aceitar, ou pause uma existente no painel."
        )

    # 4) pedido repetido: devolve a existente, nunca edita nem apaga.
    repetida = _same_automation(skill, days, at, params, store)
    if repetida is not None:
        descricao = _describe(skill, days, at, delivery, label)
        return ProposalResult(
            status="existing",
            level="alta",
            confidence=confidence,
            action="ask",
            skill=skill,
            message=(
                f"Isso ja existe ({repetida['id']}): {descricao}. "
                "Se quiser mudar horario ou pausar, use o painel de automacoes."
            ),
            reason="pedido repetido",
            automation_id=str(repetida["id"]),
            days=days,
            at=at,
            delivery=delivery,
            label=label,
            params=params,
        )

    # 5) cria. Media entra pausada (a pessoa confirma no painel).
    ativo = confidence >= 7
    automation_id = _definitive_id(skill, label, store)
    until = window_for(at)
    store.upsert_automation(
        id=automation_id,
        skill=skill,
        enabled=ativo,
        days=",".join(str(dia) for dia in days),
        at=at,
        until=until,
        delivery=delivery,
        name=_describe(skill, days, at, delivery, label),
        params=params,
    )
    store.claim_meta(
        f"{_creation_prefix(momento)}{automation_id}",
        momento.isoformat(timespec="seconds"),
    )
    descricao = _describe(skill, days, at, delivery, label)
    if ativo:
        message = (
            f"Automacao criada e ativa: {descricao}. "
            f"Ela aparece no painel de automacoes (id {automation_id})."
        )
    elif not days:
        message = (
            f"Criei PAUSADA (id {automation_id}): {descricao}, mas faltam os "
            f"dias. Confirme no painel de automacoes escolhendo os dias e "
            "ligando o checkbox."
        )
    else:
        message = (
            f"Proposta de confianca media ({confidence}/10): {descricao}. "
            f"Criei PAUSADA (id {automation_id}) para voce conferir; "
            "ligue no painel de automacoes se estiver certo."
        )
    return ProposalResult(
        status="created" if ativo else "paused",
        level="alta" if ativo else "media",
        confidence=confidence,
        action="create",
        skill=skill,
        message=message,
        automation_id=automation_id,
        days=days,
        at=at,
        until=until,
        delivery=delivery,
        label=label,
        params=params,
    )


__all__ = [
    "MAX_PER_DAY",
    "SKILL_AGENDA",
    "SKILL_ESTUDOS",
    "SKILL_NAMES",
    "SKILL_REMINDER",
    "SKILL_REQUIRES",
    "ProposalResult",
    "build_params",
    "creations_today",
    "parse_days",
    "parse_delivery",
    "parse_label",
    "parse_target",
    "parse_time",
    "propose_automation",
    "score",
    "window_for",
]
