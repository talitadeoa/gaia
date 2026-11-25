"""Construcao do adaptador a partir da configuracao."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from gaia.agent.errors import AgentError
from gaia.agent.providers.anthropic import AnthropicProvider
from gaia.agent.providers.base import Provider
from gaia.agent.providers.openai_compat import OpenAICompatibleProvider


@dataclass(frozen=True)
class Preset:
    api: str
    base_url: str
    env_keys: tuple[str, ...]
    # Arquivo JSON em que o CLI original do provedor guarda a chave. Lido so
    # quando nao ha chave no ambiente/.env (ex.: ~/.commandcode/auth.json).
    auth_file: tuple[str, ...] = ()
    # Caminho da chave dentro do ``auth_file`` (chaves separadas por ponto).
    auth_key_path: tuple[str, ...] = ()
    # Presets que aparecem sempre no catalogo, mesmo sem chave propria, porque
    # a credencial pode estar num arquivo (e nao no ambiente).
    ambient: bool = False
    # Modelo usado ao entrar no preset pelo atalho (ex.: `/modelo cc`).
    default_model: str = ""
    # Modelo pequeno do preset, para pedidos curtos. Fica vazio nos presets
    # que nao tem um id mais barato: repetir o principal nao economiza nada.
    fast_model: str = ""


PRESETS: dict[str, Preset] = {
    "deepseek": Preset(
        api="openai",
        base_url="https://api.deepseek.com/v1",
        env_keys=("DEEPSEEK_API_KEY",),
        # A direta so expoe deepseek-flash (DeepSeek-V4.1-Flash) e v4-pro. Nao
        # ha variante "fast": o rapido e o proprio V4.1 Flash.
        default_model="deepseek-flash",
        fast_model="deepseek-flash",
    ),
    "openai": Preset(
        api="openai",
        base_url="https://api.openai.com/v1",
        env_keys=("OPENAI_API_KEY",),
    ),
    "openrouter": Preset(
        api="openai",
        base_url="https://openrouter.ai/api/v1",
        env_keys=("OPENROUTER_API_KEY",),
    ),
    "ollama": Preset(
        api="openai",
        base_url="http://127.0.0.1:11434/v1",
        env_keys=(),
    ),
    "anthropic": Preset(
        api="anthropic",
        base_url="https://api.anthropic.com",
        env_keys=("ANTHROPIC_API_KEY",),
    ),
    # Command Code Provider API (modelos abertos: DeepSeek, GPT, GLM...).
    "commandcode": Preset(
        api="openai",
        base_url="https://api.commandcode.ai/provider/v1",
        env_keys=("COMMAND_CODE_API_KEY", "COMMANDCODE_API_KEY"),
        auth_file=("~/.commandcode/auth.json",),
        auth_key_path=("apiKey",),
        ambient=True,
        default_model="deepseek/deepseek-v4.1-flash",
        fast_model="deepseek/deepseek-v4.1-flash-fast",
    ),
    # Command Code no wire Anthropic (modelos Claude).
    "commandcode-anthropic": Preset(
        api="anthropic",
        base_url="https://api.commandcode.ai/provider",
        env_keys=("COMMAND_CODE_API_KEY", "COMMANDCODE_API_KEY"),
        auth_file=("~/.commandcode/auth.json",),
        auth_key_path=("apiKey",),
        ambient=True,
        default_model="claude-haiku-4-5-20251001",
    ),
    # OpenDesign (gateway compatível com OpenAI, o mesmo do pi).
    # A chave é a do pi (`~/.pi/agent/auth.json`) ou OPENDESIGN_API_KEY.
    "opendesign": Preset(
        api="openai",
        base_url="https://amr-link.open-design.ai/v1",
        env_keys=("OPENDESIGN_API_KEY", "OPEN_DESIGN_API_KEY"),
        auth_file=("~/.pi/agent/auth.json",),
        auth_key_path=("opendesign", "key"),
        ambient=True,
        default_model="deepseek-v4.1-flash",
        # O gateway tem a variante barata do MESMO modelo ("eco"): mesma
        # janela e mesmas ferramentas, custo menor no pedido curto.
        fast_model="deepseek-v4.1-flash-eco",
    ),
    # OpenCode Zen (gateway compativel com OpenAI).
    "opencode": Preset(
        api="openai",
        base_url="https://opencode.ai/zen/v1",
        env_keys=("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"),
        auth_file=("~/.local/share/opencode/auth.json", "~/.config/opencode/auth.json"),
        auth_key_path=("opencode", "key"),
    ),
    "groq": Preset(
        api="openai",
        base_url="https://api.groq.com/openai/v1",
        env_keys=("GROQ_API_KEY",),
    ),
    "mistral": Preset(
        api="openai",
        base_url="https://api.mistral.ai/v1",
        env_keys=("MISTRAL_API_KEY",),
    ),
    "xai": Preset(
        api="openai",
        base_url="https://api.x.ai/v1",
        env_keys=("XAI_API_KEY",),
    ),
}

# Aliases amigaveis para o mesmo adaptador/preset.
ALIASES = {
    "claude": "anthropic",
    "openai-compatible": "openai",
    "compatible": "openai",
    "local": "ollama",
    "goat": "commandcode",
    "cc": "commandcode",
    "commandcode-claude": "commandcode-anthropic",
    "cc-claude": "commandcode-anthropic",
    "zen": "opencode",
    "od": "opendesign",
    "open-design": "opendesign",
    "grok": "xai",
}

PUBLIC_PROVIDERS = tuple(PRESETS)


def normalize_provider(value: str) -> str:
    name = (value or "").strip().lower()
    return ALIASES.get(name, name)


# Atalhos do `/modelo`: um destino por apelido, sem consultar a rede. O valor
# e `("provedor", "modelo")`; modelo vazio = o ultimo usado naquele provedor
# (ou o padrao do preset). `tests/test_providers_presets.py` falha se dois
# atalhos apontarem para o mesmo destino.
SHORTCUTS: dict[str, tuple[str, str]] = {
    "cc": ("commandcode", ""),
    "ds": ("deepseek", "deepseek-flash"),
    "od": ("opendesign", "deepseek-v4.1-flash"),
    "zen": ("opencode", ""),
    "glm": ("commandcode", "z-ai/glm-5.3-flash"),
    "turbo": ("commandcode", "deepseek/deepseek-v4.1-flash-fast"),
}


def resolve_shortcut(
    value: str, last_models: dict[str, str] | None = None
) -> tuple[str, str] | None:
    """Traduz o atalho (`cc`, `ds`, `glm`...) em (provedor, modelo).

    `last_models` guarda o ultimo modelo usado por provedor: voltar para a
    Command Code recoloca o modelo que estava ativo la, nao o padrao. Sem
    historico nem padrao no preset, devolve None (nada a sugerir).
    """
    name = (value or "").strip().lower()
    target = SHORTCUTS.get(name)
    if target is None:
        return None
    provider, model = target
    if model:
        return provider, model
    remembered = (last_models or {}).get(provider, "").strip()
    if remembered:
        return provider, remembered
    preset = PRESETS.get(provider)
    default = preset.default_model.strip() if preset else ""
    return (provider, default) if default else None


def fast_model_ref(provider: str, value: str = ""):
    """Modelo rapido da config, mas so se ele pertencer ao provedor atual.

    `fast_model` fica gravado no config e sobrevive a troca de provedor. Mandar
    o id da Command Code para a API direta da DeepSeek dava 400, entao um valor
    de OUTRO provedor e descartado e vale o rapido declarado no preset. Sem
    valor, tambem vale o preset (vazio = pedido curto no modelo principal).
    """
    from gaia.agent.types import ModelRef

    name = normalize_provider(provider)
    raw = (value or "").strip()
    owner, separador, rest = raw.partition("/")
    if separador and normalize_provider(owner) == name:
        raw = rest
    elif separador:
        raw = ""
    if not raw:
        preset = PRESETS.get(name)
        raw = preset.fast_model.strip() if preset else ""
    return ModelRef(provider_id=name, model_id=raw) if raw else None


def _key_from_auth_file(preset: Preset) -> str:
    """Le a chave gravada pelo CLI original do provedor (se houver)."""
    if not preset.auth_file:
        return ""
    for raw in preset.auth_file:
        path = Path(raw).expanduser()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        node: object = data
        for part in preset.auth_key_path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def preset_api_key(provider: str) -> str:
    """Chave propria do preset: ambiente primeiro, depois o arquivo do CLI."""
    preset = PRESETS.get(normalize_provider(provider))
    if preset is None:
        return ""
    for name in preset.env_keys:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return _key_from_auth_file(preset)


def resolve_api_key(provider: str, configured: str = "") -> str:
    if configured.strip():
        return configured.strip()
    key = os.environ.get("GAIA_AGENT_API_KEY", "").strip()
    if key:
        return key
    return preset_api_key(provider)


def describe_provider(provider: str) -> dict[str, str]:
    name = normalize_provider(provider)
    preset = PRESETS.get(name)
    if preset is None:
        return {"name": name, "api": "openai", "base_url": ""}
    return {"name": name, "api": preset.api, "base_url": preset.base_url}


def build_provider(cfg) -> Provider:
    """Cria o provedor a partir de ``config.AgentConfig``."""
    name = normalize_provider(cfg.provider)
    preset = PRESETS.get(name)
    base_url = (cfg.base_url or "").strip() or (preset.base_url if preset else "")
    if not base_url:
        raise AgentError(
            f"provider desconhecido '{cfg.provider}': defina agent.base_url "
            f"ou use um de: {', '.join(PUBLIC_PROVIDERS)}"
        )
    api_key = resolve_api_key(cfg.provider, cfg.api_key)
    api = preset.api if preset else "openai"
    if api == "anthropic":
        return AnthropicProvider(
            name=name,
            base_url=base_url,
            api_key=api_key,
            timeout=cfg.request_timeout,
            include_usage=cfg.usage,
        )
    return OpenAICompatibleProvider(
        name=name,
        base_url=base_url,
        api_key=api_key,
        timeout=cfg.request_timeout,
        include_usage=cfg.usage,
    )
