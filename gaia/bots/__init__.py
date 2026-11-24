"""Bots especialistas: descritor declarativo (registry) e, nas fases seguintes,
busca entre bots. Nada aqui e processo: um unico escritor (`gaia serve`)."""

from gaia.bots.registry import (
    DEFAULT_BOTS,
    KNOWN_TOOLS,
    Bot,
    BotIndisponivel,
    bot_aliases,
    bot_problem,
    get_bot,
    load_bots,
    normalize_key,
    persona_aliases,
    require_bot,
)

__all__ = [
    "DEFAULT_BOTS",
    "KNOWN_TOOLS",
    "Bot",
    "BotIndisponivel",
    "bot_aliases",
    "bot_problem",
    "get_bot",
    "load_bots",
    "normalize_key",
    "persona_aliases",
    "require_bot",
]
