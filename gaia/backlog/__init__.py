"""Backlog: itens da Agenda/chat despachados ao pi como PRs.

Modulos:

* ``extract`` - converte o HTML de uma pagina da Agenda em itens de backlog;
* ``service`` - estado no SQLite (fonte de verdade) e agrupamento para a tela;
* ``job``     - prepara o clone, roda o pi e garante o PR no GitHub;
* ``worker``  - fila serial que executa um item por vez e notifica o fim.

A tela (card no web) e as rotas so falam com o ``BacklogService``/worker pelo
``Orchestrator``; nada aqui depende de HTTP.
"""

from __future__ import annotations

from gaia.backlog.service import BacklogService, BacklogUnavailable
from gaia.backlog.worker import BacklogWorker

__all__ = ["BacklogService", "BacklogUnavailable", "BacklogWorker"]
