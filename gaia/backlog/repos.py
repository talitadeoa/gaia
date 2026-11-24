"""Repositorios dos projetos do backlog: o mapa e a validacao do ``owner/repo``.

O backlog nao chuta repositorio: projeto sem linha em ``[backlog.repos]`` fica
sem despacho e o card pede o repo no ponto de adicionar. Este modulo guarda o
default (que o seed do config repete) e a unica regra de formato aceita.
"""

from __future__ import annotations

import re

# Default do ``BacklogConfig.repos``. Fica aqui tambem para o card e os testes
# terem a mesma lista sem carregar o pydantic.
DEFAULT_REPOS: dict[str, str] = {
    "gaia": "talitadeoa/gaia",
    "agenda": "talitadeoa/agenda",
    "suite": "talitadeoa/abin-suite",
}

# owner/repo do GitHub: letras, numeros, `.`, `-` e `_`. Sem espacos, sem URL.
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9._-]{1,100}$")


def validate_repo(repo: str) -> str:
    """Normaliza e valida ``dono/repo``; ``ValueError`` com o motivo se nao.

    Aceita ``https://github.com/dono/repo`` e ``dono/repo.git`` (o que a pessoa
    cola do navegador) e devolve sempre ``dono/repo``.
    """
    texto = " ".join(str(repo or "").split())
    if not texto:
        raise ValueError("repositorio vazio (use dono/repo)")
    texto = re.sub(r"^https?://(www\.)?github\.com/", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\.git$", "", texto, flags=re.IGNORECASE)
    texto = texto.strip("/")
    if not _REPO.match(texto):
        raise ValueError(f"repositorio invalido: {repo!r} (esperado dono/repo)")
    return texto


__all__ = ["DEFAULT_REPOS", "validate_repo"]
