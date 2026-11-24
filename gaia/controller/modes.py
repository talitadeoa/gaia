"""Modos (personas) da Gaia -- hoje um shim do registry de bots.

Cada modo injeta uma diretriz de comportamento no prompt enviado ao agente,
sem alterar a infraestrutura. O modo ativo e persistido em `MemoryStore.meta`.

O que mudou na fase 1 dos bots: a fonte da verdade das personas passou a ser
`gaia/bots/registry.py` (`Bot.directive`). Este modulo continua sendo a API que
a TUI, o `/modo`, o `parse_mode_switch` e o `compose_system` usam, com o mesmo
`dataclass Persona`; `PERSONAS` e **derivado** do registry e `ALIASES` aceita os
nomes dos bots e as chaves antigas.

Compatibilidade obrigatoria (ha conversa e config no disco):

* `assistente` -> `gaia`, `pesquisadora` -> `donna`, `trabalhadora` ->
  `sabrina`, `investidora` -> `ariana` continuam resolvendo;
* conversas antigas com `mode="pesquisadora"` continuam abrindo: o
  `Orchestrator` passa a persona do registry efetivo (`_persona_for_mode`) e
  `resolve_mode`/`get_persona` aceitam a chave antiga nos outros caminhos. O
  `mode` gravado no banco so muda quando a pessoa troca de bot de proposito;
* `interface.default_mode = "conversa"` (arquivo antigo) continua valendo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gaia.bots.registry import (
    DEFAULT_BOTS,
    Bot,
    bot_aliases,
    default_bots,
    normalize_key,
)


@dataclass(frozen=True)
class Persona:
    key: str
    name: str
    summary: str
    directive: str


def persona_from_bot(bot: Bot) -> Persona:
    """Persona (o que o prompt e a UI usam) de um bot do registry."""
    return Persona(
        key=bot.id,
        name=bot.name,
        summary=bot.role,
        directive=bot.directive,
    )


def _build(registry: dict[str, Bot]) -> dict[str, Persona]:
    return {bot_id: persona_from_bot(bot) for bot_id, bot in registry.items()}


# Personas do codigo (fallback da TUI sem `Orchestrator` e do `parse_mode_switch`).
# O `Orchestrator` deriva as suas do registry efetivo (`load_bots`), entao o
# override do TOML (nome/diretriz) vale no prompt sem mexer neste dict.
PERSONAS: dict[str, Persona] = _build(DEFAULT_BOTS)

DEFAULT_MODE = "gaia"

# Apelidos aceitos pelo `/modo`, pelo `parse_mode_switch` e pelo `Tab` da TUI.
# Derivado do registry (id, nome completo e `Bot.aliases`): quem acrescentar um
# bot novo nao precisa mexer aqui.
ALIASES: dict[str, str] = bot_aliases(DEFAULT_BOTS)

MODE_KEY_RENAMES: dict[str, str] = {
    chave_antiga: bot_id
    for chave_antiga, bot_id in ALIASES.items()
    if chave_antiga != bot_id
}

_MODE_WORD = r"(?P<mode>[a-zA-ZÀ-ÿ]+)"

_MODE_SWITCH_PATTERNS = [
    re.compile(
        r"^(?:gaia[,\s]+)?(?:muda|mude|troca|troque|ativa|ative|entra|entre|"
        r"vai|volta|quero|vamos|bora)\s+(?:para\s+|pro\s+|no\s+)?(?:o\s+)?modo\s+"
        + _MODE_WORD
        + r"\s*[.!?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:gaia[,\s]+)?modo\s+" + _MODE_WORD + r"\s*[.!?]?\s*$", re.IGNORECASE),
    re.compile(
        r"^(?:gaia[,\s]+)?(?:vira|vire|assume|assuma)\s+(?:a\s+)?"
        r"(?:parceira\s+(?:de\s+)?)?" + _MODE_WORD + r"\s*[.!?]?\s*$",
        re.IGNORECASE,
    ),
]


def normalize_mode_key(value: str) -> str:
    return normalize_key(value)


def get_persona(key: str | None) -> Persona | None:
    if not key:
        return None
    normalized = normalize_mode_key(key)
    normalized = ALIASES.get(normalized, normalized)
    return PERSONAS.get(normalized)


def parse_mode_switch(text: str) -> str | None:
    """Reconhece pedidos falados como 'vira Donna' ou 'modo trabalhadora'."""
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    for pattern in _MODE_SWITCH_PATTERNS:
        match = pattern.match(cleaned)
        if not match:
            continue
        persona = get_persona(match.group("mode"))
        if persona:
            return persona.key
    return None


def resolve_mode(key: str | None) -> Persona:
    return get_persona(key) or PERSONAS[DEFAULT_MODE]


def persona_block(persona: Persona) -> str:
    return (
        f"[Modo ativo da Gaia: {persona.name}]\n"
        f"{persona.directive}\n"
        "Trate a usuária por 'você' e responda em português do Brasil."
    )


def stored_mode_keys(bot_id: str, registry: dict[str, Bot] | None = None) -> tuple[str, ...]:
    """Chaves de `conversations.mode` que resolvem para este bot.

    Inclui o id novo e as chaves antigas (`pesquisadora`, `trabalhadora`, ...):
    a lista de sessoes precisa achar a conversa gravada antes da fase 1, senao
    o historico antigo some da tela (mas nao do banco).
    """
    alias = bot_aliases(registry)
    return tuple(sorted({chave for chave, alvo in alias.items() if alvo == bot_id}))


def personas_from_bots(registry: dict[str, Bot] | None = None) -> list[Persona]:
    """Personas na ordem do registry (para quem precisa da lista crua)."""
    return list(_build(registry or default_bots()).values())
