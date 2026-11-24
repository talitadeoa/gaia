"""O Control Center por dentro: junta os modulos de ``gaia/control``.

Esta classe existe para o transporte nao conhecer detalhe nenhum: o servidor
HTTP so traduz rota -> metodo -> JSON. Tudo que depende de rede (testar
provider, trocar modelo, listar modelos) entra como ``runner``/``switcher``,
que o ``OrchestratorTarget`` liga ao loop do motor.

Sem token de controle configurado, nada aqui e alcancavel: a ausencia do
segredo e a propria autorizacao (ver ``gaia/serve/server.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from gaia.config import Config
from gaia.control import automations as automations_mod
from gaia.control import health as health_mod
from gaia.control import providers as providers_mod
from gaia.control import routines as routines_mod
from gaia.control import runs as runs_mod
from gaia.control import secrets as secrets_mod
from gaia.control import settings as settings_mod
from gaia.control import skills as skills_mod
from gaia.memory.store import MemoryStore
from gaia.secondbrain.index import SecondBrainIndex

logger = logging.getLogger("gaia.control")


class ControlCenter:
    """Superficie do painel sobre o motor em execucao."""

    def __init__(
        self,
        cfg: Config,
        store: MemoryStore | None,
        *,
        index: SecondBrainIndex | None = None,
        orch: Any = None,
        env_path: Path | None = None,
        config_path: Path | None = None,
        runner: Callable[..., Any] | None = None,
        switcher: Callable[[str], list[str]] | None = None,
        models: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.index = index
        self.orch = orch
        self.env_path = Path(env_path or Config.default_dotenv_path()).expanduser()
        self.config_path = Path(
            config_path or cfg.config_file or Config.default_config_path()
        ).expanduser()
        self._runner = runner
        self._switcher = switcher
        self._models = models

    # ------------------------------------------------------------------ leitura
    def overview(self) -> dict[str, Any]:
        skills = skills_mod.describe_skills(self.cfg, self.store)
        autos = automations_mod.describe_automations(self.cfg, self.store)
        saude = health_mod.system_health(
            self.cfg,
            self.store,
            orch=self.orch,
            index=self.index,
            providers=providers_mod.describe_providers(self.cfg),
            tasks=self._tasks(),
        )
        alertas = [
            {
                "kind": "automation",
                "id": item["id"],
                "message": f"{item['name']}: {item['status']}",
            }
            for item in automations_mod.needs_attention(autos)
        ]
        alertas += [
            {"kind": "health", "id": item["id"], "message": item["detail"]}
            for item in saude
            if item["status"] == "error"
        ]
        return {
            "engine": {
                "model": self.cfg.agent.model_slug(),
                "provider": self.cfg.agent.provider,
                "session": getattr(self.orch, "session_id", ""),
                "channel": getattr(self.orch, "channel", ""),
                "busy": bool(getattr(self.orch, "_busy", False)),
            },
            "status": health_mod.overall_status(saude),
            "health": saude,
            "skills": {
                "total": len(skills),
                "active": sum(1 for s in skills if s["status"] == "active"),
                "error": sum(1 for s in skills if s["status"] == "error"),
            },
            "automations": {
                "total": len(autos),
                "active": sum(1 for a in autos if a["enabled"]),
                "missed": [a["id"] for a in autos if a["status"] == "missed"],
                "failed": [
                    a["id"]
                    for a in autos
                    if (a["last_run"] or {}).get("status") == "failed"
                ],
            },
            "credentials": providers_mod.credentials_summary(self.cfg),
            "recent_runs": runs_mod.recent_runs(self.store, 10) if self.store else [],
            "summary": runs_mod.summary_since(self.store, 1) if self.store else {},
            "alerts": alertas,
        }

    def health(self) -> dict[str, Any]:
        itens = health_mod.system_health(
            self.cfg,
            self.store,
            orch=self.orch,
            index=self.index,
            providers=providers_mod.describe_providers(self.cfg),
            tasks=self._tasks(),
        )
        return {"status": health_mod.overall_status(itens), "items": itens}

    def skills(self) -> dict[str, Any]:
        return {"skills": skills_mod.describe_skills(self.cfg, self.store)}

    def automations(self) -> dict[str, Any]:
        """Agenda + rotinas continuas + estado da proatividade.

        `routines` e aditivo: a tela de automacoes mostra as duas familias e
        nao paga uma rota extra por isso. `workers` vem do mesmo `_tasks()` que
        o health usa — rotina com worker morto aparece como `error` em vez de
        mentir "ativa".
        """
        cfg = self.cfg.proactive
        workers = self._tasks() or None
        return {
            "automations": automations_mod.describe_automations(self.cfg, self.store),
            "routines": routines_mod.describe_routines(
                self.cfg, self.store, workers=workers
            ),
            "routines_summary": routines_mod.summarize(
                self.cfg, self.store, workers=workers
            ),
            "proactive": {
                "enabled": bool(cfg.enabled),
                "reminders": bool(cfg.reminders),
                "idle_checkin": bool(cfg.idle_checkin),
                "quiet_hours": str(cfg.quiet_hours),
                "daily_max": int(cfg.daily_max),
            },
        }

    def activity(self, limit: int = 50, kind: str = "", status: str = "") -> dict[str, Any]:
        if self.store is None:
            return {"runs": [], "summary": {}}
        linhas = self.store.list_runs(limit=limit, kind=kind, status=status)
        return {
            "runs": [runs_mod.render_run(row) for row in linhas],
            "summary": runs_mod.summary_since(self.store, 7),
        }

    def errors(self, limit: int = 50) -> dict[str, Any]:
        if self.store is None:
            return {"errors": [], "recurring": []}
        falhas = [
            runs_mod.render_run(row)
            for row in self.store.list_runs(limit=limit, status="failed")
        ]
        contagem: dict[str, int] = {}
        for item in falhas:
            chave = f"{item['kind']}|{item['error_kind']}"
            contagem[chave] = contagem.get(chave, 0) + 1
        recorrentes = [
            {"kind": chave.split("|")[0], "error_kind": chave.split("|")[1], "count": total}
            for chave, total in sorted(contagem.items(), key=lambda par: -par[1])
            if total > 1
        ]
        return {"errors": falhas, "recurring": recorrentes}

    def providers(self) -> dict[str, Any]:
        return {
            "providers": providers_mod.describe_providers(self.cfg),
            "agent": providers_mod.agent_settings(self.cfg),
        }

    def settings(self) -> dict[str, Any]:
        return {
            "path": str(self.config_path),
            "exists": self.config_path.is_file(),
            "sections": settings_mod.effective(
                self.cfg, path=self.config_path
            ),
        }

    def secrets(self) -> dict[str, Any]:
        return secrets_mod.describe_secrets(self.env_path)

    # ------------------------------------------------------------------ escrita
    async def test_provider(self, name: str) -> dict[str, Any]:
        return await providers_mod.test_provider(self.cfg, name)

    def switch_model(self, slug: str) -> dict[str, Any]:
        if self._switcher is None:
            raise RuntimeError("troca de modelo indisponivel neste motor")
        mensagens = self._switcher(slug)
        return {"ok": True, "model": slug, "messages": mensagens}

    def list_models(self) -> dict[str, Any]:
        """Lista de modelos do catalogo.

        **Sincrono de proposito.** ``self._models`` (o ``alvo.models``) ja
        entra no loop do motor com ``run_coroutine_threadsafe().result()``;
        chamar isso de dentro do proprio loop travaria o processo. Rodando na
        thread do HTTP, o loop fica livre para responder.
        """
        if self._models is None:
            return {"models": []}
        return {"models": self._models()}

    def update_setting(
        self, section: str, key: str, value: Any
    ) -> dict[str, Any]:
        resultado = settings_mod.update(
            self.config_path, section, key, value, cfg=self.cfg
        )
        # Reflete no processo sem reiniciar: os workers leem ``self.cfg`` (o
        # mesmo objeto do motor). Sem isso, mudar `serve.voice_source` so
        # valeria no proximo `gaia serve` — e a tela ficaria mentindo.
        secoes = [self.cfg]
        if self.orch is not None:
            secoes.append(self.orch.cfg)
        for cfg in secoes:
            alvo = getattr(cfg, section, None)
            if alvo is not None and hasattr(alvo, key):
                setattr(alvo, key, resultado["value"])
        return resultado

    def remove_override(self, section: str, key: str) -> dict[str, Any]:
        return settings_mod.remove_env_override(self.env_path, section, key)

    def update_automation(
        self, automation_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Edita uma automacao na tabela (pausar/retomar, horario, dias...).

        Nao grava config: ``enabled`` aqui e a decisao do painel/tela, que o
        agendador obedece a cada tick. Valor invalido vira ``ValueError``
        (422) e id desconhecido ``KeyError`` (404).
        """
        if self.store is None:
            raise RuntimeError("motor sem banco: agenda indisponivel")
        return automations_mod.update(self.store, self.cfg, automation_id, payload)

    async def run_automation(self, automation_id: str) -> dict[str, Any]:
        """Executa a rotina agora (``trigger=manual``) e devolve o run.

        Delega ao agendador do motor (``Orchestrator.run_automation_now``), que
        limpa a reserva do dia, reinicia o relogio de retry e registra o run
        com ``trigger="manual"``. **Assincrono**: buscar vagas/cotacoes leva
        segundos a minutos, e a espera tem de acontecer no loop do motor.

        ``KeyError`` = id desconhecido (404), ``RuntimeError`` = motor ocupado
        com um turno (409). Rodar uma automacao pausada e permitido: o clique e
        explicito e o resultado aparece na hora.
        """
        executar = getattr(self.orch, "run_automation_now", None)
        if not callable(executar):
            raise RuntimeError("este motor nao executa automacao sob demanda")
        return dict(await executar(automation_id))

    def update_routine(
        self, routine_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Pausa/retoma uma rotina continua (tabela `routines`, schema v9).

        ``KeyError`` = id desconhecido (404), ``ValueError`` = payload invalido
        (422). Nao aceita horario: rotina nao tem janela.
        """
        if self.store is None:
            raise RuntimeError("motor sem banco: rotinas indisponiveis")
        return routines_mod.update(
            self.store, self.cfg, routine_id, payload, workers=self._tasks() or None
        )

    async def run_routine(self, routine_id: str) -> dict[str, Any]:
        """Executa a rotina agora (``trigger=manual``) e devolve o run.

        ``KeyError`` = id desconhecido ou rotina sem handler (404 antes de
        tocar o motor, para o transporte nao devolver 409 de "ocupado" para um
        pedido que nunca vai existir), ``RuntimeError`` = motor ocupado ou
        motor sem o metodo (409).
        """
        item = routines_mod.find(routine_id)
        if item is None or not item.runnable:
            raise KeyError(routine_id)
        executar = getattr(self.orch, "run_routine_now", None)
        if not callable(executar):
            raise RuntimeError("este motor nao executa rotina sob demanda")
        return dict(await executar(routine_id))

    async def set_proactive(self, enabled: bool) -> dict[str, Any]:
        """Liga/desliga a iniciativa proativa (TOML + processo).

        **Assincrono de proposito**: ligar cria a ``_proactive_task``, e criar
        tarefa exige estar no loop do motor. O transporte roda isto com
        ``run_awaitable``, como o resto que mexe no processo.

        Grava primeiro e aplica depois: se o valor vier do ``.env`` o update
        levanta ``ValueError`` e nada muda (o processo nao pode divergir do
        arquivo).
        """
        valor = bool(enabled)
        resultado = settings_mod.update(
            self.config_path, "proactive", "enabled", valor, cfg=self.cfg
        )
        self.cfg.proactive.enabled = valor
        aplicar = getattr(self.orch, "set_proactive_enabled", None)
        if callable(aplicar):
            resultado["running"] = bool(aplicar(valor))
        else:
            resultado["running"] = valor
        return resultado

    def set_secret(self, key: str, value: str) -> dict[str, Any]:
        return secrets_mod.set_secret(self.env_path, key, value)

    def remove_secret(self, key: str) -> dict[str, Any]:
        return secrets_mod.remove_secret(self.env_path, key)

    # ------------------------------------------------------------------ interno
    # Rotulo do painel -> atributo do motor que precisa estar vivo. Briefing e
    # vagas nao tem mais tarefa propria desde a fase C: quem dispara as duas e
    # o ``_scheduler_task``. Vigiar ``_briefing_task``/``_career_task`` (que
    # nunca mais sao criadas) acusava "worker parado" num motor saudavel.
    _TAREFAS: ClassVar[dict[str, str]] = {
        "iniciativa": "_proactive_task",
        "memoria": "_memory_task",
        "briefing": "_scheduler_task",
        "vagas": "_scheduler_task",
    }

    def _tasks(self) -> dict[str, bool]:
        """Workers do motor vivos? Falso = automacao nao vai rodar."""
        if self.orch is None:
            return {}
        estado: dict[str, bool] = {}
        for rotulo, atributo in self._TAREFAS.items():
            if not self._worker_esperado(rotulo):
                # Worker desligado pela config nao e worker morto.
                estado[rotulo] = True
                continue
            tarefa = getattr(self.orch, atributo, None)
            estado[rotulo] = tarefa is not None and not tarefa.done()
        return estado

    def _worker_esperado(self, rotulo: str) -> bool:
        cfg = self.cfg
        if rotulo == "iniciativa":
            return bool(cfg.proactive.enabled)
        if rotulo == "memoria":
            return bool(cfg.memory.enabled)
        if rotulo == "briefing":
            return bool(cfg.briefing.enabled and cfg.telegram.enabled)
        if rotulo == "vagas":
            return bool(cfg.career.enabled and cfg.telegram.enabled)
        return False


__all__ = ["ControlCenter"]
