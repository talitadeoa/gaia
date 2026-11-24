"""Feed de atividade dos bots e o segundo plano que o alimenta.

Duas coisas moram aqui, e so elas:

* **leitura/escrita do feed** (``bot_messages``, schema v12): o painel mostra
  o que cada bot fez e o badge de nao-lido zera por ``bot_seen``. E o caminho
  que a Ariana usa para entregar sem Telegram (design §4.5a).
* **segundo plano por prompt** (``run_bot_task``): manda um pedido na sessao de
  um bot, com a allowlist e o workspace dele, consome a fila **daquela** sessao
  e devolve o texto. Nao passa pelo ``Orchestrator.session_id``: um job nao pode
  trocar a conversa que a pessoa esta lendo.

Nada aqui amplia permissao (D7): ``write``/``edit``/``bash`` continuam pedindo
aprovacao e, num job, nao ha ninguem para aprovar -- a permissao e negada, o job
falha e o feed avisa. Silencio nao e opcao.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from gaia.bots import Bot, BotIndisponivel, require_bot
from gaia.controller.contract import OrchestratorContract
from gaia.controller.modes import persona_from_bot
from gaia.controller.prompt import compose_system

# Canal das conversas de segundo plano. Separado do `cli`/`serve` de proposito:
# um job nao pode aparecer na lista de sessoes da conversa da pessoa nem
# reinjetar o historico errado no proximo turno interativo.
JOB_CHANNEL = "job"

# Teto de espera de um job por prompt (o modelo pode demorar, mas nao infinito).
BOT_TASK_TIMEOUT = 600.0

# Espera maxima pela sessao de um bot ocupado (chamada entre bots e
# automacao no mesmo minuto). Depois disso o chamador recebe "ocupado".
BOT_SLOT_TIMEOUT = 120.0


class BotsMixin(OrchestratorContract):
    # -------------------------------------------------------------- feed
    def bot_activity(self, limit: int = 30, bot: str = "") -> dict[str, Any]:
        """Feed do painel: mensagens + execucoes recentes + nao-lidos por bot.

        Leitura pura: nao dispara job, nao toca em rede. ``bot`` vazio devolve o
        feed de todos (a tela filtra por bot ativo, mas o rail precisa dos
        outros para o badge).
        """
        try:
            limite = max(1, min(int(limit or 30), 200))
        except (TypeError, ValueError):
            limite = 30
        alvo = str(bot or "").strip()
        mensagens = self.store.list_bot_messages(alvo, limit=limite)
        linhas = self.store.list_runs(limit=limite, bot=alvo)
        return {
            "messages": [
                {
                    "id": int(row.get("id") or 0),
                    "bot": str(row.get("bot") or ""),
                    "kind": str(row.get("kind") or "job"),
                    "title": str(row.get("title") or ""),
                    "text": str(row.get("text") or ""),
                    "when": str(row.get("created_at") or ""),
                    "seen": bool(row.get("seen")),
                }
                for row in mensagens
            ],
            "runs": [
                {
                    "id": int(row.get("id") or 0),
                    "bot": str(row.get("bot") or ""),
                    "kind": str(row.get("kind") or ""),
                    "automation": str(row.get("automation") or ""),
                    "status": str(row.get("status") or ""),
                    "when": str(row.get("started_at") or ""),
                    "detail": str(row.get("detail") or ""),
                    "error": str(row.get("error") or ""),
                }
                for row in linhas
            ],
            "unread": self.store.unread_by_bot(),
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def bot_seen(self, bot: str = "") -> dict[str, Any]:
        """Zera o badge (``bot`` vazio = todos) e devolve a contagem nova."""
        try:
            self.store.mark_bot_seen(str(bot or "").strip())
        except Exception as exc:  # pragma: no cover - banco indisponivel
            self.logger.warning("feed: nao consegui marcar como lido: %s", exc)
        return {"ok": True, "unread": self.store.unread_by_bot()}

    def bot_message(
        self, bot: str, kind: str = "job", title: str = "", text: str = ""
    ) -> int:
        """Grava um item no feed do bot e avisa a tela por SSE (``bot_activity``).

        Devolve o id da mensagem (0 quando o banco falhou). Nunca levanta: um
        job nao pode morrer porque o feed nao gravou.
        """
        store = getattr(self, "store", None)
        if store is None:  # pragma: no cover - motor sem banco
            return 0
        destino = str(bot or "gaia").strip() or "gaia"
        try:
            message_id = store.add_bot_message(destino, kind, title, text)
        except Exception as exc:  # pragma: no cover - banco indisponivel
            self.logger.warning("feed: nao consegui gravar mensagem: %s", exc)
            return 0
        emitir = getattr(self.ui, "emit", None)
        if callable(emitir):
            try:
                emitir(
                    "bot_activity",
                    id=message_id,
                    bot=destino,
                    kind=kind,
                    title=title,
                    text=text,
                    unread=store.unread_by_bot(),
                )
            except Exception as exc:  # pragma: no cover - UI nao derruba job
                self.logger.debug("feed: evento bot_activity falhou: %s", exc)
        return message_id

    # ------------------------------------------------------------- ocupacao
    def bot_slot(self, bot_id: str) -> asyncio.Lock:
        """Trava por bot (nunca dois turnos na mesma sessao)."""
        chave = str(bot_id or "gaia").strip() or "gaia"
        travas = getattr(self, "_bot_slots", None)
        if travas is None:
            travas = {}
            self._bot_slots = travas
        trava = travas.get(chave)
        if trava is None:
            trava = asyncio.Lock()
            travas[chave] = trava
        return trava

    def bot_busy(self, bot_id: str) -> bool:
        """A sessao deste bot esta ocupada (turno da pessoa ou job)?

        O turno interativo so existe no bot ativo; um job de outro bot nao
        bloqueia a automacao do bot ativo (design §4.5). Dentro do mesmo bot
        nunca ha dois turnos na mesma sessao: e o que a trava garante.
        """
        chave = str(bot_id or "").strip()
        travas = getattr(self, "_bot_slots", None) or {}
        if chave and chave in travas and travas[chave].locked():
            return True
        if chave and chave != self.mode_key:
            return False
        # O turno da pessoa (e a voz) so ocupa a sessao do bot ativo: a
        # automacao da Ariana nao espera a conversa da Gaia (design §4.5).
        return bool(self._busy or self._recording or self._listening)

    # ------------------------------------------------------- segundo plano
    async def run_bot_task(
        self,
        bot_id: str,
        prompt: str,
        *,
        title: str = "",
        automation_id: str = "",
        trigger: str = "schedule",
        attempt: int = 1,
        depth: int = 0,
    ) -> dict[str, Any]:
        """Roda um pedido na sessao de um bot (segundo plano, sem UI).

        A sessao e criada/retomada no workspace do bot e a politica dele e
        aplicada antes de qualquer turno (mesmo caminho do chat). O historico do
        job mora em ``channel='job'``: nao aparece na lista de sessoes da pessoa
        nem volta no proximo turno interativo.

        Devolve ``{"ok", "text", "run_id", "persona"}``; falha vira texto de erro
        no feed (o chamador nunca fica sem resposta).
        """
        texto = " ".join(str(prompt or "").split())
        if not texto:
            raise ValueError("pedido vazio")
        bot = require_bot(bot_id, getattr(self, "bots", None))
        disponivel, motivo = self.bot_status(bot)
        if not disponivel:
            raise BotIndisponivel(self.bot_unavailable_message(bot, motivo))

        conv = self.store.get_or_create_conversation(
            str(self.directory), bot.id, JOB_CHANNEL
        )
        conversation_id = int(conv["id"])
        persona = persona_from_bot(bot)
        trava = self.bot_slot(bot.id)
        try:
            await asyncio.wait_for(trava.acquire(), timeout=BOT_SLOT_TIMEOUT)
        except TimeoutError:
            raise RuntimeError(
                f"o bot {bot.name} esta ocupado; tente de novo em instantes"
            ) from None
        try:
            return await self._run_job(
                bot,
                persona,
                conversation_id,
                texto,
                title=title,
                automation_id=automation_id,
                trigger=trigger,
                attempt=attempt,
                depth=depth,
            )
        finally:
            trava.release()

    async def _run_job(
        self,
        bot: Bot,
        persona: Any,
        conversation_id: int,
        texto: str,
        *,
        title: str,
        automation_id: str,
        trigger: str,
        attempt: int,
        depth: int,
    ) -> dict[str, Any]:
        """Corpo do job, ja com a trava do bot em maos."""
        from gaia.bots.bus import DEPTH
        from gaia.control.runs import record_run

        session_id, _criada = await self._open_session(conversation_id, persona, bot)
        # Profundidade da chamada entre bots: o ``to_thread`` das ferramentas
        # copia o contexto, entao `falar_com_bot` dentro deste turno sabe em
        # que nivel esta (sem variavel global).
        token = DEPTH.set(max(0, int(depth)))
        try:
            composed = compose_system(
                persona,
                self._preferences_block(),
                concise=self.cfg.interface.concise,
                humor=self.cfg.interface.humor,
                now=datetime.now().astimezone(),
            )
            self.store.add_turn(conversation_id, "user", texto)
            titulo = title or f"{persona.name}: {texto[:60]}"
            resposta = ""
            erro = ""
            async with record_run(
                self.store,
                kind="bot_task",
                automation=automation_id,
                trigger=trigger,
                delivery="local",
                attempt=attempt,
                bot=bot.id,
            ) as run:
                try:
                    resposta = await asyncio.wait_for(
                        self._prompt_session(
                            session_id,
                            texto,
                            system=composed.static,
                            dynamic=composed.dynamic,
                            cache_prefix=composed.static,
                        ),
                        timeout=BOT_TASK_TIMEOUT,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    erro = str(exc) or exc.__class__.__name__
                    run.fail(erro)
                if resposta:
                    self.store.add_turn(conversation_id, "assistant", resposta)
                    run.detail = resposta[:200]
                if erro:
                    self.bot_message(bot.id, "job", f"{titulo} (falhou)", erro)
                elif resposta:
                    self.bot_message(bot.id, "job", titulo, resposta)
        finally:
            DEPTH.reset(token)
        return {
            "ok": not erro,
            "text": resposta,
            "error": erro,
            "run_id": run.id,
            "persona": persona.name,
        }

    async def _prompt_session(
        self,
        session_id: str,
        text: str,
        *,
        system: str = "",
        dynamic: str = "",
        cache_prefix: str = "",
    ) -> str:
        """Turno isolado numa sessao (fila da sessao, sem tocar no estado ativo).

        Permissao pedida num job e **negada** na hora: nao existe pessoa olhando
        a tela para aprovar e deixar pendente travaria o job ate o timeout.
        """
        from gaia.agent.events import (
            PermissionAsked,
            TextDelta,
            TurnCancelled,
            TurnCompleted,
            TurnFailed,
        )

        prompt = text
        if dynamic:
            prompt = (
                f"{text}\n\n---\n\n"
                "[Contexto do turno — não é um pedido]\n"
                f"{dynamic}"
            )
        stop = asyncio.Event()
        fila: asyncio.Queue[Any] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in self.client.stream_session_events(
                    session_id, stop=stop
                ):
                    await fila.put(event)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # pragma: no cover - defensivo
                self.logger.warning("job: stream da sessao caiu: %s", exc)

        tarefa = asyncio.create_task(pump())
        pedacos: list[str] = []
        negados: list[str] = []
        try:
            await self.client.prompt_async(
                session_id,
                prompt,
                system=system,
                cache_prefix=cache_prefix,
            )
            while True:
                evento = await fila.get()
                if isinstance(evento, TextDelta):
                    pedacos.append(evento.text)
                elif isinstance(evento, PermissionAsked):
                    negados.append(evento.tool)
                    await self.client.reply_permission(
                        session_id, evento.request_id, "reject"
                    )
                elif isinstance(evento, TurnCompleted):
                    if not pedacos and evento.text:
                        pedacos.append(evento.text)
                    break
                elif isinstance(evento, TurnFailed):
                    raise RuntimeError(evento.message or "o turno do bot falhou")
                elif isinstance(evento, TurnCancelled):
                    raise RuntimeError("job cancelado")
        finally:
            stop.set()
            tarefa.cancel()
            try:
                await tarefa
            except (asyncio.CancelledError, Exception):
                pass
        texto = "".join(pedacos).strip()
        if negados and not texto:
            raise RuntimeError(
                "ferramenta exigiu aprovacao ("
                + ", ".join(negados)
                + ") e nao havia ninguem para aprovar"
            )
        return texto


__all__ = ["BOT_SLOT_TIMEOUT", "BOT_TASK_TIMEOUT", "JOB_CHANNEL", "BotsMixin"]
