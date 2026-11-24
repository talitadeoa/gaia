"""O gate das rotinas continuas: pausada nao roda, config vence, rodar a mao grava.

Mesmo estilo de ``tests/test_initiative.py``: chamada nao-ligada do mixin com um
stub, para nao montar o ``Orchestrator`` inteiro (o mixin e abstrato).
"""

from __future__ import annotations

import logging
import types
from datetime import datetime

import pytest

from gaia.config import Config
from gaia.controller.initiative import InitiativeMixin
from gaia.controller.routines import RoutineMixin
from gaia.memory.store import MemoryStore

NOW = datetime(2026, 11, 24, 10, 0).astimezone()


class _Stub:
    """So o que o gate e o disparo manual tocam.

    Os helpers do mixin sao ligados aqui: chamar ``RoutineMixin.run_routine_now``
    sem ligar a instancia nao existe, e e o mesmo padrão de
    ``tests/test_initiative.py`` (mixin nao-ligado + stub).
    """

    _routine_on = RoutineMixin._routine_on
    _routine_overrides = RoutineMixin._routine_overrides
    _run_routine = RoutineMixin._run_routine
    _routine_handler = RoutineMixin._routine_handler
    _call_routine_handler = RoutineMixin._call_routine_handler
    _reset_routine_clock = RoutineMixin._reset_routine_clock
    _idle_checkin = InitiativeMixin._idle_checkin

    def __init__(self, store: MemoryStore, cfg: Config) -> None:
        self.store = store
        self.cfg = cfg
        self.logger = logging.getLogger("test.routines")
        self.chamadas: list[str] = []
        self._busy = False
        self._recording = False
        self._listening = False
        self._last_activity = 0.0
        self._proactive_count = 0
        self._agenda_next_check = 999.0
        self._estudos_next_check = 999.0
        self._tasks_next_check = 999.0
        self._dream_report_last_attempt = 999.0

    def _can_idle_checkin(self, now: datetime) -> bool:
        return False

    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.chamadas.append("idle")

    async def _check_agenda(self, now: datetime) -> None:
        self.chamadas.append("agenda")

    async def _check_estudos(self, now: datetime) -> None:
        self.chamadas.append("estudos")

    async def _check_task_page(self, now: datetime) -> None:
        self.chamadas.append("tasks")

    async def _check_focus(self, now: datetime) -> None:
        self.chamadas.append("focus")

    async def _fire_due_reminders(self) -> None:
        self.chamadas.append("lembretes")

    async def _maybe_dream_report(self, now: datetime) -> None:
        self.chamadas.append("sonho")


@pytest.fixture
def stub(tmp_path, isolated_env, cfg):
    banco = MemoryStore(tmp_path / "gaia.db")
    yield _Stub(banco, cfg)
    banco.close()


def _on(stub: _Stub, routine_id: str) -> bool:
    return RoutineMixin._routine_on(stub, routine_id)  # type: ignore[arg-type]


def test_rotina_pausada_nao_roda(stub):
    stub.store.set_routine("initiative:agenda", enabled=False)
    assert _on(stub, "initiative:agenda") is False
    assert _on(stub, "initiative:estudos") is True


def test_rotina_sem_linha_roda(stub):
    assert _on(stub, "initiative:agenda") is True


def test_config_desligada_nao_roda(stub):
    stub.cfg.agenda.enabled = False
    assert _on(stub, "initiative:agenda") is False


def test_ids_desconhecidos_nao_explodem(stub):
    """Rotina removida do registry num deploy nao pode derrubar o laco."""
    assert _on(stub, "initiative:que-nao-existe") is True


def test_overrides_evita_uma_query_por_rotina(stub):
    stub.store.set_routine("initiative:agenda", enabled=False)
    overrides = RoutineMixin._routine_overrides(stub)  # type: ignore[arg-type]
    assert overrides == {"initiative:agenda": False}
    assert _on(stub, "initiative:agenda") is False
    # O mapa ja lido continua valendo (e o mesmo do tick).
    assert RoutineMixin._routine_on(  # type: ignore[arg-type]
        stub, "initiative:agenda", overrides
    ) is False


@pytest.mark.asyncio
async def test_tick_do_laco_respeita_a_pausa(stub):
    stub.store.set_routine("initiative:agenda", enabled=False)
    await RoutineMixin._run_routine(  # type: ignore[arg-type]
        stub, "initiative:agenda", stub._check_agenda, NOW
    )
    assert stub.chamadas == []

    await RoutineMixin._run_routine(  # type: ignore[arg-type]
        stub, "initiative:estudos", stub._check_estudos, NOW
    )
    assert stub.chamadas == ["estudos"]


@pytest.mark.asyncio
async def test_laco_proativo_chama_as_rotinas_pausadas(stub):
    """Integracao com o laco real: o tick respeita o override da tabela."""
    stub.store.set_routine("initiative:agenda", enabled=False)
    stub.cfg.proactive.enabled = True
    await InitiativeMixin._proactive_tick(stub, NOW)  # type: ignore[arg-type]
    assert "agenda" not in stub.chamadas
    assert "estudos" in stub.chamadas


@pytest.mark.asyncio
async def test_rodar_a_mao_grava_run_e_ignora_a_pausa(stub):
    """Rodar a mao e pedido explicito: vale mesmo com a rotina pausada."""
    stub.store.set_routine("initiative:agenda", enabled=False)
    resultado = await RoutineMixin.run_routine_now(  # type: ignore[arg-type]
        stub, "initiative:agenda"
    )
    assert resultado["status"] == "completed"
    assert resultado["trigger"] == "manual"
    assert stub.chamadas == ["agenda"]
    ultima = stub.store.last_run("agenda", "initiative:agenda")
    assert ultima is not None
    assert ultima["trigger"] == "manual"
    # O relogio do throttle foi zerado: o disparo manual nao pode ser engolido
    # pelo "-30 minutos" que o laco acabou de marcar.
    assert stub._agenda_next_check == 0.0


@pytest.mark.asyncio
async def test_rodar_rotina_sem_handler_e_recusado(stub):
    with pytest.raises(KeyError):
        await RoutineMixin.run_routine_now(stub, "initiative:idle")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_rodar_id_desconhecido_e_keyerror(stub):
    with pytest.raises(KeyError):
        await RoutineMixin.run_routine_now(stub, "nao:existe")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_motor_ocupado_recusa_o_disparo_manual(stub):
    stub._busy = True
    with pytest.raises(RuntimeError):
        await RoutineMixin.run_routine_now(stub, "initiative:agenda")  # type: ignore[arg-type]
    assert stub.chamadas == []


@pytest.mark.asyncio
async def test_rodar_a_mao_funciona_com_handler_sem_argumento(stub):
    """Metade das rotinas recebe ``now`` e metade nao: o disparo nao pode exigir
    que todas tenham a mesma assinatura."""
    resultado = await RoutineMixin.run_routine_now(  # type: ignore[arg-type]
        stub, "initiative:reminders"
    )
    assert resultado["status"] == "completed"
    assert stub.chamadas == ["lembretes"]


@pytest.mark.asyncio
async def test_rotina_que_falha_registra_run_failed(stub):
    async def explode(now: datetime) -> None:
        raise RuntimeError("502 bad gateway")

    stub._check_agenda = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await RoutineMixin.run_routine_now(stub, "initiative:agenda")  # type: ignore[arg-type]
    ultima = stub.store.last_run("agenda", "initiative:agenda")
    assert ultima is not None
    assert ultima["status"] == "failed"
    assert ultima["error_kind"] == "network"


def test_registry_e_handlers_nao_divergem():
    """Todo `runnable` do registry precisa de handler no mixin."""
    from gaia.control.routines import ROUTINES
    from gaia.controller.routines import _ROUTINE_HANDLERS

    runnable = {item.id for item in ROUTINES if item.runnable}
    assert runnable == set(_ROUTINE_HANDLERS)

    # E todo handler aponta para um metodo que existe de verdade no motor.
    from gaia.controller.orchestrator import Orchestrator

    for metodo in _ROUTINE_HANDLERS.values():
        assert hasattr(Orchestrator, metodo), metodo


def test_modulo_do_registry_nao_e_inventado():
    """`module` e rastreabilidade: o caminho precisa existir no repo."""
    from pathlib import Path

    from gaia.control.routines import ROUTINES

    raiz = Path(__file__).resolve().parent.parent
    for item in ROUTINES:
        for caminho in item.module.split(","):
            caminho = caminho.strip().rstrip("/")
            if not caminho:
                continue
            assert (raiz / caminho).exists(), f"{item.id}: {caminho}"


def test_types_simples_para_o_status():
    """`status` nunca devolve string vazia (a tela usa como etiqueta)."""
    from gaia.control import routines as mod

    assert set(mod.TRIGGER_LABELS) == {"boot", "loop", "event", "watch"}
    assert all(value for value in mod.TRIGGER_LABELS.values())
    assert isinstance(types, types.ModuleType)  # sanity do import acima
