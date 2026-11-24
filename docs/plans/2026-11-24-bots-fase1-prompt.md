# Prompt de continuação — bots especialistas, Fase 1

> **Como usar:** cole o bloco "PROMPT" numa sessão nova, na raiz do repo
> (`~/gaia`). O estado abaixo foi verificado em 24/11/2026, com `pytest`, `ruff`
> e `mypy` verdes na branch `docs/bots-especialistas-design` (PR #1 aberto).
> Design: `docs/plans/2026-11-24-bots-especialistas-design.md`.
> Fase 0 (feita): `docs/plans/2026-11-24-bots-fase0.md`.

---

## Estado no fim da sessão anterior

**Fase 0 está implementada em código** (7 commits, +2434/−27, 17 arquivos),
mas **não mergeada**: está tudo na branch `docs/bots-especialistas-design`,
PR https://github.com/talitadeoa/gaia/pull/1. `main` continua no `69f37c3`.

O que já existe e **não** precisa ser reescrito:

| peça | onde | o que garante |
|---|---|---|
| eventos por sessão | `AgentClient.subscribe_session` / `stream_session_events` / `_drain_events(session_id)` | dois turnos concorrentes não se roubam |
| `SessionPolicy` | `gaia/agent/session.py` (persistida no JSON da sessão) | tools/directory/scope/bot sobrevivem ao restart |
| `set_session_policy` | `gaia/agent/runtime.py` | allowlist da política ganha da lista do turno |
| `ToolContext.scope` | `gaia/agent/tools/base.py` (último campo, de propósito) | teto de filesystem separado do cwd |
| schema v12 | `gaia/memory/store.py` | `bot` em `runs`/`automations`, tabela `bot_messages` + `unread_by_bot`/`mark_bot_seen` |
| `text_extensions` | `SecondBrainConfig` + `_iter_markdown` | indexa `.tex`/`.bib`/`.txt`; título por `\section` |

---

## PROMPT (colar numa sessão nova)

```
Repo ~/gaia (Python 3.12, .venv). Objetivo: implementar a FASE 1 dos bots
especialistas (registry + seleção), em cima da fase 0 que já está pronta.

ANTES DE QUALQUER CODIGO

1. Leia AGENTS.md inteiro (invariantes, receitas, armadilhas).
2. Leia docs/plans/2026-11-24-bots-especialistas-design.md (design aprovado,
   §4.1 registry, §4.2 isolamento em 4 camadas, §4.3 roteamento, §7 fases).
3. Leia docs/plans/2026-11-24-bots-fase0.md (o que já foi entregue).
4. Rode os tres verificadores e confirme que estao verdes ANTES de mexer:
       .venv/bin/python -m pytest -q
       .venv/bin/ruff check gaia tests
       .venv/bin/mypy

ESTADO ENTREGUE (FASE 0) — NAO REFAZER, NAO REDESENHAR

- Eventos por sessao: AgentClient.subscribe_session(session_id),
  unsubscribe_session, stream_session_events; _emit faz fan-out;
  _drain_events(session_id) limpa so a fila do turno. turns.py usa
  stream_session_events e nao filtra mais por session_id.
- SessionPolicy(tools, directory, scope, bot) em gaia/agent/session.py,
  persistida em AgentSession.policy (JSON em data/sessions/).
- AgentClient.set_session_policy(session_id, policy); _allowed_tools_for
  (politica > tool_names do turno > catalogo) e _tool_context_for
  (dataclasses.replace com directory/scope da politica).
- ToolContext.scope (ULTIMO campo do dataclass: ha chamador posicional em
  tests/test_planilhas.py — nao reordene).
- Schema v12: runs.bot, automations.bot, tabela bot_messages com
  add_bot_message / list_bot_messages / mark_bot_seen / unread_by_bot.
  list_runs(bot=...) filtra.
- SecondBrainConfig.text_extensions (padrao [".md"]); _iter_markdown respeita;
  _record extrai titulo de \section/\chapter para .tex.

O QUE FALTA AGORA (FASE 1 do design §7)

Task 1 — gaia/bots/registry.py (novo)
  @dataclass(frozen=True) Bot: id, name, role, directive, workspace, scope,
  tools (tuple|None), model, aliases, can_message.
  DEFAULT_BOTS com os cinco: gaia, donna, ariana, sabrina, ada.
    gaia    = todas as tools + falar_com_bot; workspace = diretorio do projeto
    donna   = read/list/glob/grep/write/edit/bash/pi; ~/dissertacao
    ariana  = bolsa/carteira/read; sem modelo no laco
    sabrina = career/read/list/glob/grep/write/bash; ~/SecondBrain/cv
    ada     = read/write/edit/bash/grep/glob/pi; ~/pr-workspace (nunca ~/gaia)
  load_bots(cfg) mescla cfg.bots (TOML) sobre os defaults.
  VALIDACAO DURA: id desconhecido em tools/can_message, workspace inexistente,
  scope que nao e diretorio absoluto existente OU que nao contem o workspace ->
  levanta ValueError com mensagem clara (bot mal configurado nao sobe).
  As diretrizes vem de gaia/controller/modes.py (renomear, nao reescrever):
  pesquisadora->donna, investidora->ariana, trabalhadora->sabrina; assistente
  vira gaia; ada e nova.

Task 2 — Config.bots (TOML)
  Secao [bots.<id>] com os campos do Bot (menos id). Em gaia/config/core.py
  (Config) + gaia/config/template.py (bloco comentado, campos opcionais).
  NAO mexa em _ENV_MAP: nao ha parser de lista/dict e viraria string.

Task 3 — controller/modes.py como shim
  PERSONAS derivado do registry (mesmo dataclass Persona), ALIASES ganha
  donna/haraway/ariana/sabrina/ada. parse_mode_switch("vira Donna") funciona.
  tests/test_modes.py precisa continuar verde; ele fixa as 4 chaves antigas em
  test_quatro_modos_esperados (COMPAT_KEYS) e uma checagem de 5 chaves novas
  (BOT_KEYS). Ajuste o teste NA MESMA MUDANCA, com comentario explicando.
  COMPATIBILIDADE (obrigatoria, ha conversa e config no disco):
  assistente->gaia, pesquisadora->donna, trabalhadora->sabrina,
  investidora->ariana continuam resolvendo via ALIASES, e as conversas
  antigas com mode="pesquisadora" continuam abrindo (select_conversation usa
  resolve_mode, que precisa aceitar a chave antiga).

Task 4 — Orchestrator.select_bot(id)
  Em gaia/controller/orchestrator.py, ao lado de select_mode: resolve o Bot,
  get_or_create_conversation(dir, bot.id, channel), _activate_conversation e,
  ANTES do primeiro turno, client.set_session_policy(sid, SessionPolicy(...)).
  Aplicar em _open_session quando a conversa retomada pertence a um bot com
  politica (senao sessao antiga volta com todas as tools).
  Modelo por bot: se bot.model preenchido, usar como model do create_session.
  /bot no command_catalog.py E no _handle_command de commands.py (o teste
  tests/test_command_catalog.py compara os dois por AST: os dois ou nada).

Task 5 — rotas do serve
  OrchestratorTarget.bots() / select_bot() em gaia/serve/app.py; GET /bots e
  POST /bot/select em gaia/serve/server.py (atras de _autorizado(), token do
  chat). Incluir "bot" no /health e no _engine(). 409 com motor ocupado
  (MotorOcupado), 400 com id desconhecido (nunca cair no padrao em silencio).
  REAPROVEITE /modes e /mode: eles continuam existindo por compatibilidade e
  devem responder a partir do mesmo registry (sem duplicar logica).

Task 6 — setup operacional da Donna (NAO e codigo)
  Clonar https://github.com/talitadeoa/dissertacao (privado, main ainda vazio)
  em ~/dissertacao, mover o conteudo de ~/SecondBrain/pesquisa (13 .md,
  2 .docx, 1 .tex, 1 .html) preservando capitulos/debate publico e _templates,
  commit + push. Decidir e registrar no config: symlink
  (~/dissertacao -> ~/SecondBrain/pesquisa, scope = ~/SecondBrain) ou clone
  solto (scope = ~/dissertacao). Atualizar o README/AGENTS com a escolha.
  Sem esse passo o validador do registry recusa a Donna subir — e o
  comportamento desejado, nao um bug.

REGRAS

- Comentarios e mensagens em portugues SEM acento ("sessao", "execucao").
- .venv/bin/python -m pytest -q && .venv/bin/ruff check gaia tests &&
  .venv/bin/mypy antes de CADA commit. Vermelho = nao commita.
- NAO toque em: gate por bot no scheduler, bot_task, BotBus/falar_com_bot,
  sidebar do web (#b/<bot>), automations.bot no seed. Isso e fase 2/3/4.
- Um processo escritor: bot NAO e processo.
- Allowlist e escopo sao a fronteira de seguranca; prompt nao e.
- Se algo deste relatorio divergir do codigo, O CODIGO VENCE: corrija a doc na
  mesma mudanca.

ANTES DE TERMINAR

  .venv/bin/python -m pytest -q
  .venv/bin/ruff check gaia tests
  .venv/bin/mypy
  git diff --stat main   # conferir que so entrou fase 1

Atualize nestes arquivos na MESMA mudanca:
- AGENTS.md: mapa do codigo (gaia/bots/), receita "novo bot", criterios.
- docs/README.md: status do plano (fase 1 feita; fases 2-4 pendentes).
- docs/plans/2026-11-24-bots-especialistas-design.md: marcar a Fase 1
  como feita no §7 (mesmo padrao do bloco "> Feita" da Fase 0).
```

---

## Decisões já tomadas (não reabrir)

| # | decisão |
|---|---|
| D1 | Bot é descritor declarativo; **não** é processo. Um escritor só (`gaia serve`). |
| D2 | `conversations.mode` **é** o id do bot (zero migração de dados). |
| D3 | `id` curto e definitivo (`gaia`, `donna`, `ariana`, `sabrina`, `ada`); `name` completo na UI. |
| — | Donna: repo `talitadeoa/dissertacao`, clone `~/dissertacao`, `bash` e `pi` liberados. |
| — | Ariana: **determinística**, sem modelo; entrega no painel (feed) + Telegram opcional. |
| — | Sabrina: CV em **LaTeX** (`cv-pt.tex` base intocável, `pdflatex` pelo `bash`). |
| — | Ada: abre PR pelo caminho que já existe (`BacklogJob`), nunca direto no `~/gaia`. |

## Pendências conhecidas que a Fase 1 não resolve

- **Gate duplo da Ariana** (fase 3): `_automation_market` exige
  `telegram.enabled` e `Skill(requires=...)` repete o gate — num `serve`
  headless a automação nunca roda e a tela mostra ligada. Conserto previsto:
  `requires` só `briefing.market_enabled` + saída em `bot_messages`.
- Sobreposição: o scheduler ainda pula o ciclo inteiro com `self._busy`
  global (gate por bot é fase 3).
- `gaia/controller/turns.py` ainda passa `tool_names = None` sempre; a
  política da sessão já resolve a allowlist, então **não** precisa mudar agora.
