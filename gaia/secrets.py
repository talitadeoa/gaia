"""Cofre de credenciais de terceiros: um arquivo por servico, lido sob demanda.

Por que nao no `~/.config/gaia/.env`: aquele arquivo e o `EnvironmentFile=` do
`gaia-serve.service`, entao tudo que esta la entra no `os.environ` do processo
e o `bash`/`pi` herdam o ambiente inteiro (pendencia conhecida do repo). Uma
chave de Google, GitHub ou X no `.env` fica legivel pelo modelo.

Aqui o valor so existe quando alguem pede: `open_service("github").get("GH_TOKEN")`.
Nada e exportado, nada e impresso, nada vai para o TOML.

Layout (mesmo espirito de `[agenda] env_file` e `[estudos] env_file`):

    ~/.config/gaia/secrets/      700
    ├── github.env               600
    ├── google.env               600
    └── x.env                    600

`set()` e o unico caminho de escrita: `chmod 600`, escrita atomica e as outras
chaves do arquivo preservadas (inclusive comentarios).
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from gaia.config.models import APP_NAME

DIR_MODE = 0o700
FILE_MODE = 0o600

_SERVICO = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_LINHA = re.compile(r"^(?P<lead>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$")


def default_dir() -> Path:
    """Diretorio do cofre. `GAIA_SECRETS_DIR` vence (usado nos testes)."""
    raw = os.environ.get("GAIA_SECRETS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / APP_NAME / "secrets"


def _limpar(valor: str) -> str:
    return (valor or "").strip().strip('"').strip("'")


def _parse(texto: str) -> dict[str, str]:
    dados: dict[str, str] = {}
    for linha in texto.splitlines():
        bruta = linha.strip()
        if not bruta or bruta.startswith("#") or "=" not in bruta:
            continue
        chave, _, valor = bruta.partition("=")
        chave = chave.strip()
        if chave:
            dados[chave] = _limpar(valor)
    return dados


class SecretFile:
    """Um arquivo `KEY=VALUE` do cofre. Leitura sempre do disco."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()

    # ------------------------------------------------------------------ leitura
    def read(self) -> dict[str, str]:
        try:
            return _parse(self.path.read_text(encoding="utf-8"))
        except OSError:
            return {}

    def get(self, *names: str) -> str:
        """Primeiro nome preenchido: arquivo primeiro, ambiente como reserva."""
        dados = self.read()
        for nome in names:
            valor = dados.get(nome, "") or (os.environ.get(nome, "") or "")
            if valor.strip():
                return valor.strip()
        return ""

    # ------------------------------------------------------------------ escrita
    def set(self, key: str, value: str) -> None:
        """Grava/rotaciona uma chave, preservando o resto do arquivo."""
        nome = (key or "").strip()
        if not _SERVICO.match(nome.lower()):
            raise ValueError(f"nome de chave invalido: {nome!r}")
        limpo = _limpar(value)
        if not limpo:
            raise ValueError("valor vazio: use remova a chave no arquivo")
        if "\n" in limpo or "\r" in limpo:
            raise ValueError("valor nao pode conter quebra de linha")

        linhas = self._linhas()
        nova = f"{nome}={limpo}"
        for indice, linha in enumerate(linhas):
            achado = _LINHA.match(linha)
            if achado is not None and achado.group("key") == nome:
                linhas[indice] = nova
                break
        else:
            if linhas and linhas[-1].strip():
                linhas.append("")
            linhas.append(nova)

        self._gravar(linhas)

    def _linhas(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def _gravar(self, linhas: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(DIR_MODE)
        except OSError:  # pragma: no cover - FS sem chmod
            pass
        # Backups anteriores nao podem acumular segredo antigo solto na pasta.
        if self.path.is_file():
            marca = datetime.now().strftime("%Y%m%d-%H%M%S")
            copia = self.path.with_name(f"{self.path.name}.bak-{marca}")
            try:
                copia.write_bytes(self.path.read_bytes())
                copia.chmod(FILE_MODE)
            except OSError:  # pragma: no cover - FS sem escrita
                pass
        texto = "\n".join(linhas).rstrip("\n") + "\n"
        temporario = self.path.with_name(self.path.name + ".tmp")
        temporario.write_text(texto, encoding="utf-8")
        try:
            temporario.chmod(FILE_MODE)
        except OSError:  # pragma: no cover - FS sem chmod
            pass
        temporario.replace(self.path)

    # ------------------------------------------------------------------ saude
    def problems(self) -> list[str]:
        """Avisos de configuracao para `gaia doctor` (nunca o valor)."""
        if not self.path.is_file():
            return []
        avisos: list[str] = []
        try:
            modo = self.path.stat().st_mode & 0o777
        except OSError:
            return avisos
        if modo & 0o077:
            avisos.append(
                f"{self.path} esta em {oct(modo)[-3:]}: use chmod 600 "
                "(segredo legivel por outro usuario)"
            )
        return avisos


def open_service(nome: str, diretorio: Path | None = None) -> SecretFile:
    """`open_service("github")` -> `<cofre>/github.env`."""
    limpo = (nome or "").strip()
    if not _SERVICO.match(limpo):
        raise ValueError(f"servico invalido: {nome!r} (use letras, digitos, _ . -)")
    base = Path(diretorio) if diretorio is not None else default_dir()
    return SecretFile(base / f"{limpo}.env")


__all__ = ["DIR_MODE", "FILE_MODE", "SecretFile", "default_dir", "open_service"]
