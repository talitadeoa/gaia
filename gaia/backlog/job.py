"""Executa um item do backlog num clone do repositorio e abre o PR.

O caminho e deterministico de proposito:

1. **clone** - o repositorio mapeado vive em ``<workspace>/<owner>-<repo>``. Se
   nao existe, ``gh repo clone``; se existe, ``git fetch`` e o branch do item e
   recriado a partir de ``origin/<base>``. Workspace sujo recusa: nao se joga
   trabalho de ninguem fora.
2. **pi** - o harness recebe o item + contexto e implementa; ele trabalha no
   clone, roda os testes e **nao** faz commit/push/PR (isso e daqui, para o
   estado do card nao depender do que o modelo resolveu fazer no fim).
3. **commit/push** - se o pi nao mudou nada, o item falha com a mensagem do
   proprio pi (nao ha PR vazio).
4. **PR** - ``gh pr create`` com ``--repo`` e ``--head <owner>:<branch>``. No
   setup do mini o ``origin`` e o fork ``piratistemica/*`` e o alvo e
   ``talitadeoa/*``; ``--head`` explicito e o que faz o PR sair do fork certo.
   Se ja existe PR para o branch, reaproveita (idempotente).

Nada aqui toca rede nos testes: ``run`` e injetavel e o ``pi`` e um callable.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gaia.config.models import BacklogConfig
from gaia.pi.config import PiConfig
from gaia.pi.runner import PiError, PiResult, PiRunner

logger = logging.getLogger("gaia.backlog")

# Resultado de um comando: (returncode, stdout, stderr). Injetavel nos testes.
CommandRunner = Callable[[list[str], Path], tuple[int, str, str]]


class BacklogJobError(RuntimeError):
    """Falha que vira ``failed`` no card, com mensagem para a pessoa."""


@dataclass
class BacklogResult:
    ok: bool
    text: str = ""
    repo: str = ""
    branch: str = ""
    pr_url: str = ""
    pr_number: int = 0
    detail: str = ""
    error: str = ""


def _run_command(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        return 127, "", f"comando nao encontrado: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", "comando excedeu 180s"
    except OSError as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def slugify(text: str, limit: int = 40) -> str:
    bruto = unicodedata.normalize("NFKD", str(text or "").lower())
    limpo = "".join(ch for ch in bruto if not unicodedata.combining(ch))
    limpo = re.sub(r"[^a-z0-9]+", "-", limpo).strip("-")
    return (limpo[:limit].rstrip("-") or "item")


def branch_name(prefix: str, item_id: int, title: str) -> str:
    base = (prefix or "gaia/backlog").strip().strip("/")
    return f"{base}/{int(item_id)}-{slugify(title)}"


def clone_dir(workspace: Path, repo: str) -> Path:
    owner, _, nome = repo.partition("/")
    return workspace / f"{owner}-{nome or 'repo'}"


def _owner_of(repo: str) -> str:
    return repo.partition("/")[0].strip()


class BacklogJob:
    """Um item -> branch -> PR. Uma instancia por item (sem estado global)."""

    def __init__(
        self,
        cfg: BacklogConfig,
        pi_cfg: PiConfig,
        *,
        run: CommandRunner | None = None,
        pi_runner: Any = None,
        workspace: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.pi_cfg = pi_cfg
        self._run = run or _run_command
        # O workspace do backlog cai no do pi quando nao ha um proprio: e a
        # mesma raiz que o runner valida (fora dela ele recusa por seguranca).
        self.workspace = Path(
            workspace or cfg.workspace or pi_cfg.workspace
        ).expanduser()
        self._pi = pi_runner or PiRunner(self._runner_config())

    def _runner_config(self) -> PiConfig:
        """PiConfig com o workspace do backlog como raiz permitida.

        O ``PiRunner`` recusa caminho fora de ``pi.workspace``. Se o backlog
        aponta para outra pasta (comum no mini), o runner precisa saber dela;
        tudo o mais (binario, modelo, timeout, protect) continua o do pi.
        """
        try:
            atual = Path(self.pi_cfg.workspace).expanduser().resolve()
            pedida = self.workspace.expanduser().resolve()
        except OSError:
            return self.pi_cfg
        if atual == pedida:
            return self.pi_cfg
        return self.pi_cfg.model_copy(update={"workspace": str(pedida)})

    # ------------------------------------------------------------------ repo
    def repo_of(self, item: dict[str, Any]) -> str:
        """``owner/repo`` do projeto, ou ``""`` (nunca chuta dono/Projeto).

        Projeto sem mapeamento em ``[backlog.repos]`` nao despacha: ``execute``
        recusa antes de tocar em git/gh/pi, com o motivo no card.
        """
        projeto = str(item.get("project") or "").strip()
        return self.cfg.repo_for(projeto) if projeto else ""

    def destination(self, item: dict[str, Any]) -> Path:
        return clone_dir(self.workspace, self.repo_of(item))

    def _exec(
        self, args: list[str], cwd: Path, *, aceitar: tuple[int, ...] = (0,)
    ) -> str:
        codigo, saida, erro = self._run(args, cwd)
        if codigo not in aceitar:
            detalhe = (erro or saida).strip()[:1200]
            raise BacklogJobError(
                f"`{' '.join(args[:3])}` falhou ({codigo}): {detalhe}"
            )
        return saida

    def prepare_clone(self, item: dict[str, Any], branch: str) -> Path:
        """Clone existente ou novo, no branch do item, a partir de ``origin/base``."""
        repo = self.repo_of(item)
        destino = self.destination(item)
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not (destino / ".git").is_dir():
            if destino.exists() and any(destino.iterdir()):
                raise BacklogJobError(
                    f"{destino} existe e nao e um clone git; esvazie ou aponte "
                    "backlog.workspace para outra pasta"
                )
            self.workspace_guard(destino)
            self._exec(
                [self.cfg.gh_binary, "repo", "clone", repo, str(destino)],
                self.workspace,
            )
        # Workspace sujo = alguem mexeu a mao. Recusar e melhor que commit
        # misturado com item alheio; o card mostra o motivo.
        status = self._exec(["git", "status", "--porcelain"], destino)
        if status.strip():
            raise BacklogJobError(
                f"o clone {destino} tem alteracoes nao commitadas; limpe antes "
                "de despachar outro item"
            )
        self._exec(["git", "fetch", "origin", "--prune"], destino)
        # `-B` recria o branch do zero a partir da base: se o item ja tinha
        # branch (retry), o resultado nao acumula commit de tentativa anterior.
        self._exec(
            ["git", "checkout", "-B", branch, f"origin/{self.cfg.base_branch}"],
            destino,
        )
        return destino

    def workspace_guard(self, destino: Path) -> None:
        """O clone nao pode cair dentro dos caminhos protegidos do pi."""
        alvo = destino.resolve()
        for proibido in self.pi_cfg.protected():
            if alvo == proibido or proibido in alvo.parents:
                raise BacklogJobError(
                    f"{alvo} esta em pi.protect; o deploy reseta esse clone"
                )

    # -------------------------------------------------------------------- pi
    def prompt(self, item: dict[str, Any], repo: str, branch: str) -> str:
        projeto = str(item.get("project") or "backlog")
        pagina = str(item.get("page_id") or "").strip()
        origem = f"pagina {pagina} da Agenda" if pagina else "chat"
        contexto = str(item.get("context") or "").strip()
        texto = (
            f"Voce esta trabalhando no repositorio {repo}, no branch {branch} "
            f"(cwd atual).\n\n"
            f"Item do backlog do projeto '{projeto}' (origem: {origem}):\n"
            f"{item.get('title', '')}\n"
        )
        if contexto:
            texto += f"\nContexto da pagina:\n{contexto}\n"
        texto += (
            "\nImplemente esse item neste repositorio. Regras:\n"
            "- siga o AGENTS.md/README do repositorio e o estilo dele;\n"
            "- rode os testes e o lint relevantes antes de terminar;\n"
            "- nao faca commit, push nem abra PR (o orquestrador faz isso);\n"
            "- se o item for ambiguo demais, implemente o minimo defensavel e "
            "explique o que ficou de fora;\n"
            "- no texto final, diga em uma linha o que mudou e quais testes rodaram."
        )
        limite = max(500, int(self.cfg.max_prompt_chars))
        if len(texto) > limite:
            texto = texto[:limite] + "\n\n[contexto truncado]"
        return texto

    def run_pi(self, item: dict[str, Any], destino: Path, repo: str, branch: str):
        try:
            resultado: PiResult = self._pi.run(
                self.prompt(item, repo, branch), destino
            )
        except PiError as exc:
            raise BacklogJobError(f"pi: {exc}") from exc
        except Exception as exc:  # runner quebrado nao pode virar stack no card
            raise BacklogJobError(f"pi: {exc}") from exc
        return resultado

    # ------------------------------------------------------- commit e push
    def commit_and_push(
        self, destino: Path, item: dict[str, Any], branch: str
    ) -> str:
        """Commita o que o pi mudou e empurra o branch. Devolve o resumo git."""
        alterado = self._exec(["git", "status", "--porcelain"], destino).strip()
        if not alterado:
            raise BacklogJobError("o pi terminou sem alterar nenhum arquivo")
        arquivos = len([linha for linha in alterado.splitlines() if linha.strip()])
        self._exec(["git", "add", "-A"], destino)
        titulo = str(item.get("title") or "item do backlog")
        mensagem = (
            f"{titulo}\n\n"
            f"Backlog #{item.get('id')} ({item.get('project') or 'chat'}).\n"
            "Implementado pelo pi via Gaia."
        )
        self._exec(["git", "commit", "-m", mensagem], destino)
        self._exec(["git", "push", "--set-upstream", "origin", branch], destino)
        return f"{arquivos} arquivo(s) alterado(s), branch {branch}"

    # -------------------------------------------------------------------- PR
    def _origin_owner(self, destino: Path) -> str:
        try:
            url = self._exec(
                ["git", "remote", "get-url", "origin"], destino
            ).strip()
        except BacklogJobError:
            return ""
        # git@github.com:owner/repo.git | https://github.com/owner/repo.git
        casado = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
        return casado.group(1) if casado else ""

    def ensure_pr(
        self, destino: Path, item: dict[str, Any], repo: str, branch: str
    ) -> tuple[str, int]:
        """Cria (ou reaproveita) o PR do branch. Devolve ``(url, numero)``."""
        base = self.cfg.base_branch
        existente = self._exec(
            [
                self.cfg.gh_binary,
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "url,number,state",
            ],
            destino,
            aceitar=(0, 1),  # 1 = repo sem PR, nao e falha fatal
        ).strip()
        numero = 0
        url = ""
        if existente:
            achado = _parse_pr(existente)
            if achado:
                url, numero = achado
        if url:
            return url, numero
        corpo = (
            f"Item do backlog **{item.get('project') or 'chat'}** "
            f"#{item.get('id')}.\n\n{item.get('title') or ''}\n\n"
            "Gerado pelo pi via Gaia."
        )
        dono_origem = self._origin_owner(destino)
        alvo_head = f"{dono_origem}:{branch}" if dono_origem else branch
        url = self._exec(
            [
                self.cfg.gh_binary,
                "pr",
                "create",
                "--repo",
                repo,
                "--base",
                base,
                "--head",
                alvo_head,
                "--title",
                str(item.get("title") or "item do backlog")[:120],
                "--body",
                corpo,
            ],
            destino,
        ).strip()
        if not url:
            raise BacklogJobError("o gh nao devolveu a URL do PR")
        numero = _numero_do_pr(url)
        return url, numero

    # ---------------------------------------------------------------- execucao
    def execute(self, item: dict[str, Any]) -> BacklogResult:
        """Caminho completo do item. Falha vira ``BacklogResult(ok=False)``."""
        repo = self.repo_of(item)
        branch = branch_name(self.cfg.branch_prefix, int(item.get("id") or 0), item.get("title", ""))
        resultado = BacklogResult(ok=False, repo=repo, branch=branch)
        try:
            if not item.get("title"):
                raise BacklogJobError("item sem titulo")
            if not repo:
                raise BacklogJobError(
                    f"projeto '{item.get('project') or 'chat'}' sem repositorio: "
                    "defina em [backlog.repos] do config.toml (o card do backlog "
                    "pede o repo do projeto)"
                )
            if shutil.which(self.cfg.gh_binary) is None:
                raise BacklogJobError(
                    f"`{self.cfg.gh_binary}` nao encontrado: o backlog precisa do "
                    "gh autenticado (gh auth login) no mini"
                )
            destino = self.prepare_clone(item, branch)
            pi = self.run_pi(item, destino, repo, branch)
            if not pi.ok:
                raise BacklogJobError(pi.error or pi.text or "o pi falhou sem mensagem")
            detalhe_git = self.commit_and_push(destino, item, branch)
            url, numero = self.ensure_pr(destino, item, repo, branch)
            resultado.ok = True
            resultado.text = pi.text
            resultado.pr_url = url
            resultado.pr_number = numero
            resultado.detail = (
                f"{detalhe_git}; PR #{numero}" if numero else detalhe_git
            )
        except BacklogJobError as exc:
            resultado.error = str(exc)
        except Exception as exc:  # nunca derrubar o worker por um item
            logger.warning("backlog: falha inesperada no item: %s", exc, exc_info=True)
            resultado.error = f"falha inesperada: {exc}"
        return resultado


def _parse_pr(json_texto: str) -> tuple[str, int] | None:
    import json

    try:
        dados = json.loads(json_texto)
    except ValueError:
        return None
    if isinstance(dados, list):
        dados = dados[0] if dados else {}
    if not isinstance(dados, dict):
        return None
    url = str(dados.get("url") or "").strip()
    if not url:
        return None
    try:
        numero = int(dados.get("number") or 0)
    except (TypeError, ValueError):
        numero = 0
    return url, numero


def _numero_do_pr(url: str) -> int:
    casado = re.search(r"/pull/(\d+)", url or "")
    return int(casado.group(1)) if casado else 0


__all__ = [
    "BacklogJob",
    "BacklogJobError",
    "BacklogResult",
    "branch_name",
    "clone_dir",
    "slugify",
]
