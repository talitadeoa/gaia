"""Bots que conversam entre si: uma chamada one-shot, com profundidade.

Design §4.6/§9 (D9): **nao** e debate multi-turno. A Gaia pergunta a Donna, a
Donna responde uma vez, a resposta volta como resultado de ferramenta e quem
decide o que dizer a pessoa e o chamador. Regras que impedem bola de neve:

* ``MAX_DEPTH`` chamadas encadeadas (A -> B -> C para; a terceira recusa);
* destino precisa estar em ``bot.can_message`` do chamador (a allowlist do
  descritor, nao o prompt);
* ``to == from`` recusado;
* o destino roda na sessao **dele**, com a politica dele (tools/workspace);
  a chamada nao amplia permissao nenhuma;
* toda chamada fica auditada em ``bot_messages`` (kind ``inter_bot``).

A execucao acontece no loop do motor: a ferramenta roda em thread
(``asyncio.to_thread``), entao ``ask_sync`` bloqueia a thread e devolve texto.
Falha do destino volta como texto legivel -- nunca silencio.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any

from gaia.bots.registry import Bot

logger = logging.getLogger("gaia.bots.bus")

# Duas chamadas encadeadas: Gaia -> Donna -> Gaia ja e o limite; a terceira
# recusa. Sem teto, "pergunta pra ela" vira loop de custo.
MAX_DEPTH = 2

# Profundidade da chamada em curso. O turno do bot chamado roda dentro do
# contexto novo, entao ``asyncio.to_thread`` (que copia o contexto) leva o
# valor ate a ferramenta daquele turno.
DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("gaia_bot_depth", default=0)


class BotBus:
    """Ponte sincrona (thread da ferramenta) -> turno assincrono de outro bot."""

    def __init__(self, orch: Any, *, timeout: float = 180.0) -> None:
        self.orch = orch
        self.timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Guarda o loop do motor (chamado no boot, ja dentro do loop)."""
        self._loop = loop

    # ---------------------------------------------------------------- ask
    def ask_sync(
        self,
        from_bot: str,
        to_bot: str,
        question: str,
        *,
        depth: int = 0,
    ) -> str:
        """Pergunta a outro bot e devolve a resposta (ou o motivo da recusa)."""
        pergunta = " ".join(str(question or "").split())
        if not pergunta:
            return "recusado: pergunta vazia"
        loop = self._loop
        if loop is None or loop.is_closed():
            return "recusado: o motor nao esta com o loop ligado (sem segundo plano)"
        futuro = asyncio.run_coroutine_threadsafe(
            self._ask(str(from_bot or ""), str(to_bot or ""), pergunta, depth),
            loop,
        )
        try:
            return futuro.result(timeout=self.timeout)
        except TimeoutError:
            futuro.cancel()
            return f"{to_bot} nao respondeu em {self.timeout:.0f}s"
        except Exception as exc:  # nunca derruba o turno do chamador
            logger.warning("bus: chamada de %s para %s falhou: %s", from_bot, to_bot, exc)
            return f"falha ao falar com {to_bot}: {exc}"

    async def _ask(
        self, from_bot: str, to_bot: str, question: str, depth: int
    ) -> str:
        registry: dict[str, Bot] = getattr(self.orch, "bots", {}) or {}
        origem = registry.get(from_bot)
        if origem is None:
            return f"recusado: bot desconhecido: {from_bot or '(vazio)'}"
        destino = registry.get(to_bot)
        if destino is None:
            # Apelido/nome completo tambem vale ("Donna Haraway").
            resolver = getattr(self.orch, "bot_for", None)
            destino = resolver(to_bot) if callable(resolver) else None
        if destino is None:
            return f"recusado: bot desconhecido: {to_bot or '(vazio)'}"
        if destino.id == origem.id:
            return "recusado: um bot nao pode falar consigo mesmo"
        if destino.id not in origem.can_message:
            permitidos = ", ".join(origem.can_message) or "ninguem"
            return (
                f"recusado: {origem.id} nao pode falar com {destino.id} "
                f"(permitidos: {permitidos})"
            )
        if depth >= MAX_DEPTH:
            return (
                f"recusado: profundidade maxima de {MAX_DEPTH} chamadas entre bots "
                "atingida"
            )

        # O turno do destino roda na sessao dele; a pergunta vai marcada como
        # chamada de outro bot (o modelo do destino sabe de quem veio).
        pergunta = (
            f"[Chamada de {origem.name} ({origem.id}) — responda uma vez, "
            f"de forma direta]\n{question}"
        )
        try:
            resultado = await self.orch.run_bot_task(
                destino.id,
                pergunta,
                title=f"{origem.id} perguntou a {destino.id}",
                trigger="inter_bot",
                depth=depth + 1,
            )
        except Exception as exc:
            return f"{destino.id} falhou: {exc}"
        texto = str(resultado.get("text") or "").strip()
        if not texto:
            erro = str(resultado.get("error") or "sem resposta")
            return f"{destino.id} nao respondeu: {erro}"
        # Auditoria: a tela mostra "Gaia perguntou a Donna" sem misturar bolhas.
        gravar = getattr(self.orch, "bot_message", None)
        if callable(gravar):
            gravar(
                destino.id,
                "inter_bot",
                f"pergunta de {origem.name}",
                f"{question}\n\n— resposta: {texto}",
            )
        return texto


__all__ = ["DEPTH", "MAX_DEPTH", "BotBus"]
