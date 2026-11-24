from __future__ import annotations

from pathlib import Path

import pytest

from gaia.config import Config, SecondBrainConfig
from gaia.memory.store import MemoryStore
from gaia.secondbrain.index import SecondBrainIndex
from gaia.secondbrain.notes import SecondBrain


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isola config/dados do ambiente real do usuario.

    ``GAIA_CONFIG`` importa: sem ele, qualquer teste que chame
    ``save_agent_model`` sem caminho explicito escreveria no config real.
    """
    monkeypatch.setenv("GAIA_DOTENV", str(tmp_path / "nao-existe.env"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_CONFIG", str(tmp_path / "config.toml"))
    # Clones do backlog: nunca o ~/pr-workspace real (o worker de um teste nao
    # pode clonar nem mexer em repositorio de verdade).
    monkeypatch.setenv("GAIA_BACKLOG_WORKSPACE", str(tmp_path / "pr-workspace"))
    return tmp_path


@pytest.fixture
def cfg(isolated_env):
    """Config isolada; o caminho vem do ``GAIA_CONFIG`` do ``isolated_env``.

    Nao recarregue a config a mao com outro ``config_path``: quem escreve no
    TOML (painel, ``save_agent_model``) tem de cair em ``tmp_path``, nunca no
    config real do usuario. Precisa de ajuste? Sobrescreva o fixture pedindo
    ``cfg`` (``def cfg(cfg): ...``), como nos testes do Control Center.
    """
    return Config.load(
        config_path=isolated_env / "config.toml",
        env={"GAIA_DATA_DIR": str(isolated_env / "data")},
    )


@pytest.fixture
def store(tmp_path):
    instance = MemoryStore(tmp_path / "gaia.db")
    yield instance
    instance.close()


@pytest.fixture
def brain(tmp_path):
    root = tmp_path / "brain"
    (root / "Inbox").mkdir(parents=True)
    cfg = SecondBrainConfig(path=str(root), default_folder="Inbox")
    index = SecondBrainIndex(cfg, tmp_path / "sb.db")
    instance = SecondBrain(cfg, index)
    yield instance
    index.close()


def write_note(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
