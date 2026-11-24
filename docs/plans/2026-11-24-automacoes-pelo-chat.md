# Automações criadas pelo chat (`criar_automacao`)

Status: **implementado** em 2026-11-24. Schema **v11**.

## O problema

A tabela `automations` já governava as rotinas do registry (briefing, vagas,
carteira, memória, sonho), mas criar uma automação nova exigia editar o
`config.toml`/o registry e reiniciar. O pedido natural — "toda sexta às 9h me
lembre de estudar" — não tinha para onde ir: o agente criava um lembrete de
prazo único (`reminders`), não uma recorrência.

## Decisão

A automação pedida em linguagem natural vira **linha na tabela `automations`**
com id `user:*`, e o agendador único passa a despachá-la pela coluna `skill`.
Nada de segundo agendador, segunda tabela ou cron.

Quem mede a confiança é **regra**, não LLM: o modelo apenas repassa a frase
inteira. Assim o comportamento é testável sem provedor e não muda de humor com
o modelo.

### Módulos

| Arquivo | Papel |
|---|---|
| `gaia/controller/automation_builder.py` | parser de slots + confiança + criação |
| `gaia/agent/tools/automations.py` | ferramenta `criar_automacao` (aprovação obrigatória) |
| `gaia/agent/tools/__init__.py` | registra a ferramenta quando há `cfg` **e** `store` |
| `gaia/agent/runtime.py` | `set_memory_store()` liga o banco ao `ToolContext` no bootstrap |
| `gaia/control/automations.py` | `load()` aceita `user:*` (linha é o descritor) |
| `gaia/controller/scheduler.py` | handlers `_user_automation_*` por skill |
| `gaia/control/skills.py` | skill `automations` (`tool_group`) |
| `gaia/memory/store.py` | schema v11 (`name`, `params`), `count_meta()` |

### Slots

- **dias**: `toda sexta`, `seg, qua e sex`, `de segunda a sexta`, `todos os
  dias`/`todo dia`, `dias úteis`, `fim de semana`.
- **hora**: `9h`, `9:30`, `às 9`, `pelas 18 horas`, `meio-dia`, `meia-noite`,
  `9h da noite` → 21:00, `7h da tarde` → 19:00. `em 2 horas` é prazo relativo e
  **não** vira horário do dia.
- **skill alvo**: `reminder` ("me lembre/avise"), `agenda_summary` ("resumo da
  agenda"), `estudos_summary` ("resumo dos estudos/simulados").
- **entrega**: `telegram` / `aqui`. Sem menção, `telegram` se o bot está
  configurado, senão `local`.
- **parâmetros**: `{"text": "..."}` para o lembrete (o rótulo sai do texto
  original, com acento, sem os slots).

### Confiança (regras)

| Regra | Peso |
|---|---|
| skill alvo reconhecida | +3 |
| verbo claro do que fazer | +2 |
| horário válido | +3 |
| dias citados | +2 |
| entrega explícita | +1 |

Tetos: sem skill **ou** sem hora → ≤3; sem dias → ≤6.

- **alta** (≥7): cria **ativada**;
- **média** (4–6): cria **pausada** e pede conferência (o checkbox do painel
  liga; a mensagem diz o que falta);
- **baixa** (<4): não cria nada e pede para reformular.

### Segurança (v1)

- skill alvo tem de estar ligada na config (`proactive.reminders`,
  `agenda.enabled`, `estudos.enabled`) — recusa explicando o caminho;
- horário precisa ser HH:MM válido;
- no máximo `MAX_PER_DAY = 10` criações automáticas por dia, contadas por
  chaves `automation_created:<dia>:<id>` em `meta` (reenvio não conta duas
  vezes, crash no meio não perde a conta);
- **nunca** edita nem apaga automação existente: pedido repetido devolve a
  linha existente e manda para o painel.

## Execução

`item.id.startswith("user:")` → `USER_SKILL_METHODS[item.skill]`:

- `reminder` → `_user_automation_reminder` (usa `_say_proactive`, então respeita
  canal: Telegram no dono do poller, voz no terminal);
- `agenda_summary` → `_user_automation_agenda` (pendentes/feitas do dia);
- `estudos_summary` → `_user_automation_estudos` (resumo da semana).

Cada handler abre um `record_run` (o `runs` é a prova de execução real) e o
retry/notificação de falha continuam sendo os do agendador.

## Testes

- `tests/test_automation_builder.py`: parser, confiança nas três faixas,
  guardas (skill desligada, horário inválido, teto diário, duplicata),
  ferramenta e painel.
- `tests/test_scheduler.py`: automação `user:*` devida executa no horário e não
  repete a janela; skill sem handler não trava o tick.

## Limites conhecidos (backlog)

- Só as três skills da v1; `career`/`market` como alvo exigiriam parâmetros e
  um gate de config diferente.
- Não existe rota do chat para listar/remover só as `user:*` (o painel já
  pausa e edita horário/dias via `POST /automations/set`).
- O painel mostra `params` só de forma indireta (nome); um detalhe por skill
  ficaria melhor.
