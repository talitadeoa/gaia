# Backlog espelho da Agenda (design)

**Data:** 2026-11-24
**Problema:** o backlog é uma *cópia* da Agenda, não um espelho. `_gravar_paginas`
só insere/atualiza e `sync_page` documenta "itens que sumiram da pagina nao sao
apagados". Resultado: apagar/renomear uma página na Agenda deixa o grupo para
sempre no card. É o caso do `zaya`: página que não existe mais, vários itens
pendentes, e o despacho cairia em `talitadeoa/zaya` por chute do `repo_of`.

**Regra única:** *a página manda*. Título terminando em `backlog` = página de
backlog. Sem página, sem grupo. Sem repo configurado, o grupo aparece mas não
despacha — pede o repo no ponto de adicionar e grava no TOML.

## Decisões (fechadas)

1. **Fonte:** páginas cujo título termina em `backlog` (`[backlog] agenda_suffix`).
2. **Identidade da página = o título** (`gaia backlog` -> `gaia`). O `page_id` do
   Supabase deixa de ser chave; vira referência (renomear a página move os itens,
   igual desmarcar/remarcar dentro dela).
3. **Repo:** `[backlog.repos]` do TOML, editado pelo card/painel. Nada de
   `owner/<projeto>` chutado; sem mapeamento o item não despacha.
4. **A purga só roda com a Agenda lida com sucesso.** Agenda fora do ar, credencial
   errada, `enabled = false`: nada é apagado (erro nunca vira perda de dado).
5. **Primeira sincronização só observa** (`backlog.agenda_seen_at` vazio -> grava,
   não apaga). O `zaya` fantasma cai na sincronização seguinte, quando a Agenda
   já foi lida de verdade.
6. **Apagar página apaga o grupo inteiro**, inclusive `pr`/`done`. O PR continua
   no GitHub; o card é da Agenda (`[backlog] remove_orphans = true`).
7. **Item de chat não morre pela página** (não tem página): segue a limpeza de
   `prune_days` que já existe.

## Comportamento novo na sincronização

`facade._gravar_paginas(paginas_de_paginas)` passa a ser uma janela de 3 fases:

| situação na Agenda | o que acontece no backlog |
|---|---|
| página nova (`estudo backlog`) | grupo novo com `needs_repo: true`, `repo: ""`; item não despacha até definir o repo |
| página existente | insere/atualiza como hoje (PR nunca volta para `pending`) |
| página renomeada (`gaia backlog` -> `gaia2 backlog`) | itens mudam de `page_key` (mesmo id, mesmo status, FIFO preservado) |
| página apagada | grupo inteiro sai do banco |
| primeira sync após a migração | nada é apagado (só grava e marca `agenda_seen_at`) |
| Agenda indisponível | `AgendaError` -> 503, banco intacto |

Detecção de renome: ao final da varredura, para cada `page_key` conhecido que
não está mais na Agenda, procura um `page_id` (renomeado) ou um grupo antigo de
mesmo conjunto de fingerprints e move. Sem casamento, apaga.

**Janela de varredura:** `agenda_scan` passa de 50 para 200. Com a regra "não
está na Agenda -> sai", uma página fora da janela sumiria por engano. Continua
uma chamada só (mesmo custo); a janela é explícita na config.

## Repo: perguntar onde a pessoa já está

- `BacklogConfig.repos` (default em código) + `[backlog.repos]` do TOML
  (mesclado por cima). O painel mostra como `repos` editável (JSON no TOML,
  aceito na leitura).
- `repo_for()` deixa de chutar: devolve `""` quando não há mapeamento.
  `BacklogJob.repo_of` -> `BacklogJobError("projeto 'x' sem repositorio; defina
  em [backlog.repos]")` antes de tocar em git/gh/pi.
- **No card/tela:** grupo sem repo ganha selo `sem repositório — definir` e, no
  formulário de item novo, um campo `dono/repo` já com o projeto selecionado.
  Salvar (`POST /backlog/repo {project, repo}`) grava
  `backlog.repos.<projeto>` no TOML via `control/settings.update` (backup,
  releitura, origem `toml`) e o motor reflete no processo na hora. Valida
  `owner/repo`.
- Comando equivalente: `/backlog repo <projeto> <dono/repo>`.

## Arquivos

| arquivo | mudança |
|---|---|
| `gaia/config/models.py` | `repos` default; `repo_for` sem chute; `remove_orphans`, `agenda_scan=200` |
| `gaia/config/template.py` | comentário do sufixo = contrato da página de backlog |
| `gaia/backlog/service.py` | `page_id` -> `page_key`; `purge_group`, `move_group`, `known_pages`, `set_repo`; match por `page_key`+fingerprint |
| `gaia/backlog/facade.py` | sync em 3 fases (gravar, reconciliação, purga) com `agenda_seen_at`; `set_repo`; `snapshot` com `needs_repo` |
| `gaia/backlog/job.py` | `repo_of` recusa sem repo; `clone_dir`/`prompt` seguem iguais |
| `gaia/controller/backlog.py` + `contract.py` | `backlog_set_repo`; `/backlog repo` |
| `gaia/controller/command_catalog.py` | linha do comando |
| `gaia/serve/server.py` + `app.py` | `POST /backlog/repo` (401/400/404/503/422) |
| `gaia/serve/static/index.html` | selo `sem repositório` + campo de repo no "novo item" |
| `tests/test_backlog.py`, `tests/test_serve_web.py` | ver abaixo |

## Testes (contrato)

1. página some -> itens do grupo somem na 3ª sync (2ª sync = primeira com
   `agenda_seen_at`); itens de chat sobrevivem.
2. página renomeada -> mesmos ids, novo `page_key`, nenhum duplicado.
3. primeira sync após a migração não apaga banco legado (`zaya`).
4. Agenda falha -> `AgendaError`, banco intacto (nenhum delete).
5. projeto sem repo -> `needs_repo` no snapshot; `run` recusa com mensagem;
   `POST /backlog/repo` grava TOML, backup, e o processo passa a ter o repo.
6. `repo_for` sem mapeamento devolve `""` (não chuta `owner/projeto`).
7. contrato da tela: selo e campo de repo presentes (`test_serve_web.py`).

## Fora de escopo

- Criar página na Agenda pelo chat (`/backlog pagina`) — fica para depois; hoje
  a página nasce na Agenda e o sync a pega.
- Histórico de PR de grupo apagado: o PR vive no GitHub, não no card.
- `title_html`/`context` continuam existindo; a sincronização ainda não os usa.
