"""Mapa `GAIA_*` -> campo do modelo e aplicacao do ambiente sobre o TOML.

Este modulo nao importa `gaia.config.models` no topo: `models.py` importa
daqui o prefixo das variaveis, e a importacao de `Config` (usada so em
`_field_is_bool`) e tardia dentro da funcao.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

APP_NAME = "gaia"
_LEGACY_PREFIX = "ZAYA_"
_ENV_PREFIX = f"{APP_NAME.upper()}_"

def _migrate_legacy_locations() -> None:
    """Copia config e dados do nome antigo (``zaya``) na primeira execucao."""
    home = Path.home()
    for old, new in (
        (home / ".config" / "zaya", home / ".config" / APP_NAME),
        (home / ".local" / "share" / "zaya", home / ".local" / "share" / APP_NAME),
    ):
        if old.is_dir() and not new.exists():
            try:
                shutil.copytree(old, new)
            except OSError:
                continue
    config_dir = home / ".config" / APP_NAME
    for name in ("config.toml", ".env"):
        target = config_dir / name
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            continue
        renamed = text.replace(_LEGACY_PREFIX, _ENV_PREFIX)
        if renamed != text:
            target.write_text(renamed, encoding="utf-8")


def _alias_legacy_env(env: dict[str, str]) -> None:
    """Espelha ``ZAYA_*`` para ``GAIA_*`` quando o nome novo nao existe."""
    for key, value in list(env.items()):
        if key.startswith(_LEGACY_PREFIX):
            env.setdefault(_ENV_PREFIX + key[len(_LEGACY_PREFIX) :], value)


def _export_env(env: dict[str, str]) -> None:
    """Espelha o .env no ambiente do processo, sem sobrescrever o que ja existe."""
    for key, value in env.items():
        os.environ.setdefault(key, value)


def _load_dotenv(path: Path, env: dict[str, str]) -> None:
    """Carrega KEY=VALUE simples sem sobrescrever o ambiente existente."""
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    except OSError:
        return


_ENV_MAP: dict[str, tuple[str, str]] = {
    "GAIA_AGENDA_ENABLED": ("agenda", "enabled"),
    "GAIA_AGENDA_ENV_FILE": ("agenda", "env_file"),
    "GAIA_AGENDA_MAX_ITEMS": ("agenda", "max_items"),
    "GAIA_AGENDA_NOTIFY_PENDING": ("agenda", "notify_pending"),
    "GAIA_AGENDA_POLL_MINUTES": ("agenda", "poll_minutes"),
    "GAIA_AGENDA_TASKS_POLL_SECONDS": ("agenda", "tasks_poll_seconds"),
    "GAIA_ESTUDOS_ENABLED": ("estudos", "enabled"),
    "GAIA_ESTUDOS_ENV_FILE": ("estudos", "env_file"),
    "GAIA_ESTUDOS_POLL_MINUTES": ("estudos", "poll_minutes"),
    "GAIA_ESTUDOS_REMIND_WHEN_LATE": ("estudos", "remind_when_late"),
    "GAIA_ESTUDOS_REMIND_AT": ("estudos", "remind_at"),
    "GAIA_ESTUDOS_MAX_ITEMS": ("estudos", "max_items"),
    "GAIA_AUDIO_AUTO_MUTE_SECONDS": ("audio", "auto_mute_seconds"),
    "GAIA_AUDIO_CONTINUOUS": ("audio", "continuous"),
    "GAIA_AUDIO_INPUT_DEVICE": ("audio", "input_device"),
    "GAIA_AUDIO_MAX_SECONDS": ("audio", "max_seconds"),
    "GAIA_AUDIO_SILENCE_MS": ("audio", "silence_ms"),
    "GAIA_AUDIO_VAD_THRESHOLD": ("audio", "vad_threshold"),
    "GAIA_PI_ENABLED": ("pi", "enabled"),
    "GAIA_PI_BINARY": ("pi", "binary"),
    "GAIA_PI_WORKSPACE": ("pi", "workspace"),
    "GAIA_PI_MODEL": ("pi", "model"),
    "GAIA_BACKLOG_ENABLED": ("backlog", "enabled"),
    "GAIA_BACKLOG_AGENDA_SUFFIX": ("backlog", "agenda_suffix"),
    "GAIA_BACKLOG_WORKSPACE": ("backlog", "workspace"),
    "GAIA_BACKLOG_GITHUB_OWNER": ("backlog", "github_owner"),
    "GAIA_BACKLOG_BASE_BRANCH": ("backlog", "base_branch"),
    "GAIA_BACKLOG_PRUNE_DAYS": ("backlog", "prune_days"),
    "GAIA_BROWSER_ENABLED": ("browser", "enabled"),
    "GAIA_BROWSER_HEADLESS": ("browser", "headless"),
    "GAIA_BROWSER_PROFILE_DIR": ("browser", "profile_dir"),
    "GAIA_BROWSER_TIMEOUT_MS": ("browser", "timeout_ms"),
    "GAIA_X_ENABLED": ("x", "enabled"),
    "GAIA_X_SECRETS_DIR": ("x", "secrets_dir"),
    "GAIA_SECRETS_ENABLED": ("secrets", "enabled"),
    "GAIA_SECRETS_DIR": ("secrets", "dir"),
    "GAIA_FOCUS_ENABLED": ("focus", "enabled"),
    "GAIA_FOCUS_PATH": ("focus", "path"),
    "GAIA_FOCUS_PROMPT_ON_MOON": ("focus", "prompt_on_moon"),
    "GAIA_CHINESE": ("interface", "chinese_echo"),
    "GAIA_ECHO_LANGUAGE": ("interface", "echo_language"),
    "GAIA_FAST": ("interface", "fast"),
    "GAIA_FAST_SMALLTALK": ("interface", "fast_smalltalk"),
    "GAIA_UI_DASHBOARD_COLLAPSED": ("interface", "dashboard_collapsed"),
    "GAIA_UI_SHOW_DIFFS": ("interface", "show_diffs"),
    "GAIA_UI_SHOW_METAS_ON_START": ("interface", "show_metas_on_start"),
    "GAIA_UI_SHOW_REASONING": ("interface", "show_reasoning"),
    "GAIA_TELEGRAM_SHOW_REASONING": ("telegram", "show_reasoning"),
    "GAIA_SERVE_ENABLED": ("serve", "enabled"),
    "GAIA_SERVE_HOST": ("serve", "host"),
    "GAIA_SERVE_PORT": ("serve", "port"),
    "GAIA_SERVE_TOKEN": ("serve", "token"),
    "GAIA_CONTROL_TOKEN": ("serve", "control_token"),
    "GAIA_SERVE_ALLOW_REMOTE": ("serve", "allow_remote"),
    "GAIA_SERVE_INSTANCE_ID": ("serve", "instance_id"),
    "GAIA_SERVE_TELEGRAM": ("serve", "telegram"),
    "GAIA_SERVE_VOICE_SOURCE": ("serve", "voice_source"),
    "GAIA_UI_SELECTION_MODE": ("interface", "selection_mode"),
    "GAIA_UI_MARKDOWN": ("interface", "markdown_render"),
    "GAIA_UI": ("interface", "ui"),
    "GAIA_AGENT_ALLOW_OUTSIDE_PROJECT": ("agent", "allow_outside_project"),
    "GAIA_AGENT_API_KEY": ("agent", "api_key"),
    "GAIA_AGENT_AUTO_APPROVE": ("agent", "auto_approve"),
    "GAIA_AGENT_BASE_URL": ("agent", "base_url"),
    "GAIA_AGENT_CACHE_PROMPT": ("agent", "cache_prompt"),
    "GAIA_AGENT_DIRECTORY": ("agent", "directory"),
    "GAIA_AGENT_FAST_MODEL": ("agent", "fast_model"),
    "GAIA_AGENT_HISTORY_SUMMARY": ("agent", "history_summary"),
    "GAIA_AGENT_HISTORY_SUMMARY_MODEL": ("agent", "history_summary_model"),
    "GAIA_AGENT_MAX_HISTORY_CHARS": ("agent", "max_history_chars"),
    "GAIA_AGENT_MAX_HISTORY_MESSAGES": ("agent", "max_history_messages"),
    "GAIA_AGENT_MAX_STEPS": ("agent", "max_steps"),
    "GAIA_AGENT_MAX_TOKENS": ("agent", "max_tokens"),
    "GAIA_AGENT_MODEL": ("agent", "model"),
    "GAIA_AGENT_PROVIDER": ("agent", "provider"),
    "GAIA_AGENT_REQUEST_TIMEOUT": ("agent", "request_timeout"),
    "GAIA_AGENT_RESUME_SESSION": ("agent", "resume_session"),
    "GAIA_AGENT_STALL_TIMEOUT": ("agent", "stall_timeout"),
    "GAIA_AGENT_SUMMARY_CHARS": ("agent", "summary_chars"),
    "GAIA_AGENT_TEMPERATURE": ("agent", "temperature"),
    "GAIA_AGENT_TOOLS": ("agent", "tools"),
    "GAIA_AGENT_TOOL_RESULT_CHARS": ("agent", "tool_result_chars"),
    "GAIA_AGENT_USAGE": ("agent", "usage"),
    "GAIA_MEMORY_AT": ("memory", "at"),
    "GAIA_MEMORY_DREAM_AT": ("memory", "dream_at"),
    "GAIA_MEMORY_DREAM_ENABLED": ("memory", "dream_enabled"),
    "GAIA_MEMORY_DREAM_FOLDER": ("memory", "dream_folder"),
    "GAIA_MEMORY_DREAM_NOTIFY": ("memory", "dream_notify"),
    "GAIA_MEMORY_DREAM_NOTIFY_AT": ("memory", "dream_notify_at"),
    "GAIA_MEMORY_DREAM_UNTIL": ("memory", "dream_until"),
    "GAIA_MEMORY_ENABLED": ("memory", "enabled"),
    "GAIA_MEMORY_FOLDER": ("memory", "folder"),
    "GAIA_MEMORY_ON_EXIT": ("memory", "on_exit"),
    "GAIA_MEMORY_RETRY_MINUTES": ("memory", "retry_minutes"),
    "GAIA_MEMORY_USE_LLM": ("memory", "use_llm"),
    "GAIA_BRIEFING_ENABLED": ("briefing", "enabled"),
    "GAIA_BRIEFING_MORNING_AT": ("briefing", "morning_at"),
    "GAIA_BRIEFING_NOON_AT": ("briefing", "noon_at"),
    "GAIA_BRIEFING_NOON_UNTIL": ("briefing", "noon_until"),
    "GAIA_BRIEFING_MORNING_ROUTINE": ("briefing", "morning_routine"),
    "GAIA_BRIEFING_MAX_TASKS": ("briefing", "max_tasks"),
    "GAIA_BRIEFING_EVENING_AT": ("briefing", "evening_at"),
    "GAIA_BRIEFING_EVENING_ENABLED": ("briefing", "evening_enabled"),
    "GAIA_BRIEFING_EVENING_INSIGHT": ("briefing", "evening_insight"),
    "GAIA_BRIEFING_EVENING_UNTIL": ("briefing", "evening_until"),
    "GAIA_BRIEFING_MARKET_ENABLED": ("briefing", "market_enabled"),
    "GAIA_BRIEFING_MARKET_AT": ("briefing", "market_at"),
    "GAIA_BRIEFING_MARKET_UNTIL": ("briefing", "market_until"),
    "GAIA_CAREER_ENABLED": ("career", "enabled"),
    "GAIA_CAREER_AT": ("career", "at"),
    "GAIA_CAREER_UNTIL": ("career", "until"),
    "GAIA_CAREER_MAX_ITEMS": ("career", "max_items"),
    "GAIA_CAREER_STATE_FILE": ("career", "state_file"),
    "GAIA_PROACTIVE_CHECK_INTERVAL_SECONDS": ("proactive", "check_interval_seconds"),
    "GAIA_PROACTIVE_DAILY_MAX": ("proactive", "daily_max"),
    "GAIA_PROACTIVE_ENABLED": ("proactive", "enabled"),
    "GAIA_PROACTIVE_GREETING_VIA_LLM": ("proactive", "greeting_via_llm"),
    "GAIA_PROACTIVE_IDLE_CHECKIN": ("proactive", "idle_checkin"),
    "GAIA_PROACTIVE_IDLE_MINUTES": ("proactive", "idle_minutes"),
    "GAIA_PROACTIVE_MIN_INTERVAL_MINUTES": ("proactive", "min_interval_minutes"),
    "GAIA_PROACTIVE_QUIET_HOURS": ("proactive", "quiet_hours"),
    "GAIA_PROACTIVE_REMINDERS": ("proactive", "reminders"),
    "GAIA_PROACTIVE_STARTUP_GREETING": ("proactive", "startup_greeting"),
    "GAIA_SB_AUTO_CONTEXT": ("secondbrain", "auto_context"),
    "GAIA_SB_CONFIRM_CREATE": ("secondbrain", "confirm_create"),
    "GAIA_SB_CONTEXT_CHARS": ("secondbrain", "context_chars"),
    "GAIA_SB_CONTEXT_MAX_CHARS": ("secondbrain", "context_max_chars"),
    "GAIA_SB_CONTEXT_RESULTS": ("secondbrain", "context_results"),
    "GAIA_SECOND_BRAIN_INDEX": ("secondbrain", "index_db"),
    "GAIA_SECOND_BRAIN_PATH": ("secondbrain", "path"),
    "GAIA_SB_EMBED_API_KEY": ("secondbrain", "embed_api_key"),
    "GAIA_SB_EMBED_BASE_URL": ("secondbrain", "embed_base_url"),
    "GAIA_SB_EMBED_BATCH_SIZE": ("secondbrain", "embed_batch_size"),
    "GAIA_SB_EMBED_MIN_SCORE": ("secondbrain", "embed_min_score"),
    "GAIA_SB_EMBED_TIMEOUT": ("secondbrain", "embed_timeout"),
    "GAIA_SB_EMBED_MODEL": ("secondbrain", "embed_model"),
    "GAIA_SB_PREFERENCES_FILE": ("secondbrain", "preferences_file"),
    "GAIA_SB_REINDEX_ON_START": ("secondbrain", "reindex_on_start"),
    "GAIA_SB_REQUIRE_CONFIRMATION": ("secondbrain", "require_confirmation"),
    "GAIA_STT_CONFIRM_TRANSCRIPT": ("stt", "confirm_transcript"),
    "GAIA_STT_LANGUAGE": ("stt", "language"),
    "GAIA_STT_MODEL": ("stt", "model"),
    "GAIA_STT_PARTIAL_INTERVAL_SECONDS": ("stt", "partial_interval_seconds"),
    "GAIA_STT_PARTIAL_TRANSCRIPTION": ("stt", "partial_transcription"),
    "GAIA_STT_PARTIAL_WINDOW_SECONDS": ("stt", "partial_window_seconds"),
    "GAIA_TELEGRAM_ALLOWED_CHAT_ID": ("telegram", "allowed_chat_id"),
    "GAIA_TELEGRAM_API_BASE_URL": ("telegram", "api_base_url"),
    "GAIA_TELEGRAM_AUTO_APPROVE": ("telegram", "auto_approve"),
    "GAIA_TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "GAIA_TELEGRAM_ENABLED": ("telegram", "enabled"),
    "GAIA_TELEGRAM_POLL_TIMEOUT": ("telegram", "poll_timeout"),
    "GAIA_TELEGRAM_SHELL_ACCESS": ("telegram", "shell_access"),
    "GAIA_TELEGRAM_VOICE_REPLIES": ("telegram", "voice_replies"),
    "GAIA_AUDIOBOOK_ENABLED": ("audiobook", "enabled"),
    "GAIA_AUDIOBOOK_INBOX": ("audiobook", "inbox"),
    "GAIA_AUDIOBOOK_VOICE": ("audiobook", "voice"),
    "GAIA_AUDIOBOOK_WATCH_MINUTES": ("audiobook", "watch_minutes"),
    "GAIA_TTS_VOICE": ("tts", "edge_voice"),
    "GAIA_TTS_ENABLED": ("tts", "enabled"),
    "GAIA_TTS_PROVIDER": ("tts", "provider"),
    "GAIA_TTS_SENTENCE_PAUSE_MS": ("tts", "sentence_pause_ms"),
    "GAIA_WHATSAPP_ACCESS_TOKEN": ("whatsapp", "access_token"),
    "GAIA_WHATSAPP_API_VERSION": ("whatsapp", "api_version"),
    "GAIA_WHATSAPP_ENABLED": ("whatsapp", "enabled"),
    "GAIA_WHATSAPP_PHONE_NUMBER_ID": ("whatsapp", "phone_number_id"),
    "GAIA_WHATSAPP_RECIPIENT": ("whatsapp", "recipient"),
    "GAIA_WHATSAPP_VERIFY_TOKEN": ("whatsapp", "verify_token"),
    "GAIA_WHATSAPP_WEBHOOK_HOST": ("whatsapp", "webhook_host"),
    "GAIA_WHATSAPP_WEBHOOK_PORT": ("whatsapp", "webhook_port"),
}




def _field_is_bool(section: str, field: str) -> bool:
    """True se o campo do modelo e booleano (parse dedicado de env)."""
    from gaia.config.core import Config

    section_field = Config.model_fields.get(section)
    if section_field is None:
        return False
    field_info = getattr(section_field.annotation, "model_fields", {}).get(field)
    return field_info is not None and field_info.annotation is bool


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}




_LEGACY_ENV_MAP: dict[str, str] = {
    "GAIA_OPENCODE_AUTO_APPROVE": "auto_approve",
    "GAIA_OPENCODE_DIRECTORY": "directory",
    "GAIA_OPENCODE_MODEL": "model",
}

# Modelos do catalogo antigo que nao existem na API direta da DeepSeek.
# Chaves de API de cada provedor, na ordem em que sao procuradas no ambiente.
# A chave so serve ao SEU provedor: usar a lista inteira mandava o token da
# DeepSeek no header da Command Code (401 em todo turno).


_PROVIDER_API_KEYS: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "claude": ("ANTHROPIC_API_KEY",),
    "commandcode": ("COMMAND_CODE_API_KEY", "COMMANDCODE_API_KEY"),
    "commandcode-anthropic": ("COMMAND_CODE_API_KEY", "COMMANDCODE_API_KEY"),
    "opendesign": ("OPENDESIGN_API_KEY", "OPEN_DESIGN_API_KEY"),
    "opencode": ("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "xai": ("XAI_API_KEY",),
}




def _apply_env(raw: dict[str, Any], env: dict[str, str]) -> None:
    # Import tardio: `migrate.py` importa `_PROVIDER_API_KEYS` daqui, entao o
    # topo deste arquivo nao pode importar de volta.
    from gaia.config.migrate import _LEGACY_MODEL_ALIASES, _provider_env_keys

    for key, (section, field) in _ENV_MAP.items():
        value = env.get(key)
        if value is None or value == "":
            continue
        if _field_is_bool(section, field):
            raw.setdefault(section, {})[field] = _parse_bool(value)
        else:
            raw.setdefault(section, {})[field] = value

    agent = raw.setdefault("agent", {})
    if isinstance(agent, dict):
        for key, field in _LEGACY_ENV_MAP.items():
            value = env.get(key)
            if value and field not in agent:
                if field == "auto_approve":
                    agent[field] = _parse_bool(value)
                elif field == "model" and "/" in value:
                    provider, _, model = value.partition("/")
                    alias = _LEGACY_MODEL_ALIASES.get(value)
                    if alias:
                        provider, model = alias
                    agent.setdefault("provider", provider)
                    agent["model"] = model
                else:
                    agent[field] = value

    # A chave lida do .env precisa chegar ao agente: `resolve_api_key` consulta
    # o ambiente do processo, mas o .env e carregado numa copia local. A chave
    # e escolhida PELO PROVEDOR CONFIGURADO: varrer todos os nomes mandava a
    # DEEPSEEK_API_KEY no header da Command Code e o turno morria em 401.
    if isinstance(agent, dict) and not str(agent.get("api_key") or "").strip():
        provider = str(agent.get("provider") or "commandcode")
        for key in ("GAIA_AGENT_API_KEY", *_provider_env_keys(provider)):
            if key and env.get(key):
                agent["api_key"] = env[key]
                break

    if env.get("GAIA_DATA_DIR"):
        raw["data_dir"] = env["GAIA_DATA_DIR"]
