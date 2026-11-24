"""Edicao do TOML preservando comentarios (`/modelo` e `save_agent_model`)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from gaia.config.core import Config


def _agent_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Intervalo (inicio do corpo, fim do corpo) da secao ``[agent]``."""
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if start is not None:
                return start, index
            if stripped == "[agent]":
                start = index + 1
    if start is None:
        return None
    return start, len(lines)


def _replace_agent_key(lines: list[str], key: str, value: str, start: int, end: int) -> bool:
    pattern = re.compile(rf"^(\s*{key}\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^#\n]*)(.*)$")
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match is None:
            continue
        comment = match.group(2)
        if comment and not comment.lstrip().startswith("#"):
            comment = ""
        lines[index] = f'{match.group(1)}"{value}"{(" " + comment.strip()) if comment.strip() else ""}'
        return True
    return False


def save_agent_model(
    provider: str,
    model: str,
    path: Path | None = None,
    *,
    fast_model: str | None = None,
) -> Path:
    """Grava ``agent.provider``/``agent.model`` no TOML preservando o resto.

    Edicao linha a linha de proposito: o arquivo tem comentarios explicativos
    que um dump de TOML destruiria. Uma copia ``.bak`` fica ao lado. Com
    ``fast_model`` explicito, ``agent.fast_model`` acompanha a troca de
    provedor (o valor antigo pertence ao provedor anterior); ``None`` deixa
    como esta.
    """
    target = Path(path or Config.default_config_path()).expanduser()
    provider = (provider or "").strip()
    model = (model or "").strip()
    fast = None if fast_model is None else (fast_model or "").strip()
    if not provider or not model:
        raise ValueError("provider e model são obrigatórios")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        original = target.read_text(encoding="utf-8")
        backup = target.with_suffix(target.suffix + ".bak")
        backup.write_text(original, encoding="utf-8")
        lines = original.splitlines()
    else:
        original = ""
        lines = []
    bounds = _agent_section_bounds(lines)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[agent]", f'provider = "{provider}"', f'model = "{model}"'])
        if fast is not None:
            lines.append(f'fast_model = "{fast}"')
    else:
        start, end = bounds
        alvo: list[tuple[str, str]] = [("provider", provider), ("model", model)]
        if fast is not None:
            alvo.append(("fast_model", fast))
        missing: list[tuple[str, str]] = []
        for key, value in alvo:
            if not _replace_agent_key(lines, key, value, start, end):
                missing.append((key, value))
        if missing:
            insert_at = end
            while insert_at > start and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines[insert_at:insert_at] = [f'{key} = "{value}"' for key, value in missing]
    updated = "\n".join(lines).rstrip("\n") + "\n"
    if updated != original:
        target.write_text(updated, encoding="utf-8")
    return target


def model_env_override(env: dict[str, str] | None = None) -> list[str]:
    """Variaveis que sobrescrevem o modelo do TOML na proxima inicializacao."""
    source = os.environ if env is None else env
    return [
        name
        for name in ("GAIA_AGENT_PROVIDER", "GAIA_AGENT_MODEL")
        if str(source.get(name) or "").strip()
    ]
