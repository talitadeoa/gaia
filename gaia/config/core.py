"""`Config`: junta as sub-configs, resolve paths e le o TOML.

Ordem de precedencia (maior vence): ambiente/`.env` > TOML > defaults.
Segredos nunca sao gravados no config.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaia.audiobook.config import AudiobookConfig
from gaia.config.env import (
    _ENV_PREFIX,
    _LEGACY_PREFIX,
    _alias_legacy_env,
    _apply_env,
    _export_env,
    _load_dotenv,
    _migrate_legacy_locations,
)
from gaia.config.migrate import _migrate_legacy_config, _migrate_legacy_model
from gaia.config.models import (
    APP_NAME,
    AgendaConfig,
    AgentConfig,
    AudioConfig,
    BacklogConfig,
    BotConfig,
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
from gaia.pi.config import PiConfig


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent: AgentConfig = Field(default_factory=AgentConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    secondbrain: SecondBrainConfig = Field(default_factory=SecondBrainConfig)
    interface: InterfaceConfig = Field(default_factory=InterfaceConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    career: CareerConfig = Field(default_factory=CareerConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    agenda: AgendaConfig = Field(default_factory=AgendaConfig)
    estudos: EstudosConfig = Field(default_factory=EstudosConfig)
    focus: FocusConfig = Field(default_factory=FocusConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    serve: ServeConfig = Field(default_factory=ServeConfig)
    audiobook: AudiobookConfig = Field(default_factory=AudiobookConfig)
    pi: PiConfig = Field(default_factory=PiConfig)
    backlog: BacklogConfig = Field(default_factory=BacklogConfig)
    # Bots especialistas: `[bots.<id>]` sobrepoe o registry do codigo. Sem
    # entrada em `_ENV_MAP` de proposito: nao existe parser de lista/dict no
    # ambiente e a variavel viraria string.
    bots: dict[str, BotConfig] = Field(default_factory=dict)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    x: XConfig = Field(default_factory=XConfig)
    data_dir: str = ""

    # Caminho de onde o config foi carregado (informativo).
    config_file: str = ""

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _env_path(suffix: str) -> str:
        for key in (f"{_ENV_PREFIX}{suffix}", f"{_LEGACY_PREFIX}{suffix}"):
            value = os.environ.get(key)
            if value:
                return value
        return ""

    @staticmethod
    def default_config_path() -> Path:
        env = Config._env_path("CONFIG")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / "config.toml"

    @staticmethod
    def default_dotenv_path() -> Path:
        env = Config._env_path("DOTENV")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".config" / APP_NAME / ".env"

    @staticmethod
    def default_data_dir() -> Path:
        env = Config._env_path("DATA_DIR")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".local" / "share" / APP_NAME

    def resolved_data_dir(self) -> Path:
        base = Path(self.data_dir).expanduser() if self.data_dir else self.default_data_dir()
        return base.resolve()

    def db_path(self) -> Path:
        path = self.resolved_data_dir() / f"{APP_NAME}.db"
        legacy = path.with_name("zaya.db")
        if not path.exists() and legacy.exists():
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(legacy) + suffix)
                if source.exists():
                    source.replace(Path(str(path) + suffix))
        return path

    def index_path(self) -> Path:
        if self.secondbrain.index_db:
            return Path(self.secondbrain.index_db).expanduser().resolve()
        return self.resolved_data_dir() / "sb_index.db"

    def log_dir(self) -> Path:
        return self.resolved_data_dir() / "logs"

    def browser_profile_dir(self) -> Path:
        if self.browser.profile_dir:
            return Path(self.browser.profile_dir).expanduser().resolve()
        return self.resolved_data_dir() / "browser"

    def second_brain_path(self) -> Path:
        return Path(self.secondbrain.path).expanduser().resolve()

    def preferences_path(self) -> Path:
        if self.secondbrain.preferences_file:
            return Path(self.secondbrain.preferences_file).expanduser().resolve()
        return self.second_brain_path() / "preferences.md"

    def project_directory(self) -> Path:
        if self.agent.directory:
            return Path(self.agent.directory).expanduser().resolve()
        return Path.cwd().resolve()

    def audiobook_inbox(self) -> Path:
        return self.audiobook.inbox_path(self.resolved_data_dir())

    def audiobook_outbox(self) -> Path:
        return self.audiobook.outbox_path(self.resolved_data_dir())

    def secrets_dir(self) -> Path:
        if self.secrets.dir.strip():
            return Path(self.secrets.dir).expanduser().resolve()
        from gaia.secrets import default_dir

        return default_dir()

    def ensure_dirs(self) -> None:
        dirs = [self.resolved_data_dir(), self.log_dir()]
        if self.audiobook.enabled:
            # A pasta de entrada precisa existir para a pessoa simplesmente
            # arrastar PDFs para ela.
            dirs += [self.audiobook_inbox(), self.audiobook_outbox()]
        for path in dirs:
            path.mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        if data.get("agent", {}).get("api_key"):
            data["agent"]["api_key"] = "***"
        for field in ("access_token", "verify_token"):
            if data.get("whatsapp", {}).get(field):
                data["whatsapp"][field] = "***"
        if data.get("telegram", {}).get("bot_token"):
            data["telegram"]["bot_token"] = "***"
        if data.get("secondbrain", {}).get("embed_api_key"):
            data["secondbrain"]["embed_api_key"] = "***"
        if data.get("serve", {}).get("token"):
            data["serve"]["token"] = "***"
        if data.get("serve", {}).get("control_token"):
            data["serve"]["control_token"] = "***"
        return data

    # ------------------------------------------------------------------ load
    @classmethod
    def load(
        cls, config_path: str | Path | None = None, env: dict[str, str] | None = None
    ) -> Config:
        real_env = env is None
        if real_env:
            _migrate_legacy_locations()
        env = dict(os.environ if env is None else env)
        _load_dotenv(cls.default_dotenv_path(), env)
        _alias_legacy_env(env)
        if real_env:
            # Deixa o que veio do .env visivel para subprocessos (ferramenta
            # de shell, CLIs filhas) sem sobrescrever o ambiente existente.
            _export_env(env)

        path = Path(config_path).expanduser() if config_path else cls.default_config_path()
        raw: dict[str, Any] = {}
        if path.is_file():
            with path.open("rb") as fh:
                raw = tomllib.load(fh)

        _migrate_legacy_config(raw)
        _migrate_legacy_model(raw)
        _apply_env(raw, env)

        cfg = cls.model_validate(raw)
        cfg.config_file = str(path)
        cfg.ensure_dirs()
        return cfg
