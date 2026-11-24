# Backlog espelho da Agenda — plano de implementação

Design: `docs/plans/2026-11-24-backlog-espelho-da-agenda-design.md`.
Verificação a cada passo: `.venv/bin/python -m pytest -q tests/test_backlog.py`,
`.venv/bin/ruff check gaia tests`, `.venv/bin/mypy`.

Ordem: modelo/config -> banco/serviço -> job -> fachada -> orquestrador/rotas ->
tela -> docs. Cada passo é commitável e testável sozinho.

---

## Passo 1 — Config: repo explícito e janela maior

`gaia/config/models.py` (`BacklogConfig`):

- `agenda_scan: int = 200`.
- `remove_orphans: bool = True` (checkpoint pessoal; sem ele o sync só grava).
- `repo_for(project)` -> `str`: só o mapeamento; sem casamento devolve `""`.
  Remover o fallback `f"{github_owner}/{project}"`. Manter `github_owner` como
  default de criação (usado por `set_repo` quando a pessoa digita só o repo).
- Docstring: "a pagina e a fonte; o repositorio nunca e chutado".

`gaia/config/template.py` (`[backlog]`): comentar o contrato —
"titulo terminando em `backlog` e a pagina do projeto; apagar/renomear na Agenda
move/apaga o grupo"; `agenda_scan = 200`, `remove_orphans = true`.

Testes (`tests/test_config.py`): `repo_for` sem mapeamento == `""`; template
contém `remove_orphans`.

---

## Passo 2 — Serviço: `page_key`, mover, apagar, repos

`gaia/backlog/service.py`:

- `page_key(project)` (a coluna `project` passa a ser a chave; não precisa de
  coluna nova — o índice `idx_backlog_page` já é `(source, page_id,
  fingerprint)`, trocar por `(source, project, fingerprint)` na migração).
- `sync_page(page_id, project, title, items)` -> grava com
  `WHERE source='agenda' AND project=? AND fingerprint=?`; `page_id` é apenas
  referência (`UPDATE` no mesmo `INSERT`).
- `known_projects() -> set[str]`: `SELECT DISTINCT project FROM backlog_items
  WHERE source='agenda'`.
- `purge_project(project) -> int`: `DELETE ... WHERE source='agenda' AND
  project=?`.
- `move_project(old, new) -> int`: `UPDATE backlog_items SET project=? WHERE
  source='agenda' AND project=?` (sem tocar em status/fingerprint; se colidir
  com item já existente no novo grupo com mesmo fingerprint, apagar o duplicado
  mais novo antes do update).
- `set_repo` não mora aqui (é TOML, ver passo 5). O item continua guardando
  `repo` do despacho, como hoje.
- `pages()` já agrupa por `project`: revisar o fallback `project or page_id`.
- `prune()` fica igual; adicionar docstring "item de chat não morre com a
  página".

Migração (`gaia/memory/store.py`): schema v10 — recriar
`idx_backlog_page` como `(source, project, fingerprint)`. A tabela não muda
(`page_id` continua). `SCHEMA_VERSION = 10` e o bloco de migração em
`_migrate()` (mesmo estilo dos anteriores).

Testes (`tests/test_backlog.py`): `sync_page` idempotente por `project`;
`purge_project` remove só o grupo; `move_project` preserva id/status e resolve
duplicata; migração v10 tem o índice novo.

---

## Passo 3 — Job: recusar projeto sem repo

`gaia/backlog/job.py`:

- `repo_of(item)` -> `self.cfg.repo_for(project)` (sem chute). Quem chama já
  trata `BacklogJobError`: em `execute()`, antes de `prepare_clone`, se
  `not repo` levantar `BacklogJobError("projeto 'x' sem repositorio; defina em
  [backlog.repos] (ou no card do backlog)")`.
- `BacklogResult.repo` continua string (vazio na falha).

Testes: ajustar `test_repo_mapeado_por_projeto` (o caso `estudos` passa a
esperar `""`); teste novo `execute` sem repo falha sem chamar `gh`/`pi`.

---

## Passo 4 — Fachada: sync em 3 fases + `set_repo`

`gaia/backlog/facade.py`:

- `_gravar_paginas(paginas)`:
  1. monta `{page_key: (page_id, title, items)}` só para títulos com sufixo;
  2. `service.sync_page(...)` para cada (como hoje);
  3. reconciliação, só se `remove_orphans` **e** `agenda_seen_at` já existir
     (`store.get_meta("backlog.agenda_seen_at")`):
     - `conhecidos = service.known_projects() - chaves_agora`;
     - para cada `antigo`: tentar renome — mesmo `page_id` presente agora
       (`service.find_by_page_id(antigo)` -> `page_id` atual -> chave) ou
       grupo novo com interseção de fingerprints `>= 80%`; `move_project`;
     - sem casamento: `purge_project`.
  4. `store.set_meta("backlog.agenda_seen_at", agora)` (a primeira leitura é só
     observação).
  5. resultado do sync leva `moved`, `purged`, `projects` (para o toast).
- `snapshot()`: cada grupo ganha `needs_repo = not cfg.repo_for(project)` e
  `repos` no JSON deixa de ser `dict(cfg.repos)` puro: unir os defaults do
  módulo (passo 2 do design) com o TOML.
- `set_repo(project, repo)` -> delega para um callable injetado (`on_set_repo`)
  para não importar `gaia.control` do backlog. Quem injeta é o mixin (passo 5).
- `sync()`/`sync_async()` devolvem `AgendaError` quando `pages()` falha (hoje a
  exceção sobe; garantir que a purga **não** roda antes da leitura — já é o
  caso, a ordem acima garante).

Novo módulo `gaia/backlog/repos.py`: `DEFAULT_REPOS` (gaia/agenda/suite) e
`merge(cfg_repos) -> dict`. O `Config` passa a ler o default daqui? Não: manter
o default no pydantic e o `repos.py` só com a constante + `owner/repo` validado
(`validate_repo`).

Testes: cenários 1–4 do design (purga, renome, primeira sync, falha sem delete).

---

## Passo 5 — Orquestrador, rotas e comando

`gaia/controller/backlog.py`:

- `backlog_service_facade()` injeta `on_set_repo=self._backlog_set_repo`.
- `_backlog_set_repo(project, repo)`: chama
  `gaia.control.settings.update(cfg.config_file, "backlog", "repos", novo,
  cfg=self.cfg)` com o dict mesclado; reflete em `self.cfg.backlog.repos` e
  agenda SSE `backlog_repo`. `cfg.config_file` vazio -> erro
  "config nao gravavel" (503).
- `backlog_remove_project(project)` para o comando (opcional: `/backlog
  remover <projeto>`).
- `_handle_backlog`: novos subcomandos `repo <projeto> <dono/repo>`; ajuda
  atualizada.

`gaia/controller/contract.py`: `def backlog_set_repo(self, project: str, repo:
str) -> dict[str, Any]: ...`.

`gaia/serve/app.py` (`OrchestratorTarget`): `backlog_set_repo(project, repo)`
via `getattr(self.orch, ...)`.

`gaia/serve/server.py` (`POST`): rota `/backlog/repo`
-> `_backlog_repo(corpo)`: corpo `{project, repo}`; 400 `missing_project` /
`missing_repo` / `bad_repo` (valida `owner/repo`); 422 `ValueError` do
`settings.update`; 503 desligado/sem config; 404 projeto inexistente (se não
houver grupo nem página); 200 `{ok, project, repo, path, backup}`.

`gaia/controller/command_catalog.py`: `"/backlog [sync|add|run|repo|remover]"`.

Testes: `tests/test_backlog.py` (comando e mixin com store real),
`tests/test_serve_web.py`/`test_backlog.py` (rota: 400/422/503/200 + TOML
gravado em `tmp_path`).

---

## Passo 6 — Tela: selo e campo de repo

`gaia/serve/static/index.html`:

- `backlogGrupo`: quando `grupo.needs_repo`, `<span class="repo">` vira
  `sem repositório` (classe `.falta`), e o `summary` ganha `title` explicando.
- `backlogCorpo`/`novo`: se o projeto selecionado tem `needs_repo`, mostrar
  (na tela e no card aberto) a linha `repositorio: <input dono/repo> [salvar]`
  ao lado do seletor; `salvar` faz `POST /backlog/repo {project, repo}` e
  `carregarBacklog()`.
- CSS: `.backlog-card .repo.falta { color: var(--err); }` e `.backlog-card
  .novo .repo-entrada { flex: 1 1 14ch; }` (classe nova, sem inventar layout).
- Não mexer nos ids existentes (`backlog-novo-<local>`, `focoDoBacklog`).

Testes (`tests/test_serve_web.py`): contrato — `needs_repo`, texto
`sem repositório`, `POST /backlog/repo` no `index.html`, e que o campo de repo
usa id `backlog-repo-<local>` (para o `focoDoBacklog` não perder o cursor).

---

## Passo 7 — Documentação

- `README.md`: card do backlog — regra "página é a fonte", purga de página
  apagada, repo obrigatório em `[backlog.repos]` (sem chute), `POST
  /backlog/repo` na lista de rotas, `/backlog repo` na tabela de comandos.
- `AGENTS.md`: receita do backlog ganha 2 linhas (página renomeada move grupo;
  projeto sem repo não despacha) + o design/plano em `docs/`.
- `gaia/backlog/__init__.py`: docstring da regra única.

---

## Risco conhecido (aceito no design)

Página que sai da janela `agenda_scan` pode ser purgada e recriada na sync
seguinte (itens pendentes voltam; PR já aberto perde só o vínculo no card). A
janela de 200 e o `remove_orphans` documentado são o controle; se incomodar, o
próximo passo é paginação real da tabela `pages`.
