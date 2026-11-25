"""Agenda declarada das automacoes: quando cada rotina roda e o estado dela.

O modulo responde tres perguntas e agora tambem guarda a resposta:

* quando e a proxima execucao (``next_run``);
* o que aconteceu na ultima (``last_run``, vindo de ``runs``);
* a automacao deveria ter rodado e nao rodou? (``missed``).

A partir da fase C a agenda deixa de ser so descricao: a tabela ``automations``
(schema v7) guarda os horarios e o ``_scheduler_loop`` do ``Orchestrator`` a
obedece. A tabela e semeada a partir do ``Config`` **uma vez**, com os mesmos
valores que os workers usavam -- e essa e a rede de seguranca da fase: depois
do seed, o comportamento observado nao pode mudar.

``describe(cfg)`` continua sendo a agenda *derivada do config* (usada no seed e
nos testes); ``load(cfg, store)`` devolve a agenda efetiva (tabela + config
quando a tabela estiver vazia).

Nada aqui executa rotina: quem chama os handlers e o ``_scheduler_loop``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gaia.briefing.market import market_due
from gaia.career.service import career_due
from gaia.config import Config
from gaia.memory.store import MemoryStore

# Tolerancia depois do fim da janela antes de considerar "perdida". Cobre o
# intervalo de checagem dos workers (30s) e o tempo de uma execucao longa.
MISSED_TOLERANCE_MINUTES = 20

# Teto de retry para erro de rede/desconhecido. auth e config nao repetem: o
# problema e humano e insistir esconde o erro.
RETRYABLE_ERRORS = frozenset({"network", "unknown"})
RETRY_MAX_LIMIT = 10
RETRY_BACKOFF_LIMIT_MINUTES = 720.0

# Prefixo dos ids das automacoes criadas pelo chat (gaia/controller/
# automation_builder.py). Elas nao tem descritor no registry: a linha da tabela
# e a fonte completa (skill, nome, horario e parametros).
USER_PREFIX = "user:"

_DAY_NAMES = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class Automation:
    """Uma rotina com horario declarado.

    ``days`` pode ser vazio: significa "nunca" (o painel mostra assim e o
    scheduler nao a considera devida). ``retry_max`` e
    ``retry_backoff_minutes`` vem da tabela; no seed recebem o padrao.
    """

    id: str
    skill: str
    name: str
    days: tuple[int, ...]  # 0 = segunda
    at: str  # HH:MM
    until: str  # HH:MM
    delivery: str  # telegram | local
    # Prefixo da chave de reserva diaria em ``meta`` (ex.: "career:{day}").
    claim_prefix: str = ""
    # Chave de ``meta`` que a rotina real grava ao terminar (quando difere do
    # prefixo, ex.: "memory_consolidated:{day}"). O painel precisa das duas:
    # uma e a reserva antes de executar, a outra e a conclusao.
    done_prefix: str = ""
    # Slot extra para rotinas com mais de uma janela por dia (briefing).
    slot: str = ""
    enabled_path: str = ""
    # Rotina oportunista: so roda se o motor estiver ocioso na janela. Nao
    # existe "perdeu a hora" quando a condicao e opcional (sonho, por ex.).
    opportunistic: bool = False
    # Estado governavel (vem da tabela; no descritor puro e o padrao).
    enabled: bool = True
    retry_max: int = 2
    retry_backoff_minutes: float = 10.0
    # Campos que so existem na tabela; guardados para o painel nao mentir.
    last_result: str = ""
    updated_at: str = ""
    # Parametros do handler (automacoes `user:*` criadas pelo chat). Vazio nas
    # do registry: nelas quem manda e a config.
    params: dict[str, Any] = field(default_factory=dict)
    # Bot dono da automacao (design §4.5). Vazio = `gaia`, o comportamento de
    # antes dos bots; a coluna da tabela vem depois do seed.
    bot: str = ""

    def claim_key(self, day: datetime) -> str:
        """Chave de ``claim_meta`` equivalente a usada pela rotina real."""
        return self._key(self.claim_prefix, day)

    def done_key(self, day: datetime) -> str:
        """Chave que a rotina grava ao concluir (vazia = a propia claim)."""
        return self._key(self.done_prefix, day) if self.done_prefix else ""

    def _key(self, prefixo: str, day: datetime) -> str:
        if not prefixo:
            return ""
        base = prefixo.format(day=day.date().isoformat())
        return f"{base}:{self.slot}" if self.slot else base

    def window_key(self, when: datetime) -> str:
        """Chave da reserva por *janela* (camada do scheduler, fase C).

        Separar a reserva do scheduler da reserva da rotina permite que as duas
        continuem existindo: a primeira impede disparo duplo entre ticks, a
        segunda continua impedindo entrega dupla entre processos/canais.
        """
        return f"scheduler:{self.id}:{when.date().isoformat()}:{self.slot or 'main'}"

    def matches_day(self, when: datetime) -> bool:
        return when.weekday() in self.days

    def in_window(self, when: datetime) -> bool:
        hora = when.strftime("%H:%M")
        if self.at <= self.until:
            return self.at <= hora < self.until
        # Janela que vira a meia-noite (ex.: sonho 23:30 -> 08:00).
        return hora >= self.at or hora < self.until


def _split(value: str) -> tuple[int, int]:
    try:
        hora, _, minuto = (value or "").partition(":")
        return int(hora), int(minuto or 0)
    except ValueError:
        return 0, 0


def valid_time(value: str) -> bool:
    """HH:MM valido (``09:99`` nao passa). Usado pela rota PATCH."""
    return bool(_HHMM_RE.match((value or "").strip()))


def normalize_time(value: str) -> str:
    """Reescreve ``7:05`` como ``07:05`` (o painel mostra sempre 2 digitos)."""
    hora, minuto = _split(value.strip())
    return f"{hora:02d}:{minuto:02d}"


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Valida o corpo de ``PATCH /automations/{id}`` e devolve o que mudou.

    Recusa horario invalido, dia fora de 0..6, entrega desconhecida e retry
    negativo. Devolve apenas os campos aceitos (com o tipo ja normalizado).
    """
    if not isinstance(payload, dict):
        raise ValueError("corpo deve ser um objeto JSON")
    aceitos: dict[str, Any] = {}
    for chave in ("at", "until"):
        if chave in payload:
            valor = str(payload[chave] or "").strip()
            if not valid_time(valor):
                raise ValueError(f"{chave} invalido: use HH:MM entre 00:00 e 23:59")
            aceitos[chave] = normalize_time(valor)
    if "enabled" in payload:
        valor = payload["enabled"]
        if not isinstance(valor, bool):
            raise ValueError("enabled deve ser true ou false")
        aceitos["enabled"] = valor
    if "days" in payload:
        dias = payload["days"]
        if not isinstance(dias, (list, tuple)):
            raise ValueError("days deve ser uma lista de 0..6 (0 = segunda)")
        numeros: list[int] = []
        for dia in dias:
            if isinstance(dia, bool) or not isinstance(dia, int):
                raise ValueError("cada dia deve ser um inteiro de 0 a 6")
            if not 0 <= dia <= 6:
                raise ValueError(f"dia fora de 0..6: {dia}")
            if dia not in numeros:
                numeros.append(dia)
        aceitos["days"] = ",".join(str(dia) for dia in sorted(numeros))
    if "delivery" in payload:
        entrega = str(payload["delivery"] or "").strip()
        if entrega not in {"telegram", "local", "none"}:
            raise ValueError("delivery deve ser telegram, local ou none")
        aceitos["delivery"] = entrega
    if "retry_max" in payload:
        valor = payload["retry_max"]
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError("retry_max deve ser inteiro")
        if not 0 <= valor <= RETRY_MAX_LIMIT:
            raise ValueError(f"retry_max deve ficar entre 0 e {RETRY_MAX_LIMIT}")
        aceitos["retry_max"] = valor
    if "retry_backoff_minutes" in payload:
        valor = payload["retry_backoff_minutes"]
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError("retry_backoff_minutes deve ser numero")
        numero = float(valor)
        if not 0.0 <= numero <= RETRY_BACKOFF_LIMIT_MINUTES:
            raise ValueError(
                f"retry_backoff_minutes deve ficar entre 0 e "
                f"{RETRY_BACKOFF_LIMIT_MINUTES:.0f}"
            )
        aceitos["retry_backoff_minutes"] = numero
    if not aceitos and not payload:
        raise ValueError("nada para alterar")
    return aceitos


def _enabled(cfg: Config, item: Automation) -> bool:
    """True se o caminho de config da automacao estiver ligado."""
    if not item.enabled:
        return False
    return _configured(cfg, item)


def _configured(cfg: Config, item: Automation) -> bool:
    """A skill esta ligada no config? (ignora a pausa da tabela)"""
    if not item.enabled_path:
        return True
    atual: Any = cfg
    for parte in item.enabled_path.split("."):
        atual = getattr(atual, parte, None)
        if atual is None:
            return False
    return bool(atual)


def describe(cfg: Config) -> list[Automation]:
    """Agenda declarada, derivada do config atual (semente da tabela).

    A ordem segue a do dia real: briefing, carteira, vagas, memoria/sonho.
    ``bot`` e o dono de cada automacao: a tela agrupa por ele e o scheduler so
    bloqueia a automacao do bot ocupado (design §4.5).
    """
    briefing = cfg.briefing
    career = cfg.career
    memory = cfg.memory
    itens: list[Automation] = [
        Automation(
            id="briefing:morning",
            skill="briefing",
            name="Briefing da manha",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=briefing.morning_at,
            until=briefing.noon_at,
            delivery="telegram",
            claim_prefix="briefing:{day}",
            slot="morning",
            enabled_path="briefing.enabled",
            bot="gaia",
        ),
        Automation(
            id="briefing:noon",
            skill="briefing",
            name="Resumo do meio-dia",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=briefing.noon_at,
            until=briefing.noon_until,
            delivery="telegram",
            claim_prefix="briefing:{day}",
            slot="noon",
            enabled_path="briefing.enabled",
            bot="gaia",
        ),
        Automation(
            id="briefing:evening",
            skill="briefing",
            name="Fecho da noite",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=briefing.evening_at,
            until=briefing.evening_until,
            delivery="telegram",
            claim_prefix="briefing:{day}",
            slot="evening",
            enabled_path="briefing.evening_enabled",
            bot="gaia",
        ),
        Automation(
            id="market:watchlist",
            skill="market",
            name="Resumo da carteira",
            days=tuple(briefing.market_days),
            at=briefing.market_at,
            until=briefing.market_until,
            # O painel e a entrega principal; o Telegram, quando ligado, recebe
            # o mesmo texto (design §4.5a).
            delivery="local",
            claim_prefix="briefing:market:{day}",
            enabled_path="briefing.market_enabled",
            # A carteira e da Ariana e e deterministica (sem modelo).
            bot="ariana",
        ),
        Automation(
            id="career:weekly",
            skill="career",
            name="Pesquisa de vagas",
            days=tuple(career.days),
            at=career.at,
            until=career.until,
            delivery="telegram",
            claim_prefix="career:{day}",
            enabled_path="career.enabled",
            # CV e vagas sao da Sabrina; a rotina continua sem modelo.
            bot="sabrina",
        ),
        Automation(
            id="memory:daily",
            skill="memory",
            name="Consolidacao da memoria",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=memory.at,
            until="23:59",
            delivery="local",
            claim_prefix="memory:{day}",
            done_prefix="memory_consolidated:{day}",
            enabled_path="memory.enabled",
            bot="gaia",
        ),
        Automation(
            id="memory:dream",
            skill="dream",
            name="Sonho noturno",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=memory.dream_at,
            until=memory.dream_until,
            delivery="local",
            claim_prefix="dream:{day}",
            done_prefix="memory_dreamed:{day}",
            enabled_path="memory.dream_enabled",
            opportunistic=True,
            bot="gaia",
        ),
    ]
    return itens


def find(cfg: Config, automation_id: str) -> Automation | None:
    for item in describe(cfg):
        if item.id == automation_id:
            return item
    return None


# --------------------------------------------------------------------- tabela
def _row_payload(item: Automation) -> dict[str, Any]:
    return {
        "id": item.id,
        "skill": item.skill,
        "enabled": item.enabled,
        "days": ",".join(str(dia) for dia in item.days),
        "at": item.at,
        "until": item.until,
        "delivery": item.delivery,
        "retry_max": item.retry_max,
        "retry_backoff_minutes": item.retry_backoff_minutes,
        "last_result": item.last_result,
        "bot": item.bot,
    }


def _parse_days(raw: Any) -> tuple[int, ...]:
    texto = str(raw or "").strip()
    if not texto:
        return ()
    dias: list[int] = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            dia = int(parte)
        except ValueError:
            continue
        if 0 <= dia <= 6 and dia not in dias:
            dias.append(dia)
    return tuple(sorted(dias))


def seed(cfg: Config, store: MemoryStore | None = None) -> int:
    """Semeia a tabela a partir do config (idempotente). Devolve quantas criou.

    So insere automacoes que ainda nao existem: se a pessoa ja pausou uma
    automacao pelo painel, um boot novo nao pode desfaze-la. ``skill`` e os
    metadados derivados do config (nome, claim, entrega padrao) sao sempre os
    do registry -- quem governa horario/estado e a tabela.
    """
    if store is None:
        return 0
    existentes = {linha["id"] for linha in store.list_automations()}
    criadas = 0
    for item in describe(cfg):
        if item.id in existentes:
            continue
        store.upsert_automation(**_row_payload(item))
        criadas += 1
    return criadas


def _parse_params(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        valor = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return dict(valor) if isinstance(valor, dict) else {}


def load(cfg: Config, store: MemoryStore | None = None) -> list[Automation]:
    """Agenda efetiva: a tabela manda; o config so preenche quando falta.

    Sem tabela (store ``None``) ou com a tabela vazia, devolve ``describe(cfg)``
    -- e o mesmo comportamento de antes da fase C.
    """
    derivadas = {item.id: item for item in describe(cfg)}
    if store is None:
        return list(derivadas.values())
    linhas = store.list_automations()
    if not linhas:
        return list(derivadas.values())
    itens: list[Automation] = []
    for linha in linhas:
        automation_id = str(linha["id"])
        base = derivadas.get(automation_id)
        if base is None:
            if not automation_id.startswith(USER_PREFIX):
                # Linha orfa do registry (skill removida num deploy): sem
                # handler nao ha o que executar. Ignora.
                continue
            # Automacao do chat: a propria linha e o descritor.
            itens.append(
                Automation(
                    id=automation_id,
                    skill=str(linha.get("skill") or ""),
                    name=str(linha.get("name") or automation_id),
                    days=_parse_days(linha.get("days")),
                    at=str(linha.get("at") or ""),
                    until=str(linha.get("until") or linha.get("at") or ""),
                    delivery=str(linha.get("delivery") or "local"),
                    claim_prefix=f"automation:{automation_id}:{{day}}",
                    enabled=bool(linha.get("enabled")),
                    retry_max=int(linha.get("retry_max") or 0),
                    retry_backoff_minutes=float(
                        linha.get("retry_backoff_minutes") or 0.0
                    ),
                    last_result=str(linha.get("last_result") or ""),
                    updated_at=str(linha.get("updated_at") or ""),
                    params=_parse_params(linha.get("params")),
                    bot=str(linha.get("bot") or "gaia"),
                )
            )
            continue
        itens.append(
            Automation(
                id=base.id,
                skill=str(linha.get("skill") or base.skill),
                name=base.name,
                days=_parse_days(linha.get("days")),
                at=str(linha.get("at") or base.at),
                until=str(linha.get("until") or base.until),
                delivery=str(linha.get("delivery") or base.delivery),
                claim_prefix=base.claim_prefix,
                done_prefix=base.done_prefix,
                slot=base.slot,
                enabled_path=base.enabled_path,
                opportunistic=base.opportunistic,
                enabled=bool(linha.get("enabled")),
                retry_max=int(linha.get("retry_max") or 0),
                retry_backoff_minutes=float(
                    linha.get("retry_backoff_minutes") or 0.0
                ),
                last_result=str(linha.get("last_result") or ""),
                updated_at=str(linha.get("updated_at") or ""),
                params=_parse_params(linha.get("params")),
                bot=str(linha.get("bot") or base.bot or "gaia"),
            )
        )
    # Automacoes do registry que ainda nao foram semeadas continuam valendo:
    # sem isso, um boot entre o deploy e o seed esqueceria uma rotina.
    presentes = {item.id for item in itens}
    for item in derivadas.values():
        if item.id not in presentes:
            itens.append(item)
    return itens


def update(
    store: MemoryStore,
    cfg: Config,
    automation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Valida e grava a edicao; devolve a automacao resultante (dict do painel).

    Levanta ``ValueError`` para payload invalido e ``KeyError`` para id
    desconhecido (o transporte traduz em 422/404).
    """
    if find_loaded(cfg, store, automation_id) is None:
        raise KeyError(automation_id)
    campos = validate_payload(payload)
    if not campos:
        raise ValueError("nada para alterar")
    if not store.update_automation(automation_id, **campos):
        # Tabela vazia (banco recem-migrado): semeia e tenta de novo.
        seed(cfg, store)
        if not store.update_automation(automation_id, **campos):
            raise KeyError(automation_id)
    atual = find_loaded(cfg, store, automation_id)
    if atual is None:  # pragma: no cover - defensivo
        raise KeyError(automation_id)
    return render(atual, cfg, store)


def find_loaded(
    cfg: Config, store: MemoryStore | None, automation_id: str
) -> Automation | None:
    for item in load(cfg, store):
        if item.id == automation_id:
            return item
    return None


# ------------------------------------------------------------------- horarios
def next_run(item: Automation, now: datetime) -> datetime | None:
    """Proxima execucao prevista (None se a automacao nunca roda)."""
    if not item.days:
        return None
    for offset in range(0, 8):
        dia = (now + timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if not item.matches_day(dia):
            continue
        hora, minuto = _split(item.at)
        alvo = dia.replace(hour=hora, minute=minuto)
        if offset == 0 and alvo <= now:
            continue
        return alvo
    return None


def status(
    item: Automation,
    cfg: Config,
    store: MemoryStore | None,
    now: datetime | None = None,
) -> str:
    """scheduled | running | completed | failed | missed | disabled | paused.

    ``running`` tem prioridade sobre ``failed`` porque um run aberto significa
    que a rotina esta executando agora. ``paused`` e a decisao explicita do
    painel (``enabled = 0`` na tabela) e nao se confunde com ``disabled``
    (a config desligou a skill). ``missed`` so aparece quando a janela ja
    fechou (com tolerancia) e nao ha reserva nem execucao no dia.
    """
    momento = now or datetime.now().astimezone()
    if not item.enabled:
        return "paused"
    if not _enabled(cfg, item):
        return "disabled"
    if not item.days:
        return "disabled"

    ultima = store.last_run(item.skill, item.id) if store is not None else None
    if ultima is not None and str(ultima.get("status")) == "running":
        return "running"

    # Ja rodou hoje? Entao o estado e o resultado dela, nao "aguardando".
    # Sem isso, uma rotina concluida as 09:02 continuaria aparecendo como
    # "agendada" ate o fim da janela — o painel mentiria para quem olha.
    if store is not None and _ran_today(item, store, momento):
        if ultima is not None:
            return str(ultima.get("status") or "completed")
        return "completed"

    if item.matches_day(momento) and item.in_window(momento):
        return "scheduled"
    if item.opportunistic:
        # Janela opcional: nao existiu "deveria ter rodado".
        return str(ultima.get("status")) if ultima is not None else "scheduled"
    if item.matches_day(momento):
        hora_atual = momento.strftime("%H:%M")
        if hora_atual >= item.until:
            fim = momento.replace(hour=0, minute=0, second=0, microsecond=0)
            hora, minuto = _split(item.until)
            fim = fim.replace(hour=hora, minute=minuto)
            if momento - fim >= timedelta(minutes=MISSED_TOLERANCE_MINUTES):
                return "missed"
    # A janela de hoje ainda nao abriu: "agendada". O resultado anterior
    # continua visivel em ``last_run``, mas nao e o estado de hoje (uma falha
    # da semana passada nao pode parecer falha da execucao que ainda vai
    # acontecer).
    return "scheduled"


def _ran_today(item: Automation, store: MemoryStore | None, now: datetime) -> bool:
    """A rotina entregou/executou hoje (reserva em meta ou run no dia)?"""
    if store is None:
        return False
    chave = item.claim_key(now)
    if chave and store.get_meta(chave):
        return True
    concluida = item.done_key(now)
    if concluida and store.get_meta(concluida):
        return True
    ultima = store.last_run(item.skill, item.id)
    if ultima is None:
        return False
    iniciado = str(ultima.get("started_at") or "")
    return iniciado.startswith(now.date().isoformat())


def _last_run_payload(item: Automation, store: MemoryStore | None) -> dict | None:
    ultima = store.last_run(item.skill, item.id) if store is not None else None
    if ultima is None:
        return None
    return {
        "id": int(ultima.get("id") or 0),
        "status": str(ultima.get("status") or ""),
        "trigger": str(ultima.get("trigger") or ""),
        "started_at": str(ultima.get("started_at") or ""),
        "duration_ms": int(ultima.get("duration_ms") or 0),
        "detail": str(ultima.get("detail") or ""),
        "error": str(ultima.get("error") or ""),
        "error_kind": str(ultima.get("error_kind") or ""),
        "attempt": int(ultima.get("attempt") or 1),
    }


def render(
    item: Automation,
    cfg: Config,
    store: MemoryStore | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Uma automacao pronta para o painel (mesmo formato de sempre)."""
    momento = now or datetime.now().astimezone()
    proxima = next_run(item, momento)
    return {
        "id": item.id,
        "skill": item.skill,
        "name": item.name,
        "days": list(item.days),
        "days_label": _days_label(item.days),
        "at": item.at,
        "until": item.until,
        "delivery": item.delivery,
        "enabled": _enabled(cfg, item),
        # Bot dono (o painel agrupa e o rail mostra a atividade dele).
        "bot": item.bot or "gaia",
        # Duas coisas diferentes, e a tela precisa das duas: `paused` e a
        # decisao da pessoa (tabela, o checkbox que ela acabou de clicar) e
        # `enabled` e o resultado final (pausada OU skill desligada no config).
        "paused": not item.enabled,
        "config_enabled": _configured(cfg, item),
        "status": status(item, cfg, store, momento),
        "next_run": proxima.isoformat(timespec="seconds") if proxima else "",
        "retry_max": item.retry_max,
        "retry_backoff_minutes": item.retry_backoff_minutes,
        "last_result": item.last_result,
        "updated_at": item.updated_at,
        "last_run": _last_run_payload(item, store),
    }


def describe_automations(
    cfg: Config,
    store: MemoryStore | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lista pronta para o painel, com proxima execucao e estado."""
    momento = now or datetime.now().astimezone()
    return [render(item, cfg, store, momento) for item in load(cfg, store)]


def _days_label(days: tuple[int, ...]) -> str:
    """Rotulo curto: "todos os dias", "seg, qua, sex" ou "nunca"."""
    if not days:
        return "nunca"
    if set(days) == set(range(7)):
        return "todos os dias"
    return ", ".join(_DAY_NAMES[day] for day in days if 0 <= day < 7)


def due_now(item: Automation, cfg: Config, now: datetime) -> bool:
    """A janela esta aberta agora (usado pelo scheduler e pelos testes).

    Reaproveita as funcoes puras que os workers ja usam; nao reimplementa a
    regra de horario.
    """
    if not _enabled(cfg, item):
        return False
    hora = now.strftime("%H:%M")
    if item.skill == "career":
        return career_due(hora, item.at, item.until, now.weekday(), item.days)
    if item.skill == "market":
        return market_due(hora, item.at, item.until, now.weekday(), list(item.days))
    return item.matches_day(now) and item.in_window(now)


def due(item: Automation, cfg: Config, now: datetime, store: MemoryStore | None) -> bool:
    """Devida agora: janela aberta e essa janela ainda nao foi reservada."""
    if not due_now(item, cfg, now):
        return False
    if store is None:
        return True
    return not store.get_meta(item.window_key(now))


def claim(item: Automation, store: MemoryStore | None, now: datetime) -> bool:
    """Reserva a janela para o scheduler (segunda barreira fica na rotina)."""
    if store is None:
        return True
    return store.claim_meta(
        item.window_key(now), now.isoformat(timespec="seconds")
    )


def release(item: Automation, store: MemoryStore | None, now: datetime) -> None:
    """Devolve a janela (execucao falhou e o retry vai tentar de novo)."""
    if store is None:
        return
    store.delete_meta(item.window_key(now))


def retry_allowed(error_kind: str, attempt: int, item: Automation) -> bool:
    """Retry so para rede/desconhecido, com teto ``retry_max``.

    ``auth`` e ``config`` nao repetem: o problema e humano e insistir esconde
    o erro (decisao C.5). ``attempt`` e o numero de tentativas ja feitas.
    """
    if error_kind not in RETRYABLE_ERRORS:
        return False
    return attempt <= max(0, item.retry_max)


def backoff_seconds(item: Automation, attempt: int) -> float:
    """Espera antes da proxima tentativa (backoff exponencial e linear)."""
    base = max(0.0, float(item.retry_backoff_minutes)) * 60.0
    if base <= 0:
        return 0.0
    return base * max(1, attempt)


def needs_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Automacoes que merecem destaque no painel (perdida ou falhou)."""
    return [
        item
        for item in items
        if item["status"] in {"missed", "failed"}
        or (item["last_run"] or {}).get("status") == "failed"
    ]


def summarize_cfg(cfg: Config, store: MemoryStore | None) -> dict[str, Any]:
    """Contagem para o Overview (nao depende do scheduler estar ativo)."""
    itens = describe_automations(cfg, store)
    return {
        "total": len(itens),
        "active": sum(1 for item in itens if item["enabled"]),
        "paused": sum(1 for item in itens if item["paused"]),
        "missed": [item["id"] for item in itens if item["status"] == "missed"],
        "failed": [
            item["id"]
            for item in itens
            if (item["last_run"] or {}).get("status") == "failed"
        ],
        "items": itens,
    }


__all__ = [
    "MISSED_TOLERANCE_MINUTES",
    "RETRYABLE_ERRORS",
    "Automation",
    "backoff_seconds",
    "claim",
    "describe",
    "describe_automations",
    "due",
    "due_now",
    "find",
    "find_loaded",
    "load",
    "needs_attention",
    "next_run",
    "release",
    "render",
    "retry_allowed",
    "seed",
    "status",
    "summarize_cfg",
    "update",
    "valid_time",
    "validate_payload",
]
