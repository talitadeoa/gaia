"""Migracoes de config antiga: `[opencode]` e slugs de modelo legados.

Import tardio de `gaia.agent.providers` de proposito: `gaia.agent` importa
`gaia.config`, entao importar no topo fecharia o ciclo config -> agent -> config.
"""

from __future__ import annotations

from typing import Any

from gaia.config.env import _PROVIDER_API_KEYS


def _preset_fast_model(provider: str) -> str:
    """Modelo rapido declarado no preset (import tardio: ciclo config/agent)."""
    from gaia.agent.providers import PRESETS, normalize_provider

    preset = PRESETS.get(normalize_provider(provider))
    return preset.fast_model if preset else ""


def _provider_env_keys(provider: str) -> tuple[str, ...]:
    """Nomes de variavel de ambiente que pertencem ao provedor.

    Import tardio de proposito: ``gaia.agent`` importa este modulo, entao um
    import no topo fecharia o ciclo config -> agent -> config.
    """
    from gaia.agent.providers import normalize_provider

    name = normalize_provider(provider)
    return _PROVIDER_API_KEYS.get(name, _PROVIDER_API_KEYS.get(provider, ()))

_LEGACY_MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "deepseek/deepseek-flash": ("commandcode", "deepseek/deepseek-v4.1-flash"),
    "deepseek/deepseek-v4.1-flash": ("commandcode", "deepseek/deepseek-v4.1-flash"),
}


def _migrate_legacy_model(raw: dict[str, Any]) -> None:
    """Traduz ``agent.provider``/``agent.model`` antigos para o padrao atual.

    Config escrita antes da Command Code guarda ``provider = "deepseek"`` e um
    id que a API direta batiza de outra forma (``deepseek-flash`` = V4.1
    Flash). Sem a traducao, quem atualiza o codigo continua preso na API
    quebrada por saldo mesmo com a Command Code configurada.
    """
    agent = raw.get("agent")
    if not isinstance(agent, dict):
        return
    # `fast_model` preenchido = config ja passou por um `/modelo`; ali a
    # escolha e do usuario e nao se mexe (senao `/modelo ds` voltaria para a
    # Command Code no proximo boot).
    if str(agent.get("fast_model") or "").strip():
        return
    provider = str(agent.get("provider") or "").strip()
    model = str(agent.get("model") or "").strip()
    if not provider and not model:
        return
    slug = f"{provider}/{model}" if provider and model else model
    alias = _LEGACY_MODEL_ALIASES.get(slug)
    if alias is None and provider == "deepseek" and model:
        # Id da API direta: o V4.1 Flash dela se chama ``deepseek-flash``.
        alias = ("commandcode", "deepseek/deepseek-v4.1-flash")
    if alias is None:
        return
    agent["provider"] = alias[0]
    agent["model"] = alias[1]
    # Marca a migracao: sem isso um config que continua sem `fast_model` seria
    # traduzido de novo a cada boot.
    preset = _preset_fast_model(alias[0])
    if preset:
        agent["fast_model"] = preset


def _migrate_legacy_config(raw: dict[str, Any]) -> None:
    """Move ``[opencode]`` para ``[agent]`` quando a config ainda e antiga."""
    legacy = raw.pop("opencode", None)
    if not isinstance(legacy, dict):
        return
    agent = raw.setdefault("agent", {})
    if not isinstance(agent, dict):
        return
    for field in (
        "auto_approve",
        "directory",
        "request_timeout",
        "resume_session",
        "stall_timeout",
        "temperature",
        "max_tokens",
    ):
        if field in legacy and field not in agent:
            agent[field] = legacy[field]
    slug = str(legacy.get("model") or "").strip()
    if slug and "model" not in agent:
        provider, _, model = slug.partition("/")
        if provider and model:
            alias = _LEGACY_MODEL_ALIASES.get(slug)
            if alias:
                provider, model = alias
            agent.setdefault("provider", provider)
            agent["model"] = model
        else:
            agent["model"] = slug
