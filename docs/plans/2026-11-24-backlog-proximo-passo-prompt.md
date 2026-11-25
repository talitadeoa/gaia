# Prompt para continuar (nova sessão) — backlog: da sincronização ao despacho inteligente

Cole o bloco abaixo numa sessão nova. O estado foi verificado em 2026-11-24,
HEAD `e97af52` no mini.

---

## Prompt

Estamos no repo `~/gaia` (Python 3.12, venv `.venv`). Leia `AGENTS.md` e
`docs/plans/2026-11-24-backlog-espelho-da-agenda-design.md` antes de começar.

**O que já está pronto e deployado** (não refazer, só entender):

- `atualizar` (`POST /backlog/sync`) faz três coisas na ordem: grava os itens,
  **move** o grupo cuja página foi renomeada (mesmo `page_id` ou ≥80% dos
  fingerprints) e **apaga** o grupo cuja página saiu da Agenda.
  `sync_page(prune=True)` espelha também **por linha**: linha removida/reescrita
  na página sai do backlog (preserva `pr`/`done`; não remove nada quando a
  página foi truncada por `max_items`).
- Identidade do item é `(project, fingerprint)` — **não** o `page_id` do
  Supabase (schema v10: `idx_backlog_page(source, project, fingerprint)`).
- Guardas: exceção em `agenda.pages()` sobe antes da purga (nunca apaga sem
  leitura); `[backlog] remove_orphans` desliga a limpeza; o meta
  `backlog.agenda_seen_at` faz a **primeira** sync só observar.
- Repo nunca é chutado: `BacklogConfig.repo_for` devolve `""`; o grupo ganha
  `needs_repo`; `POST /backlog/repo` (e `/backlog repo <projeto> <dono/repo>`)
  grava `[backlog.repos]` no TOML via `control/settings.update`; o card mostra
  `sem repositório` + campo `dono/repo` no formulário de item novo.
- Worker: o limite de concorrência usa `running_count()` (só `running`), não
  `active_count()` — o bug que travava a fila em `queued` já foi corrigido.

**O problema a resolver agora:** a página `gaia backlog` é um *brain dump* e o
backlog trata **cada linha como ticket para um agente autônomo**. Estado real no
mini: 19 linhas em `gaia backlog` (≈4 tarefas de verdade, o resto ideia/wish e
até uma linha que é só `-`), 11 em `agenda backlog`, 2 em `suite backlog`.
Cada linha = 1 branch + 1 pi + 1 PR. O item #65 ("novo icon a partir da imagem
em `data/imagem.jpg`") gastou 15 min de pi e falhou no timeout de 900 s —
`data/` é gitignored e não existe no clone: era falta de contexto, não de
capacidade.

**Três mudanças, nesta ordem:**

1. **A unidade de trabalho é a página, não a linha.** "Executar" no grupo
   (cabeçalho do projeto no card) → **um** branch, **um** pi, **um** PR com
   todas as pendências no prompt. O botão por linha continua para o caso
   pontual. Implementar em `gaia/backlog/` (job/worker) + rota
   `POST /backlog/run` aceitando `{project}` além de `{id}` + botão no
   `index.html`. Revise `BacklogJob.prompt` (hoje é 1 item) e
   `prepare_clone`/`commit_and_push` (branch por item → branch por projeto,
   ex.: `gaia/backlog/<projeto>-<data>`).
2. **Triagem antes do despacho.** Classificar cada linha em `tarefa`
   (imperativa/escopada) | `ideia` (precisa decisão) | `sem_contexto` (depende
   de arquivo/URL fora do repo). `ideia`/`sem_contexto` **não** vão ao pi: o
   card mostra o rótulo e a ação vira "planejar com a Gaia" (turno de chat que
   quebra em itens executáveis). **Decisão pendente do dono:** classificação
   automática (modelo barato no sync) com override manual, ou manual por
   prefixo na página (`? ` = ideia). Pergunte antes de implementar.
3. **Timeout do backlog separado do pi.** `[backlog] pi_timeout = 1800`
   (hoje herda `[pi] timeout = 900`); `BacklogJob._runner_config` já copia o
   mesmo `PiConfig`, então basta sobrescrever `timeout` ali. Retry é seguro:
   `prepare_clone` faz `checkout -B <branch> origin/<base>`.

Higiene junto: descartar linha sem nenhum caractere alfanumérico em
`gaia/backlog/extract.py::normalize_item` (a linha `-` sai de graça) + teste.

**Armadilhas conhecidas do backlog:** o pi roda com `cwd` no clone
(`~/pr-workspace/<owner>-<repo>`), nunca em `~/gaia`; `data/` é gitignored e
não está no clone (contexto de item que cita `data/...` precisa ir no prompt ou
falhar rápido); `gh` e `gh auth login` têm de existir no mini; o clone sujo
recusa o despacho de propósito; o worker grava `running` antes de executar,
então item preso em `running` só se o processo morrer.

**Verificação obrigatória antes de commit** (o CI roda exatamente isto):
`.venv/bin/python -m pytest -q && .venv/bin/ruff check gaia tests && .venv/bin/mypy`
Testes do domínio: `tests/test_backlog.py`, `tests/test_serve_web.py`.
Deploy: `git push origin main && gmin deploy` (mini roda
`/home/talita/gaia`; `gmin serve status` confirma). Nada de `rsync`.

**Não mexa** nos arquivos já modificados no working tree por outro trabalho em
andamento (`gaia/controller/automation_builder.py`, `tests/test_memory.py`,
`tests/test_scheduler.py`, `docs/plans/2026-11-24-automacoes-pelo-chat.md`) —
não são do backlog.

Comece confirmando o estado: `git log --oneline -3`,
`POST /backlog/sync` no mini e o `GET /backlog` (contagens por grupo). Depois
proponha o plano da mudança 1 antes de escrever código.
