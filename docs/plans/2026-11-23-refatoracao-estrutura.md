# Refatoracao de estrutura: pastas, god objects e funcoes longas

> **Plano ativo, nao aplicado.** Escrito em 2026-11-23 sobre o commit `084bd39`
> (`feat(pi): ...`). Cada etapa e um commit proprio; nada aqui muda
> comportamento. Verificado na epoca: `pytest` 1041 passando, `ruff check gaia
> tests` limpo, `mypy` sem erro em 132 arquivos.

Escopo: `gaia/` tem 31 330 linhas em 132 arquivos. O problema nao e o tamanho
total, e a **concentracao**: 5 classes carregam ~35% do codigo e 4 metodos
passam de 120 linhas. A organizacao de pastas esta correta no desenho (por
dominio, nao por camada), mas e inconsistente em quatro pontos — e um deles e
armadilha de leitura para quem chega agora.

## Regras que valem para todas as etapas

- Um commit por etapa, mensagem em portugues (`refactor:`), padrao do repo.
- Antes de cada commit: `pytest -q` verde, `ruff check gaia tests` limpo,
  `mypy` sem erro. Nada de commit com qualquer um vermelho.
- **Nenhum teste muda de comportamento.** A unica alteracao aceita em `tests/`
  e a linha de `import`. Se um teste precisar mudar para passar, a etapa
  quebrou contrato e para.
- Nada de "arrumar de passagem": o que aparecer fora do escopo vai para a secao
  final (achados), nao para o mesmo commit.
- Movimento de arquivo e quebra de arquivo sao commits **diferentes**. Misturar
  os dois produz um diff de 150 arquivos que esconde a mudanca que importa.
- Comentarios de codigo em ASCII sem acento ("sessao", "execucao"); docstring
  e mensagem de UI em portugues normal.

---

## 1. Diagnostico (medido, nao estimado)

### 1.1 God objects

| Classe | Arquivo | Linhas | Metodos |
| --- | --- | --- | --- |
| `_Handler` | `serve/server.py:118` | 1101 | 48 |
| `GaiaTextualApp` | `interface/textual_app.py:136` | 1145 | 89 |
| `AgentClient` | `agent/runtime.py:65` | 873 | 47 |
| `CommandsMixin` | `controller/commands.py:53` | 840 | 32 |
| `MemoryStore` | `memory/store.py:37` | ~800 | 60 |
| `Orchestrator` | `controller/orchestrator.py:49` | 701 | 43 |

`GaiaTextualApp` sai deste plano: a TUI sera aposentada em
[`2026-11-23-aposentar-tui.md`](2026-11-23-aposentar-tui.md). Se aquela etapa
nao acontecer, a quebra dele e a Etapa 3 deste documento.

### 1.2 Funcoes longas (>= 100 linhas, medido por AST)

| Linhas | Onde | Funcao |
| --- | --- | --- |
| 216 | `serve/server.py:433` | `do_POST` |
| 174 | `agent/runtime.py:599` | `_run_turn` |
| 147 | `controller/turns.py:130` | `_handle_task_impl` |
| 130 | `controller/commands.py:54` | `_handle_command` |
| 130 | `cli_parser.py:10` | `build_parser` |
| 125 | `serve/server.py:307` | `do_GET` |
| 123 | `controller/turns.py:384` | `_consume_events` |
| 118 | `controller/voice.py:22` | `_continuous_loop` |
| 115 | `cli_integrations.py:199` | `_career_command` |
| 114 | `controller/orchestrator.py:62` | `Orchestrator.__init__` |
| 107 | `cli_serve.py:37` | `_serve_command` |
| 106 | `serve/control_api.py:111` | `post` |

`build_parser` e `_serve_command` ficam fora: sao tabelas de argumentos e
sequencia de boot, quebrar so esconde a ordem.

### 1.3 Arquivos maiores

`config.py` 1408 · `serve/server.py` 1321 · `textual_app.py` 1280 ·
`agent/runtime.py` 937 · `controller/commands.py` 892 · `memory/store.py` 834 ·
`controller/orchestrator.py` 749 · `control/automations.py` 732 ·
`secondbrain/index.py` 657 · `interface/terminal.py` 615.

### 1.4 Colisao de nomes (armadilha de leitura)

Oito nomes existem nos dois lugares com papeis diferentes:

| Cola do Orchestrator | Regra de dominio |
| --- | --- |
| `controller/briefing.py` | `briefing/compose.py` |
| `controller/memory.py` | `memory/` |
| `controller/career.py` | `career/` |
| `controller/voice.py` | `audio/` |
| `controller/speech.py` | `tts/` |

Nada vai ser movido por isso. A correcao e **declarar a regra** no `AGENTS.md`
(Etapa 7) e manter: `gaia/controller/<nome>.py` = mixin que cola o Orchestrator
a um servico (sem regra de negocio); `<dominio>/` = regra pura, testavel sem
orquestrador. Quem le `controller/briefing.py` precisa saber, na primeira linha,
que a composicao do texto esta em `briefing/compose.py`.

### 1.5 Pacotes com 2-3 arquivos soltos na raiz

`browser/` (121 l.), `tasks/` (147), `stt/` (169), `agenda/` (282), `client/`
(286), `finance/` (182), `estudos/` (273), `whatsapp/` (275), `pi/` (299),
`briefing/` (433), `audio/` (598). Onze pacotes + onze modulos soltos na raiz de
`gaia/`.

---

## 2. Etapa 1 — `gaia/config.py` vira pacote — **APLICADA** (2026-11-23)

1408 linhas em 1 arquivo, sendo 260 de template TOML escrito a mao. Virou
pacote com reexport, entao **os 31 arquivos que importam `gaia.config` nao
mudaram uma linha**.

```
gaia/config/             linhas
  __init__.py             119   reexport (Config, *Config, ECHO_*, helpers)
  models.py               466   constantes + 17 sub-configs pydantic
  core.py                 208   Config: paths, ensure_dirs, public_dict, load()
  env.py                  330   _ENV_MAP/_apply_env, parse de bool, .env, aliases
  migrate.py              104   [opencode] -> [agent], slugs de modelo antigos
  template.py             284   _DEFAULT_CONFIG_TEMPLATE + write_default_config
  edit.py                 105   edicao linha a linha do TOML (save_agent_model)
```

O corte por faixa revelou uma dependencia que o desenho original nao previa:
`models.py` teria 633 linhas porque carregava `Config` junto. `Config` foi para
`core.py`, e os arquivos ficaram entre 104 e 466 linhas.

Ordem de import (sem ciclo): `env` (constantes + mapa) <- `migrate` <-
`models` (sub-configs) <- `core` (`Config`) <- `template`/`edit` <-
`__init__`. Duas importacoes tardias, ambas com comentario no codigo:
`env._field_is_bool` -> `core.Config` e `env._apply_env` -> `migrate`.

### O que foi feito

- [x] Pacote criado movendo por faixa de AST (nenhum corpo de funcao
      reescrito); `git show HEAD:gaia/config.py` foi a fonte.
- [x] `_ENV_MAP`, `_LEGACY_ENV_MAP`, `_PROVIDER_API_KEYS`, `_LEGACY_MODEL_ALIASES`
      e os helpers continuam importaveis de `gaia.config`
      (`control/settings.py:28` depende de `_ENV_MAP`).
- [x] Import tardio de `gaia.agent.providers` preservado em `migrate.py`
      (o comentario "ciclo config/agent" mudou de arquivo, nao de razao).
- [x] `.gitignore`: `config/` virou `/config/`. A regra antiga ignorava
      **qualquer** diretorio chamado `config`, inclusive `gaia/config/` — o
      pacote novo nao entrava no git. Era uma bomba armada para o proximo
      pacote com esse nome.
- [x] `tests/test_config_template.py` (novo, 4 testes): template x modelos
      pelo AST/pydantic — cobre o achado A1.
- [x] `AGENTS.md`, `README.md` e `docs/README.md` apontando para os arquivos
      novos.

### Verificacao aplicada

```bash
pytest                                  # 1045 passed (1041 + 4 novos)
ruff check gaia tests                   # All checks passed
mypy                                    # Success: 142 arquivos
GAIA_CONFIG=/tmp/x/config.toml gaia config --init
diff <template de antes> <template gerado>   # identico, byte a byte
```

Tambem conferido por script: `_DEFAULT_CONFIG_TEMPLATE`, `_ENV_MAP`,
`_LEGACY_ENV_MAP`, `_PROVIDER_API_KEYS`, `_LEGACY_MODEL_ALIASES`, `ECHO_*`, os
prefixos e os campos de `Config` sao **identicos** aos do modulo antigo. O que
nao foi reexportado e so nome de biblioteca (`Any`, `Path`, `BaseModel`,
`field_validator`, `os`, `re`, `shutil`, `tomllib`) — nenhum chamador usava.

**Ganho:** o maior arquivo do repo virou sete arquivos nomeados por assunto, e
o template passou a ter um teste que cobra divergencia.

---

## 3. Etapa 2 — `serve/server.py` vira `serve/http/` (risco: medio)

`_Handler` tem 1101 linhas e 48 metodos, sendo 341 linhas de `if rota == ...`
em `do_GET` (125) + `do_POST` (216).

```
gaia/serve/http/
  handler.py   # _Handler: auth (bearer, cookie gaia_chat, _autorizado),
               # _corpo, _corpo_bytes, _json, _erro, _host, log_message
  routes.py    # as TABELAS: _GET = {"/health": "_r_health", ...},
               # _POST = {...}. O dispatch deixa de ser `if` e passa a ser dict
  chat.py      # turn, permission, abort, engine, modes, languages, history,
               # sessions, models, commands, automations, providers, search
  voice.py     # /voice, /voice/upload, /voice/listen, /voice/speak (rotas)
  sse.py       # _eventos, _escrever
  assets.py    # /, /favicon, /static/*, /control, /control/static/*
  server.py    # ServeServer (thread, bind, base_url) — sem mudanca
```

Checklist:

- [ ] Extrair por assunto em mixins (`_Handler` herda o que precisa) **antes**
      de trocar o despacho por tabela. Dois commits se ficar grande demais.
- [ ] A tabela de rotas tem que falhar alto no boot se uma rota apontar para
      metodo inexistente (`test_serve.py`: teste novo de contrato, sem mudar
      teste existente).
- [ ] `gaia/serve/control_api.py` continua com o proprio `get/post`: e o painel,
      nao o chat.
- [ ] Rotas e ordem de autenticacao sao invariantes: `/health`, `/instances`,
      `/` e `/favicon.ico` publicos; o resto atras de `_autorizado()`; o
      `POST /voice/upload` responde **antes** do `_corpo()` comum. Se algum
      teste de `test_serve.py`/`test_serve_voice.py` falhar em 401/403, a
      tabela mudou a ordem e esta errado.
- [ ] Atualizar a lista de rotas no docstring do modulo e no `AGENTS.md`.

**Verificacao:** `pytest -q tests/test_serve.py tests/test_serve_web.py
tests/test_serve_voice.py tests/test_serve_instances.py tests/test_control_api.py
tests/test_serve_app.py` verde, depois a suite inteira. `wc -l gaia/serve/http/*.py`
< 400 cada.

---

## 4. Etapa 3 — `agent/runtime.py`: `_run_turn` e o resto (risco: medio/alto)

`AgentClient` tem 47 metodos (873 linhas) e `_run_turn` tem 174 linhas. E a
funcao mais critica do projeto.

```
gaia/agent/
  runtime.py     # AgentClient: ciclo de vida, provider, sessao, fallback,
                 # prompt_async. Fica com o que e "conversa com o provedor"
  transcript.py  # _trim_text, _trim_value, _trim_history_payloads,
                 # _history_transcript, _summary_model
  usage.py       # usage(), _record_usage, contagem de tokens
  stream.py      # _emit, _drain_events, _consume_task, _tool_calls
```

E `_run_turn` (599) quebra em quatro, chamadas na ordem atual:

1. `_prepare_turn` — monta mensagens, anexa ferramentas, valida orcamento.
2. `_stream_provider` — consome o stream, emite `TextDelta`/`ReasoningDelta`,
   acumula `usage`.
3. `_apply_tool_calls` — executa ferramenta em `asyncio.to_thread`, checa
   permissao, aplica resultado (`_tool_calls` sai para `stream.py`).
4. `_finalize_turn` — grava a sessao, registra uso, emite o evento final.

Checklist:

- [ ] Nenhuma mudanca de ordem, de evento ou de texto: o que muda e so onde
      cada bloco mora.
- [ ] `_run_turn` fica como orquestracao de 20-30 linhas, chamando as quatro.
- [ ] Rodar `tests/test_agent.py`, `tests/test_agent_fallback.py`,
      `tests/test_token_budget.py`, `tests/test_permissions.py` **antes e
      depois**, comparando a saida (mesmo numero de passed).

**Verificacao:** suite inteira; `wc -l gaia/agent/*.py` < 400 cada.

---

## 5. Etapa 4 — `memory/store.py` vira `memory/store/` (risco: medio)

60 metodos, 834 linhas, um DDL de 94 linhas (`_init_schema`) + `_migrate`.

```
gaia/memory/store/
  __init__.py       # MemoryStore compondo os mixins (API publica identica)
  base.py           # conexao, _now, close, context manager, stats
  schema.py         # _init_schema, _migrate, _rename_modes_v3,
                    # _rename_session_column_v4, schema_version
  conversations.py  # current/create/get/get_or_create/list/set_current/
                    # set_session/touch/rehome/richest_mode/project_has_turns
  turns.py          # add_turn, turns, turns_between
  records.py        # record_note, records_between, recent_records
  reminders.py      # add/due/pending/mark_spoken/cancel/stats
  meta.py           # set/claim/delete/get_meta
  runs.py           # begin_run, finish_run, list_runs, last_run,
                    # runs_summary, prune_runs
  automations.py    # list/get/upsert/update_automation
```

Checklist:

- [ ] `MemoryStore` continua **uma** classe instanciavel: `MemoryStore(path)`,
      `with MemoryStore(...) as store`. Nada de mudar `__init__`.
- [ ] Schema e migracao sao o unico lugar que pode escrever DDL. Nada de
      `CREATE TABLE` em modulo de dominio.
- [ ] `tests/test_memory.py` e `tests/test_control_runs.py` verdes sem edicao.
- [ ] Banco criado do zero e banco existente (schema v6/v7) abrem: rodar
      `gaia doctor` num `GAIA_DATA_DIR` temporario e, depois, contra uma copia
      do banco real.

---

## 6. Etapa 5 — `controller/commands.py`: despacho por catalogo (risco: medio)

`_handle_command` tem 130 linhas de `elif`. O catalogo ja e fonte de verdade na
ajuda (`command_catalog.py`, comparado por AST no `test_command_catalog.py`).

```
gaia/controller/commands/
  dispatch.py  # CommandsMixin com _handle_command curto: normaliza o nome,
               # consulta a tabela, delega. Desconhecido -> mesma mensagem de hoje
  model.py     # /modelo, /rapido, /raciocinio, /contexto, _save_model_choice
  modes.py     # /modo (delega em controller/modes.py)
  voice.py     # /voz, /mudo
  languages.py # /idiomas, /chines, translate_echo
  toggles.py   # /auto, /confirmar, /criar-confirmacao, /status
  history.py   # /historico, /sessoes
  tasks.py     # /tarefa e afins (_find_task)
```

Checklist:

- [ ] A tabela de despacho tem que sair do **mesmo** registry que o catalogo
      (`command_catalog.All`): o teste por AST ja garante que catalogo e
      despacho coincidem; ao virar dict, o teste continua valido.
- [ ] `OrchestratorContract` acompanha: mixin novo = declaracao nova no
      contrato, senao o mypy reclama (esse e o sinal de que esta certo).
- [ ] `/status` e `_show_history` nao mudam de texto: ha teste comparando.

---

## 7. Etapa 6 — `control/automations.py` e `controller/turns.py`

`control/automations.py` (732 l., 40 funcoes de modulo):

```
gaia/control/automations/
  __init__.py   # reexporta tudo (os testes importam do modulo, nao do pacote)
  model.py      # Automation, claim_key, done_key, window_key, matches_day,
                # in_window, valid_time, normalize_time, validate_payload
  store.py      # seed, load, update, find, find_loaded, _row_payload, _parse_days
  schedule.py   # next_run, due_now, due, claim, release, retry_allowed,
                # backoff_seconds
  status.py     # status, render, describe, describe_automations, next_run
                # label, needs_attention, summarize_cfg, _ran_today, _last_run
```

`controller/turns.py` (537 l.): `_handle_task_impl` (147) -> `turns/task.py`;
`_consume_events` (123) -> `turns/events.py`.

Risco baixo nos dois: o agendador e exercitado por `test_scheduler.py`,
`test_control_automations.py` e `test_runs_instrumentation.py`, que nao mudam.

---

## 8. Etapa 7 — pastas: CLI por dominio e agrupamento (risco: baixo / churn alto)

### 8.1 CLI por dominio

`cli_integrations.py` (313 l.) e resto de quando Spotify e WhatsApp Cloud API
viviam aqui. Cada subcomando vai para o pacote do seu dominio:

- `_telegram_command`, `_telegram_poll`, `_telegram_serve`, `_telegram_summary`
  -> `gaia/telegram/cli.py`
- `_whatsapp_command`, `_message_id`, `_serve_whatsapp` -> `gaia/whatsapp/cli.py`
- `_browser_command` -> `gaia/browser/cli.py`
- `_career_command` (115 l.) -> `gaia/career/cli.py`
- `cli_integrations.py` fica com 4 imports e o `__all__` (ou desaparece).

`_build_app` (em `cli_runtime.py`) e usado por todos: continua onde esta.

### 8.2 Agrupamento

```
gaia/
  integrations/   agenda/ estudos/ career/ finance/ tasks/ browser/ whatsapp/
                  telegram/ pi/ client/
  media/          audio/ tts/ stt/
  storage/        memory/ secondbrain/
  agent/ controller/ control/ serve/ interface/ briefing/ audiobook/ focus/
  config/ cli*.py
```

**Custo real:** churn mecanico de imports em ~150 arquivos, um commit so,
`ruff check --fix` + `sed` de caminho. **Ganho:** a raiz de `gaia/` cai de 22
pacotes para ~10 e fica obvio o que e integracao externa, o que e midia e o que
e persistencia. E o item mais adiavel do plano — se ficar caro, faca so
`integrations/` (10 pacotes de uma vez).

Checklist:

- [ ] `git mv` (nao copiar e apagar) para preservar historico.
- [ ] Nenhum pacote movido pode ter import de `gaia.controller` nem de
      `gaia.serve` (a separacao e o ponto). Verificar com grep.
- [ ] `gaia/focus.py` -> `gaia/focus/` (`store.py`, `window.py`) e o unico
      modulo solto na raiz que nao e CLI.

---

## 9. Etapa 8 — guarda no CI (risco: nenhum)

Sem isso, os god objects voltam em tres meses. `tests/test_estrutura.py`, no
espirito do `test_command_catalog.py` (AST, sem importar nada):

- falha se algum arquivo de `gaia/**/*.py` passar de **500** linhas;
- falha se alguma classe passar de **400** linhas;
- falha se alguma funcao passar de **80** linhas, **exceto** uma lista de
  excecoes declarada com motivo (`build_parser`, `_serve_command`, ...);
- a lista de excecoes so pode encolher: um teste extra compara com um
  `set` congelado no proprio arquivo (adicionar excecao = commit visivel).

Sugestao de mensagem de falha: `"serve/http/handler.py: _Handler tem 1520
linhas (teto 400) — adicione ao plano ou quebre por assunto"`.

---

## 10. Ordem, risco e ganho

| Etapa | O que | Risco | Diff | Ganho |
| --- | --- | --- | --- | --- |
| ~~1~~ | ~~`config/` como pacote~~ | baixo | **feito** | 7 arquivos, nenhum > 466 |
| 2 | `serve/http/` + tabela de rotas | medio | ~1300 movidas | handler de 1101 -> ~5 x 200 |
| 3 | `agent/` transcript/usage/stream + 4 `_run_turn` | medio/alto | ~900 movidas | funcao critica legivel |
| 4 | `memory/store/` | medio | ~830 movidas | schema isolado |
| 5 | `controller/commands/` por catalogo | medio | ~890 movidas | mata 130 l. de `elif` |
| 6 | `control/automations/` + `turns/` | baixo | ~1270 movidas | — |
| 7 | CLI por dominio + agrupamento | baixo/churn | ~150 imports | raiz enxuta |
| 8 | guarda no CI | nenhum | 1 teste | nao regride |

Sequencia sugerida: ~~1~~ -> **2** -> 3 -> 4 -> 5 -> 6 -> 8 -> 7. A etapa 1 ja
foi aplicada (secao 2). A guarda entra antes do agrupamento porque o
agrupamento mexe em caminho de muita coisa — com o teto de linhas no CI, o
`sed` de caminho quebra o teste em vez de passar silencioso.

Se a TUI for aposentada antes (plano separado), `interface/textual_*.py`
(2108 linhas) sai da conta e a Etapa 3 deixa de disputar atencao com ela.

---

## 11. O que NAO esta neste plano

- **Mixins do `Orchestrator` / `contract.py`** (404 linhas de declaracao para o
  mypy). E feio e funcional; trocar por composicao e projeto a parte, com
  risco alto e ganho estetico.
- **Gerar `_DEFAULT_CONFIG_TEMPLATE` do `model_json_schema()`.** Os
  comentarios escritos a mao sao o valor do template. A alternativa barata esta
  no achado A1.
- **Dividir `tests/`.** `test_orchestrator.py` (1708 l.) e
  `test_token_budget.py` (827) sao grandes, mas dividir troca de lugar, nao
  reduz risco.
- **`secondbrain/index.py`** (657 l.) e `interface/terminal.py` (615): grandes,
  mas coesos. So entram se a guarda da Etapa 8 reprovar.

## 12. Achados fora de escopo

- **A1 — template x models.** ~~Nao existe teste comparando
  `_DEFAULT_CONFIG_TEMPLATE` com os campos dos models pydantic.~~ **Resolvido
  na Etapa 1**: `tests/test_config_template.py` falha se o template tiver
  chave que o modelo nao conhece, secao inexistente ou campo obrigatorio
  ausente. Campo com default pode faltar de proposito (segredo ou ajuste
  fino) — a lista dos que faltam hoje e: `agent.api_key`,
  `agent.stall_timeout`, `agent.fallback_models`, `secondbrain.embed_*`,
  `memory.*`, `briefing.check_interval_seconds`, `career.*`, tokens do
  whatsapp/telegram/serve. Nenhum deles e obrigatorio, entao o teste passa.
- **A2 — event flood no SSE.** `RemoteUI.status()` e chamado a cada `_tick_status`
  da TUI (1 s) e emite evento `status` mesmo com texto identico. Sem a TUI,
  isso vira ruido puro. Sugestao: dedupe no `emit` (mesmo tipo + mesmo payload
  consecutivo = nao publica).
- **A3 — 16 eventos que o web nao desenha.** Medido em `remote.py` x
  `index.html`: `check`, `dashboard`, `diff` (vai por `tool`), `erase_input`,
  `error`/`hint`/`info`/`success`/`warning` (vao por `notice`), `note_preview`,
  `note`, `partial`, `transcript`, `prompt`, `reminders`, `watchlist`. Parte e
  intencional (o web usa `notice`), parte e lacuna real de paridade
  (`reminders`, `watchlist`, `note`). Resolver junto com a aposentadoria da TUI,
  que e quando essas telas passam a ter um lugar so.
- **A4 — `cli_parser.build_parser`** tem 130 linhas de `add_argument`: e tabela,
  nao logica. Entra na lista de excecoes da guarda, com motivo.
- **A5 — `Orchestrator.__init__`** recebe 12 servicos posicionais. Um
  `dataclass Services` + `build_services(cfg)` reduz a assinatura sem tocar em
  mixin. Proposta registrada, nao incluida: mexe em todos os testes de
  orquestrador.
