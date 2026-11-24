# Gaia

Assistente pessoal de voz, local, com agente próprio e Second Brain em Markdown.
Roda no macOS e no Linux (inclusive num mini PC headless, comandado pelo
Telegram), fala e ouve em português, executa ferramentas no computador e guarda
memória entre sessões.

- **STT local** (`faster-whisper`, pt-BR) — o áudio não sai da máquina.
- **Agente próprio** — loop de ferramentas, sessões, permissões e streaming.
- **Second Brain** — notas Markdown indexadas com SQLite FTS5.
- **Proatividade** — briefing diário, lembretes, rotina de vagas.

Para trabalhar no código, veja [`AGENTS.md`](AGENTS.md): comandos, mapa dos
módulos, invariantes e armadilhas. O índice da documentação está em
[`docs/README.md`](docs/README.md).

---

## Requisitos

- macOS ou Linux (Ubuntu/Debian testado; roda também headless, sem tela).
- Python **3.12** (travado: o `faster-whisper` não tem wheel para 3.13+).
- [`uv`](https://docs.astral.sh/uv/), `ffmpeg` e PortAudio.
- Uma chave de API de algum provedor de modelo.

```bash
# macOS
brew install ffmpeg portaudio

# Ubuntu/Debian
sudo apt install ffmpeg libportaudio2 libsndfile1
```

A reprodução de áudio escolhe sozinha o player disponível: `afplay` no macOS;
`ffplay`, `pw-play`, `paplay`, `mpv` ou `aplay` no Linux. A voz do sistema
(`/voz say`) é exclusiva do macOS — no Linux use `/voz edge`.

## Instalação

```bash
git clone https://github.com/talitadeoa/gaia.git
cd gaia

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# opcional: comando `gaia` no PATH
uv tool install --python 3.12 --editable .
```

No Ubuntu/Debian tem um script que faz tudo: dependências de sistema, `uv`,
Python 3.12, venv, o comando `gaia` em `~/.local/bin` e um atalho de tela cheia
para tablet.

```bash
./scripts/install-linux.sh              # instala tudo
./scripts/install-linux.sh --no-apt     # sem sudo/apt
./scripts/install-linux.sh --no-launcher # sem o atalho gráfico
```

Dependências opcionais:

```bash
# navegador automatizado
uv pip install --python .venv/bin/python -e ".[browser]"
.venv/bin/playwright install chromium
```

## Primeiro uso

```bash
gaia config --init   # cria ~/.config/gaia/config.toml
gaia doctor          # confere ambiente, provedor, voz e Second Brain
gaia                 # abre a interface
```

Chaves de API vão em `~/.config/gaia/.env` (ou no ambiente), nunca no TOML:

```bash
GAIA_AGENT_API_KEY=...          # vale para o provedor configurado
# ou a nativa do provedor: COMMAND_CODE_API_KEY, DEEPSEEK_API_KEY,
# OPENAI_API_KEY, OPENROUTER_API_KEY, ANTHROPIC_API_KEY
```

As variáveis conhecidas estão em `.env.example`, comentadas uma a uma.

## Modelo

```toml
[agent]
# Presets: commandcode | deepseek | openai | openrouter | ollama | anthropic |
# opencode | groq | mistral | xai | opendesign | custom
provider = "commandcode"
model = "deepseek/deepseek-v4.1-flash"
base_url = ""             # obrigatório apenas em "custom"
```

O padrão é a **Command Code**, que é um gateway e por isso é o jeito fácil de
alternar: um provedor, todos os modelos. A chave vem do `cmd` CLI
(`~/.commandcode/auth.json`) ou de `COMMAND_CODE_API_KEY` — normalmente nem
precisa colar nada. Para os modelos Claude dela, use
`provider = "commandcode-anthropic"` (mesma chave, wire Anthropic). Requer
plano com acesso à Provider API.

O **OpenDesign** (`provider = "opendesign"`, `base_url`
`https://amr-link.open-design.ai/v1`) é um gateway compatível com OpenAI, o
mesmo que o pi usa: um endpoint, os modelos de todas as famílias (Claude, GPT,
Gemini, DeepSeek, GLM, Kimi, Qwen...). A chave é a mesma do pi, lida de
`~/.pi/agent/auth.json`; também vale `OPENDESIGN_API_KEY`.

| Atalho | Vai para | Consulta a rede? |
|---|---|---|
| `/modelo cc` | Command Code, com o último modelo usado lá | não |
| `/modelo ds` | DeepSeek direta (`deepseek-flash`, o V4.1 Flash deles) | não |
| `/modelo od` | OpenDesign (`deepseek-v4.1-flash`, o gateway do pi) | não |
| `/modelo glm` | `commandcode/z-ai/glm-5.3-flash` | não |
| `/modelo turbo` | `commandcode/deepseek/deepseek-v4.1-flash-fast` | não |
| `/modelo zen` | OpenCode Zen, com o último modelo usado lá | não |

Os atalhos saem de uma tabela local: trocam na hora, sem `GET /models`. Cada
provedor lembra o último modelo usado nele, então `cc → ds → cc` volta para
onde você estava. `/modelo` sem argumento mostra os atalhos e a lista ao vivo;
`/modelo 3` e `/modelo openai/gpt-4o` também funcionam, e `F6` abre o seletor.
A escolha é salva no `config.toml` (com `.bak`) e vale no turno seguinte.
`GAIA_AGENT_PROVIDER` e `GAIA_AGENT_MODEL` no ambiente têm prioridade.

O `fast_model` (pedidos curtos) acompanha a troca: cada preset declara o seu.
Um `fast_model` de outro provedor é descartado em vez de ir no request (era 400
na DeepSeek direta).

## Interface

```bash
gaia                 # TUI (padrão; config interface.ui)
gaia -old            # terminal clássico (Rich)
gaia --ui terminal   # força o terminal
gaia --watch         # reinicia quando os .py mudam
```

Numa máquina headless (mini PC, servidor) a TUI só serve para manutenção por
SSH. Há dois canais possíveis, e **só um processo pode ser dono do banco e do
Telegram**:

- `gaia telegram serve` — o Telegram responde texto e áudio e entrega a
  iniciativa proativa no chat, sem depender de tela.
- `gaia serve` — o motor num processo e as telas em outros (ver abaixo).

## Servidor e cliente remoto

`gaia serve` é a arquitetura de referência em produção: **um** processo é dono
do `gaia.db`, do Second Brain, dos workers proativos e do poller do Telegram, e
publica HTTP+SSE para clientes finos.

```bash
gaia serve                     # escuta no host/porta de [serve]
gaia serve --host 127.0.0.1 --port 8787
gaia serve --no-telegram       # não sobe o poller junto
gaia --remote http://gaia:8787 # cliente interativo (TUI remota)
gaia --remote http://gaia:8787 ask "resuma o dia"
```

- Rotas do chat: `GET /`, `/health`, `/session`, `/instances`, `/engine`,
  `/modes`, `/languages`, `/commands`, `/voice`, `/sessions`, `/history`,
  `/automations`, `/providers`, `/models`, `/search`,
  `/events` (SSE); `POST /session`, `/turn`, `/permission/{id}`, `/abort`,
  `/model`, `/mode`, `/language`, `/chinese`, `/automations/set`, `/automations/run`,
  `/proactive`, `/backlog/sync`, `/backlog/add`, `/backlog/run`,
  `/backlog/repo`,
  `/voice/listen`, `/voice/speak`, `/voice/stop`,
  `/session/open`, `/session/new`; `DELETE /session`.
- Escutar fora do loopback **exige** `GAIA_SERVE_TOKEN`; a guarda recusa subir
  caso contrário.
- A página web em `GET /` é um cliente completo: streaming, ferramentas,
  aprovação por botão, busca, troca de modelo e de modo, sessões anteriores e
  as próximas automações. Digitar `/` abre a paleta de comandos (mesmo catálogo
  da ajuda do terminal, filtrada enquanto se digita; Tab/Enter completam, Esc
  fecha) e `/ajuda` imprime a lista no chat. O header tem `+ nova` (sessão
  nova), `sessões` (conversas anteriores) e `idiomas` (abre o dialog com os
  idiomas do eco: 中文, English e Français; escolher um liga a tradução embaixo
  de cada mensagem e `desligar o eco` desliga — o mesmo `/chines` da TUI,
  estado do processo, não do TOML). `GET /languages` devolve a lista
  (`key`/`label`/`code`/`current`/`enabled`) e `POST /language` com
  `{"key": "en"}` escolhe/liga (ou `"enabled": false` desliga). O idioma do
  eco também fica em `[interface] echo_language` (`GAIA_ECHO_LANGUAGE`,
  `zh`/`en`/`fr`); o tradutor é o mesmo do `/chines` (`translate_echo`).
- **Tela de automações no web.** O botão `automações` do header leva a uma tela
  própria (a URL vira `#automacoes`; F5 e o voltar do navegador continuam nela)
  e o composer fica escondido ali. Cada rotina aparece como um cartão com
  nome, checkbox **ativa/pausada**, janela (`at–until`), dias, entrega, próxima
  execução, status em português (`agendada`/`rodando`/`deu certo`/`falhou`/
  `não rodou`/`pausada`/`desligada`), **última execução com data e hora** (e a
  tentativa, quando houve retry) e o erro específico quando falhou. No topo, um
  cartão da proatividade com o mesmo checkbox.

  Os dois checkboxes gravam de verdade: `POST /automations/set` (`{id,
  enabled}`) pausa/retoma a rotina na tabela `automations`, que é o que o
  agendador obedece a cada ciclo; `POST /proactive` (`{enabled}`) grava
  `[proactive] enabled` no TOML e liga/desliga o laço na hora (se o valor vier
  de `GAIA_PROACTIVE_ENABLED` no `.env`, a rota responde 422 explicando, em vez
  de divergir do arquivo). As duas usam o **token do chat**: pausar uma rotina é
  operação, não administração de credencial — o `/control` continua separado.
  Sem `GAIA_CONTROL_TOKEN` o motor não tem agenda e a tela avisa; o card do
  aside mostra as próximas execuções. Vagas e carteira aparecem aí como
  *Pesquisa de vagas* e *Resumo da carteira*.

  Cada cartão tem **rodar agora** (`POST /automations/run`), que executa a
  rotina de verdade sem esperar a janela — com confirmação, porque a busca de
  vagas/cotações faz rede e pode mandar mensagem no Telegram. O run fica
  registrado com `trigger=manual` e o resultado (ou o erro) aparece na hora; o
  botão mostra `rodando…` enquanto espera e responde 409 se o motor estiver num
  turno. Rodar uma rotina pausada é permitido: o clique é explícito.
- **Automação pelo chat.** "toda sexta às 9h me lembre de estudar" cria uma
  linha nova na tabela `automations` (id `user:*`) pela ferramenta
  `criar_automacao`, que aparece no painel como qualquer outra e passa a ser
  disparada pelo agendador no horário. Quem interpreta é
  `controller/automation_builder.py`: parser de slots (dias, hora, skill alvo,
  entrega, parâmetros) e **confiança medida por regras, não pelo modelo** —
  alta (≥7) cria ativada, média (4–6) cria **pausada** e pede conferência no
  painel, baixa (<4) não cria nada e pede para reformular. Na v1 as skills
  alvo são `reminder` ("me lembre de X"), `agenda_summary` ("resumo da
  agenda") e `estudos_summary` ("resumo dos estudos"). Guardas: a skill alvo
  tem de estar ligada na config, o horário tem de ser HH:MM válido, no máximo
  10 criações automáticas por dia e **nunca** edita nem apaga automação
  existente (pedido repetido devolve a que já existe; pausar/editar é no
  painel). Ver `docs/plans/2026-11-24-automacoes-pelo-chat.md`.
- **Modo voz no web** (`🎙 voz`, aparece quando o servidor consegue transcrever).
  O microfone é o **de quem conversa**: o navegador grava WAV e manda em
  `POST /voice/upload`, e o motor transcreve com o faster-whisper local (o mini
  pode ser headless, sem microfone). Com `[serve] voice_source = "server"` o
  motor volta a gravar com o próprio microfone (`POST /voice/listen`, janela de
  15 s + VAD). `Enter` envia a transcrição sem esperar a janela; 15 s sem fala
  silenciam o microfone e só a tecla `M` (ou o botão) volta a ouvir. A resposta
  é sintetizada pelo mesmo TTS da TUI (`/voice/speak`, mp3/aiff em base64) e
  tocada no navegador — no mini headless não há caixa de som, então é a página
  que fala. Quando o áudio acaba, ela volta a ouvir; clicar de novo no botão sai
  do modo voz e volta ao chat. O botão mostra "transcrevendo…" assim que o VAD
  fecha a fala (a tela sonda `GET /voice` para isso). Sem STT o botão nem
  aparece (`GET /voice` devolve o motivo).
- **Card de backlog no web.** Abaixo das automações, um card recolhido no
  aside (contagem + `atualizar`) que **abre ao clique**; na tela `#automacoes`
  ele aparece aberto, logo abaixo das rotinas. Ele consolida dois lugares: as
  páginas da Agenda cujo título termina em `backlog` (`gaia backlog`,
  `agenda backlog`, `suite backlog` — `[backlog] agenda_suffix`) e os itens
  adicionados por texto ou voz (`POST /backlog/add`). Cada projeto é um
  collapsible com o repositório mapeado (`gaia` → `talitadeoa/gaia`, `agenda` →
  `talitadeoa/agenda`, `suite` → `talitadeoa/abin-suite`) e cada item mostra o
  status (`pendente` → `enviado ao mini` → `em execução no pi` → `PR aberto` /
  `concluído`, ou `erro` com o motivo) e o botão **executar** (`POST
  /backlog/run`), que enfileira o item na fila do mini: o pi implementa no clone,
  a Gaia faz commit/push e abre o PR com `gh`, e o card é avisado pelo evento
  SSE `backlog`.

  **A página é a fonte.** `atualizar` (`POST /backlog/sync`) relê a Agenda e, na
  mesma passada, (1) grava os itens, (2) **move** o grupo cuja página só mudou
  de nome (mesmo `page_id` ou ≥80% dos textos iguais — o PR aberto continua
  ligado ao item) e (3) **apaga** o grupo cuja página saiu da Agenda
  (`[backlog] remove_orphans`). Nada é apagado se a Agenda falhar, e a primeira
  sincronização de todas apenas grava (`backlog.agenda_seen_at`), para não
  limpar banco legado sem nunca ter lido a fonte. Item de chat não morre com a
  página.

  **Projeto sem repositório não despacha.** Não existe chute de
  `owner/<projeto>`: o grupo aparece com `sem repositório` e o formulário de item
  novo ganha o campo `dono/repo`, que grava em `[backlog.repos]` do
  `config.toml` (backup + releitura) por `POST /backlog/repo` — o mesmo caminho
  do `/backlog repo <projeto> <dono/repo>`. Itens concluídos somem do card
  depois de `[backlog] prune_days` dias (7) — some da visualização, nada é
  apagado do banco.
- **Sessão de cookie em vez de token a cada visita.** `POST /session` troca o
  `Authorization: Bearer` por um nonce em cookie `HttpOnly` (`gaia_chat`,
  `Path=/`, `SameSite=Strict`); o `Bearer` continua valendo para `gaia
  --remote` e `curl`. O nonce vive em memória: reiniciar o `serve` derruba as
  sessões. Guardar o token no `localStorage` é **opt-in** ("lembrar token
  neste navegador") — desmarcado, nenhum segredo fica no navegador e a sessão
  vale só até o cookie vencer (12 h).
- O token vai no `.env`/ambiente, nunca no TOML.
- Unidades systemd prontas em `deploy/gaia-serve.service` e
  `deploy/gaia-serve-tailnet.service.example`; no mini, `gmin serve
  install|cutover|rollback|status|logs`.

### Várias instâncias (mini e Mac, por exemplo)

O seletor de instância do header lê `instances.json` em `GAIA_DATA_DIR`
(`~/.local/share/gaia/instances.json`), escrito à mão e com `chmod 600` porque
contém segredos:

```json
[
  {"id": "mini", "name": "Mini PC", "url": "http://mini.local:8787",
   "serve_token": "...", "control_token": "..."},
  {"id": "mac", "name": "Mac Studio", "url": "http://mac.local:8787",
   "serve_token": "...", "control_token": "..."}
]
```

- `GET /instances` é **público** e nunca devolve token (só `has_serve_token`/
  `has_control_token`); o status online/offline é medido pelo servidor com
  `GET <url>/health` em paralelo e timeout curto.
- Trocar de instância no seletor **navega** para a URL escolhida: o cookie de
  sessão é por origem (`SameSite=Strict`), então não há CORS com credenciais.
  Cada origem tem a própria sessão (e o próprio "lembrar token").
- `GAIA_SERVE_INSTANCE_ID` (ou `[serve] instance_id`) marca qual item do
  arquivo é esta máquina; vazio, a marcação tenta casar pelo endereço aberto.
- Arquivo ausente ou inválido = lista vazia: a tela funciona sem seletor.

## Control Center

Painel web para configurar, supervisionar e diagnosticar o agente, servido pelo
próprio `gaia serve` em `http://<host>:<porta>/control`.

```bash
# gere o segredo do painel (separado do token do chat)
python -c "import secrets; print(secrets.token_urlsafe(32))"
# e grave em ~/.config/gaia/.env (nunca no TOML):
# GAIA_CONTROL_TOKEN=<o valor gerado>
```

O token é pedido uma vez por navegador: a tela troca o `Bearer` por uma
sessão (cookie `HttpOnly`, `Path=/control`, `SameSite=Strict`) via
`POST /control/api/session` e as chamadas seguintes usam só o cookie. O token
nunca vai na URL; `curl` e o cliente fino continuam podendo usar
`Authorization: Bearer`. Sem `GAIA_CONTROL_TOKEN`, `/control`
mostra a instrução e `/control/api/*` responde 503 — **o token do chat
(`GAIA_SERVE_TOKEN`) não vale aqui**, de propósito: ele só conversa, não
administra credenciais.

| Aba | O que mostra |
|---|---|
| **Overview** | modelo ativo, saúde dos componentes, automações e últimas execuções |
| **Models & Providers** | presets com estado da credencial (mascarada), teste de conexão real (401/402/timeout), troca de modelo |
| **Skills** | catálogo derivado do núcleo: status, quando roda, última execução, módulo |
| **Automations** | agenda governada (tabela `automations`): `next_run`, estado real, retry configurado e última execução |
| **Activity** | histórico de execuções (`runs`) filtrável por skill e status |
| **Error Center** | falhas com tipo de erro, tentativa e detalhes técnicos |
| **System Settings** | config efetiva com **origem** (`toml`/`.env`/default); campo travado quando o `.env` vence, com botão para remover o override |

Editar em System Settings grava **no `config.toml`** preservando comentários,
com `.bak` ao lado e releitura de conferência: se a gravação não bater, o
backup é restaurado e a tela mostra o erro. Segredos ficam no `.env`
(`chmod 600`, backup `.env.bak`) e só aparecem mascarados (`sk-…1234`).

O agendador é **único** e vive no `gaia serve` (mesmo processo que é dono do
banco): a cada ciclo ele lê a tabela `automations`, calcula se a rotina está
devida e chama o handler da skill. Não há cron do sistema nem APScheduler.
Cada rotina mantém a reserva `claim_meta` como segunda barreira — o painel é a
primeira, o banco continua impedindo entrega dupla se algo escapar. Além das
rotinas do registry, o agendador despacha as automacoes criadas pelo chat
(`user:*`) pela coluna `skill`: `reminder` entrega o texto, `agenda_summary`
manda o resumo do dia e `estudos_summary` o progresso dos simulados.

- **Retry:** falha de rede/desconhecido é tentada de novo com backoff
  exponencial até `retry_max` (padrão 2). `auth` e `config` **não** repetem: o
  problema é humano e insistir esconde o erro.
- **Notificação:** falha de automação com `delivery=telegram` manda uma
  mensagem curta no chat — uma por falha, não por tentativa. Falha local
  (memória, sonho) vai só para o Error Center.
- **Proatividade no `serve`:** o processo que é dono do poller entrega no chat;
  o TTS local não toca numa máquina headless.

O que ainda **não** existe: as rotas equivalentes no painel `/control`
(`PATCH/run/pause/resume` por lá; pausar, retomar e rodar agora já funcionam
na tela de automações do chat), CRUD de skills no painel, níveis de autonomia
da proatividade (`observe`/`inform`/`act`), retenção
de execuções e exportação de diagnóstico. O estado item por item está em
`docs/plans/2026-11-22-control-center-fases-cde.md`.

**Voz:** `F` fala uma vez, `V` alterna a voz, `C` liga a escuta contínua
(microfone aberto: fala → executa → responde → volta a ouvir), `M` silencia.
A fala termina sozinha depois de `audio.silence_ms` de silêncio.

**Texto:** `Enter` ou `I`. Não há etapa de confirmar transcrição — o que ela
entendeu vai direto para o agente.

| Tecla | Ação |
|---|---|
| `F1` | paleta de comandos |
| `F2` | sessões anteriores |
| `F3` / `Tab` | trocar de modo |
| `F4` | alternar seleção de texto |
| `F5` | recolher/abrir os painéis |
| `F6` | seletor de modelo |
| `Ctrl+C` | copiar seleção |
| `Ctrl+L` | limpar a tela |
| `Esc` | interromper a resposta |

Execução única, sem interface:

```bash
gaia ask "resuma o que eu anotei hoje"
gaia ask --no-voice "responda apenas: ok"
```

## Modos

Troca com `/modo <nome>`, `Tab` ou `F3`. Cada modo injeta uma diretriz no prompt
e fica salvo entre sessões.

| Modo | Para quê |
|---|---|
| `assistente` (padrão) | agenda, rotinas, prioridades, conversa |
| `pesquisadora` | mestrado, leitura, fichamento, escrita, metodologia |
| `trabalhadora` | currículo, vagas, entrevistas, negociação |
| `investidora` | bolsa, indicadores, orçamento, risco |

O padrão se configura em `[interface] default_mode`.

## Agente e ferramentas

O motor fica em `gaia/agent/` e fala direto com a API do provedor — sem
servidor intermediário, sem harness externo.

Sete ferramentas de base: `read`, `list`, `glob`, `grep`, `write`, `edit`,
`bash` — iguais em todos os modos.

Quando `agenda.env_file` tem credencial válida, entram mais cinco, específicas
da Agenda (não dependem de shell):

| Ferramenta | Para quê |
|---|---|
| `agenda_dia` | tarefas pendentes (com id), feitas e placar da rotina do dia |
| `agenda_rotinas` | rotinas cadastradas e os dias de cada uma |
| `agenda_paginas` | lista páginas da Agenda ou lê uma pelo título |
| `agenda_nova_tarefa` | cria tarefa (exige `confirm=true`) |
| `agenda_marcar_tarefa` | marca/desmarca por id ou texto (exige `confirm=true`) |

E uma de leitura de planilhas, quando o SecondBrain está configurado:

| Ferramenta | Para quê |
|---|---|
| `planilha` | lista abas e tamanhos, mostra uma faixa de linhas ou busca um termo nos `.xlsx` de `~/SecondBrain/planilhas` |

O `.xlsx` é lido com a biblioteca padrão (`zipfile` + XML), sem `openpyxl` e
sem shell — funciona no canal do Telegram mesmo com `shell_access = false`.

Com `estudos.env_file` válido (credenciais do Abin Suite), entram três de
leitura do progresso dos simulados — também só leitura, sem shell:

| Ferramenta | Para quê |
|---|---|
| `estudos_progresso` | simulados, minutos e nota média da semana, meta em dia ou atrasada |
| `estudos_metas` | metas semanais (simulados, minutos, nota alvo) |
| `estudos_pendentes` | itens do caderno de erros vencidos para revisar hoje |

```toml
[agent]
tools = true
auto_approve = false              # escrita e shell pedem confirmação
allow_outside_project = true      # necessário para alcançar o Second Brain
max_steps = 32
request_timeout = 900
stall_timeout = 90
```

- Ações que alteram algo pedem confirmação (`o` uma vez, `a` sempre, `r` rejeita).
- O histórico histórico é comprimido num resumo ao passar de
  `max_history_messages` / `max_history_chars`.
- O prompt é montado em duas camadas: prefixo estável (persona e preferências,
  aproveitado pelo cache) e bloco dinâmico (data, hora, modo).
- `/status` mostra modelo, sessão e tokens (quando o provedor informa usage);
  `/tokens` lista os últimos turnos.

## Second Brain

Uma pasta Markdown que é sua, indexada localmente.

```markdown
---
tags: [mestrado, campo]
---

# Diário de campo — aldeia

Discussão sobre parentesco com [[Nota sobre Lévi-Strauss]].
#antropologia
```

- Índice FTS5 incremental, offline, em `sb_index.db` dentro do diretório de
  dados (`~/.local/share/gaia` por padrão; `GAIA_DATA_DIR` muda).
- Links `[[Título]]`, tags no frontmatter e `#tag` no corpo.
- Criar nota é aditivo; editar e excluir pedem confirmação, geram backup em
  `.gaia/backups` e movem para `.gaia/trash`.
- Nunca escreve fora da raiz configurada.
- A busca só entra no turno quando a mensagem tem pista de retomada ("lembra",
  "aquele projeto"), com poucos trechos e teto de caracteres.

```toml
[secondbrain]
path = "~/SecondBrain"
context_results = 2
```

```bash
gaia index --force
gaia search parentesco
gaia note "Diário de campo"
```

**Consolidação diária:** às `memory.at` (23:00), os turnos do dia viram
`Memoria/Memória AAAA-MM-DD.md` (Síntese, Decisões, Conversas, Registros).
Consolida o pendente ao sair e recupera o dia anterior se você abrir sem ter
fechado. Se o modelo falhar, cai num resumo local determinístico.

**Sonho:** na janela `memory.dream_at`–`memory.dream_until` (02:00–08:00), se
a Gaia estiver ociosa, ela sonha. O sonho lê a memória do dia, procura no
índice as memórias antigas que ressoam com ele e escreve
`Sonhos/Sonho AAAA-MM-DD.md` — parágrafo onírico, **Ecos do dia**,
**Ressonâncias**, **Pergunta para amanhã** e a lista de memórias revisitadas
(no máximo dois trechos por nota, sem repetir sonhos nem a memória do dia).
A nota já nasce ligada à memória do dia (`[[Memória AAAA-MM-DD]]`).

Os sonhos ficam em pasta própria, separada do diário consolidado, para que
`/buscar` não misture sonho com registro. De madrugada o sonho é sobre o dia
que acabou; à noite (se a janela começar antes da meia-noite) é sobre o dia em
curso. Se o modelo falhar, a nota sai de qualquer forma com a costura
determinística do dia com as notas antigas.

**No dia seguinte** a Gaia avisa, em uma linha, com o que sonhou e a pergunta
que ficou — sem segunda chamada ao modelo, fora das quiet hours, uma vez por
dia. O aviso sai pelo canal em uso (TUI ou Telegram) e não conta como
iniciativa proativa.

```toml
[memory]
dream_enabled = true
dream_at = "02:00"        # início da janela noturna (pode virar a meia-noite)
dream_until = "08:00"     # fim da janela
dream_folder = "Sonhos"   # pasta própria, fora do diário consolidado
dream_notify = true       # avisa no dia seguinte
dream_notify_at = "08:00" # depois disso o aviso pode sair
retry_minutes = 10.0      # espera entre tentativas se o envio falhar
```

Para sonhar na hora — sobre hoje, ontem ou um dia específico:

```
/sonhar                  # hoje
/sonhar ontem            # recupera a noite passada
/sonhar 2026-11-09 forcar
/sonhar descartar ontem  # esquece o sonho (nota vai para a lixeira)
```

O sonho retoma os dois sonhos anteriores como fio narrativo, então há
continuidade entre as noites. Se o modelo devolver texto fora do formato
pedido, a prosa dele é mantida e a moldura (Ecos, Ressonâncias, Pergunta) é
reconstruída — o aviso nunca fica sem conteúdo sem avisar no log.

## Automações

Horário e determinísticas na maior parte — sem custo de token, **exceto** o
insight do fecho da noite (`briefing.evening_insight`) e o comentário opcional
do briefing (`briefing.personalize`), que chamam o modelo.

| Rotina | Quando | O que faz |
|---|---|---|
| Briefing da manhã | 08:00 | tarefas do dia, sem rotinas |
| Aviso do sonho | 08:00 (quando as quiet hours acabam) | o sonho da noite, em uma linha |
| Resumo do meio-dia | 12:00 (até 18:00) | feito e pendente, sem rotinas |
| Fecho da noite | 18:00 (até 23:00) | feito, não feito, rotina matinal e insight do modelo |
| Carteira | seg/qua/sex | cotações do que está na carteira |
| Vagas | segunda 09:00 (até 18:00) | busca, filtra, deduplica, pontua e envia |

```toml
[briefing]
enabled = true
morning_at = "08:00"
noon_at = "12:00"
noon_until = "18:00"
morning_routine = "matinal"   # usada no fecho da noite
max_tasks = 15
evening_enabled = true
evening_at = "18:00"
evening_until = "23:00"
evening_insight = true        # chama o modelo
market_enabled = true
market_days = [0, 2, 4]       # seg/qua/sex
market_at = "12:00"

[career]
enabled = true
days = [0]      # 0 = segunda
at = "09:00"
until = "18:00"
max_items = 8

[proactive]
idle_minutes = 45
daily_max = 4
quiet_hours = "23:00-08:00"

[memory]
at = "23:00"
use_llm = true
dream_enabled = true
dream_at = "02:00"
dream_until = "08:00"
dream_notify = true
dream_notify_at = "08:00"
```

O método da rotina de vagas (fontes, filtros, rubrica de score) vive em
`~/SecondBrain/cv/playbook-vagas.md`; o estado, em `career/state.json` dentro do
diretório de dados (`~/.local/share/gaia` por padrão).

```bash
gaia career status
gaia career buscar [--json]   # busca sem enviar
gaia career enviar            # busca e manda no Telegram
```

## Integrações

**Telegram**

```bash
gaia telegram updates    # descobre o chat_id
gaia telegram me
gaia telegram send "bom dia" [--chat ID]
gaia telegram audio voz.mp3 [--voice] [--chat ID]
gaia telegram poll [--echo]   # long polling, sem o agente
gaia telegram serve      # escuta texto e áudio e responde
```

`auto_approve = true` (com `allowed_chat_id` fixado) aprova no chat as
ferramentas que pedem confirmação — inclusive as `agenda_*`, que é como a Gaia
consulta e altera a Agenda pelo Telegram.

`bash`, `write` e `edit` ficam **negados por padrão mesmo com `auto_approve`**:
`shell_access = true` é o único jeito de liberar, e só vale junto de
`auto_approve` + `allowed_chat_id`. Com o padrão, quem controlar o chat mexe na
Agenda — não roda comando na máquina.

```toml
[telegram]
auto_approve = true
shell_access = false   # mantenha desligado: shell remoto é o risco maior
```

**Audiobooks** (PDF/texto → MP3 narrado com voz neural)

Três caminhos, todos com a mesma voz e a mesma limpeza de texto:

```bash
# 1) arquivo no chat: manda o PDF/TXT como documento para o bot
# 2) texto colado: /audiobook <texto> (mín. 200 caracteres)
# 3) pasta: joga os PDFs em data/audiobooks/inbox e manda /audiobooks
```

No terminal/app: `/audiobook ~/livro.pdf "Meu Livro"` e `/audiobooks`.
O MP3 sai em `data/audiobooks/outbox` (com ID3, título, álbum e faixas
`CHAP` por capítulo) e é enviado pelo Telegram quando termina. A voz padrão é
`pt-BR-ThalitaNeural`; a limpeza remove Markdown, URLs, números de página e
cabeçalhos repetidos, junta palavras quebradas e marca capítulos
(`Capitulo N`, `Parte N`, títulos em CAPS).

```toml
[audiobook]
voice = "pt-BR-ThalitaNeural"   # pt-BR-FranciscaNeural, pt-BR-AntonioNeural, ...
rate = "-4%"                    # um pouco mais lento lê melhor
inbox = ""                      # vazio => <data_dir>/audiobooks/inbox
outbox = ""                     # vazio => <data_dir>/audiobooks/outbox
watch_minutes = 0.0             # >0 = confere a inbox sozinho (ex.: 5)
```

PDF escaneado (sem camada de texto) não é lido: a Gaia avisa e move o arquivo
para `inbox/erros`. Passe por um OCR ou envie `.txt`.

**Abin Suite (progresso de estudos)**

O Abin Suite guarda tudo num Supabase próprio (separado da Agenda); a Gaia só
**lê** a tabela `study_items` (collections `results`, `goals` e `review`) e
nunca escreve no banco do app.

```toml
[estudos]
enabled = true
env_file = "~/abin-suite/.env"   # VITE_SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ESTUDOS_USER_ID
poll_minutes = 60
remind_when_late = true          # cobra quando nao estudou e a meta esta atrasada
remind_at = "20:00"              # so a partir daqui, uma vez por dia
study_days = [6, 2, 4, 5]        # 0=seg..6=dom
```

As credenciais ficam no `.env` do projeto do app:

```bash
VITE_SUPABASE_URL=https://SEU-PROJETO.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...    # secret/service_role do projeto (nunca no cliente)
ESTUDOS_USER_ID=...              # uuid da conta no Supabase (auth.users)
```

Sem os três, o cliente fica desabilitado (as ferramentas `estudos_*` não
aparecem e a cobrança proativa não roda). A cobrança usa `claim_meta`
(uma vez por dia), respeita as quiet hours e só dispara se você ainda não fez
nenhum simulado hoje com a meta atrasada.

**WhatsApp** (Cloud API da Meta)

```bash
gaia whatsapp send "bom dia"
gaia whatsapp audio voz.mp3
gaia whatsapp serve
```

**Navegador** (Playwright, perfil persistente com seus logins)

```bash
gaia browser login https://exemplo.com
gaia browser open https://exemplo.com
gaia browser text https://exemplo.com
gaia browser html https://exemplo.com
gaia browser screenshot https://exemplo.com captura.png
```

## Comandos dentro do app

| Comando | Função |
|---|---|
| `/ajuda` · `/status` · `/tokens` | comandos · provedor/modelo/sessão · consumo |
| `/buscar <termo>` · `/nota <título>` | Second Brain |
| `/indexar` · `/novo` · `/consolidar [dia]` · `/sonhar [dia]` | reindexa · nova conversa · memória do dia · sonho noturno |
| `/modelo [nº\|slug]` · `/modo [nome]` | lista/troca modelo · troca persona |
| `/bolsa <ticker>` · `/carteira [add\|remover <ticker>]` | cotação · carteira |
| `/metas` · `/painel` · `/foco [definir\|limpar]` | metas do ciclo · painel (`/painel` é alias de `/metas`) · foco |
| `/tarefas [sync\|fazer\|concluir\|limpar]` | tarefas de longo prazo |
| `/lembrar <texto>` · `/lembretes` · `/cancelar <id>` | lembretes |
| `/voz on\|off\|edge\|say\|none` · `/continuo on\|off` | voz e escuta |
| `/audiobook <arquivo> [título]` · `/audiobooks` | narra um PDF/TXT em MP3 · narra todos da pasta |
| `/fala` · `/texto` · `/interromper` | modo de entrada |
| `/rapido on\|off` · `/proativo on\|off` | modo rápido · proatividade |
| `/pensando on\|off` | mostra/esconde o raciocínio na tela (o Telegram tem interruptor próprio) |
| `/contexto` · `/historico` · `/sessao` | contexto, histórico, sessão |
| `/preferencia <texto>` · `/preferencias` | ensina · mostra preferências |
| `/confirmar` · `/autoaprovar` | confirmação de notas · ferramentas |
| `/chines on\|off\|en\|fr` · `/reiniciar` · `/sair` | eco de tradução (chinês, inglês ou francês) · reinicia · encerra |
| `/backlog [sync\|add\|run\|repo\|remover]` | lista · relê as páginas da Agenda (grava, move e apaga órfão) · adiciona item · despacha ao pi · define o repositório do projeto · apaga |

`/ajuda` lista tudo com a descrição de cada um.

## Configuração

Arquivo: `~/.config/gaia/config.toml` (segredos só no `.env`).
Seções: `agent`, `stt`, `tts`, `audiobook`, `audio`, `secondbrain`, `interface`,
`memory`, `briefing`, `career`, `proactive`, `focus`, `agenda`, `estudos`,
`browser`, `telegram`, `whatsapp`, `serve`.

```bash
gaia config          # mostra a configuração (segredos redigidos)
gaia config --init   # cria o padrão
gaia config --force  # sobrescreve
```

A maioria dos campos tem uma variável `GAIA_*` correspondente (veja
`.env.example` e `_ENV_MAP` em `gaia/config.py`).
Configurações no formato antigo (`[opencode]`, `GAIA_OPENCODE_*`) ainda são
aceitas e migradas na leitura.

## Estrutura

```text
gaia/
  agent/          # loop do agente, providers, ferramentas, sessões
  audio/          # microfone, VAD, pré-roll
  stt/            # faster-whisper
  tts/            # edge-tts, say, limpeza de Markdown
  audiobook/      # PDF/texto -> MP3: extracao, limpeza, capitulos, ID3
  secondbrain/    # índice FTS5, frontmatter, notas, embeddings opcionais
  memory/         # conversas e turnos (SQLite), consolidação e sonho
  control/        # Control Center: runs, skills, automations, health, secrets
  briefing/       # composição dos textos de manhã, meio-dia e noite
  controller/     # orquestrador, turnos, comandos, personas, iniciativa
  interface/      # TUI Textual, terminal Rich e RemoteUI
  serve/          # HTTP+SSE do `gaia serve`, página web e rotas /control
  client/         # cliente `gaia --remote`
  agenda/         # cliente da Agenda (Supabase)
  career/         # rotina de vagas
  finance/        # cotações e carteira
  tasks/          # tarefas de longo prazo
  telegram/       # bot e serviço do Telegram
  whatsapp/       # Cloud API da Meta
  browser/        # Playwright
  cli*.py         # parser, comandos, runtime, integrations, serve
  focus.py        # foco e lua
```

Dados (`gaia.db`, `sb_index.db`, `sessions/`, `career/`, `logs/`) ficam em
`~/.local/share/gaia` por padrão, fora do repo. O launcher `Abrir Gaia.command`
define `GAIA_DATA_DIR="$REPO/data"` para usar `./data`.

## Privacidade

- Áudio nunca sai da máquina; é transcrito localmente.
- Só o texto da resposta vai para o TTS `edge`. Use `/voz say` (macOS) para
  ficar 100% offline.
- Chaves apenas em `.env` ou no ambiente; o `config` as mostra redigidas.
- Nada da transcrição é executado direto: tudo passa pelo agente e pelas
  permissões.
- `agent.auto_approve` é opt-in e vem desligado.

## Desenvolvimento

```bash
.venv/bin/python -m pytest        # sem microfone, download ou rede
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

Os três comandos são exatamente o que o CI roda. Para trabalhar no código, leia
`AGENTS.md` (mapa, invariantes e armadilhas). Planos e notas de sessão ficam em
`docs/`; `docs/README.md` classifica o que é referência viva, plano ou
histórico.
