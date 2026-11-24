"""Cliente do X (Twitter) para a Gaia.

Existe uma coisa que este modulo faz: publicar um post. A leitura de timeline
e respostas fica de fora de proposito -- cada verbo a mais e uma permissao a
mais num token que publica em nome publico.

Autenticacao: OAuth2 **PKCE com refresh token**. O `.env` do cofre
(`~/.config/gaia/secrets/x.env`) guarda `X_CLIENT_ID`, `X_CLIENT_SECRET` e
`X_REFRESH_TOKEN`; a cada publicacao trocamos o refresh por um access token. O
X **rotaciona** o refresh token em cada troca, por isso o arquivo e reescrito
(o valor antigo deixa de valer: perde-lo significa reautorizar no navegador).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from gaia.config import XConfig
from gaia.secrets import open_service

TOKEN_URL = "https://api.x.com/2/oauth2/token"
TWEET_URL = "https://api.x.com/2/tweets"
LIMITE = 280  # teto de um post simples (o premium muda, nao conte com isso)


class XError(RuntimeError):
    pass


class XClient:
    def __init__(self, cfg: XConfig):
        self.cfg = cfg
        self._env = open_service("x", diretorio=self._dir())

    def _dir(self) -> Path | None:
        bruto = (getattr(self.cfg, "secrets_dir", "") or "").strip()
        return Path(bruto).expanduser() if bruto else None

    def _get(self, *names: str) -> str:
        return self._env.get(*names)

    @property
    def enabled(self) -> bool:
        if not self.cfg.enabled:
            return False
        return bool(
            self._get("X_CLIENT_ID")
            and (self._get("X_REFRESH_TOKEN") or self._get("X_ACCESS_TOKEN"))
        )

    # ------------------------------------------------------------------ tokens
    def _basico(self) -> str:
        par = f"{self._get('X_CLIENT_ID')}:{self._get('X_CLIENT_SECRET')}"
        return base64.b64encode(par.encode()).decode()

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        """Troca o refresh token por um access token (e persiste o novo refresh)."""
        refresh = self._get("X_REFRESH_TOKEN")
        if not refresh:
            raise XError(
                "sem X_REFRESH_TOKEN no cofre: rode a autorizacao OAuth2 PKCE "
                "(veja docs/acesso-mini.md) e grave o refresh token em x.env"
            )
        corpo = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": self._get("X_CLIENT_ID"),
        }
        try:
            resposta = await client.post(
                TOKEN_URL,
                data=corpo,
                headers={
                    "Authorization": f"Basic {self._basico()}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.HTTPError as exc:
            raise XError(f"falha ao conectar no X: {exc}") from exc
        if not resposta.is_success:
            raise XError(
                f"X recusou a autorizacao ({resposta.status_code}): "
                "o refresh token pode ter expirado; reautorize no navegador"
            )
        dados = resposta.json() if resposta.content else {}
        access = str(dados.get("access_token") or "")
        if not access:
            raise XError("X nao devolveu access_token")
        novo = str(dados.get("refresh_token") or "")
        if novo and novo != refresh:
            # Rotacionou: guardar agora, senao o proximo post quebra.
            self._env.set("X_REFRESH_TOKEN", novo)
        return access

    # ------------------------------------------------------------------- post
    async def _post(self, texto: str) -> dict[str, Any]:
        limpo = (texto or "").strip()
        if not limpo:
            raise XError("texto vazio")
        if len(limpo) > LIMITE:
            raise XError(f"texto tem {len(limpo)} caracteres (limite {LIMITE})")
        async with httpx.AsyncClient(timeout=20.0) as client:
            access = await self._access_token(client)
            try:
                resposta = await client.post(
                    TWEET_URL,
                    json={"text": limpo},
                    headers={"Authorization": f"Bearer {access}"},
                )
            except httpx.HTTPError as exc:
                raise XError(f"falha ao conectar no X: {exc}") from exc
        if not resposta.is_success:
            detalhe = ""
            try:
                erro = resposta.json()
                detalhe = str(erro.get("detail") or erro.get("title") or "")
            except ValueError:
                detalhe = resposta.text[:200]
            raise XError(
                f"X respondeu {resposta.status_code} ao publicar"
                + (f": {detalhe}" if detalhe else "")
            )
        dados = resposta.json() if resposta.content else {}
        return dados.get("data") or {"id": "", "text": limpo}

    def post(self, texto: str) -> dict[str, Any]:
        """Sincrono para a ferramenta (que roda numa thread)."""
        import asyncio

        return asyncio.run(self._post(texto))


__all__ = ["LIMITE", "TOKEN_URL", "TWEET_URL", "XClient", "XError"]
