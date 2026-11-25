"""Backlog do painel: extracao, estado no banco, despacho ao pi e limpeza.

Nada aqui toca rede de verdade: a Agenda e o ``gh`` sao fakes e o pi e um
callable. O que este arquivo prova:

* a extracao do HTML da pagina (``<div>``/``<li>``) vira itens, com ``[x]`` e
  ``~~riscado~~`` contando como concluido;
* a sincronizacao preserva o estado de um item que ja foi despachado (PR nao
  volta para ``pending``) e atualiza os demais;
* a limpeza de 7 dias some da tela e, no item de chat, sai do banco;
* o ``BacklogJob`` faz clone/branch/commit/push/PR na ordem certa, recusa
  clone sujo e falha quando o pi nao muda nada (nada de PR vazio);
* o worker serializa, marca ``running`` -> ``pr`` e notifica uma vez;
* as rotas do card devolvem o JSON certo e recusam o que nao da para executar.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gaia.agenda.client import AgendaError
from gaia.backlog.extract import items_from_content, lines_from_html, normalize_item
from gaia.backlog.facade import BacklogFacade, project_from_title
from gaia.backlog.job import (
    BacklogJob,
    BacklogJobError,
    branch_name,
    clone_dir,
    slugify,
)
from gaia.backlog.service import BacklogService, fingerprint
from gaia.backlog.worker import BacklogWorker
from gaia.pi.runner import PiResult

# ---------------------------------------------------------------- extracao ---


def test_lines_from_html_respeita_blocos_e_ignora_script():
    html = (
        "<div>primeira linha</div>"
        "<div>segunda <b>com negrito</b></div>"
        "<script>alert('x')</script>"
        "<ul><li>item a</li><li>[x] item b</li></ul>"
        "<div><br></div>"
    )
    assert lines_from_html(html) == [
        "primeira linha",
        "segunda com negrito",
        "item a",
        "[x] item b",
    ]


def test_pagina_em_texto_puro_tambem_vale():
    assert lines_from_html("a\n\n  b  \nc") == ["a", "b", "c"]


def test_normalize_remove_marcadores_e_detecta_concluido():
    assert normalize_item("- comprar cafe") == ("comprar cafe", False)
    assert normalize_item("1. revisar PR") == ("revisar PR", False)
    assert normalize_item("[ ] escrever teste") == ("escrever teste", False)
    assert normalize_item("[x] escrever teste") == ("escrever teste", True)
    assert normalize_item("✅ deploy") == ("deploy", True)
    assert normalize_item("~~antigo~~") == ("antigo", True)
    assert normalize_item("   ") == ("", False)


def test_items_from_content_na_ordem_sem_vazios():
    html = "<div>um</div><div></div><div>[x] dois</div>"
    assert items_from_content(html) == [("um", False), ("dois", True)]


def test_project_from_title_casa_o_sufixo():
    assert project_from_title("gaia backlog", "backlog") == ("gaia", True)
    assert project_from_title("  Agenda   Backlog ", "backlog") == ("Agenda", True)
    assert project_from_title("suite - backlog", "backlog") == ("suite", True)
    assert project_from_title("notas gerais", "backlog") == ("notas gerais", False)


def test_fingerprint_ignora_caixa_acento_e_pontuacao():
    assert fingerprint("Revisar o PR do Gaia!") == fingerprint("revisar o pr do gaia")
    assert fingerprint("a") != fingerprint("b")


# ------------------------------------------------------------------- banco ---


@pytest.fixture
def service(store):
    return BacklogService(store, prune_days=7)


def test_schema_v8_tem_a_tabela_do_backlog(store):
    assert store.schema_version() >= 8
    assert store.backlog_ready() is True
    colunas = {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(backlog_items)").fetchall()
    }
    assert {
        "id",
        "source",
        "page_id",
        "project",
        "title",
        "status",
        "source_done",
        "fingerprint",
        "repo",
        "branch",
        "pr_url",
        "finished_at",
    } <= colunas


def test_schema_v10_indexa_por_projeto(store):
    """A identidade do item e (project, fingerprint); o indice acompanha."""
    assert store.schema_version() >= 10
    indices = {
        row["name"]
        for row in store._conn.execute("PRAGMA index_list(backlog_items)").fetchall()
    }
    assert "idx_backlog_page" in indices
    colunas = [
        row["name"]
        for row in store._conn.execute("PRAGMA index_info(idx_backlog_page)").fetchall()
    ]
    assert colunas == ["source", "project", "fingerprint"]


def test_sync_grava_itens_e_nao_duplica(service):
    itens = [("fazer a", False), ("fazer b", True)]
    primeiro = service.sync_page(page_id="p1", project="gaia", title="gaia backlog", items=itens)
    assert primeiro == {"inserted": 2, "updated": 0, "removed": 0, "total": 2}
    segundo = service.sync_page(page_id="p1", project="gaia", title="gaia backlog", items=itens)
    assert segundo == {"inserted": 0, "updated": 2, "removed": 0, "total": 2}
    itens_pagina = service.list_items(page_id="p1")
    assert [i["title"] for i in itens_pagina] == ["fazer a", "fazer b"]
    # Concluido na pagina nasce "done" (a limpeza pode agir).
    estados = {i["title"]: i["status"] for i in itens_pagina}
    assert estados == {"fazer a": "pending", "fazer b": "done"}


def test_sync_nao_volta_pr_para_pendente(service):
    service.sync_page(page_id="p1", project="gaia", title="gaia backlog", items=[("x", False)])
    item = service.list_items(page_id="p1")[0]
    service.mark_pr(int(item["id"]), pr_url="https://github.com/o/r/pull/9", pr_number=9)

    # Desmarcar na pagina NAO pode apagar o PR ja aberto.
    service.sync_page(
        page_id="p1", project="gaia", title="gaia backlog", items=[("x", True)]
    )
    atual = service.get(int(item["id"]))
    assert atual is not None
    assert atual["status"] == "pr"
    assert atual["pr_url"].endswith("/pull/9")


def test_desmarcar_na_pagina_volta_de_done_para_pending(service):
    service.sync_page(
        page_id="p1", project="gaia", title="gaia backlog", items=[("item", True)]
    )
    item = service.list_items(page_id="p1")[0]
    assert item["status"] == "done"
    service.sync_page(
        page_id="p1", project="gaia", title="gaia backlog", items=[("item", False)]
    )
    atual = service.get(int(item["id"]))
    assert atual is not None and atual["status"] == "pending"


def test_linha_apagada_da_pagina_sai_do_backlog(service):
    """Espelhar o item, nao so o grupo: editar/apagar linha tambem reflete.

    Era o outro lado do 'zaya': 19 linhas na pagina e 64 no banco, porque a
    sincronizacao so sabia somar.
    """
    service.sync_page(
        page_id="p1",
        project="gaia",
        title="gaia backlog",
        items=[("um", False), ("dois", False), ("tres", False)],
    )
    resultado = service.sync_page(
        page_id="p1", project="gaia", title="gaia backlog", items=[("um", False), ("tres", False)]
    )
    assert resultado["removed"] == 1
    assert [i["title"] for i in service.list_items()] == ["um", "tres"]


def test_pr_aberto_sobrevive_a_linha_apagada(service):
    """Historico do despacho nao some quando a pessoa limpa a pagina."""
    service.sync_page(page_id="p1", project="gaia", title="gaia backlog", items=[("x", False)])
    item = service.list_items()[0]
    service.mark_pr(int(item["id"]), pr_url="https://github.com/o/r/pull/1", pr_number=1)
    resultado = service.sync_page(page_id="p1", project="gaia", title="gaia backlog", items=[])
    assert resultado["removed"] == 0
    assert service.get(int(item["id"]))["status"] == "pr"


def test_pagina_truncada_nao_remove_o_que_foi_cortado(service):
    """Corte por `max_items` nao e remocao: o item nao pode sumir do card."""
    service.sync_page(
        page_id="p1", project="gaia", title="gaia backlog", items=[("a", False), ("b", False)]
    )
    resultado = service.sync_page(
        page_id="p1", project="gaia", title="gaia backlog", items=[("a", False)], prune=False
    )
    assert resultado["removed"] == 0
    assert len(service.list_items()) == 2


def test_item_de_chat_nao_duplica(service):
    primeiro = service.add_chat_item("  Arrumar o README  ", project="gaia")
    segundo = service.add_chat_item("arrumar o readme", project="gaia")
    assert primeiro["id"] == segundo["id"]
    assert len(service.list_items()) == 1
    with pytest.raises(ValueError):
        service.add_chat_item("   ")


def test_limpeza_de_7_dias_esconde_e_apaga_item_de_chat(service):
    antigo = datetime.now().astimezone() - timedelta(days=9)
    recente = datetime.now().astimezone() - timedelta(days=2)
    velho = service.add_chat_item("concluido ha 9 dias")
    novo = service.add_chat_item("concluido ha 2 dias")
    service.mark_pr(int(velho["id"]), pr_url="u", pr_number=1)
    service.mark_pr(int(novo["id"]), pr_url="u", pr_number=2)
    # Grava o finished_at no passado para exercitar o corte.
    with service.store._lock, service.store._conn as conn:
        conn.execute(
            "UPDATE backlog_items SET finished_at = ? WHERE id = ?",
            (antigo.isoformat(timespec="seconds"), velho["id"]),
        )
        conn.execute(
            "UPDATE backlog_items SET finished_at = ? WHERE id = ?",
            (recente.isoformat(timespec="seconds"), novo["id"]),
        )

    visiveis = {i["id"] for i in service.list_items()}
    assert velho["id"] not in visiveis
    assert novo["id"] in visiveis

    removidos = service.prune()
    assert removidos == 1
    assert service.get(int(velho["id"])) is None
    assert service.get(int(novo["id"])) is not None


def test_pendente_antigo_nao_some(service):
    item = service.add_chat_item("nunca despachado")
    antigo = (datetime.now().astimezone() - timedelta(days=30)).isoformat(
        timespec="seconds"
    )
    with service.store._lock, service.store._conn as conn:
        conn.execute(
            "UPDATE backlog_items SET finished_at = ? WHERE id = ?",
            (antigo, item["id"]),
        )
    # Continua pendente: sem conclusao, o corte de 7 dias nao se aplica.
    assert [i["id"] for i in service.list_items()] == [item["id"]]


def test_paginas_agrupam_por_projeto_e_abrem_os_com_pendencia(service):
    service.sync_page(page_id="p1", project="gaia", title="gaia backlog", items=[("a", False)])
    service.sync_page(
        page_id="p2", project="agenda", title="agenda backlog", items=[("b", True)]
    )
    grupos = service.pages()
    assert [g["project"] for g in grupos] == ["gaia", "agenda"]
    assert grupos[0]["open"] is True
    assert grupos[1]["open"] is False
    assert grupos[0]["title"] == "gaia"
    assert grupos[1]["counts"]["done"] == 1


def test_counts_e_next_queued(service):
    a = service.add_chat_item("a")
    service.add_chat_item("b")
    service.mark_queued(int(a["id"]))
    assert service.active_count() == 1
    assert service.running_count() == 0
    fila = service.next_queued()
    assert fila is not None and fila["id"] == a["id"]
    contadores = service.counts()
    assert contadores["queued"] == 1 and contadores["pending"] == 1
    assert contadores["total"] == 2


def test_mark_failed_guarda_o_motivo(service):
    item = service.add_chat_item("x")
    service.mark_failed(int(item["id"]), "gh: 401", detail="tentativa 1")
    atual = service.get(int(item["id"]))
    assert atual is not None
    assert atual["status"] == "failed" and "401" in atual["error"]


# ------------------------------------------------------------------- job -----


class FakeCommand:
    """Roteiro de comandos: registra as chamadas e responde por prefixo.

    ``respostas`` aceita uma resposta ou uma lista delas; a lista e consumida
    em ordem (o ``git status`` do prepare precisa vir limpo e o do commit
    sujo, por exemplo).
    """

    def __init__(
        self,
        respostas: dict[str, tuple[int, str, str] | list[tuple[int, str, str]]] | None = None,
    ):
        self.chamadas: list[list[str]] = []
        self.respostas = respostas or {}

    def __call__(self, args: list[str], cwd: Path) -> tuple[int, str, str]:
        self.chamadas.append(list(args))
        chave = " ".join(args[:3])
        for prefixo, resposta in self.respostas.items():
            if not chave.startswith(prefixo):
                continue
            if isinstance(resposta, list):
                return resposta.pop(0) if len(resposta) > 1 else resposta[0]
            return resposta
        return 0, "", ""

    def nomes(self) -> list[str]:
        return [" ".join(args[:3]) for args in self.chamadas]


class FakePi:
    def __init__(self, resultado: PiResult | None = None, erro: Exception | None = None):
        self.resultado = resultado or PiResult(ok=True, text="implementado", tokens=10)
        self.erro = erro
        self.prompts: list[tuple[str, Path]] = []

    def run(self, prompt: str, workspace: Path | None = None):
        self.prompts.append((prompt, workspace or Path(".")))
        if self.erro is not None:
            raise self.erro
        return self.resultado


def _job(tmp_path, cfg, fake: FakeCommand, pi: FakePi | None = None, **kwargs):
    cfg.backlog.workspace = str(tmp_path / "pr-workspace")
    job = BacklogJob(
        cfg.backlog,
        cfg.pi,
        run=fake,
        pi_runner=pi or FakePi(),
        workspace=tmp_path / "pr-workspace",
    )
    return job


def test_slugify_e_branch_name():
    assert slugify("Arrumar o README do Gaia!") == "arrumar-o-readme-do-gaia"
    assert slugify("") == "item"
    assert branch_name("gaia/backlog", 7, "Arrumar README") == "gaia/backlog/7-arrumar-readme"


def test_clone_dir_por_owner_e_repo():
    assert clone_dir(Path("/w"), "talitadeoa/gaia") == Path("/w/talitadeoa-gaia")


def test_repo_mapeado_por_projeto(cfg, tmp_path):
    fake = FakeCommand()
    job = _job(tmp_path, cfg, fake)
    assert job.repo_of({"project": "gaia"}) == "talitadeoa/gaia"
    assert job.repo_of({"project": "Agenda"}) == "talitadeoa/agenda"
    assert job.repo_of({"project": "suite"}) == "talitadeoa/abin-suite"
    # Projeto sem mapeamento NAO chuta owner/<projeto>: o despacho recusa.
    assert job.repo_of({"project": "estudos"}) == ""
    assert job.repo_of({"project": ""}) == ""


def test_execute_sem_repo_recusa_antes_de_gh_e_pi(cfg, tmp_path):
    fake = FakeCommand()
    job = _job(tmp_path, cfg, fake)
    rodou: list[str] = []
    job._pi = type(  # type: ignore[assignment]
        "Pi", (), {"run": staticmethod(lambda *a, **k: rodou.append("pi"))}
    )()
    resultado = job.execute({"id": 1, "title": "x", "project": "estudos"})
    assert resultado.ok is False
    assert "sem repositorio" in (resultado.error or "")
    assert "[backlog.repos]" in (resultado.error or "")
    assert rodou == [] and fake.chamadas == []


def test_execute_caminho_feliz_clone_branch_pi_commit_push_pr(cfg, tmp_path):
    clone = tmp_path / "pr-workspace" / "talitadeoa-gaia"
    (clone / ".git").mkdir(parents=True)
    fake = FakeCommand(
        {
            "gh repo clone": (0, "", ""),
            "gh pr list": (0, "", ""),
            "gh pr create": (0, "https://github.com/talitadeoa/gaia/pull/42\n", ""),
            "git remote get-url": (0, "git@github.com:piratistemica/gaia.git\n", ""),
            "git status --porcelain": [(0, "", ""), (0, " M gaia/x.py\n", "")],
        }
    )
    pi = FakePi()
    job = _job(tmp_path, cfg, fake, pi)
    item = {"id": 7, "title": "Arrumar README", "project": "gaia", "page_id": "p1"}

    resultado = job.execute(item)

    assert resultado.ok is True, resultado.error
    assert resultado.pr_url.endswith("/pull/42")
    assert resultado.pr_number == 42
    nomes = fake.nomes()
    # Ordem obrigatoria: fetch -> checkout -> pi -> add -> commit -> push -> PR.
    assert "git fetch origin" in nomes
    assert "git checkout -B" in nomes
    assert "git add -A" in nomes
    assert "git commit -m" in nomes
    assert "git push --set-upstream" in nomes
    assert nomes.index("git checkout -B") < nomes.index("git add -A")
    # O PR sai do fork com --head owner:branch (origin = piratistemica).
    criar = next(args for args in fake.chamadas if args[:3] == ["gh", "pr", "create"])
    assert "--repo" in criar and criar[criar.index("--repo") + 1] == "talitadeoa/gaia"
    assert "--head" in criar
    assert criar[criar.index("--head") + 1] == "piratistemica:gaia/backlog/7-arrumar-readme"
    # O prompt leva o item e proibe commit/push dentro do pi.
    prompt, destino = pi.prompts[0]
    assert "Arrumar README" in prompt
    assert "nao faca commit, push nem abra PR" in prompt
    assert destino == clone


def test_execute_falha_sem_clone_sujo(cfg, tmp_path):
    clone = tmp_path / "pr-workspace" / "talitadeoa-gaia"
    (clone / ".git").mkdir(parents=True)
    fake = FakeCommand({"git status --porcelain": (0, " M sujo.py\n", "")})
    job = _job(tmp_path, cfg, fake)
    resultado = job.execute({"id": 1, "title": "x", "project": "gaia"})
    assert resultado.ok is False
    assert "alteracoes nao commitadas" in resultado.error
    assert not any(args[:2] == ["git", "fetch"] for args in fake.chamadas)


def test_execute_falha_quando_pi_nao_muda_nada(cfg, tmp_path):
    clone = tmp_path / "pr-workspace" / "talitadeoa-gaia"
    (clone / ".git").mkdir(parents=True)
    fake = FakeCommand({"git status --porcelain": (0, "", "")})
    job = _job(tmp_path, cfg, fake)
    resultado = job.execute({"id": 2, "title": "x", "project": "gaia"})
    assert resultado.ok is False
    assert "sem alterar nenhum arquivo" in resultado.error
    assert not any(args[:3] == ["gh", "pr", "create"] for args in fake.chamadas)


def test_execute_falha_quando_pi_falha(cfg, tmp_path):
    clone = tmp_path / "pr-workspace" / "talitadeoa-gaia"
    (clone / ".git").mkdir(parents=True)
    fake = FakeCommand()
    pi = FakePi(PiResult(ok=False, text="", error="modelo sem saldo"))
    job = _job(tmp_path, cfg, fake, pi)
    resultado = job.execute({"id": 3, "title": "x", "project": "gaia"})
    assert resultado.ok is False
    assert "modelo sem saldo" in resultado.error


def test_pr_existente_e_reaproveitado(cfg, tmp_path):
    clone = tmp_path / "pr-workspace" / "talitadeoa-gaia"
    (clone / ".git").mkdir(parents=True)
    fake = FakeCommand(
        {
            "gh pr list": (
                0,
                '[{"url": "https://github.com/talitadeoa/gaia/pull/5", "number": 5, "state": "OPEN"}]',
                "",
            ),
            "git status --porcelain": [(0, "", ""), (0, " M a.py\n", "")],
        }
    )
    job = _job(tmp_path, cfg, fake)
    resultado = job.execute({"id": 4, "title": "x", "project": "gaia"})
    assert resultado.ok is True
    assert resultado.pr_number == 5
    assert not any(args[:3] == ["gh", "pr", "create"] for args in fake.chamadas)


def test_workspace_protegido_e_recusado(cfg, tmp_path):
    protegido = tmp_path / "repo-do-deploy"
    protegido.mkdir()
    cfg.pi.protect = [str(protegido)]
    cfg.backlog.workspace = str(protegido)
    job = BacklogJob(cfg.backlog, cfg.pi, run=FakeCommand(), pi_runner=FakePi())
    with pytest.raises(BacklogJobError, match=r"pi\.protect"):
        job.workspace_guard(protegido / "talitadeoa-gaia")


def test_prompt_trunca_contexto_gigante(cfg, tmp_path):
    cfg.backlog.max_prompt_chars = 500
    job = _job(tmp_path, cfg, FakeCommand())
    texto = job.prompt(
        {"id": 1, "title": "x", "project": "gaia", "context": "a" * 5000},
        "talitadeoa/gaia",
        "gaia/backlog/1-x",
    )
    assert "[contexto truncado]" in texto
    assert len(texto) < 900


# ------------------------------------------------------------------ worker ---


class JobFake:
    def __init__(self, resultado):
        self.resultado = resultado
        self.itens: list[dict[str, Any]] = []

    def execute(self, item):
        self.itens.append(item)
        return self.resultado


@pytest.mark.asyncio
async def test_worker_executa_e_marca_pr(store):
    service = BacklogService(store)
    item = service.add_chat_item("implementar", project="gaia")
    service.mark_queued(int(item["id"]))
    from gaia.backlog.job import BacklogResult

    resultado = BacklogResult(
        ok=True, pr_url="https://github.com/o/r/pull/1", pr_number=1, detail="ok"
    )
    notificados: list[tuple[str, dict, BacklogResult]] = []

    async def notify(status, alvo, res):
        notificados.append((status, alvo, res))

    eventos: list[dict] = []
    worker = BacklogWorker(
        service,
        lambda alvo: JobFake(resultado),
        notify=notify,
        on_change=eventos.append,
    )
    await worker.run_item(service.get(int(item["id"])) or item)

    atual = service.get(int(item["id"]))
    assert atual is not None
    assert atual["status"] == "pr" and atual["pr_number"] == 1
    assert [e["type"] for e in eventos] == ["backlog_started", "backlog_finished"]
    assert notificados and notificados[0][2].ok is True


@pytest.mark.asyncio
async def test_worker_marca_failed_e_avisa(store):
    service = BacklogService(store)
    item = service.add_chat_item("quebrar", project="gaia")
    service.mark_queued(int(item["id"]))
    from gaia.backlog.job import BacklogResult

    avisos: list[str] = []

    async def notify(status, alvo, res):
        avisos.append(res.error)

    worker = BacklogWorker(
        service,
        lambda alvo: JobFake(BacklogResult(ok=False, error="gh caiu")),
        notify=notify,
    )
    await worker.run_item(service.get(int(item["id"])) or item)
    atual = service.get(int(item["id"]))
    assert atual is not None and atual["status"] == "failed"
    assert avisos == ["gh caiu"]


@pytest.mark.asyncio
async def test_worker_pega_o_item_que_esta_apenas_queued(store):
    """Regressao: `queued` contava como "em voo" e a fila nunca andava.

    O item #65 do mini ficou `queued` por horas porque `active_count()`
    (queued + running) era comparado com ``max_running=1``.
    """
    service = BacklogService(store)
    item = service.add_chat_item("fila travada", project="gaia")
    service.mark_queued(int(item["id"]))
    from gaia.backlog.job import BacklogResult

    executados: list[int] = []

    class Job:
        def execute(self, alvo):
            executados.append(int(alvo["id"]))
            return BacklogResult(ok=True, pr_url="https://github.com/o/r/pull/2", pr_number=2)

    worker = BacklogWorker(service, lambda alvo: Job())
    await worker._tick()
    assert executados == [int(item["id"])]
    assert service.get(int(item["id"]))["status"] == "pr"


@pytest.mark.asyncio
async def test_worker_nao_roda_dois_itens_de_uma_vez(store):
    service = BacklogService(store)
    primeiro = service.add_chat_item("um", project="gaia")
    service.add_chat_item("dois", project="gaia")
    service.mark_queued(int(primeiro["id"]))
    service.mark_running(int(primeiro["id"]))
    executados: list[int] = []

    class Job:
        def execute(self, alvo):
            executados.append(int(alvo["id"]))
            raise AssertionError("nao deveria executar")

    worker = BacklogWorker(service, lambda alvo: Job())
    await worker._tick()
    assert executados == []


def test_enqueue_recusa_com_backlog_desligado(store):
    service = BacklogService(store)
    item = service.add_chat_item("x")
    worker = BacklogWorker(service, lambda alvo: JobFake(None), enabled=False)
    with pytest.raises(RuntimeError, match="desligado"):
        worker.enqueue(int(item["id"]))


@pytest.mark.asyncio
async def test_enqueue_marca_queued_e_acorda(store):
    service = BacklogService(store)
    item = service.add_chat_item("x")
    worker = BacklogWorker(service, lambda alvo: JobFake(None))
    atual = worker.enqueue(int(item["id"]))
    assert atual["status"] == "queued"
    assert atual["attempts"] == 1


# ----------------------------------------------------------------- fachada ---


class FakeAgenda:
    enabled = True

    def __init__(self, paginas: list[dict[str, Any]]):
        self.paginas = paginas

    async def pages(self, limit: int = 20):
        return self.paginas


@pytest.mark.asyncio
async def test_fachada_sincroniza_so_paginas_com_o_sufixo(store, cfg):
    agenda = FakeAgenda(
        [
            {
                "id": "p1",
                "title": "gaia backlog",
                "content": "<div>um</div><div>[x] dois</div>",
            },
            {"id": "p2", "title": "agenda backlog", "content": "<li>tres</li>"},
            {"id": "p3", "title": "notas", "content": "ignorar"},
        ]
    )
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    resultado = await fachada.sync_async()
    assert resultado["pages"] == 2
    assert resultado["inserted"] == 3
    snapshot = fachada.snapshot()
    # Os dois projetos tem pendencia: a ordem e alfabetica entre eles.
    assert [g["project"] for g in snapshot["groups"]] == ["agenda", "gaia"]
    repos = {g["project"]: g["repo"] for g in snapshot["groups"]}
    assert repos == {"gaia": "talitadeoa/gaia", "agenda": "talitadeoa/agenda"}


def test_fachada_sync_sem_agenda_explica(store, cfg):
    fachada = BacklogFacade(cfg.backlog, store, agenda=None)
    with pytest.raises(RuntimeError, match="Agenda desabilitada"):
        fachada.sync()


# ------------------------------------------- espelho da Agenda (purga/move) ---


def _pagina(page_id: str, titulo: str, *linhas: str) -> dict[str, Any]:
    return {
        "id": page_id,
        "title": titulo,
        "content": "".join(f"<div>{linha}</div>" for linha in linhas),
    }


@pytest.mark.asyncio
async def test_pagina_apagada_na_agenda_apaga_o_grupo(store, cfg):
    """O caso do 'zaya': pagina que nao existe mais nao fica pendente no card."""
    agenda = FakeAgenda(
        [
            _pagina("p1", "gaia backlog", "um", "dois"),
            _pagina("p2", "zaya backlog", "velho"),
        ]
    )
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    await fachada.sync_async()  # primeira leitura: so observa
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia", "zaya"}

    agenda.paginas = [_pagina("p1", "gaia backlog", "um", "dois")]
    resultado = await fachada.sync_async()
    assert resultado["purged"] == 1
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia"}
    assert store._conn.execute(
        "SELECT COUNT(*) AS n FROM backlog_items WHERE project = 'zaya'"
    ).fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_primeira_sync_nao_apaga_banco_legado(store, cfg):
    """Sem nunca ter lido a Agenda, apagar seria perder trabalho."""
    antigo = BacklogService(store)
    antigo.sync_page(
        page_id="velho", project="zaya", title="zaya backlog", items=[("pendente", False)]
    )
    agenda = FakeAgenda([_pagina("p1", "gaia backlog", "um")])
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    resultado = await fachada.sync_async()
    assert resultado["purged"] == 0
    # O grupo legado fica nesta rodada; a seguinte, com a Agenda ja lida, purga.
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia", "zaya"}
    resultado = await fachada.sync_async()
    assert resultado["purged"] == 1
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia"}


@pytest.mark.asyncio
async def test_pagina_renomeada_move_os_itens_sem_duplicar(store, cfg):
    agenda = FakeAgenda([_pagina("p1", "gaia backlog", "um", "dois")])
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    await fachada.sync_async()
    await fachada.sync_async()  # marca agenda_seen_at
    antes = {i["title"]: i["id"] for i in fachada.snapshot()["items"]}
    # Renomear no Supabase mantem o id da pagina e muda o titulo.
    agenda.paginas = [_pagina("p1", "gaia2 backlog", "um", "dois")]
    resultado = await fachada.sync_async()
    assert resultado["moved"] == 1
    assert resultado["purged"] == 0
    depois = {i["title"]: i["id"] for i in fachada.snapshot()["items"]}
    assert depois == antes  # mesmo id, mesmo texto, grupo novo
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia2"}


@pytest.mark.asyncio
async def test_pagina_renomeada_sem_page_id_casa_pelo_texto(store, cfg):
    agenda = FakeAgenda([_pagina("p1", "gaia backlog", "um", "dois")])
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    await fachada.sync_async()
    await fachada.sync_async()
    # Pagina recriada na Agenda (id novo) com o mesmo conteudo.
    agenda.paginas = [_pagina("p9", "gaia3 backlog", "um", "dois")]
    resultado = await fachada.sync_async()
    assert resultado["moved"] == 1
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia3"}


@pytest.mark.asyncio
async def test_agenda_fora_do_ar_nao_apaga_nada(store, cfg):
    class Quebrada(FakeAgenda):
        async def pages(self, limit: int = 20):
            raise AgendaError("supabase 500")

    agenda = FakeAgenda([_pagina("p1", "gaia backlog", "um")])
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    await fachada.sync_async()
    await fachada.sync_async()
    fachada.agenda = Quebrada([])  # type: ignore[assignment]
    with pytest.raises(AgendaError):
        await fachada.sync_async()
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia"}


@pytest.mark.asyncio
async def test_remove_orphans_desligado_so_grava(store, cfg):
    cfg.backlog.remove_orphans = False
    agenda = FakeAgenda([_pagina("p1", "gaia backlog", "um")])
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    await fachada.sync_async()
    agenda.paginas = []
    resultado = await fachada.sync_async()
    assert resultado["purged"] == 0
    assert {g["project"] for g in fachada.snapshot()["groups"]} == {"gaia"}


@pytest.mark.asyncio
async def test_projeto_sem_repo_aparece_com_needs_repo(store, cfg):
    agenda = FakeAgenda([_pagina("p1", "youtube backlog", "editar video")])
    fachada = BacklogFacade(cfg.backlog, store, agenda=agenda)  # type: ignore[arg-type]
    await fachada.sync_async()
    grupo = fachada.snapshot()["groups"][0]
    assert grupo["project"] == "youtube"
    assert grupo["repo"] == "" and grupo["needs_repo"] is True


def test_set_repo_valida_e_persiste_pelo_gancho(store, cfg):
    gravados: list[tuple[str, str]] = []
    fachada = BacklogFacade(
        cfg.backlog,
        store,
        on_set_repo=lambda projeto, repo: gravados.append((projeto, repo))
        or {"saved": True, "path": "config.toml"},
    )
    salvo = fachada.set_repo("youtube", "https://github.com/talitadeoa/youtube.git")
    assert gravados == [("youtube", "talitadeoa/youtube")]
    assert salvo["repo"] == "talitadeoa/youtube"
    assert cfg.backlog.repo_for("youtube") == "talitadeoa/youtube"
    with pytest.raises(ValueError, match="invalido"):
        fachada.set_repo("youtube", "sem barra")
    with pytest.raises(ValueError, match="vazio"):
        fachada.set_repo("", "talitadeoa/gaia")


def test_set_repo_sem_config_gravavel_nao_finge(store, cfg):
    fachada = BacklogFacade(
        cfg.backlog,
        store,
        on_set_repo=lambda projeto, repo: {"saved": False, "path": ""},
    )
    assert fachada.set_repo("youtube", "talitadeoa/youtube")["saved"] is False


def test_set_repo_sem_gancho_recusa(store, cfg):
    fachada = BacklogFacade(cfg.backlog, store)
    with pytest.raises(RuntimeError, match="config gravavel"):
        fachada.set_repo("youtube", "talitadeoa/youtube")


def test_fachada_add_e_run_chamam_o_worker(store, cfg):
    chamadas: list[int] = []
    fachada = BacklogFacade(cfg.backlog, store, kick=lambda: chamadas.append(1))
    item = fachada.add("melhorar isso", "gaia")
    atual = fachada.run(int(item["id"]))
    assert atual["status"] == "queued"
    assert chamadas == [1]
    assert fachada.remove(int(item["id"])) is True


def test_fachada_desligada_recusa(store, cfg):
    cfg.backlog.enabled = False
    fachada = BacklogFacade(cfg.backlog, store)
    with pytest.raises(RuntimeError, match="desligado"):
        fachada.add("x")
    assert fachada.snapshot()["enabled"] is False


# ------------------------------------------------- rotas do card (servidor) --


def test_rotas_do_backlog_no_servidor(tmp_path, isolated_env):
    import http.client
    import json

    from gaia.interface.remote import RemoteUI
    from gaia.serve.server import ServeServer

    class Target:
        def __init__(self) -> None:
            self.pedidos: list[tuple[str, Any]] = []

        def health(self):
            return {"model": "fake"}

        def sessions(self):
            return []

        def models(self):
            return []

        def switch_model(self, slug):
            return []

        def search(self, query, limit=8):
            return []

        def turn(self, text):
            return "r1"

        def decide(self, request_id, decision):
            return None

        def abort(self):
            return None

        def run_awaitable(self, aguardavel, timeout=60.0):
            return asyncio.run(aguardavel) if asyncio.iscoroutine(aguardavel) else aguardavel

        def backlog_snapshot(self):
            self.pedidos.append(("snapshot", None))
            return {"enabled": True, "groups": [], "counts": {"total": 0}, "labels": {}}

        async def backlog_sync(self):
            self.pedidos.append(("sync", None))
            return {"ok": True, "pages": 1, "inserted": 2, "updated": 0, "items": 2}

        def backlog_add(self, text, project=""):
            self.pedidos.append(("add", (text, project)))
            return {"id": 1, "title": text, "project": project, "status": "pending"}

        def backlog_enqueue(self, item_id):
            self.pedidos.append(("run", item_id))
            if item_id == 99:
                raise KeyError(item_id)
            return {"id": item_id, "status": "queued", "status_label": "Enviado ao Mini PC"}

        def backlog_set_repo(self, project, repo):
            self.pedidos.append(("repo", (project, repo)))
            if project == "youtube" and repo == "sem-barra":
                raise ValueError("repositorio invalido: 'sem-barra' (esperado dono/repo)")
            return {"saved": True, "project": project, "repo": repo,
                    "path": "config.toml", "backup": "config.toml.bak"}

    alvo = Target()
    servidor = ServeServer(alvo, RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = servidor.start()

    def pedir(metodo, caminho, corpo=None):
        host, porta = base.removeprefix("http://").split(":")
        conexao = http.client.HTTPConnection(host, int(porta), timeout=10)
        dados = json.dumps(corpo).encode() if corpo is not None else None
        cabecalhos = {"Content-Type": "application/json"} if dados else {}
        conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
        resposta = conexao.getresponse()
        bruto = resposta.read()
        conexao.close()
        try:
            return resposta.status, json.loads(bruto)
        except ValueError:
            return resposta.status, bruto

    try:
        status, corpo = pedir("GET", "/backlog")
        assert status == 200 and corpo["groups"] == []

        status, corpo = pedir("POST", "/backlog/sync", {})
        assert status == 200 and corpo["inserted"] == 2

        status, corpo = pedir("POST", "/backlog/add", {"text": "novo", "project": "gaia"})
        assert status == 200 and corpo["item"]["id"] == 1
        assert ("add", ("novo", "gaia")) in alvo.pedidos

        status, corpo = pedir("POST", "/backlog/add", {})
        assert status == 400 and corpo["error"] == "empty_text"

        status, corpo = pedir("POST", "/backlog/run", {"id": 1})
        assert status == 202 and corpo["item"]["status"] == "queued"

        status, corpo = pedir("POST", "/backlog/run", {"id": 99})
        assert status == 404 and corpo["error"] == "unknown_item"

        status, corpo = pedir("POST", "/backlog/run", {})
        assert status == 400 and corpo["error"] == "missing_id"

        status, corpo = pedir("POST", "/backlog/repo", {"project": "youtube", "repo": "talitadeoa/youtube"})
        assert status == 200 and corpo["repo"] == "talitadeoa/youtube"
        assert ("repo", ("youtube", "talitadeoa/youtube")) in alvo.pedidos

        status, corpo = pedir("POST", "/backlog/repo", {"repo": "talitadeoa/youtube"})
        assert status == 400 and corpo["error"] == "missing_project"

        status, corpo = pedir("POST", "/backlog/repo", {"project": "youtube"})
        assert status == 400 and corpo["error"] == "missing_repo"

        status, corpo = pedir("POST", "/backlog/repo", {"project": "youtube", "repo": "sem-barra"})
        assert status == 400 and corpo["error"] == "bad_repo"
    finally:
        servidor.shutdown()


# ------------------------------------------------------- Ada no feed (fase 3) ---


@pytest.mark.asyncio
async def test_aviso_do_pr_tambem_vira_feed_da_ada(store):
    """O PR aberto no backlog alimenta o feed da Ada (design §7, item 13).

    O Telegram continua opcional; o painel e a entrega que sempre existe.
    """
    import logging

    from gaia.backlog.job import BacklogResult
    from gaia.controller.backlog import BacklogMixin

    class _UI:
        def __init__(self) -> None:
            self.proativos: list[str] = []

        def proactive(self, texto: str) -> None:
            self.proativos.append(texto)

        def emit(self, tipo: str, **dados: Any) -> None:
            return None

    class _Telegram:
        enabled = False

    stubs = {nome: (lambda *a, **k: None) for nome in BacklogMixin.__abstractmethods__}
    Concreta = type("FakeBacklog", (BacklogMixin,), stubs)

    class Fake(Concreta):
        def __init__(self) -> None:
            self.store = store
            self.ui = _UI()
            self.telegram = _Telegram()
            self.logger = logging.getLogger("test.backlog.feed")
            self._feed: list[tuple[str, str, str, str]] = []

        def _routine_on(self, routine_id: str) -> bool:
            return True

        def bot_message(self, bot, kind="job", title="", text=""):
            self._feed.append((bot, kind, title, text))
            return 1

    orch = Fake()
    resultado = BacklogResult(
        ok=True, pr_url="https://github.com/o/r/pull/9", pr_number=9
    )
    item = {"id": 4, "title": "corrigir login", "project": "gaia"}
    await orch._backlog_notify("pr", item, resultado)

    assert orch.ui.proativos and "PR pronto" in orch.ui.proativos[0]
    bot, kind, titulo, texto = orch._feed[0]
    assert (bot, kind) == ("ada", "job")
    assert "PR pronto" in titulo and "pull/9" in texto
