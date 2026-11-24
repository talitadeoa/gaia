"""Configuracao da Gaia: pacote com reexport (o import publico nao mudou).

Antes era um `gaia/config.py` de 1408 linhas com cinco assuntos no mesmo
arquivo; agora:

* ``models.py``   — sub-configs pydantic + ``Config`` (paths, load, public_dict)
* ``env.py``      — ``_ENV_MAP``/``_apply_env``, parse de bool, aliases legados
* ``migrate.py``  — ``[opencode]`` -> ``[agent]`` e slugs de modelo antigos
* ``template.py`` — ``_DEFAULT_CONFIG_TEMPLATE`` e ``write_default_config``
* ``edit.py``     — edicao linha a linha do TOML (``save_agent_model``)

Nada aqui muda comportamento: as funcoes foram movidas inteiras.
"""

from __future__ import annotations

from gaia.config.core import Config
from gaia.config.edit import (
    _agent_section_bounds,
    _replace_agent_key,
    model_env_override,
    save_agent_model,
)
from gaia.config.env import (
    _ENV_MAP,
    _LEGACY_ENV_MAP,
    _PROVIDER_API_KEYS,
    _alias_legacy_env,
    _apply_env,
    _export_env,
    _field_is_bool,
    _load_dotenv,
    _migrate_legacy_locations,
    _parse_bool,
)
from gaia.config.env import _ENV_PREFIX as _ENV_PREFIX
from gaia.config.env import _LEGACY_PREFIX as _LEGACY_PREFIX
from gaia.config.migrate import (
    _LEGACY_MODEL_ALIASES,
    _migrate_legacy_config,
    _migrate_legacy_model,
    _preset_fast_model,
    _provider_env_keys,
)
from gaia.config.models import (
    APP_NAME,
    ECHO_LABELS,
    ECHO_LANGUAGES,
    ECHO_NAMES,
    AgendaConfig,
    AgentConfig,
    AudioConfig,
    BriefingConfig,
    BrowserConfig,
    CareerConfig,
    EstudosConfig,
    FocusConfig,
    InterfaceConfig,
    MemoryConfig,
    ProactiveConfig,
    SecondBrainConfig,
    SecretsConfig,
    ServeConfig,
    STTConfig,
    TelegramConfig,
    TTSConfig,
    WhatsAppConfig,
    XConfig,
)
from gaia.config.template import _DEFAULT_CONFIG_TEMPLATE, write_default_config

# Nomes internos reexportados por compatibilidade: `control/settings.py` le
# `_ENV_MAP` e os testes das migracoes tocam os helpers. Sem eles em `__all__`
# o ruff acusa F401 (import que parece morto, mas e a API do pacote).
__all__ = [
    "APP_NAME",
    "ECHO_LABELS",
    "ECHO_LANGUAGES",
    "ECHO_NAMES",
    "_DEFAULT_CONFIG_TEMPLATE",
    "_ENV_MAP",
    "_ENV_PREFIX",
    "_LEGACY_ENV_MAP",
    "_LEGACY_MODEL_ALIASES",
    "_LEGACY_PREFIX",
    "_PROVIDER_API_KEYS",
    "AgendaConfig",
    "AgentConfig",
    "AudioConfig",
    "BriefingConfig",
    "BrowserConfig",
    "CareerConfig",
    "Config",
    "EstudosConfig",
    "FocusConfig",
    "InterfaceConfig",
    "MemoryConfig",
    "ProactiveConfig",
    "STTConfig",
    "SecondBrainConfig",
    "SecretsConfig",
    "ServeConfig",
    "TTSConfig",
    "TelegramConfig",
    "WhatsAppConfig",
    "XConfig",
    "_agent_section_bounds",
    "_alias_legacy_env",
    "_apply_env",
    "_export_env",
    "_field_is_bool",
    "_load_dotenv",
    "_migrate_legacy_config",
    "_migrate_legacy_locations",
    "_migrate_legacy_model",
    "_parse_bool",
    "_preset_fast_model",
    "_provider_env_keys",
    "_replace_agent_key",
    "model_env_override",
    "save_agent_model",
    "write_default_config",
]
