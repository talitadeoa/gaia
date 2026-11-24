# Aposentar a TUI e ficar so com o web

> **Plano ativo, nao aplicado.** Escrito em 2026-11-23 sobre `084bd39`.
> Depende de decisao de produto (secao 9) e deve ir **depois** da Etapa 1
> (pacote `config/`) e **antes** da Etapa 2/3 do plano
> [`2026-11-23-refatoracao-estrutura.md`](2026-11-23-refatoracao-estrutura.md),
> para nao quebrar `textual_app.py` que vai ser apagado.

Decisao: o web (`gaia serve` + `GET /`) passa a ser a **unica** interface
interativa. A TUI Textual sai do repo, `textual` sai das dependencias, e o
`gaia` sem argumento passa a abrir o navegador no motor local.

Hoje a TUI e a interface padrao (`interface.ui = "textual"`) e carrega 2108
linhas de codigo proprio + 1148 linhas de teste — o maior bloco isolado do
projeto — para uma funcao que o web ja faz, com uma excecao real: **o web nao
desenha 5 telas** que so a TUI tem (secao 3).

## 1. O que sai (inventario medido)

| Arquivo | Linhas | Destino |
| --- | --- | --- |
| `gaia/interface/textual_app.py` | 1280 | apagar |
| `gaia/interface/textual_screens.py` | 443 | apagar |
| `gaia/interface/textual_render.py` | 180 | apagar |
| `gaia/interface/textual_markdown.py` | 158 | apagar |
| `gaia/interface/textual_sink.py` | 47 | apagar |
| `tests/test_textual_app.py` | 1056 | apagar |
| `tests/test_textual_markdown.py` | 92 | apagar |
| `pyproject.toml` -> `textual>=8.2,<9` | -- | remover dependencia |
| **Total** | **3256** | |

Fica: `interface/terminal.py` (615 l., UI Rich simples), `interface/remote.py`
(406 l., `RemoteUI` que alimenta o SSE) e `interface/markdown_text.py` (44 l.,
`strip_markdown`, usado pelo TTS em `controller/speech.py`).

**Nao** apagar `interface/terminal.py`. Ele tem tres usos que sobrevivem:

1. `RemoteClient` (cliente fino de `gaia --remote`) desenha por ele.
2. `TelegramService` sobe com `TerminalUI(quiet=True)` (`telegram/service.py:66`).
3. `gaia ask`, `gaia doctor`, `gaia config`, `gaia search` usam para mensagem
   de saida.

## 2. Etapas

### Etapa 1 — Paridade antes de apagar (risco: medio)

Nada sai do codigo antes disso. Estas cinco coisas existem **so** na TUI e o
web nao tem rota nem tela:

| Recurso | HOJE (TUI) | Onde | Falta no web |
| --- | --- | --- | --- |
| `/lembretes` | `ui.reminders()` -> tabela Rich | `commands.py:136` | evento `reminders` emitido, `index.html` nao desenha (A3) |
| `/carteira` (watchlist) | `ui.watchlist()` -> tabela | `commands.py:543` | evento `watchlist` nao desenhado |
| `/foco` | `ui.dashboard()` -> painel + foco + metas + lua | `commands.py:457` | evento `dashboard` nao desenhado |
| `/nota` (preview) | `ui.print_note()`/`note_preview()` | `commands.py:90` | evento `note`/`note_preview` nao desenhado |
| Permissao "sempre" | qualquer decisao na TUI | `textual_app.request_permission` | ja existe no web (`cartaoPermissao`) |

Checklist:

- [ ] `index.html::tratarEvento`: `case "reminders"`, `case "watchlist"`,
      `case "dashboard"`, `case "note"` / `case "note_preview"` — mesmo padrao
      de `mostrarHits` (lista simples, `md()` para o texto).
- [ ] Teste de contrato em `tests/test_serve_web.py`: para cada evento novo, um
      teste de que o `index.html` tem o `case` (o arquivo ja e lido como texto
      nesse teste; ver os contratos estaveis que existem hoje).
- [ ] `/lembretes` no web: conferir que o comando chega ao motor e que a
      resposta nao depende de `terminal.py`.
- [ ] `tests/test_remote_ui.py` continua sendo o guarda: `RENDER_METHODS` nao
      muda nesta etapa.

**Verificacao:** abrir `gaia serve` sem TUI, rodar `/lembretes`, `/carteira`,
`/foco` e `/nota` no navegador e ver a mesma informacao que a TUI mostrava.

### Etapa 2 — Rota de bootstrap: `gaia` sem argumento abre o web (risco: baixo)

O binario `gaia` sem subcomando hoje escolhe interface por `interface.ui`
(`textual` | `terminal`). Passa a ser:

- `gaia` -> motor local + navegador (`http://127.0.0.1:8787`), como o
  `gaia serve` ja faz, mas com o navegador aberto no fim do boot.
- `gaia -old` / `gaia --ui terminal` **saem** do parser (ouviram aviso de
  remocao; ver secao 7).
- `gaia serve` continua sendo o comando de producao (systemd, mini, Telegram).
- `gaia --remote URL` continua com o `RemoteClient` (terminal Rich). Se um dia
  quiser TUI remota, e outro projeto.

Checklist:

- [ ] `cli_parser.py`: remover `--ui`, `-tui/--tui`, `-old/--old`; adicionar
      `--no-browser` (para o boot em SSH/systemd nao tentar abrir navegador).
- [ ] `cli.py`: sem `cfg.interface.ui`; sem `_run_app` textual; o caminho
      padrao chama o mesmo que `_serve_command` com `open_browser=True`.
- [ ] `cli_runtime.py`: apagar `_run_app`/`_run_textual`/`_reexec` se ninguem
      mais usa (o `42` de reexec morre com a TUI).
- [ ] `config.py`: `interface.ui` sai do modelo, do `_ENV_MAP`
      (`GAIA_UI`), e do `_DEFAULT_CONFIG_TEMPLATE`. Ver secao 5.
- [ ] Abrir navegador so quando houver TTY e `servidor.start()` ja respondeu
      `/health` (nada de abrir aba em boot que ainda vai falhar).

**Verificacao:** `gaia` abre o navegador; `gaia --no-browser` so sobe; `gaia
serve` inalterado; `pytest -q tests/test_cli.py tests/test_serve_app.py`.

### Etapa 3 — Apagar a TUI e as dependencias (risco: baixo)

- [ ] `git rm` nos 5 modulos e nos 2 testes da tabela 1.
- [ ] `pyproject.toml`: remover `textual` das dependencias. Conferir com
      `grep -rn "textual" --include=*.py gaia tests` = zero.
- [ ] `tests/test_cli.py`: apagar `test_textual_modules_expose_public_api` e
      `test_welcome_art_wordmark_cabe_na_largura`; o
      `test_parser_top_level_flags` perde `--ui` e passa a testar
      `--no-browser`. **Este e o unico teste que muda**, e porque a flag que ele
      exercita deixou de existir.
- [ ] `tests/test_config.py:139`: a linha `"GAIA_UI": "terminal"` e o assert
      `cfg.interface.ui == "terminal"` saem junto com o campo. Trocar por um
      assert de que `GAIA_UI` **nao** contamina nada, se quiser manter o
      espirito do teste (string antes tratada como bool).
- [ ] `gaia/interface/__init__.py` continua exportando `TerminalUI`.
- [ ] Rodar a suite com `-p no:randomly`-equivalente: order matters? Nao ha
      plugin; so rodar tudo.

**Verificacao:**

```bash
um=$(printf '\u00e9'); grep -rn "textual" gaia tests pyproject.toml | wc -l   # 0
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -2   # sem textual
pytest -q && .venv/bin/ruff check gaia tests && .venv/bin/mypy
.venv/bin/gaia doctor
```

### Etapa 4 — `interface/` encolhe e o mapa do codigo muda (risco: baixo)

Depois da Etapa 3, `interface/` fica com `terminal.py` (615), `remote.py`
(406), `markdown_text.py` (44), `__init__.py`:

- [ ] `terminal.py`: o que era so da TUI sai (`selection_mode`, copia de
      selecao, mouse). Sobra a UI Rich de mensagem/stream/status.
- [ ] Renomear `interface/terminal.py` -> `interface/cli_ui.py`? **Nao.**
      `TerminalUI` e o nome que o `RemoteUI` imita e que o Telegram usa; trocar
      so gera diff.
- [ ] `AGENTS.md`: mapa do codigo sem `textual_app.py`; invariante de canal
      sem `mode` da TUI; "Interface" no `README.md` reescrito para web primeiro.
- [ ] `docs/README.md`: status dos planos (apontar para este arquivo e para o
      de refatoracao).

### Etapa 5 — Configuracao de interface (risco: baixo, mas e breaking change)

Campos de `InterfaceConfig` que so existiam para a TUI:

| Campo | Usado por | Acao |
| --- | --- | --- |
| `ui` | escolha da interface | **remover** (`GAIA_UI`) |
| `selection_mode` | `textual_app` | remover |
| `dashboard_collapsed` | `textual_app` | remover |
| `show_metas_on_start` | `orchestrator.py:441` | manter (o dashboard do web usa) |
| `chinese_echo` / `echo_language` | ambas | **manter** (web usa) |
| `markdown_render` | `textual_app` | remover (o web sempre renderiza) |
| `show_reasoning` / `show_diffs` | `turns.py` | manter (filtra evento) |
| `concise`, `humor`, `fast*`, `steering` | prompt | manter |

- [ ] Remover do modelo, do `_ENV_MAP` (`GAIA_UI`, `GAIA_UI_SELECTION_MODE`,
      `GAIA_UI_DASHBOARD_COLLAPSED`, `GAIA_UI_MARKDOWN`) e do template TOML.
- [ ] `.env.example`: remover as linhas correspondentes.
- [ ] `config.toml` de quem ja tem: `extra="ignore"` ja ignora chave antiga —
      nenhum usuario quebra. Confirmar com teste em `tests/test_config.py`
      (carregar TOML com `ui = "textual"` e nao falhar).

### Etapa 6 — CLI de manutencao (opcional, decisao de produto)

Nada no repo depende da TUI, mas ha um cenario: entrar no mini por SSH e
conversar. Opcoes:

- (a) `gaia ask "..."` (ja existe, turno unico, sem interface) — **suficiente**
  para manutencao; combina com `gmin ask`.
- (b) manter `gaia --remote URL` (terminal Rich) para conversar com o motor
  remoto. Este caminho **fica** e nao depende da TUI.
- (c) `ssh -L 8787:localhost:8787 mini` + navegador do Mac — o modo que a doc
  de acesso ao mini ja recomenda.

Recomendacao: (a) + (c), e nao escrever interface nova.

## 3. O que se perde de verdade (e o que fazer)

| Perda | Gravidade | Alternativa |
| --- | --- | --- |
| Paleta de comandos (F1) | baixa | o web ja tem paleta (`GET /commands`, paleta no `index.html`) |
| Dashboard de foco/metas/lua em painel | media | Etapa 1 desenha no chat via `dashboard` |
| Copiar selecao com F4/mouse | nenhuma | o navegador faz isso nativamente |
| Modo voz push-to-talk local | nenhuma | modo voz do web (WAV do navegador) e superior |
| Streaming no terminal | nenhuma | o web faz SSE |
| Rodar sem navegador em maquina sem browser | baixa | `gaia ask`, `gaia serve` + `--remote`, Telegram |
| `gaia --watch` (reinicio ao mudar .py) | baixa | mantem: e do processo, nao da UI. So cuidar do `_reexec` que morria na TUI |

## 4. Riscos e como nao se machucar

- **Nao apagar antes da paridade.** Se a Etapa 3 rodar sem a Etapa 1, `/foco`,
  `/carteira`, `/lembretes` e `/nota` passam a responder com eco vazio (o evento
  e emitido e o web ignora) — sem erro, o que e pior. Ordem: **1 -> 2 -> 3 ->
  4 -> 5**.
- **`_HELP` unico.** Hoje a ajuda sai de `interface/textual_render.py::_HELP`
  (citado pela TUI) e de `command_catalog.py` (citado pelo web e pelo terminal).
  Ao apagar `textual_render`, conferir que `terminal.py` nao importa `_HELP`:
  hoje nao importa (`grep`), a duplicacao e de conteudo, nao de import.
- **`show_metas_on_start`** e lido pelo `Orchestrator`
  (`orchestrator.py:441`), nao pela TUI: nao remover por engano na Etapa 5.
- **Dependencia unica.** `textual` sai do `pyproject.toml`; `rich` fica (e do
  `TerminalUI` e do `RemoteClient`). Nao confundir os dois.
- **`tests/test_intents.py`** aparecia no `grep` por "textual" por causa da
  palavra em um comentario/docstring — nao ha dependencia de codigo.
- **Systemd/deploy**: nenhuma unidade usa `--ui`/`--tui`/`--old`
  (`deploy/gaia-serve.service` chama `gaia serve`), entao o deploy nao muda.
  Conferir com `grep -rn "ExecStart" deploy/`.

## 5. Impacto na documentacao (fazer no mesmo commit de cada etapa)

| Arquivo | O que muda |
| --- | --- |
| `README.md` | "Interface" comeca pelo web; `gaia` abre o navegador; TUI sai de "uso"; secao 715 (`interface/`) some a TUI |
| `AGENTS.md` | mapa do codigo (sem `textual_app.py`), comandos (sem `-old`/`--tui`), invariante de canal (sem `mode` da TUI), receitas que citam `test_textual_app.py` (2 ocorrencias) |
| `docs/README.md` | este plano + o de refatoracao na tabela de status |
| `.env.example` | sem `GAIA_UI*` que morreram |
| `docs/plans/2026-11-22-mac-cliente-fino.md` | a "lacuna 4" (12 eventos nao desenhados) passa a ser **so** do web; anotar |

## 6. Ordem, risco, ganho

| Etapa | O que | Risco | Ganho |
| --- | --- | --- | --- |
| 1 | paridade web (5 eventos) | medio | nada se perde |
| 2 | `gaia` abre o navegador | baixo | fluxo de entrada novo |
| 3 | apagar TUI + `textual` | baixo | -2108 prod, -1148 teste |
| 4 | `interface/` encolhe + docs | baixo | mapa do codigo honesto |
| 5 | tirar campos de config | baixo (breaking) | config sem chaves mortas |
| 6 | decisao sobre manutencao SSH | nenhum | — |

Depois disso, o plano de refatoracao perde a Etapa 3 original (TUI) e
`interface/` deixa de ser um dos maiores pacotes.

## 7. Decisoes pendentes

1. **`gaia` sem argumento deve abrir o navegador** ou apenas subir o motor e
   imprimir a URL? (abrir aba em maquina remota/SSH falha silencioso; por isso
   o `--no-browser`).
2. **Remover `-old`/`--old` de vez** ou manter por um ciclo avisando que sera
   removido? Sugestao: remover agora e citar no README (`gaia ask` e o
   substituto).
3. **Aposentar `TerminalUI` tambem?** Nao. Ele e dependencia do `RemoteClient`
   e do Telegram; aposenta-lo seria reescrever o cliente fino.
4. **`gaia --watch`**: fica, mas o `_reexec` (codigo 42) morre com a TUI.
5. **Manutencao por SSH**: `gaia ask` basta? (secao 2, Etapa 6).
