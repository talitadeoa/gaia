# Plano — `gaia serve` + cliente fino (uma inteligência, várias telas)

> **Status: fases 1 e 2 implementadas e em produção** (`gaia serve`,
> `gaia --remote`, cliente web em `GET /`). As partes 2 e 3 (fila durável,
> compactação por janela, AG-UI, cards) continuam backlog.

**Data:** 2026-11-19 (atualizado 2026-11-20: fase 1 **em produção** e fase 2
entregue)
**Status:** fase 1 implementada, **cutover feito** no mini (o `gaia-serve` é o
dono do Telegram desde 20/11 19:13) e **cliente web** (fase 2) em `GET /`.
Partes 2 e 3 seguem backlog.
**Decisão da Talita (2026-11-19):** o **mini PC é a fonte da verdade**. Ele
processa, guarda o banco e o Second Brain, e é o único escritor. Mac, navegador e
celular são **interface** — tela e entrada, nada de estado.

---

## 1. Problema

Hoje a Gaia é um processo único e monlítico: TUI + orquestrador + agente + banco
no mesmo processo e na mesma máquina. Isso gerou o problema real de 18–19/11:
dois bancos divergentes (Mac parado em 17/11 22:26, mini vivo até 19/11 19:15),
dois pollers do Telegram brigando (`409 Conflict`), vault de Markdown com 11
memórias de um lado e 2 do outro, e nenhuma forma de diagnosticar o mini de fora
de casa.

Sincronizar bancos é a resposta errada: SQLite sobre rede (SSHFS/SMB) corrompe
em queda de conexão, e merge de `turns` com IDs colidindo é frágil. A resposta
certa é **um escritor só** e uma fronteira de rede entre estado e tela.

## 2. O encaixe já existe no código

O que descobrimos no levantamento, e que muda o custo do projeto:

| Peça | Onde | Situação |
|---|---|---|
| Protocolo de eventos tipado | `gaia/agent/events.py` | `TurnStarted`, `TextDelta`, `ToolStarted`, `ToolFinished`, `PermissionAsked`, `TurnCompleted` — "nenhum formato de provedor vaza para a interface" |
| Stream assíncrono | `AgentRuntime.stream_events()` (`runtime.py:810`) | já existe |
| Aprovação como evento + resposta | `PermissionAsked` + `respond_permission(id, once\|always\|reject)` | já existe |
| Superfície de renderização | `ui.*` no controller | **193 chamadas, 25 métodos distintos** — duck-typed, sem base comum |
| Servidor HTTP sem dependência | `gaia/whatsapp/webhook.py` | `ThreadingHTTPServer` da stdlib; precedente existe |

Conclusão: **não se reescreve o orquestrador**. O `ui` já é a costura injetada
(`TerminalUI`, `TextualUI`). Basta uma terceira implementação, `RemoteUI`, que em
vez de desenhar **serializa eventos**. O cliente fino só sabe desenhar.

## 3. Arquitetura

```text
mini 100.x.y.z (dono do estado)              clientes (sem estado)
┌──────────────────────────────────┐            ┌───────────────────────────┐
│ gaia serve                       │   SSE      │ gaia --remote  (Rich)     │
│   Orchestrator (prompt, tools,   │◄──────────►│ web (HTML + SSE)          │
│   memória, proatividade, voz)    │  HTTP      │ celular (mesmo HTML)      │
│   RemoteUI ──► queue.Queue ──┐   │            │                           │
│                              │   │            │ STT/TTS local (whisper,   │
│   POST /turn /permission     │   │            │ edge-tts, say)            │
│   GET  /events (SSE)         │   │            └───────────────────────────┘
│   GET  /sessions /health     │   │
│   SQLite + SecondBrain       │   │
└──────────────────────────────────┘
        Tailscale: nada exposto à internet
```

### Decisões

| Decisão | Escolha | Alternativa descartada |
|---|---|---|
| Fronteira | **orquestrador remoto** (servidor processa tudo) | runtime remoto (cliente rodaria prompt e memória → cérebro partido) |
| Costura | nova implementação de `ui` (`RemoteUI`) | evento novo em cada uma das 193 chamadas (retrabalho enorme) |
| Transporte | **SSE** (`text/event-stream`) + POST | WebSocket (bidirecional que não precisamos; mais código de reconexão) |
| Servidor | `ThreadingHTTPServer` (stdlib) | FastAPI/uvicorn (dependência nova para 5 rotas) |
| Ponte thread↔asyncio | `queue.Queue` + `run_coroutine_threadsafe` | tornar tudo sync (perderia o loop async do agente) |
| Escritor | só o `gaia serve` escreve | sync de bancos (corrompe/frágil) |
| Voz | **fica no cliente** (áudio nunca sai do Mac) | mandar áudio ao servidor (perde a privacidade, que é o diferencial) |
| Auth | Bearer token + bind no IP do tailnet | OAuth/SSO (overkill para 1 pessoa) |
| Protocolo | nomes **AG-UI** onde existem; `custom` no resto | inventar formato próprio (perde interoperabilidade grátis) |

## 4. Contrato do fio

### 4.1 Eventos servidor → cliente

Um envelope, um stream. `seq` monotônico permite reconectar sem perder ordem.

```json
{"v":1,"seq":12,"ts":"2026-11-19T20:00:00-03:00","run":"r_ab12","type":"say","text":"..."}
```

| `type` | Payload | Origem (`ui.*` / evento) |
|---|---|---|
| `run_started` | `run`, `session` | `TurnStarted` |
| `say` | `text` (markdown) | `ui.print_markdown` |
| `stream_begin` / `stream_delta` / `stream_end` | `text` | `ui.begin_stream`, `TextDelta`, `ui.finish_stream` |
| `reasoning` | `text` | `ui.reasoning`, `ReasoningDelta` |
| `tool` | `name`, `detail`, `status` (`running\|ok\|error`), `diff` | `ui.tool`, `ToolStarted`, `ToolFinished` |
| `permission` | `request_id`, `tool`, `detail`, `resources[]` | `PermissionAsked` (o runtime já é o emissor) |
| `notice` | `level` (`info\|success\|warning\|error\|hint`), `text` | `ui.info/success/warning/error/hint` (**agrupa 129 das 193 chamadas**) |
| `status` | `text` (transitório, `null` limpa) | `ui.set_status`, `ui.clear_status` |
| `tokens` | `prompt`, `completion`, `total` | `ui.token` |
| `context_hits` | `hits[]` | `ui.context_hits` |
| `run_finished` | `run`, `ok`, `error` | `TurnCompleted` |

### 4.2 Mapa AG-UI (parte 3, já respeitado agora)

| Gaia | AG-UI |
|---|---|
| `run_started` | `RUN_STARTED` |
| `stream_begin/delta/end` | `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` |
| `reasoning` | `THINKING_TEXT_MESSAGE_CONTENT` |
| `tool` | `TOOL_CALL_START` / `TOOL_CALL_END` |
| `run_finished` | `RUN_FINISHED` |
| `permission`, `notice`, `status`, `tokens`, `context_hits` | `CUSTOM` (name `gaia.*`) |

Manter os nomes deste plano como **aliases** desde o dia 1 custa quase nada e
permite, na parte 3, expor `/agui` sem refatorar o cliente.

### 4.3 Rotas

| Método | Rota | Corpo / resposta |
|---|---|---|
| `POST` | `/turn` | `{"text":"..."}` → `202 {"run":"r_ab12"}` |
| `POST` | `/permission/{request_id}` | `{"decision":"once\|always\|reject"}` |
| `POST` | `/abort` | encerra a execução corrente |
| `GET` | `/events?since=<seq>` | SSE; `Last-Event-ID` retoma |
| `GET` | `/sessions` | lista conversas (id, canal, modo, updated_at) |
| `GET` | `/health` | `{"ok":true,"model":"...","db":"...","version":"..."}` |
| `GET` | `/` | redireciona para o cliente web (fase 2) |

Regra de segurança: **recusar bind em host não-loopback sem `GAIA_SERVE_TOKEN`**.

---

## 5. Fase 1 — implementar agora

**Entrega:** `gaia serve` no mini + `gaia --remote` no Mac, com streaming,
aprovação e sessões. Sem web ainda.

### Task 1 — Config e CLI
- `[serve]` em `gaia/config.py`: `host = "127.0.0.1"`, `port = 8787`,
  `token_env = "GAIA_SERVE_TOKEN"`, `allow_remote = false`.
- `gaia/cli_parser.py`: subcomando `serve` com `--host`, `--port`, `--token`;
  flag global `--remote URL` (em `gaia`, `ask` e `-old`).
- Teste: `build_parser()` aceita `serve --port 0`; `--remote` aparece em `cfg`.

### Task 2 — `RemoteUI` (o coração)
- `gaia/interface/remote.py`: implementa os 25 métodos de `ui.*`, cada um
  empurrando um dict no `queue.Queue` injetado. Nenhum I/O, nenhum print.
- **Aprovação não bloqueia.** A permissão chega pronta do runtime como
  `PermissionAsked` (é ele quem emite e espera num `Future`); `RemoteUI` só a
  serializa e o `POST /permission/{id}` chama `runtime.respond_permission(...)`.
  O hook `ui.permission`/`permission_callback` que `TerminalUI` usa **não** é o
  caminho do servidor — serve só às interfaces locais.
- `ui.prompt` (input síncrono do usuário) devolve `""` no modo remoto: pergunta
  ao usuário vira `notice`/`card`, nunca um `input()` bloqueante no servidor.
- Teste: cada método gera o evento esperado; `notice` agrupa os 5 níveis;
  nenhum método toca `stdout`; `permission` nunca bloqueia.

### Task 3 — Servidor SSE
- `gaia/serve/server.py`: `ThreadingHTTPServer` + handler; `GET /events` drena o
  `queue.Queue` e escreve `event:`/`data:`/`id:`; `POST` usa
  `asyncio.run_coroutine_threadsafe` no loop do orquestrador.
- `gaia/serve/auth.py`: Bearer, 401 sem token quando `allow_remote`.
- Teste: framing SSE correto; 401 sem header; `since` filtra; POST `/turn`
  chama o orquestrador falso e devolve `run`.

### Task 4 — Comando `gaia serve`
- `gaia/cli_serve.py`: monta `_build_app` headless (`TerminalUI(quiet=True)`
  para logs), injeta `RemoteUI`, sobe o servidor e roda o loop de proatividade +
  Telegram como hoje (sem duplicar poller: `--telegram/--no-telegram`).
- Teste: `gaia serve --port 0` sobe, `/health` responde, `Ctrl+C` encerra limpo.

### Task 5 — Cliente `gaia --remote`
- `gaia/client/remote_client.py`: `RemoteUI` invertido — consome SSE e chama os
  mesmos métodos de `TerminalUI` (Rich). Entrada por `input()`; permissão vira
  pergunta `o/a/r` e `POST /permission/{id}`.
- Teste: cliente com servidor falso reproduz a ordem `say → tool → stream → done`.

### Task 6 — Empacotamento no mini
- `deploy/gaia-serve.service` (loopback) e
  `deploy/gaia-serve-tailnet.service.example` (escuta no `100.x` com
  `GAIA_SERVE_ALLOW_REMOTE=true`).
- `scripts/gmin.sh`: `gmin serve install` (copia a unidade, **não liga**),
  `gmin serve cutover` (para `gaia-telegram` e liga `gaia-serve` — um processo
  só), `gmin serve logs`, `gmin serve status`.
  `gmin deploy` passa a reiniciar a unidade **ativa** (serve ou telegram).
- Roadmap: `launchctl` do Mac continua **disabled** (poller é o mini).

**Cutover (feito em 20/11 19:13):**
1. `gmin deploy` (código novo no mini, sem trocar a unidade);
2. `gmin serve install --tailnet`;
3. testar com a unidade parada: `gmin shell` + `gaia serve --port 8799` com
   `GAIA_DATA_DIR=/tmp/...` e `--no-telegram` (nunca no banco vivo);
4. `gmin serve cutover`;
5. conferir no Telegram que uma mensagem responde e que o briefing das 12:00
   não sai duas vezes.

**Saída de emergência:** `gmin serve rollback` (volta o dono para
`gaia-telegram`).

> Notas de execução do cutover (20/11): a unidade tailnet falhou no primeiro
tentativa porque tinha `Requires=tailscaled.service` — unidade de **sistema**,
inválida num `systemd --user`. Corrigido para `After=network-online.target`
(unidade de usuário não pode exigir unidade de sistema).
> O `gaia serve` fecha em ~13 s no SIGTERM (o worker de memória é esperado com
`shield` por 15 s); o systemd aguenta, mas cada `gmin deploy` custa isso.
> Cold start com `sb_index` vazio reindexa **dentro do loop** (embeddings locais
no Ollama): o HTTP só sobe depois. Com o índice quente (produção) é rápido.

**Critério de aceite da fase 1:** no Mac, `gaia --remote http://gaia:8787`
responde em streaming, mostra ferramenta rodando e pede aprovação; matar o
cliente e reabrir com `since=` não perde mensagem; o banco no Mac **não** é
escrito.

**Fora de escopo na fase 1:** web, voz remota, múltiplos usuários, Textual
remota (primeiro Rich, depois Textual).

### Fase 2 — cliente web (entregue em 20/11)

`GET /` serve `gaia/serve/static/index.html`: uma página, sem build e sem CDN,
com o mesmo contrato da §4.1.

| Peça | Como |
|---|---|
| Transporte | SSE por `fetch` + `ReadableStream` (o `EventSource` não aceita cabeçalho) |
| Token | `localStorage` → `Authorization: Bearer`; botão "token" para trocar |
| Reconexão | `?since=<último seq>`; heartbeat de 15 s do servidor |
| Streaming | `stream_begin/delta/end`; sem deltas, o texto final vem em `run_finished.text` |
| Permissão | cartão com "uma vez / sempre / recusar" → `POST /permission/{id}` |
| Second Brain | `GET /search?q=` → mesmo índice da TUI (lexical + semântica) |
| Turno | `POST /turn`; 409 vira aviso "ainda no turno anterior" |

O eco da mensagem do usuário é local: o servidor não emite `user` no `POST
/turn` (só `run_started`).

---

## 6. Parte 2 — ideias do `nightly-labs/openbot` (implementar depois)

> **Licença: PolyForm Noncommercial 1.0.0.** Não é open source e proíbe uso
> comercial. **Ler ideias, nunca copiar código.** Nada deste repositório entra
> nos arquivos da Gaia (MIT).

| # | Ideia de lá | O que resolveria aqui | Onde encaixa | Esforço |
|---|---|---|---|---|
| 2.1 | **Fila FIFO de mensagens com pause/resume/cancel e persistência a prova de crash** | hoje `Esc`/restart do Telegram perde o turno em andamento (`turns` só grava o final) | tabela `turn_queue` no `gaia.db` + worker; `RemoteUI` já emite `status` | 2 dias |
| 2.2 | **Compactação por uso real da janela do modelo** | `max_history_messages=20`/`max_history_chars=24000` são por contagem, não por token vs limite do modelo | `controller/prompt.py` + **criar** catálogo de janela por modelo (hoje `agent/catalog.py` só lista modelos: não guarda `context_window`) | 2 dias |
| 2.3 | **Navegador como ferramenta do agente** (persistente, inspecionável) | Playwright hoje é só CLI (`gaia browser text`) — o agente não navega | `agent/tools/browser.py`, com `requires_approval=True` | 1–2 dias |
| 2.4 | **Temas em JSON observados em runtime** | TUI tem tema fixo | `interface/` lê `~/.config/gaia/themes/*.json` | 2 h |
| 2.5 | **Reassign/Archive/Restore em conversas** | sessões só trocam (`F2`), não arquivam | `conversations.is_current` já existe; falta `archived_at` | 4 h |

**Ideia deles que a Gaia deve rejeitar explicitamente:** o README avisa que os
agentes rodam com `danger-full-access` e `approvalPolicy: never`. A Gaia faz o
contrário — aprovação com escopo e auditoria. Registrar isso no README como
diferencial.

## 7. Parte 3 — ideias do `CopilotKit/openbot` (implementar depois)

> **Licença:** o repositório é MIT, mas o **CopilotKit Intelligence** (memória,
> learning, governança) tem licença separada e exige trial/produção. O MIT cobre
> o *template*. Copiar código do Intelligence é risco jurídico.

| # | Ideia de lá | O que resolveria aqui | Onde encaixa | Esforço |
|---|---|---|---|---|
| 3.1 | **AG-UI como protocolo do `/turn`** | formato próprio de eventos | §4.2 já preparado; expor `/agui` em paralelo | 2 dias |
| 3.2 | **"Interactive results": cards estruturados por superfície** | agente só devolve texto; aprovação é o único card | `events.py`: `card{kind,payload}`; web renderiza botão, TUI renderiza texto | 3 dias |
| 3.3 | **Policy/audit na API, não no agente** | `auto_approve` booleano, `_always_approved` em memória | tabela `audit` + `policy` por ferramenta/escopo (já recomendado na comparação com o agentes.chat) | 3 dias |
| 3.4 | **Computador plugável (runner)** | `bash` roda no host do processo | registrar o Mac como "computador" do mini (`gaia runner`), restrito por diretório | 1 semana |
| 3.5 | **Frontends desacoplados (web/desktop/mobile/Slack)** | valida a arquitetura, não é feature | já coberto pela fase 1 + 2 | — |

**Não copiar:** Kubernetes/Helm, Postgres, CopilotKit Intelligence, multi-tenant.

## 8. Riscos

| Risco | Mitigação |
|---|---|
| SSE sobre Tailscale cai ao trocar de rede | `seq` + `Last-Event-ID`; cliente reconecta com backoff |
| Banco SQLite com dois processos (serve + `gmin ask`) | `gmin ask` passa a usar `POST /turn`; nada abre o `.db` fora do serve |
| Token vazando em log | token só em `.env`; header nunca logado; `--verbose` redige |
| `RemoteUI` divergir da `ui.*` real | teste que falha se um método novo em `TerminalUI` não existir em `RemoteUI` (reflexão) |
| Duplicar proatividade (serve + Telegram) | `serve` é o único dono; `--telegram` embutido, nunca dois processos |

## 9. Ordem de trabalho

```text
Task 1 (config/CLI) → Task 2 (RemoteUI) → Task 3 (servidor) → Task 4 (serve)
                                   ↘ Task 5 (cliente --remote) → Task 6 (deploy)
Parte 2: 2.2 → 2.1 → 2.3 → 2.5 → 2.4
Parte 3: 3.1 → 3.2 → 3.3 → 3.4
```

Regra da casa: um commit por task, `pytest -q` verde antes de cada um.
