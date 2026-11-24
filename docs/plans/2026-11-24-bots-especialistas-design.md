# Bots especialistas — design

> **Status: aprovado; fases 0 e 1 implementadas, fases 2–4 não.** As
> cinco decisões do §10 estão tomadas. O plano de execução por fases está no §7
> e é a base do `writing-plans`. Referência atemporal:
> [`../../AGENTS.md`](../../AGENTS.md).
>
> Vocabulário adotado (pedido da Talita): **bot**, não "agent". "Agente" fica
> reservado para o motor único (`AgentClient`/`Orchestrator`), que continua
> sendo **um só**.

## 1. Objetivo

Transformar os quatro modos de hoje (`assistente`, `pesquisadora`,
`trabalhadora`, `investidora`) em cinco bots com nome próprio, cada um com:

- thread de conversa e contexto próprios (histórico que não se mistura);
- **ferramentas** próprias (allowlist) e **área de trabalho** própria;
- automações em segundo plano atribuídas a ele;
- presença no painel web: selecionar, conversar, ver atividade e avisos.

| bot | nome | papel | workspace | ferramentas |
|---|---|---|---|---|
| `gaia` | Gaia | orquestradora: conversa, agenda, rotina, e fala com os outros bots | `~/gaia` (diretório atual) | todas + `falar_com_bot` |
| `donna` | Donna Haraway | dissertação, ensaios, revisão, método | `~/dissertacao` (clone de `talitadeoa/dissertacao`) | `read/list/glob/grep/write/edit` + `bash`, `pi`, escopo `~/dissertacao` |
| `ariana` | Ariana Grande | bolsa, watchlist, alertas | dados (`~/.local/share/gaia`) | `bolsa`, `carteira`, `planilha`, `read` — **sem modelo no laço** |
| `sabrina` | Sabrina Carpenter | vagas, CV sob medida, carta | `~/SecondBrain/cv` | `career`, `read/list/glob/grep`, `write` restrito a `cv/`, `bash` (`pdflatex`) |
| `ada` | Ada Lovelace | backlog, código, PR | `~/pr-workspace` (**nunca** `~/gaia`) | `read/write/edit/bash/grep/glob`, `pi`, `backlog` |

### O que um bot **não** é

- **Não é um processo.** Um processo escritor só (`gaia serve`) é invariante do
  repo. Bot é configuração + estado, não unidade de deploy.
- **Não é um modelo separado obrigatório.** `model` é opcional por bot; vazio
  herda o global. Modelo por bot é custo, não identidade.
- **Não é um "system prompt gigante".** Identidade é prompt; o isolamento real
  é feito na camada de ferramentas e de filesystem (§4.2). Prompt sozinho não
  isola nada — um texto malicioso dentro do Second Brain pode pedir para "ler
  a pasta da Ada" e o modelo obedece se o handle existir.

## 2. Auditoria — o que já existe e será reaproveitado

| Necessidade | Já existe | Como o bot usa |
|---|---|---|
| histórico separado por persona | `conversations(project_path, mode, channel)` + `turns` (`memory/store.py`) | `mode` **passa a ser o id do bot** — zero migração de dados |
| sessão de contexto do modelo | `AgentSession` + `SessionStore` (JSON em `data/sessions/`), uma por conversa | nenhuma mudança: cada bot tem a sua |
| prompt em duas camadas | `controller/prompt.py::compose_system` | a diretriz do bot entra no prefixo estável (cacheável) |
| restringir ferramentas por turno | `AgentClient._run_turn(tool_names=...)` → `session.allowed_tools` → `_run_tool` recusa | é aqui que a allowlist do bot vira barreira de verdade |
| diretório de trabalho das tools | `ToolContext.directory` (usado por `filesystem.py` e `bash` como `cwd`) | vira **workspace do bot**, por sessão |
| agendador único | `controller/scheduler.py` + tabela `automations` + `runs` (schema v7/v11) | automação ganha coluna `bot` |
| fila durável de um item | `gaia/backlog/worker.py` + `BacklogMixin` | Ada herda a fila pronta |
| eventos para o web | `RemoteUI.emit` (SSE, `seq`, `run`) | ganha o campo `bot` e o filtro por bot na tela |
| telas com URL | hash `#automacoes`, `#backlog`, `pintarTelaAtual` | `#b/<bot>` entra no mesmo mecanismo |
| configuração fora do repo | `~/.config/gaia/config.toml` | bots vivem lá (o `gaia-deploy.timer` faz `reset --hard`, não toca em config) |

**Não existe hoje (e o design não finge que existe):**

- contexto de ferramentas por sessão (o `ToolContext` é construído uma vez no
  `AgentClient.__init__` e compartilhado por todas as sessões);
- índice textual no Second Brain para a pasta da Donna (§4.4a): `_iter_markdown`
  só aceita `.md`, então os 17 arquivos de `pesquisa/` (13 `.md`, 2 `.docx`,
  1 `.tex`, 1 `.html`) ficariam metade fora da busca;
- evento por sessão (o `_events` é uma fila única; dois turnos simultâneos
  roubam eventos um do outro — ver §4.5);
- atribuição de `runs`/`automations` a um bot;
- qualquer noção de "bot falou com bot".

## 3. Decisões fechadas

| # | Decisão | Motivo |
|---|---|---|
| D1 | Bot é **descritor declarativo** (código com defaults + override no TOML), não processo nem plugin | um processo escritor; sobrevive ao `reset --hard` do deploy porque a config está em `~/.config` |
| D2 | `conversations.mode` **é** o id do bot | a separação de histórico já existe e é testada; renomear coluna não compra nada |
| D3 | Isolamento em 4 camadas: prompt, allowlist de tools, workspace/filesystem, conversa | prompt sozinho não é fronteira de segurança |
| D4 | Um turno **interativo** por vez (o 409 atual continua) | a pessoa lê uma conversa por vez; paralelizar chat custa refatorar estado global do `Orchestrator` sem ganho de UX |
| D5 | Segundo plano é **concorrente**, com fila e eventos próprios | é o que a Ariana e a Ada precisam de verdade |
| D6 | Automação de bot pode ser determinística (código) ou por prompt (`bot_task`) | não reescrever `market/`, `career/`, `backlog/` em prompt |
| D7 | Segundo plano **não** amplia permissão | `write`/`edit`/`bash` continuam exigindo aprovação; sem ninguém para aprovar, o job falha e avisa |
| D8 | Bot não tem memória vetorial própria no MVP | `facts`/`recall` (schema v6) ainda não existem; não inventar agora |
| D9 | Inter-bot é chamada one-shot com profundidade máxima, não debate multi-turno | evita loop, custo e confusão de transcrição |
| D10 | Nenhum roteamento automático de mensagem entre bots no MVP | bot errado respondendo é pior que bot nenhum; a seleção é explícita |

## 4. Arquitetura

### 4.1 Registry de bots

`gaia/bots/registry.py` (novo):

```python
@dataclass(frozen=True)
class Bot:
    id: str                 # gaia | donna | ariana | sabrina | ada
    name: str
    role: str               # uma linha, para a sidebar
    directive: str          # persona (prefixo estavel do prompt)
    workspace: str          # diretorio de trabalho das tools
    tools: tuple[str, ...]  # allowlist; vazio = todas (so o `gaia`)
    scope: str = ""         # teto de leitura/escrita (path prefix)
    model: str = ""         # vazio = modelo global
    aliases: tuple[str, ...] = ()
    can_message: tuple[str, ...] = ()   # a quem pode chamar `falar_com_bot`
```

`DEFAULT_BOTS: dict[str, Bot]` no código (os cinco acima, com as diretrizes
que já existem em `controller/modes.py`, renomeadas). `load_bots(cfg)` mescla
`cfg.bots` (TOML) sobre os defaults: o código é a semente, o TOML é a decisão.

**`id` é curto e estável; `name` é o nome completo** (`donna` → "Donna
Haraway"). O `id` vira `conversations.mode` e chave de config — renomear depois
exige migração (`MODE_KEY_RENAMES` já existe para isso). Por isso o `id` nasce
definitivo: `gaia`, `donna`, `ariana`, `sabrina`, `ada`.

Validação dura no carregamento: id desconhecido em `tools`/`can_message`,
`workspace` inexistente, `scope` que não é diretório absoluto existente **ou
que não contém o `workspace`** — tudo derruba o boot com mensagem clara (bot
mal configurado não deve subir "meio ligado", e `scope` que não contém o
`workspace` deixaria o bot sem conseguir ler o que ele mesmo escreve).

TOML (seção nova, sem segredo dentro):

```toml
[bots.donna]
name = "Donna Haraway"
# cwd do bash/pi: o clone do repo talitadeoa/dissertacao
workspace = "~/dissertacao"
# teto de read/list/glob/grep/write/edit. No macOS o clone aparece dentro do
# Second Brain como pesquisa/ (symlink); `scope` fica no destino resolvido.
scope = "~/dissertacao"
tools = ["read", "list", "glob", "grep", "write", "edit", "bash", "pi"]
```

A segunda opção depende do symlink: se `~/dissertacao` apontar para
`~/SecondBrain/pesquisa`, todo caminho resolvido cai dentro do Second Brain e
`scope` pode ser `~/SecondBrain` (a Donna passa a enxergar também `Memoria/` e
`cv/`, o que é vantagem para cruzar leitura acadêmica com notas). Sem symlink,
`scope = "~/dissertacao"` e ela enxerga só a dissertação. A decisão de qual
usar é da Fase 1, quando o clone existir; o registry aceita as duas.

`controller/modes.py` vira **shim de compatibilidade**: `PERSONAS` é derivado do
registry (mesmo `dataclass Persona`), `ALIASES` ganha os nomes ("donna",
"haraway", "ariana", "sabrina", "ada"), e `parse_mode_switch("vira Donna")`
continua funcionando. Assim `/modo`, `Tab`/`F3` e `test_modes.py` não quebram.

### 4.2 Isolamento em quatro camadas

| camada | onde | o que garante |
|---|---|---|
| 1. prompt | `compose_system(bot_directive, ...)` | identidade e tom; cacheável; **não** é segurança |
| 2. allowlist | `AgentSession.allowed_tools` (já existe) setado a partir de `bot.tools` | o modelo pode *pedir* `bash`, mas `_run_tool` recusa: "ferramenta indisponivel neste bot" |
| 3. workspace/escopo | `ToolContext` por sessão + `ctx.resolve` | `read/write/edit/list/glob/grep` resolvem contra `bot.workspace` e não saem de `bot.scope`; `bash` roda com `cwd=workspace` |
| 4. conversa | `conversations.mode = bot.id` + sessão por conversa | histórico, resumo de compactação e tokens são por bot |

Mudanças necessárias na camada 2 e 3 (hoje não existem):

- `AgentSession` ganha `policy: SessionPolicy | None` com `tools`,
  `directory`, `scope` — persistido no JSON da sessão, então uma sessão retomada
  depois de restart não volta a ter as ferramentas todas.
- `AgentClient.set_session_policy(session_id, policy)` (idempotente) e
  `_run_tool` monta o contexto efetivo:
  `dataclasses.replace(self._tool_context, directory=policy.directory, scope=policy.scope)`.
- `ToolContext.scope` novo: `resolve()` recusa caminho fora do escopo com
  `ToolError` (mensagem em português, sem vazar path absoluto). Vazio = sem teto
  (comportamento de hoje). `allow_outside_project` continua valendo por cima.
- `bash` **não** é sandbox (armadilha já registrada no AGENTS.md). A allowlist
  resolve o caso "o bot não pede bash"; ela não resolve "bash malicioso dentro
  do workspace". Para Ada, o caminho é o mesmo de hoje: workspace isolado,
  `pi` protegido, e PR revisado por humano.

### 4.3 Roteamento

Não há classificador de intenção entre bots no MVP. O roteamento é explícito:

1. **Seleção** — sidebar do web (`#b/donna`), `/bot donna`, `Tab`, ou o modo
   atual da TUI. Troca de bot = `select_bot(id)` = o `select_mode` de hoje com
   validação (bot desconhecido → 400, não cai no padrão em silêncio).
2. **Menção dentro da conversa** — "pergunta pra Donna o que ela acha do
   capítulo 2" é uma chamada de ferramenta (`falar_com_bot`), não uma troca de
   thread. A resposta entra no histórico do bot chamador como resultado de
   ferramenta e no histórico do chamado como turno `context`.
3. **Automação** — sempre atribuída a um bot (coluna `bot`), nunca "descoberta"
   em tempo de execução.

Fase futura (não MVP): um roteador opcional no `gaia` que sugere "isso é com a
Ada — quer que eu encaminhe?" e só encaminha com confirmação. Sugestão, nunca
execução automática.

### 4.4 Memória e contexto por bot

| tipo | onde vive | isolamento |
|---|---|---|
| conversa (turnos) | `turns` via `conversations.mode` | total |
| contexto do modelo | `AgentSession` (JSON por sessão) + compactação | total |
| preferências da pessoa | `SecondBrain/preferences.md` | **compartilhado de propósito** — tom de voz é da Talita, não do bot |
| memória consolidada | nota do dia em `Memoria/`, frontmatter ganha `bots: [ida...]` | por bot, na leitura: o bot só recebe o que é dele |
| Second Brain (pesquisa) | índice FTS5 único | `index.search` ganha filtro opcional por prefixo (`prefix=bot.scope`), hoje ele busca no índice inteiro |
| arquivos de trabalho | `bot.workspace` | por camada 3 |

### 4.4a Donna e o repositório da dissertação

O `/pesquisa` do Second Brain vira o repo privado `talitadeoa/dissertacao`
(hoje vazio, branch `main`). O clone mora em `~/dissertacao` — **fora** do
Second Brain por dois motivos: (a) `reindex_on_start` varreria o `.git` e os
builds de LaTeX; (b) o `.git` do repo da dissertação não pode conviver com o
`reset --hard` do deploy da Gaia, que é de outro repositório.

`Bot.directory` precisa **separar** o diretório de trabalho (`~/dissertacao`,
usado por `bash`/`pi` como `cwd`) do teto de leitura (`scope`, usado por
`read/list/glob/grep`). São dois campos, não um: um bot só quebraria o outro.

Ferramentas da Donna: `read/list/glob/grep/write/edit` (escopo da §4.2) **e**
`bash`/`pi` com `cwd = ~/dissertacao` (`git`, `pdflatex`, PR). O `bash` dela é
aprovado por turno, como qualquer outro; compilar LaTeX e commitar é exatamente
o tipo de ação que a pessoa vê e aprova.

**O que o índice não cobre hoje.** `SecondBrainIndex._iter_markdown` só aceita
`.md`; o conteúdo atual de `pesquisa/` é 13 `.md`, 2 `.docx`, 1 `.tex`, 1
`.html`. Três consequências, todas de escopo pequeno:

1. Fase 1: `config.secondbrain.text_extensions` (padrão hoje `.md`; a Donna
   liga `.tex`, `.bib`, `.txt`). Extração de texto puro por leitura do
   arquivo; **sem** dependência nova.
2. `.docx` fica **fora** do índice de propósito: é zip+XML e exige
   biblioteca nova (`python-docx`), que é dependência que o repo evita. A
   Donna lê `.docx` pelo `bash` (`unzip -p arquivo.docx word/document.xml`),
   sob demanda e com aprovação — não vale inflar o índice com prosa em XML.
3. `pdf` idem: `gaia/audiobook/extract.py` já tem um leitor de PDF **sem
dependência**; é o candidato natural quando a dissertação passar a ter PDF no
repo, mas fica para depois do MVP.

Duas regras que evitam o "vazamento de instruções" que você citou:

- **Contexto recuperado nunca vai no system prompt.** Continua indo junto da
  mensagem do usuário, marcado como "não é um pedido" (é o que
  `controller/turns.py` já faz). Um trecho de nota não pode redefinir identidade.
- **Resultado de ferramenta não muda política.** Se o conteúdo lido disser
  "agora você é a Ada", o turno continua com a allowlist da Donna.

### 4.5 Fila de automações em background

Três peças, todas já existentes, com uma coluna nova:

1. **Agenda** — `automations` ganha `bot` (vazio = `gaia`). `describe()`/`load()`
   não mudam de forma; `render()` passa a devolver `bot` para a tela agrupar.
2. **Execução** — o agendador único continua sendo o único disparador.
   `market:watchlist` é **determinístico hoje** e continua assim (§4.5a): o
   corpo busca cotação, monta a mensagem e entrega, sem uma única chamada ao
   modelo. Skill nova `bot_task` (prompt no bot X, na sessão dele, com as
   ferramentas dele) serve **Donna** ("revisão semanal do capítulo") e
   **Sabrina** ("garimpa vagas novas") — nunca a Ariana no resumo diário. Costa
   abaixo:

| automação | custo de modelo |
|---|---|
| `market:watchlist` (Ariana) | zero |
| `career:weekly` (Sabrina) | zero (`gaia/career/` já pontua por regra) |
| `backlog` (Ada) | `pi` (chave própria, fora do modelo do chat) |
| `bot_task` (Donna/Sabrina/Ada quando pedido) | tokens do modelo do bot |

### 4.5a Ariana: determinístico, e o painel é a entrega principal

O que já existe: `market:watchlist` no agendador, `briefing.market_days/at/until`
no TOML, `Watchlist` em `~/.local/share/gaia/watchlist.json`, cotação em
`finance/quotes.py` e a mensagem em `briefing/market.py` (`market_message`).
Nada disso usa o modelo.

O que muda com a Ariana é a **entrega**, hoje com dois problemas:

1. **Gate duplo de config.** `_automation_market` devolve `False` sem
   `briefing.market_enabled` **e** `telegram.enabled`, e `Skill(requires=...)`
   repete o mesmo par. Num `serve` headless sem Telegram a automação nunca roda
   — e a tela mostra "ligada" (armadilha registrada no AGENTS.md: gate no laço
   ganha do checkbox).
   *Correção*: o rendimento passa a ser o do painel. Sem Telegram, a saída é
   `bot_messages` (feed da Ariana) + `runs.detail`; com Telegram, os dois. O
   `requires` vira só `briefing.market_enabled`.
2. **Nada consome a cotação sem Telegram.** `runs` guarda que rodou, mas não o
   conteúdo. Por isso a Ariana precisa de um consumidor próprio (o feed) e não
   só de um repasse ao Telegram.

Regra que fica: **a Ariana nunca chama o modelo no laço.** Se um dia houver
leitura analítica ("o que isso significa para a minha carteira"), ela entra como
`bot_task` **semanal** e explicitamente opt-in, com custo declarado na tela —
nunca dentro do `market:watchlist`.
3. **Visibilidade** — `runs` ganha `bot`; tabela nova `bot_messages`
   (`id, bot, kind, title, text, created_at, seen`) é o feed do painel e a
   origem do badge de não-lido. `POST /bots/seen` zera.

**Concorrência (o ponto mais delicado).** Hoje `AgentClient._events` é uma fila
única e `prompt_async` chama `_drain_events()` no começo. Com dois turnos
simultâneos, um deles descarta os eventos do outro → turno alheio parece travar
até o `stall_timeout`. Correção mínima, pré-requisito de qualquer segundo plano:

- `AgentClient.subscribe_session(session_id) -> asyncio.Queue[Event]`;
  `_emit` entrega também nas filas por sessão; `stream_events` passa a ser
  `stream_events_session(session_id, stop)` e o `pump` de `turns.py` deixa de
  filtrar por `session_id` (não rouba mais nada);
- `_drain_events()` só limpa a fila por sessão do turno que está começando.

Com isso, segundo plano pode rodar **sem** `RemoteUI`: o job consome a fila da
sua sessão, resume o resultado, grava `runs`/`bot_messages` e, se o bot estiver
aberto na tela, emite um evento `bot_activity` (não os deltas) — a tela mostra
"Ada abriu PR #123", não um streaming de 200 linhas.

**Sobreposição de trabalho**: o agendador hoje pula o ciclo se `self._busy`.
Com bots, a regra passa a ser por bot: `bot_busy(bot)` bloqueia automação
daquele bot (nunca dois turnos na mesma sessão), mas Ariana não espera a Ada.

### 4.6 Bots conversando entre si

Base mínima, desenhada para não virar bola de neve:

- `BotBus` (in-process, `gaia/bots/bus.py`): `ask(from_bot, to_bot, question,
  depth) -> str`. Roda na sessão do destino, com a allowlist dele, enfileirando
  no mesmo lock por bot (se o destino está ocupado, espera com timeout).
- Ferramenta `falar_com_bot(to, question)`: existe só nos bots com
  `can_message` não vazio (`gaia` → todos; `donna` → `gaia` só). `to` limitado a
  `bot.can_message`; `depth` máximo 2; `to == from` recusado; orçamento de
  tokens por chamada (`max_steps` menor, `max_tokens` menor).
- Auditoria: cada chamada grava uma linha em `bot_messages` (kind
  `inter_bot`) e um turno `context` no histórico dos dois lados. A tela
  consegue mostrar "Gaia perguntou à Donna" sem misturar as bolhas.
- Falha do destino (bot ocupado, erro, timeout) volta como resultado de
  ferramenta com erro legível: o chamador decide o que dizer. Nunca silêncio.

### 4.7 UI (painel web)

A estrutura de telas existente é reaproveitada; o hash é a fonte de verdade.

```
#b/<bot>        conversa daquele bot (default #b/gaia)
#automacoes     automações, agrupadas por bot
#backlog        backlog (Ada)
#control        painel administrativo (token próprio, inalterado)
```

- **Rail à esquerda**: inicial em círculo, **nome completo** ("Donna Haraway"
  — é o que define o bot para você; o `id` só aparece no tooltip e na config),
  badge de não-lido, indicador de "rodando". Clique = `location.hash =
  "#b/<id>"` (F5 e voltar continuam). Com cinco bots o rail não precisa recolher;
  se crescer, colapsa para iniciais com o nome no tooltip.
- **Troca de bot**: `POST /bot/select {id}` → o motor troca conversa+sessão e
  devolve `{bot, engine, history, sessions}`; o cliente redesenha as bolhas
  (mesmo caminho de `/session/open`).
- **Eventos** ganham `bot`. O cliente ignora eventos de bot que não é o atual,
  exceto `bot_activity`/`notice`, que vão para o feed de atividade.
- **Atividade**: `GET /bots/activity?limit=` devolve os últimos `runs` +
  `bot_messages` com `bot`, `status`, `when`, `title`. O card no aside mostra os
  cinco bots com a última atividade (o mesmo componente do aside hoje, um por
  bot). "Rodar agora" continua em `/automations/run`.
- **Nova thread**: "+ nova" já existe por modo; passa a ser por bot.
- **Sessões**: `GET /sessions` filtra pelo bot ativo (já é por persona hoje).

A TUI não é caminho crítico (está em processo de aposentadoria —
`2026-11-23-aposentar-tui.md`), mas `/bot` entra no catálogo de comandos para
não divergir.

### 4.8 API (rotas novas, token do chat)

| método | rota | devolve |
|---|---|---|
| `GET` | `/bots` | `[{id, name, role, tools, workspace_label, unread, busy}]` |
| `POST` | `/bot/select` | `{bot, engine, history, sessions}` |
| `GET` | `/bots/activity` | `{messages: [...], runs: [...]}` |
| `POST` | `/bots/seen` | `{ok, unread: 0}` |

Regra mantida: `run`/pausar continua no token do **chat**; credencial e
admin continuam só em `/control/api/*` (`GAIA_CONTROL_TOKEN`).

## 5. Modelo de dados — schema v12

```sql
-- atribuicao: automacao e execucao pertencem a um bot (). vazio = `gaia`.
ALTER TABLE automations ADD COLUMN bot TEXT NOT NULL DEFAULT '';
ALTER TABLE runs        ADD COLUMN bot TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_runs_bot ON runs(bot, started_at DESC);

-- feed do painel: resultado de segundo plano e recados entre bots
CREATE TABLE IF NOT EXISTS bot_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'job',   -- job | inter_bot | aviso
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    seen INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_messages_bot
    ON bot_messages(bot, seen, created_at DESC);
```

Migração é `ALTER TABLE` idempotente (mesmo padrão da v10/v11). Nenhuma tabela
existente muda de semântica; `mode` continua sendo a chave do histórico.

## 6. Fluxos

**Turno interativo**

```
web (#b/donna) -> POST /bot/select -> Orchestrator.select_bot (troca conversa+sessao)
web -> POST /turn -> turns.py compose_system(bot) -> AgentClient.prompt_async
     policy do bot -> _run_tool recusa fora da allowlist/escopo
     eventos (com bot) -> SSE -> tela desenha so o bot ativo
```

**Segundo plano (Ariana)**

```
scheduler_tick -> automation bot=ariana due? -> claim
  -> handler market (deterministico) OU bot_task(prompt)
  -> runs(bot=ariana) + bot_messages(ariana, "3 altas, 1 alerta PETR4")
  -> SSE bot_activity -> badge de nao-lido na sidebar
```

**Inter-bot**

```
turno da Gaia -> tool falar_com_bot("donna", "status do capitulo 2?")
  -> Bus.ask(depth=1) -> sessao da Donna (policy dela) -> resposta
  -> volta como resultado de ferramenta -> Gaia responde a Talita
```

## 7. Plano por fases

Cada fase fecha verde (`pytest`, `ruff check gaia tests`, `mypy`) e é
commitável sozinha.

### Fase 0 — fundação (pré-requisito, sem UI nova)

> **Feita** (plano executável em `2026-11-24-bots-fase0.md`): eventos por
> sessão, `SessionPolicy` + `ToolContext.scope`, schema v12 e
> `secondbrain.text_extensions`. O índice também ganhou título por
> `\section`/`\chapter` para o `.tex` da Donna.

1. **Eventos por sessão** — `AgentClient.subscribe_session` +
   `_emit` com fan-out + `_drain_events(session_id)`; `turns.py` passa a usar a
   fila da sessão. *Teste*: dois `prompt_async` simultâneos em sessões
   diferentes recebem exatamente os próprios eventos (hoje um perde).
2. **Política por sessão** — `SessionPolicy` (tools/directory/scope) em
   `AgentSession`, `set_session_policy`, `_run_tool` com contexto efetivo,
   `ToolContext.scope` + teste de `resolve` recusando fora do escopo.
   *Teste*: sessão com allowlist não executa `bash`; `read` fora do escopo
   devolve `ToolError`; sessão retomada do disco mantém a política.
3. **Schema v12** — colunas `bot` + `bot_messages` + `MemoryStore` métodos
   (`add_bot_message`, `list_bot_messages`, `mark_bot_seen`, `unread_by_bot`).
   *Teste*: migração idempotente, ordem e contagem do feed.
4. **Índice com extensão configurável** — `secondbrain.text_extensions`
   (padrão `[".md"]`); `_iter_markdown` passa a aceitar as extensões da
   config e a guardar o texto cru para `.tex`/`.txt`/`.bib`. Respeita
   `exclude` e ignora dotdir (`.git`, `.venv`) como hoje.
   *Teste*: árvore fake com `.md`/`.tex`/`.docx`; só os configurados entram,
   `.docx` continua fora, reindex remove arquivo apagado. Sem dependência nova.

### Fase 1 — MVP: bots isolados e selecionáveis

> **Feita** (registry, `Config.bots`, `select_bot`/`/bot`, `/bots`/`/bot/select`
> e o clone da dissertação). `gaia/bots/registry.py` e o TOML `[bots.<id>]` sao
> a fonte das personas; `controller/modes.py` virou shim com as chaves antigas
> (`assistente`→`gaia`, `pesquisadora`→`donna`, `trabalhadora`→`sabrina`,
> `investidora`→`ariana`) resolvendo por alias, inclusive nas conversas antigas.
> A politica da sessao e aplicada no `_open_session` em toda ativacao (e na
> retomada), `/modes` e `POST /mode` continuam existindo como alias das rotas
> novas, e o `GET /health`/`/engine` levam `bot`. O clone da dissertação virou
> `talitadeoa/dissertacao` (commit `581db92`), com `~/SecondBrain/pesquisa`
> migrado e **sem** symlink: `[bots.donna] scope = "~/dissertacao"`.
> Fora do que o plano pedia: um bot bem configurado cuja pasta ainda nao existe
> fica **indisponivel** (409 + motivo) em vez de derrubar o processo; TOML
> invalido continua levantando no boot.

4. **Registry + TOML** (`gaia/bots/registry.py`, `Config.bots`) com validação;
   `controller/modes.py` como shim (mesmas chaves de persona + aliases novos).
   *Teste*: defaults válidos; TOML sobrepõe; tool inexistente levanta erro;
   `parse_mode_switch("vira Donna")`.
5. **`Orchestrator.select_bot(id)`** aplicando política à sessão no
   `_open_session` (workspace, tools, modelo) e gravando
   `mode = bot.id`. `/bot` no `command_catalog.py` + despacho em `commands.py`
   (o teste de catálogo×AST exige os dois).
   *Teste*: trocar para `donna` cria conversa `mode=donna`, chama
   `set_session_policy` com o workspace certo; bot desconhecido não cai no
   padrão.
6. **Rotas `/bots` e `/bot/select`** no `Target` + `server.py`, com `bot` no
   `/health` e no `engine`. *Teste*: contrato em `test_serve_web.py`
   (id sem duplicata, seletor existente) e `test_serve_app.py` (409 ocupado).
7. **Clone da dissertação** (passo operacional, não de código):
   `git clone git@github.com:talitadeoa/dissertacao.git ~/dissertacao`, mover
   o conteúdo de `~/SecondBrain/pesquisa` para lá (preservando
   `capitulos/debate publico/*.md` e `_templates/`), commit + push, e decidir
   §4.4a (symlink ou `scope` separado). Sem esse passo a Donna sobe sem
   workspace e o validador do registry recusa — que é o comportamento
   desejado.

### Fase 2 — sidebar e atividade (fecha o MVP visível)

8. **Sidebar + `#b/<bot>`** no `index.html` (rail, badge, indicador de rodando;
   `corpo` do chat redesenha em `/bot/select`). *Teste*: `test_serve_web.py`
   confere hash→tela, ids e que o rail é montado a partir de `/bots`.
9. **Feed de atividade + não-lido** (`GET /bots/activity`, `POST /bots/seen`,
   evento `bot_activity` no `tratarEvento`). *Teste*: evento com `bot` alheio
   não entra na conversa; badge zera.

### Fase 3 — automações por bot

10. **`automations.bot`/`runs.bot`** no seed (`describe`), no `render`, no
    `/automations` (agrupado) e no scheduler (gate por bot, não global).
    *Teste*: Ariana e Ada devidas no mesmo minuto rodam; automação da Ariana
    não roda com a sessão da Ariana ocupada e não bloqueia a Ada.
11. **Ariana entrega no painel** (§4.5a) — `market:watchlist` grava
    `bot_messages` além do Telegram opcional; `requires` perde
    `telegram.enabled`; `_automation_market` deixa de exigir Telegram; o
    `runs.detail` passa a carregar o resumo. *Teste*: sem Telegram a automação
    roda e o feed recebe a mensagem; com Telegram, os dois; cotação
    indisponível vira `detail` com o ticker, não silêncio.
12. **Skill `bot_task`** + `run_bot_task(bot_id, prompt)` consumindo a fila da
    sessão, gravando `runs` + `bot_messages`; `criar_automacao` aceita
    `bot` na lista fechada. *Teste*: job grava mensagem, erro vira `runs.failed`
    com `error_kind` e mensagem no feed.
13. **Ada no backlog** — `BacklogWorker` etiqueta `bot=ada`; o PR aberto vira
    `bot_messages`. *Teste*: `test_backlog.py` com o campo novo.
14. **Sabrina em LaTeX** — a persona/diretriz passa a exigir `.tex` (base
    `cv-pt.tex` intocável, variante `cv-pt-<empresa>.tex`, `pdflatex` pelo
    `bash` aprovado, nunca editar a base nem inventar experiência).
    *Teste*: diretriz da Sabrina menciona `cv-pt.tex` e `pdflatex`;
    `scope` dela é `~/SecondBrain/cv`.

### Fase 4 — bots conversando

15. **`BotBus` + ferramenta `falar_com_bot`** com profundidade, allowlist de
    destino e auditoria. *Teste*: chamada de 2 níveis funciona, 3 níveis recusa,
    destino desconhecido recusa, ciclo A→B→A para.
16. **Tela**: agrupar `inter_bot` no feed. *Teste* de contrato.

## 8. Escopo cortado (YAGNI deliberado)

- **Processo por bot.** Multiplica escritor de `gaia.db`, poller do Telegram e
  memória. Fora.
- **Paralelismo de chat interativo.** Um turno por vez, como hoje. Quando doer
  de verdade, a Fase 0 já deixou o caminho pronto (eventos por sessão).
- **MCP / plugin por bot.** A allowlist resolve o problema real (o bot não
  chama o que não deve); infra de plugin é outro projeto.
- **Memória vetorial por bot.** Depende do schema v6, que não existe.
- **Roteador automático de mensagem.** Só sugestão, e depois.
- **Auto-merge de PR da Ada.** PR aberto, revisão humana. Sempre.

## 9. Critérios de aceite do MVP (Fases 0–2)

1. Cinco bots na sidebar; cada um abre a própria thread, com histórico próprio
   preservado depois de F5 e de restart do `serve`.
2. Donna não consegue escrever fora do `scope` nem rodar `pi` — verificado por
   teste, não por prompt. (`bash` ela **tem**: é o que compila LaTeX e faz o
   PR no repo da dissertação; a aprovação por turno continua valendo.)
3. Ariana não vê a conversa da Ada em `/history`, `/sessions` nem no prompt.
4. Um segundo plano rodando não rouba evento nem atrasa o turno interativo de
   outro bot.
5. `pytest`, `ruff check gaia tests` e `mypy` verdes; nenhuma rota nova sem
   teste de contrato.

## 10. Decisões tomadas (todas fechadas)

1. **Donna** — repo privado `talitadeoa/dissertacao` (branch `main`, ainda
   vazio), clone em `~/dissertacao`, conteúdo de `~/SecondBrain/pesquisa` migra
   para lá. Ver §4.4a. Único resto é setup da Fase 1: symlink vs. `scope`
   separado.
2. **Ariana** — **determinística**. `market:watchlist` continua sem uma linha
   de modelo (já é assim em `briefing/market.py`), e a entrega principal passa a
   ser o painel (`bot_messages`), com Telegram opcional. Regra fixa: leitura
   analítica só como `bot_task` semanal opt-in, nunca no laço. Ver §4.5a.
3. **Sabrina** — CV em **LaTeX**: base `cv-pt.tex` intocável, variante
   `cv-pt-<empresa>.tex`, `pdflatex` pelo `bash` aprovado. É o que já existe em
   `~/SecondBrain/cv` (8 `.tex` + PDFs). `scope` = `~/SecondBrain/cv`.
4. **Ada** — abre PR **direto a partir dos itens do backlog**, pelo caminho que
   já existe (`BacklogJob`: clone → branch → `pi` → commit → push → `gh pr
   create`). O chat dela serve para acompanhar/revisar, não para substituir o
   `pi`.
5. **Nomes** — `id` curto e definitivo (`gaia`, `donna`, `ariana`, `sabrina`,
   `ada`), `name` completo na interface ("Donna Haraway", "Ariana Grande",
   "Sabrina Carpenter", "Ada Lovelace"). Como o `id` vira
   `conversations.mode`, ele nasce estável; renomear depois exigiria
   `MODE_KEY_RENAMES`.
