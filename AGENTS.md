# AGENTS.md — manual do agente que trabalha neste repo

Este arquivo é a fonte de verdade para agentes de código (humanos ou IA) que
mexem na Gaia. Se o código e a documentação divergirem, **o código vence** — e a
doc deve ser corrigida na mesma mudança. `README.md` é o manual do usuário;
`docs/README.md` classifica o resto da documentação.

## O que é

Gaia: assistente pessoal local em Python 3.12. Fala e ouve em pt-BR (STT
`faster-whisper` local, TTS `edge-tts`/`say`), roda um loop de agente próprio com
ferramentas, guarda memória em SQLite + Second Brain em Markdown, e é proativa
(briefing, lembretes, vagas, sonho noturno). Roda em macOS e Linux, inclusive
headless num mini PC controlado por Telegram ou por `gaia serve`.

## Comandos

```bash
# setup (o venv é obrigatório para os comandos abaixo)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# verificação — o CI roda exatamente estes três
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy

# uso
.venv/bin/gaia                  # TUI (padrão; config interface.ui)
.venv/bin/gaia -old             # terminal Rich
.venv/bin/gaia --watch          # reinicia ao mudar .py
.venv/bin/gaia ask "..."        # turno único, sem interface
.venv/bin/gaia serve            # motor + HTTP/SSE + (opcional) Telegram
.venv/bin/gaia --remote URL     # cliente fino do serve
.venv/bin/gaia config --init    # cria ~/.config/gaia/config.toml
.venv/bin/gaia doctor           # ambiente, provedor, voz, Second Brain
```

O CI (`.github/workflows/ci.yml`) instala as deps e roda `pytest`, `ruff check
gaia tests` e `mypy` em Python 3.12. Não faça commit com qualquer um vermelho.

## Mapa do código

| Caminho | Responsabilidade |
|---|---|
| `gaia/cli.py`, `cli_parser.py`, `cli_commands.py`, `cli_integrations.py`, `cli_runtime.py`, `cli_serve.py` | entrada, subcomandos, bootstrap |
| `gaia/config.py` | modelo de config (pydantic), env `GAIA_*`, migração legada, template TOML |
| `gaia/agent/runtime.py` | loop do agente: provedor, streaming, ferramentas, permissões |
| `gaia/agent/session.py` | `AgentSession` e `SessionStore` (JSON em `sessions/`) |
| `gaia/agent/providers/` | `factory.py` (presets), `openai_compat.py`, `anthropic.py` |
| `gaia/agent/tools/` | `build_tools()` registra `read/list/glob/grep/write/edit/bash` + agenda/planilha/audiobook/estudos + `pi` (quando `[pi] enabled`) |
| `gaia/controller/orchestrator.py` | cola entre UI e agente; conversa, sessão, workers |
| `gaia/controller/turns.py` | execução de um turno, eventos, permissão |
| `gaia/controller/commands.py` | comandos de barra (`/status`, `/modelo`, `/sonhar`…) |
| `gaia/controller/command_catalog.py` | catálogo dos comandos: ajuda do terminal, `GET /commands` e paleta do web |
| `gaia/controller/modes.py` | personas/modos (prompt, não infraestrutura) |
| `gaia/controller/prompt.py` | prompt em camadas (prefixo estável + bloco dinâmico) |
| `gaia/controller/initiative.py` | lembretes e check-in proativo; `briefing.py` | briefings |
| `gaia/controller/memory.py` | consolidação diária e sonho |
| `gaia/memory/store.py` | SQLite (`gaia.db`, schema v6: conversations/turns/records/meta/reminders/**runs**) |
| `gaia/memory/consolidation.py`, `dream.py` | memória do dia e sonho |
| `gaia/secondbrain/` | índice FTS5, frontmatter, notas, embeddings opcionais |
| `gaia/briefing/compose.py` | texto dos briefings (manhã, meio-dia, noite) |
| `gaia/interface/terminal.py` | UI Rich; `textual_app.py` | TUI |
| `gaia/interface/remote.py` | `RemoteUI`: serializa eventos para o serve (fan-out + histórico) |
| `gaia/serve/server.py` | HTTP + SSE + `/control`; `app.py` | ponte `OrchestratorTarget`; `voice.py` | modo voz do web (microfone/STT do motor + audio do TTS); `control_api.py` | rotas do painel; `session.py` | nonces de cookie (chat e painel); `instances.py` | `instances.json` e sonda `/health`; `web.py`/`static/` | web (`shared.js` e comum as duas telas) |
| `gaia/control/` | Control Center: `runs` (histórico), `skills`, `automations` (agenda + seed/load/retry), `health`, `secrets`, `settings`, `providers`, `center` |
| `gaia/controller/scheduler.py` | agendador único: lê `automations`, calcula `due()`, chama o handler da skill, faz retry e notifica falha |
| `gaia/client/remote_client.py` | cliente `gaia --remote` |
| `gaia/telegram/`, `whatsapp/`, `browser/` | canais e integrações |
| `gaia/audiobook/` | PDF/texto → MP3: `extract.py` (PDF sem dependência), `normalize.py` (limpeza + capítulos), `jobs.py` (narração, ID3/CHAP), `service.py` (lote inbox→outbox) |
| `gaia/agenda/` | cliente da Agenda (Supabase), usado por ferramentas `agenda_*` |
| `gaia/estudos/` | progresso dos simulados do Abin Suite (Supabase proprio), tools `estudos_*` e cobranca proativa |
| `gaia/career/`, `finance/`, `tasks/`, `focus.py` | rotinas de vagas, bolsa, tarefas, foco |
| `gaia/pi/` | ponte com o pi: `config.py` (`[pi]` do TOML), `runner.py` (subprocesso `pi --print --mode json` → JSONL → `PiResult`) |
| `gaia/backlog/` | card do backlog: `extract.py` (HTML da Agenda → itens), `service.py` (SQLite, schema v8), `job.py` (clone/branch/pi/commit/push/PR), `worker.py` (fila de um item), `facade.py`; o mixin e `gaia/controller/backlog.py` |

## Fluxo de um turno

1. A UI (terminal/TUI/Telegram/serve) chama `Orchestrator`.
2. `turns.py` monta o prompt via `controller/prompt.py`, registra o turno no
   `MemoryStore` e chama `AgentClient.prompt_async(session_id, ...)`.
3. `AgentClient` fala direto com a API do provedor (sem servidor intermediário),
   emite eventos (`TextDelta`, `ReasoningDelta`, `ToolStarted`,
   `PermissionAsked`…) e executa ferramentas em thread (`asyncio.to_thread`).
4. A UI consome eventos. No `serve`, `RemoteUI` serializa em `queue.Queue` e o
   transporte HTTP/SSE entrega aos clientes.

## Invariantes — não quebre

- **Um único processo escritor.** `gaia serve` é dono do `gaia.db`, do Second
  Brain, dos workers proativos e do poller do Telegram. Nunca rode dois
  processos com o mesmo banco nem dois pollers do Telegram (409 `getUpdates` e
  briefing duplicado).
- **Canais têm escopo.** O modo ativo é salvo por canal (`mode` no `cli`,
  `mode@telegram`/`mode@serve` fora dele). `channel` é `cli`, `telegram` ou
  `serve`.
- **Permissões são a fronteira de segurança.** `write`, `edit` e `bash` exigem
  aprovação. `agent.auto_approve` é opt-in. No Telegram, `auto_approve` só vale
  com `allowed_chat_id` fixado, e `bash/write/edit` continuam negados a menos
  que `telegram.shell_access = true`. Em `serve`, a decisão remota passa pelo
  `RemoteUI` (`POST /permission/{id}`).
- **Segredos só no `.env`/ambiente**, nunca no TOML. A precedência é
  ambiente/`.env` > `config.toml`. `Config.public_dict()` redige os segredos
  conhecidos (`agent.api_key`, WhatsApp, Telegram, embed, `serve.token` e
  `serve.control_token`).
- **O Control Center tem token próprio.** `/control/api/*` exige
  `GAIA_CONTROL_TOKEN`, nunca `GAIA_SERVE_TOKEN` (o token do chat não
  administra credenciais). Sem ele, a API responde 503 e não existe por
  omissão: o painel não herda poder acidentalmente. Toda resposta mascara
  segredo (`sk-…1234`) e nenhum valor completo aparece em JSON ou log.
- **Duas sessões de cookie, dois segredos.** O chat autentica com
  `GAIA_SERVE_TOKEN` (`POST /session` -> cookie `gaia_chat`, `Path=/`); o painel
  com `GAIA_CONTROL_TOKEN` (`POST /control/api/session` -> `gaia_control`,
  `Path=/control`). Um nonce nunca vale no conjunto do outro, e o `Bearer`
  continua aceito nas duas (cliente fino/`curl`). Guardar token no
  `localStorage` é opt-in ("lembrar token neste navegador"); o padrão é não
  guardar.
- **`instances.json` é manual e nunca exposto.** Fica em `GAIA_DATA_DIR`
  (segredos, `chmod 600`); `GET /instances` devolve só `id`/`name`/`url`/
  `current`/`has_*_token` + status medido pelo servidor. O seletor **navega**
  para a outra URL: cookie é por origem, então não há CORS com credenciais.
- **Sessão do painel é cookie `HttpOnly` derivado do token.**
  `POST /control/api/session` troca um `Bearer` correto por um nonce opaco
  (`gaia/serve/session.py`, em memória, TTL 12h, `Path=/control`,
  `SameSite=Strict`); `GET /control/api/session` só diz `authenticated`.
  `DELETE` revoga e apaga o cookie. O `Bearer` continua aceito — é o que o
  `curl` e o cliente fino usam. O nonce nunca é o token e não é derivado dele:
  reiniciar o `serve` derruba as sessões (o painel refaz o login sozinho se
  tiver o token no `localStorage`). Por isso o modal não volta ao trocar de
  origem (`gaia:8787` vs IP do tailnet) nem por limpeza periódica de storage.
- **Dados ficam fora do repo por padrão:** `~/.local/share/gaia`
  (`GAIA_DATA_DIR`). O repo só usa `./data` quando o launcher seta
  `GAIA_DATA_DIR="$REPO/data"` (`Abrir Gaia.command`).
- **Legado:** `ZAYA_*` é espelhado para `GAIA_*` e `zaya.db` é renomeado para
  `gaia.db` na primeira execução. Não remova essa migração sem migrar os dados.
- **Second Brain:** escrever fora da raiz é proibido; editar/excluir pede
  confirmação, gera backup em `.gaia/backups` e move para `.gaia/trash`.
- **Prompt em duas camadas:** prefixo estável (persona/preferências, cacheável) +
  bloco dinâmico (data/hora/modo). Respeite isso ao adicionar contexto.
- **Estilo do repo:** UI, mensagens e testes em português; comentários em ASCII
  sem acento ("sessao", "execucao"). Evite dependência nova.
- **Audiobook é estado em disco, não em banco.** `inbox` = pendente;
  `processados`/`erros` = terminado (nunca reprocessa). Trechos já narrados ficam
  em `tmp/<hash>/NNNN.mp3` para retomar após queda; só são apagados no sucesso.
  A ferramenta `audiobook` roda em `asyncio.create_task` e devolve na hora — quem
  entrega o MP3 é o canal (`AgentClient.set_audiobook_watch`), não a ferramenta.

## Receitas

- **Nova ferramenta:** crie o spec/handler em `gaia/agent/tools/`, registre em
  `build_tools()` e marque `requires_approval=True` se alterar algo. Adicione
  teste em `tests/test_agent.py` e, se for específica, no arquivo do domínio.
- **Ferramenta `pi` (delegar codigo ao harness):** desligada por padrao em
  `[pi] enabled = false`. O pi roda com `cwd = pi.workspace` (padrao
  `~/pr-workspace`), **nunca** em `~/gaia`: o `gaia-deploy.timer` faz
  `reset --hard` a cada 5 min e apagaria o trabalho. `pi.protect` bloqueia
  caminhos mesmo dentro da raiz. O binario sai de `pi.binary`, do `PATH` ou de
  `~/.pi/agent/bin/pi` (o runner acrescenta esse diretorio ao `PATH`, porque a
  unidade de usuario nao tem login shell). Credencial do modelo e do ambiente:
  `DEEPSEEK_API_KEY`. O GitHub **nao** vai no `.env`: `gh auth login` grava
  `~/.config/gh/hosts.yml` (600) e o subprocesso herda por `HOME`; o workspace
  usa o fork `piratistemica/*` como `origin` (`upstream` = `talitadeoa/*`) e o
  PR sai com `--repo`/`--head piratistemica:<branch>`. Testes em
  `tests/test_pi.py` (JSONL fake, nada de rede, nenhum pi de verdade).
- **Nova skill no Control Center:** adicione um `Skill(...)` em
  `gaia/control/skills.py` (id, kind, `requires`, `provides`, `schedule_hint`,
  `module`). O painel nao muda: ele le o registry. Se a skill tiver horario,
  descreva a janela em `gaia/control/automations.py::describe` (vira semente da
  tabela `automations`) e registre o handler em
  `gaia/controller/scheduler.py::_AUTOMATION_METHODS`. Se ela executar de
  verdade, envolva o corpo com `record_run(...)` **depois** do gate de horario
  — iniciar nao e sucesso. Testes: `tests/test_control_skills.py`,
  `tests/test_control_automations.py`, `tests/test_runs_instrumentation.py`,
  `tests/test_scheduler.py`.
- **Nova rota do chat:** adicione no `do_GET`/`do_POST` de
  `gaia/serve/server.py` (atrás de `_autorizado()`), implemente o metodo
  opcional no `OrchestratorTarget` (`gaia/serve/app.py`) e teste em
  `tests/test_serve_instances.py` (rotas de estado), `tests/test_serve_web.py`
  (tela/comandos) ou `tests/test_serve.py` (transporte).
- **Tela de automacoes (web):** e uma *view* do `index.html` (`#view-automacoes`),
  nao um dialog: o hash `#automacoes` e a fonte de verdade (`pintarTelaAtual`),
  o composer some e o botao do header so navega. Os checkboxes escrevem em
  rotas do CHAT (`POST /automations/set`, `/automations/run`, `/proactive`) e
  passam pelo `ControlCenter`; o motor exponhe incognitas como `paused` (decisao
  da tabela) e `config_enabled` (skill ligada no TOML) para a tela nao mentir.
  "Rodar agora" delega em `Orchestrator.run_automation_now` (trigger=manual,
  limpa a reserva do dia; KeyError=404, motor ocupado=409) — a espera roda no
  loop do motor via `run_awaitable`. O
  `status`/`last_run` ja vem de `describe_automations` — nao reimplemente a
  regra na tela. Testes: `tests/test_serve_web.py` (contrato da view) e
  `tests/test_control_api.py` (rotas, 401/404/422/503, TOML e processo).
- **Backlog (web):** duas peles para o mesmo dado (`GET /backlog`). O card do
  aside (`#backlog-card`, `backlogCard`) nasce RECOLHIDO (`7 itens · 3
  pendentes` + botoes "tela"/"atualizar") e abre ao clique no cabecalho; a tela
  propria (`#view-backlog`, hash `#backlog`, como a de automacoes) usa o mesmo
  corpo (`backlogCorpo(dados, {local})`) sem cabecalho recolhivel, com resumo e
  botoes no header da view. `local` e `card` ou `agenda`: cada pele tem o seu
  aberto/fechado de grupo (`estado.backlogGrupos`, chave `local:projeto`), o seu
  rascunho (`estado.backlogRascunho`) e o seu id de campo
  (`backlog-novo-<local>`), senao o redesenho do SSE fecha o que a pessoa abriu
  — `pintarBacklog` guarda o foco antes e devolve depois. O aberto/fechado do
  card (`estado.backlogAbertura`, so `card`) tambem fica no estado. A tela de
  automacoes NAO mostra mais o backlog: `naTela` navega entre `#automacoes` e
  `#backlog` (`telaAtual`/`irParaTela`); o botao do header so aparece quando
  `GET /backlog` responde com `enabled`. Escrita: `POST /backlog/sync` (Agenda),
  `/backlog/add` (chat/voz) e `/backlog/run` (despacha ao pi; 409 com o motor
  ocupado). Status vem do servidor (`status`/`status_label`) — nao reimplemente
  a regra na tela; a limpeza de 7 dias e do SQL (`prune_days`). `el()` em
  `shared.js` achata listas de filhos com `flat(Infinity)`: devolver uma lista
  de `<details>` sem isso imprime `[object HTMLDetailsElement]`.
  Testes: `tests/test_serve_web.py` (contrato das duas peles) e
  `tests/test_backlog.py` (rotas e o worker).
- **Voz no web:** duas portas para o mesmo Whisper (`gaia/serve/voice.py`).
  `[serve] voice_source = "browser"` (padrao) faz o *navegador* gravar WAV
  16-bit mono e mandar em `POST /voice/upload` — o mini headless so transcreve;
  `"server"` mantem o microfone do motor (`POST /voice/listen`,
  `record_until_silence`). O texto vai no `/turn` e o audio volta de `POST
  /voice/speak`. Nao existe TTS local no caminho: no mini headless ele nao tem
  para onde tocar. O upload nao toca o disco (decodifica em memoria, so WAV/PCM:
  nada de ffmpeg/librosa). Nao existe `MediaRecorder`: o `index.html` usa
  `ScriptProcessorNode` para ter PCM cru. O botao da tela chama `vozEnter()` no
  Enter e `M` silencia; quem cala depois de 15 s e o `setTimeout` do
  `index.html`, nao o servidor. `GET /voice` publica `state`
  (`idle`/`ouvindo`/`transcrevendo`) para a tela sair de "ouvindo" sem esperar o
  POST. Testes em `tests/test_serve_voice.py` (rotas, janela, silencio, WAV
  invalido e lock de uma escuta).
- **Nova rota do painel:** adicione no `get`/`post` de
  `gaia/serve/control_api.py` e o metodo correspondente em
  `gaia/control/center.py`. Toda rota `/control/api/*` exige
  `GAIA_CONTROL_TOKEN` (separado de `GAIA_SERVE_TOKEN`) e teste em
  `tests/test_control_api.py`.
- **Novo comando de barra:** adicione o `elif` em
  `controller/commands.py:_handle_command` **e** o `Command(...)` em
  `controller/command_catalog.py` (de lá saem a ajuda do terminal e a paleta do
  web; `tests/test_command_catalog.py` compara catálogo × despacho por AST).
  Teste em `tests/test_cli.py`/`test_orchestrator.py`. Se o Telegram precisar do
  comando, veja `gaia/telegram/service.py`.
- **Texto novo na tela do web:** o `RemoteUI` emite o evento; o `index.html`
  precisa de um `case` em `tratarEvento`. Para testar a tela sem servidor real,
  `/tmp/fake_serve.py` (Target falso + `ServeServer`) serve; os contratos
  estáveis (id duplicado, seletor inexistente, catálogo) estão em
  `tests/test_serve_web.py`.
- **Audiobook:** lógica em `gaia/audiobook/` (testes em `tests/test_audiobook.py`,
  com o sintetizador stubado — nada de rede). Entrada nova (outro formato) é em
  `extract.py`; regra de limpeza, em `normalize.py`; o cmd `/audiobook` está em
  `controller/commands.py` e no `telegram/service.py` (documento, texto e pasta).
- **Novo provedor:** edite `gaia/agent/providers/factory.py` (`PRESETS`,
  `PUBLIC_PROVIDERS`, `preset_api_key`) e documente em `.env.example` e README.
- **Novo campo de config:** adicione no modelo pydantic, em `_ENV_MAP`, no
  `_DEFAULT_CONFIG_TEMPLATE` e em `.env.example`; atualize README se for visível.
- **Novo idioma do eco (tradução embaixo de cada mensagem):** é um item em
  `ECHO_LANGUAGES`/`ECHO_LABELS`/`ECHO_NAMES` (`gaia/config.py`, os mesmos para
  TUI e web) mais a tupla `(instrucao, system, titulo)` em `ECHO_PROMPTS`
  (`controller/commands.py`). O caminho é um só: `Orchestrator.translate_echo`
  (cache e sessão de modelo por idioma) — `translate_zh` fica como atalho do
  chinês. No `serve`, `IDIOMAS_ECO` (`gaia/serve/app.py`) deriva a lista do
  config e `POST /language` troca `interface.echo_language`; o evento continua
  `zh` (por compatibilidade) mas agora leva `traducao` e `lang`. Testes:
  `tests/test_serve_web.py` (rotas, evento por idioma), `test_orchestrator.py`
  (prompt/cache) e `test_textual_app.py` (rótulo na TUI).
- **Novo modo:** `controller/modes.py` (`PERSONAS`) e teste em `test_modes.py`.
- **Mudança no fluxo de deploy:** o script roda no mini
  (`deploy/gaia-deploy.sh` → `~/.local/bin/deploy-gaia`), as unidades são
  `deploy/gaia-deploy.service`/`.timer` e o `gmin deploy` só orquestra por SSH
  (`install`, `status`, `logs`, `--dry-run`). Teste sem SSH nem rede em
  `tests/test_gmin_deploy.py` (repo bare em `tmp_path`, stubs de `systemctl` e
  do `python` do venv).
- **Nova automação:** `controller/briefing.py` (janelas `*_at`/`*_until`) ou
  `controller/initiative.py` (lembretes/check-in); use `claim_meta` para
  garantir "uma vez por dia". Ex.: `_check_estudos` (cobra estudo atrasado).
- **Proteger branch no GitHub:** `scripts/branches-protegidas.sh` (usuário
  `talitadeoa`) aplica o ruleset `protege-branch-padrao` na branch padrao
  (`~DEFAULT_BRANCH`, cobre `main`/`master`): proibe `deletion` e
  `non_fast_forward` (push normal continua livre, sem PR obrigatorio) e sem
  `bypass_actors`. Rode `--check` antes; e idempotente (reaproveita o ruleset
  pelo nome). Nada de token no script: a credencial e a do `gh auth login`
  (`~/.config/gh/hosts.yml`), nunca o `.env`. Repos cobertos por padrao:
  `gaia`, `second-brain`, `agenda`, `abin-suite`, `estudos`.

## Armadilhas já descobertas

- **SSE:** assine a fila **antes** de enviar os cabeçalhos; o cliente dispara no
  header e o evento se perde para sempre.
- **Permissão:** quem espera é o `RemoteUI`, não o `Target`; o transporte resolve
  a UI primeiro e só depois avisa o alvo (senão trava até o timeout).
- **Proatividade no `serve` não chega no Telegram.** `_say_proactive` decide
  por `self.channel == "telegram"`; com o poller embutido no `gaia serve`
  (`channel="serve"`) a mensagem cai em `_speak()` — áudio local numa máquina
  headless — e num evento SSE sem cliente. Briefing/fecho passam porque chamam
  `telegram.send_message` direto. Correção em
  `docs/plans/2026-11-22-proatividade-e-ambiente.md` (ainda não aplicada).
- **`ui.permission` precisa de `request_id`**; sem ele, nega por padrão.
- **Texto final do turno** no `serve`/web vem em `run_finished.text`; o
  `stream_end` pode chegar sem texto (provedor sem deltas).
- **Reindex no start** roda antes de `servidor.start()`; com índice frio o HTTP
  fica sem resposta por minutos. Evite `reindex_on_start` no serve.
- **SIGTERM leva ~13 s** (worker de memória com `shield`); cada deploy paga isso.
- **`bash` não é sandbox:** o guard de caminho cobre `read/write/edit/list/glob/
  grep`, não o shell. Hoje o env do subprocesso herda o `.env` inteiro (chaves e
  tokens) — trate como pendência de segurança.
- **`always` de permissão** hoje é global ao processo, não à sessão (pendência
  registrada em `docs/prompt-correcoes.md`).
- **`bash` herda o `.env` inteiro** hoje; não use a ferramenta para inspecionar
  ambiente/segredos até isso ser filtrado (pendência).
- **`rsync` nunca copia `.git`.** O mini já foi atualizado por `rsync` e por
  isso o índice ficou 75 commits atrás do disco: `git status` mentia e não havia
  rollback. Hoje o mini é clone git e o `gaia-deploy.timer` (5 min) faz
  `fetch` + `reset --hard origin/main` + restart da unidade ativa. `rsync` no
  mini só como fallback manual, sempre com `--dry-run` antes.

## Testes

- `pytest` com `asyncio_mode = "auto"`; use `tmp_path` para dados e fakes para
  rede/provedor. A suíte não toca microfone, rede nem baixa modelos.
- Padrões úteis: `tests/test_agent.py` (ferramentas/provider),
  `tests/test_orchestrator.py` (fluxo), `tests/test_serve_app.py` (thread↔loop),
  `tests/test_telegram.py` (permissões), `tests/test_config.py` (env/TOML).
- Para bug, escreva o teste que reproduz antes de corrigir; para permissão,
  teste os dois lados (auto_approve e shell_access).

## Status e backlog

- `docs/prompt-correcoes.md` é uma **spec ativa ainda não aplicada**: lembretes
  perdidos, `always` por sessão e truncamento do bash mantendo o fim.
- Control Center: Fases A e B implementadas (`gaia/control/`, `/control`).
  Fase C **em andamento**: schema v7 (`automations`), agendador unico
  (`gaia/controller/scheduler.py`) substituindo os gates por rotina, retry
  com backoff so em `network`/`unknown` e notificacao de falha no Telegram
  (uma por falha). A divida da duplicacao de agenda foi paga: a tabela
  `automations` e a fonte de verdade e os workers nao decidem mais a hora.
  A tela de automacoes do web (`#automacoes`) pausa/retoma rotina, liga/
  desliga a proatividade e executa na hora (token do chat; `POST
  /automations/set`, `/automations/run` e `/proactive`).
  Faltam em C: rotas `run`/`pause`/`resume`/`runs` do painel, CRUD de skills
  e botoes na UI do `/control`. Fases D/E (`[proactivity] autonomy`, sugestao de automacao,
  retencao de 90 dias, export de diagnostico, integracao ponta a ponta) ainda
  **nao** implementadas: `docs/plans/2026-11-22-control-center-fases-cde.md`
  tem a tabela de status item por item — mantenha-a atualizada.
  A correcao de proatividade no `serve` (`owns_telegram`, `_speak` mudo em
  `serve`) esta aplicada (commit `12f15b1`).
- Web do `serve` em paridade com a TUI: `instances.json` (mini/Mac),
  sessao de cookie no chat (`gaia_chat`) e no painel (`gaia_control`), seletor
  de instancia no header, modos, sessoes anteriores e proximas automacoes.
  Design em `docs/plans/2026-11-22-web-instances-sessao-design.md`.
- Card de backlog (`gaia/backlog/`, schema v8) implementado de ponta a ponta:
  sync das paginas "* backlog" da Agenda, itens do chat/voz, worker que roda um
  item por vez no pi e abre o PR, evento SSE `backlog` e limpeza visual de 7
  dias. No web ele e o MESMO componente no aside (recolhido, abre ao clique) e
  na tela `#automacoes` (aberto). O que falta: o worker depende do pi/`gh`
  autenticados no mini (o card mostra `erro` quando falta) e nao existe botao
  para remover um item na tela (so `/backlog remover <id>`).
- Segurança pendente: sandbox/env do `bash`, lock no `POST /turn`, aprovação
  remota vs. callback do Telegram, redação de `serve.token`, timeout de
  permissão.
- `docs/plans/2026-11-20-handoff.md` tem o estado operacional e o backlog do
  serve (fila durável, compactação por janela real, reindex fora do start).
- Memória: schema v5 no código; o schema v6 (`facts`, `recall`, check-in 18h) de
  `docs/memoria-plano.md` e `docs/memoria-checkin-18h.md` **não** está
  implementado.
