# Plano: tirar dado pessoal do repo público

> **Status: Fase 1 aplicada; fases 2–5 pendentes.** Repo é PÚBLICO
> (`github.com/talitadeoa/gaia`): tudo que estiver versionado é leitura livre,
> inclusive no histórico (`git log -p`, forks, cache do GitHub).

**Objetivo:** a Gaia roda igual para qualquer pessoa. O que é preferência
pessoal (cargo-alvo, piso salarial, cidade, dias de estudo, dono do GitHub)
sai do código e vira config — com os valores de hoje preservados no
`config.toml` local, que já está fora do repo.

**Regra de ouro:** o repo entrega *capacidade*, nunca *perfil*. `DEFAULT_*`
genérico, valor pessoal no TOML, e teste que prove que sem config a rotina não
chuta perfil de ninguém.

---

## Fase 1 — feito aqui

- IPs do tailnet/LAN e MagicDNS fora dos docs e das unidades:
  `deploy/gaia-serve-tailnet.service.example` virou molde com
  `__TAILNET_IP__`; `gmin serve install --tailnet` preenche com
  `tailscale ip -4` na hora de instalar (nada de IP no repo, e o mini continua
  recebendo a unidade cheia). `docs/acesso-mini.md` e os planos passaram a usar
  `100.x.y.z` / `100.a.b.c` / `<mini>.<tailnet>.ts.net`.
- Testes usam documentação reservada (`203.0.113.10`) em vez do IP real.
- Verificação: `pytest -q`, `ruff check gaia tests`, `mypy`, `bash -n
  scripts/gmin.sh`.

---

## Fase 2 — perfil de vagas (`gaia/career/`) — prioridade alta

Hoje a rubrica *é* o perfil de uma pessoa: cargos-alvo de TI/suporte, cidade
(Natal/RN), piso `1700` e teto `2500`, empresas conhecidas, senioridade
aceita. Está tudo em módulo de código, então qualquer leitor do repo sabe qual
vaga ela procura e quanto espera ganhar.

**Alvo:** `CareerProfile` (pydantic) em `gaia/config/models.py`, seção
`[career.profile]` do TOML, e `score.py` lendo do perfil em vez de constante.

Campos (nome, tipo, default **genérico**):

| Campo | Tipo | Default do repo | Valor pessoal (TOML local) |
|---|---|---|---|
| `titles_primary` | `list[str]` | `[]` | lista atual de `PRIMARY_TITLES` |
| `titles_secondary` | `list[str]` | `[]` | `SECONDARY_TITLES` |
| `titles_off` | `list[str]` | `[]` (vazio = não filtra) | `OFF_TITLES` |
| `cities` | `list[str]` | `[]` | `["Natal", "Parnamirim", "Mossoró", "RN"]` |
| `remote_ok` | `bool` | `true` | `true` |
| `salary_floor` | `float` | `0.0` | `1700` |
| `salary_good` | `float` | `0.0` | `2500` |
| `currency` | `str` | `"BRL"` | `"BRL"` |
| `seniorities` | `list[str]` | `["junior", "pleno"]` | igual |
| `max_years` | `int` | `3` | `3` |
| `known_companies` | `list[str]` | `[]` | lista atual |
| `red_flags` | `list[str]` | lista atual (é genérica, pode ficar no código) | igual |

Tarefas:

1. `gaia/config/models.py`: `CareerProfile` + `profile: CareerProfile` dentro de
   `CareerConfig`; `_ENV_MAP` só ganha entrada para o que virar env
   (`GAIA_CAREER_PROFILE_FILE` é suficiente — o resto é lista, não escala em env).
2. `gaia/config/template.py`: bloco `[career.profile]` **comentado** com o
   exemplo genérico e uma linha dizendo "seu perfil mora aqui, não no repo".
3. `gaia/career/score.py`: trocar leitura de constante por `profile`; manter as
   constantes como `_DEFAULT_*` de compatibilidade só se houver import externo
   (hoje `tests/test_career.py` importa `title_points`, não as listas — pode
   remover). `score_job(job, profile=...)` com `profile=None` => perfil da config.
4. Perfil vazio não pode explodir: `title_points` devolve `0` e
   `reject_reason` devolve `""` (nada é descartado por cargo sem perfil
   configurado) — documentar que sem `[career.profile]` a rotina só ordena.
5. `gaia/controller/modes.py`: tirar "Talita" e `~/SecondBrain/cv/...` das
   diretrizes; usar `{nome_usuario}`/`{secondbrain}/cv/playbook-vagas.md`
   montados de `interface.user_name` (campo novo, ver Fase 4).
6. `gaia/career/sources.py`: `_USER_AGENT` com `(+pessoal)` é ok; conferir que
   nenhuma URL carrega user/email.
7. Testes: `tests/test_career.py` passa perfil explícito nos helpers
   (`profile=perfil_de_teste()`), + teste novo "perfil vazio não filtra e não
   pontua por cargo". `tests/test_config.py` cobre TOML → perfil.

**Migração:** `gmin`/`gaia doctor` avisa quando `[career.profile]` está vazio e
`career.enabled = true`. Não inventar perfil automaticamente.

---

## Fase 3 — `[estudos]` e `[backlog]`: nome de projeto privado

- `EstudosConfig.study_days = [6, 2, 4, 5]` é rotina pessoal de estudo.
  Default vira `[]` = "todos os dias" e o comentário do template explica
  `[6, 2, 4, 5]` como exemplo. `tests/test_estudos.py:310` já parametriza.
- `BacklogConfig.repos` / `github_owner` / `DEFAULT_REPOS`
  (`gaia/backlog/repos.py`) expõem `talitadeoa/agenda` e `talitadeoa/abin-suite`
  (repos privados, nomes de projeto pessoal). Default vira `{}` + `github_owner
  = ""`; o seed `[backlog.repos]` sai do template para um bloco **comentado**
  de exemplo (`dono/repo`). `validate_repo` continua igual e
  `BacklogFacade.set_repo` grava no TOML quando a pessoa informar.
  Testes: `tests/test_backlog.py` (repo ausente => `needs_repo`, nada despacha)
  e `tests/test_control_api.py` (rota `/backlog/repo`).
- `AgendaConfig.env_file = "~/agenda-git/agenda/.env"` e
  `EstudosConfig.env_file = "~/abin-suite/.env"`: default vira `""` (desligado
  até a pessoa apontar) ou `~/agenda/.env`/`~/estudos/.env` genérico. Escolher
  `""` + aviso no `gaia doctor`; o README mostra o exemplo.

---

## Fase 4 — nome de quem usa

`"Talita"` aparece em ~15 arquivos (`interface/terminal.py`, `textual_app.py`,
`agent/session.py`, `agent/runtime.py`, `memory/consolidation.py`,
`memory/dream.py`, `controller/modes.py`, `agent/tools/agenda.py`,
`agent/tools/estudos.py`, `career/service.py`, `controller/briefing.py`,
`controller/initiative.py`, `controller/voice.py`,
`controller/orchestrator.py`, docstrings).

1. Campo `interface.user_name` (default `"você"` ou `"Usuário"`).
2. Um helper único (`gaia/controller/prompt.py` ou `gaia/identity.py`) que
   resolve o rótulo: `cfg.interface.user_name or "você"`. Nada de `if
   cfg...` espalhado.
3. Substituir as ocorrências por esse helper — inclusive o rótulo do card da
   TUI e o `speaker` do histórico (que vai para o prompt e para a memória).
4. `tests/test_textual_app.py`, `tests/test_orchestrator.py` e
   `tests/test_memory.py` ganham o rótulo a partir da config.

---

## Fase 5 — caminhos e usuário do SO

| Onde | Hoje | Alvo |
|---|---|---|
| `deploy/com.gaia.sbsync.plist` | `/Users/talita/...` fixo | `~`-expandido no install (`sed` do `HOME`) ou `ProgramArguments` com `$HOME` via `sh -c` |
| `scripts/gmin.sh` | `/home/talita/gaia`, `/home/talita/SecondBrain` | defaults `~/gaia` e `~/SecondBrain` resolvidos no mini: `REMOTE_REPO="${GMIN_REMOTE_REPO:-$("${SSH[@]}" 'echo $HOME')/gaia}"` |
| `scripts/hardening-ssh.sh` | `/home/talita/gaia/...` no comentário | caminho relativo ao script |
| `scripts/install-linux.sh:99` | `grep "talita/gaia"` | `grep "$(id -un)/gaia"` |
| `scripts/branches-protegidas.sh` | `OWNER:-talitadeoa` | `OWNER` obrigatório (falha com mensagem) |
| `README.md:41` | `git clone .../talitadeoa/gaia.git` | `.../<seu-usuario>/gaia.git` |
| `docs/acesso-mini.md` | `User talita`, `talita-default-string.local`, PRs `talitadeoa/*` | `<usuario>`, `<host>-default-string.local`, `<dono>/*` |

Testes: `tests/test_gmin_deploy.py` já monta repo temporário; adicionar caso
"`GMIN_REMOTE_REPO` não setado resolve `$HOME` remoto" com stub de `ssh`.

---

## Fase 6 — histórico do git (decidir, não fazer às pressas)

O e-mail pessoal está na autoria dos ~250 commits, e os IPs/planos antigos
continuam em `git log -p` mesmo depois das fases acima.

- **E-mail:** `git filter-repo --mail-map mailmap.txt` (troca autor/comitter)
  ou `--email-callback`. Reescreve todos os SHAs — exige `push --force` em
  todas as branches, aviso a quem clonou (o mini faz `reset --hard origin/main`
  e se recupera sozinho) e novo `origin`.
- **IPs e planos antigos:** mesma reescrita com `git filter-repo --replace-text`
  (arquivo de padrões: `<ip-do-tailnet>==>100.x.y.z`, etc.).
- **Alternativa sem reescrever:** aceitar o histórico e tratar como aceitável
  (IP de tailnet é privado por design e o e-mail já é público em qualquer
  commit from this repo). Registrar a decisão aqui.
- Antes de qualquer reescrita: `git clone --mirror` de backup e `chmod 600`.
- Depois de reescrever: rotacionar `GAIA_SERVE_TOKEN`, `GAIA_CONTROL_TOKEN` e
  conferir se nenhum fork antigo mantém o conteúdo.

---

## Fase 7 — áudio da fixture de VAD

`tests/fixtures/vad_ruido_e_fala.npz` (7,5 s, 16 kHz) foi documentada no
commit `421ff51` como "áudio real do microfone". É uma fixture de teste (não
tem fala reconhecível na análise espectral), mas se for voz gravada:

1. regerar a fixture com fonte sintética (ruído + tom modulado em 1,5–5 s) e
   ajustar `tests/test_audio_segmenter.py`;
2. ou movê-la para `data/` (fora do repo) e gerar no `conftest.py` na primeira
   execução;
3. confirmar o limiar do Silero com a nova fixture (o teste de histerese é o
   que importa, não o áudio específico).

---

## Ordem sugerida

1. Fase 2 (perfil de vagas) — é o vazamento mais concreto e mexe em lógica.
2. Fase 4 (nome) — mecânica, toca muitos arquivos, mas é `sed` + helper.
3. Fase 3 (estudos/backlog) — defaults e template.
4. Fase 5 (caminhos e scripts) — operacional, sem risco de comportamento.
5. Fase 7 (fixture).
6. Fase 6 (histórico) só depois, e com backup espelhado.

Cada fase: `pytest -q`, `ruff check gaia tests`, `mypy` verdes e uma linha de
status aqui mesmo (como o `docs/README.md` faz para os outros planos).
