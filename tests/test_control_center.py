"""O painel so pode acusar 'worker parado' quando o worker esta parado.

Regressao da fase C: o agendador unico substituiu as tarefas de briefing e
vagas (``_briefing_task``/``_career_task``), mas o health do Control Center
continuava olhando essas duas — que nunca mais sao criadas. Resultado: painel
em vermelho ("worker parado: briefing, vagas") com tudo funcionando.
"""

from __future__ import annotations

from typing import Any

from gaia.control.center import ControlCenter


class _Tarefa:
    """Suficiente para o health: so ``done()`` importa."""

    def __init__(self, done: bool = False) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


class _Orch:
    """Motor no estado real de hoje: sem ``_briefing_task``/_career_task."""

    def __init__(self, **tarefas: Any) -> None:
        for nome, valor in tarefas.items():
            setattr(self, nome, valor)


def test_briefing_e_vagas_seguem_o_agendador(cfg, store):
    cfg.telegram.enabled = True
    cfg.briefing.enabled = True
    cfg.career.enabled = True
    cfg.memory.enabled = True
    cfg.proactive.enabled = True
    orch = _Orch(
        _proactive_task=_Tarefa(),
        _memory_task=_Tarefa(),
        _scheduler_task=_Tarefa(),
    )

    estado = ControlCenter(cfg, store, orch=orch)._tasks()

    assert estado == {
        "iniciativa": True,
        "memoria": True,
        "briefing": True,
        "vagas": True,
    }


def test_agendador_parado_derruba_briefing_e_vagas(cfg, store):
    cfg.telegram.enabled = True
    cfg.briefing.enabled = True
    cfg.career.enabled = True
    cfg.memory.enabled = True
    cfg.proactive.enabled = True
    orch = _Orch(
        _proactive_task=_Tarefa(),
        _memory_task=_Tarefa(),
        _scheduler_task=_Tarefa(done=True),
    )

    estado = ControlCenter(cfg, store, orch=orch)._tasks()

    assert estado["briefing"] is False
    assert estado["vagas"] is False
    assert estado["iniciativa"] is True


def test_worker_desligado_pela_config_nao_e_morto(cfg, store):
    # Telegram desligado: briefing/vagas nao precisam de agendador.
    cfg.telegram.enabled = False
    cfg.briefing.enabled = True
    cfg.career.enabled = True
    cfg.memory.enabled = False
    cfg.proactive.enabled = False
    orch = _Orch(_scheduler_task=_Tarefa(done=True))

    assert ControlCenter(cfg, store, orch=orch)._tasks() == {
        "iniciativa": True,
        "memoria": True,
        "briefing": True,
        "vagas": True,
    }
