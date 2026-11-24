# Handoff: backlog fila funcional, clones e robustez

Estado: 2026-11-24, HEAD `4fc7140` no Mac e no mini. O commit `cc4a13b` já corrigiu o gate da fila (`running_count`); não refazer. Esta pauta é continuar o trabalho de robustez do backlog.

---

## Escopo (origem: prompt do usuário)

Tarefa: consertar a fila do backlog (`gaia/backlog/`) que nunca executa no painel web.

### Diagnóstico já feito (reproduzido)

1. **Gate travado** (JÁ CORRIGIDO em `cc4a13b`):
   - `worker.py:111` fazia `if self.service.active_count() >= self._max_running(): return`.
   - `active_count()` contava `queued` + `running`.
   - Item na fila contava como "em voo", então o worker nunca pegava.
   - Prova: item #65 do mini ficou `queued` desde 00:11, `attempts=1`, `started_at=""`.
   - Fix: `BacklogWorker._tick` agora compara `self.service.running_count() >= self._max_running()`.

2. **Item preso em `running` após restart/deploy** (PENDENTE):
   - `worker.stop()` cancela o laço no meio de `await asyncio.to_thread(job.execute)`.
   - O item continua `running` no banco.
   - `start_backlog` (controller/backlog.py) não reseta para `queued`.
   - Resultado: fila travada até editar o SQLite manualmente.

3. **Clones com nome errado** (PENDENTE):
   - `clone_dir()` gera `<workspace>/<owner>-<repo>` → `~/pr-workspace/talitadeoa-gaia`.
   - Os clones existentes no mini são `~/pr-workspace/gaia`, `agenda`, `abin-suite`.
   - O backlog cria clone novo com `origin=talitadeoa/gaia` em vez de reaproveitar o fork `piratistemica/gaia`.
   - `~/pr-workspace` ainda é clone do `gaia`, então há repo dentro de repo.

4. **Rota `/backlog/run` devolve 503 em vez de 409** (PENDENTE):
   - `index.html:949` trata 409 como "motor ocupado".
   - `/backlog/run` devolve 503 quando o motor está ocupado, não 409.

---

## Tarefas para a próxima sessão

### 1. Recuperar `running -> queued` no boot

Em `gaia/controller/backlog.py::start_backlog`, depois de criar o worker e antes de `worker.start()`:

```python
self.store.requeue_running_backlog_items()
```

Ou delegar ao service:

```python
self.backlog_service_facade().service().requeue_running()
```

Critérios:
- Só itens com `status='running'`.
- `attempts` não deve ser incrementado (o `mark_queued` incrementa; decidir se importa).
- `error=''` e `detail='reinicio do motor'`.
- Teste: criar item `running`, reiniciar worker (ou chamar `start_backlog`), verificar que ele volta para `queued` e é executado.

### 2. Reaper opcional de `running` mais velho que `pi.timeout`

No `_loop` do worker, antes de pegar um novo item, verificar se há algum `running` com `started_at` anterior a `now() - pi.timeout`.

Se sim, marcar como `failed` com `error="timeout de seguranca: o pi nao respondeu"`.

Opcional porque o processo `pi` pode estar vivo e lento; se implementar, o reaper deve ser conservador (talvez 2x o timeout).

### 3. Corrigir o layout dos clones

Decisão a confirmar com o usuário na próxima sessão:

- **Opção A**: mudar `clone_dir` para `<workspace>/<nome-curto>` (ex.: `~/work/gaia` em vez de `~/work/talitadeoa-gaia`).
- **Opção B**: manter `clone_dir` atual, mas migrar o mini para uma pasta vazia `~/work` e validar que o clone existente aponta para o repo esperado.

Recomendação: **Opção A + workspace `~/work`**:
- `pi.workspace = "~/work"` e `backlog.workspace = "~/work"`.
- `clone_dir(Path("~/work"), "talitadeoa/gaia")` → `~/work/gaia`.
- Se o diretório existe mas o `origin` aponta para outro repo, recusar com erro claro.
- Se não existe, clonar do fork `piratistemica/gaia` e adicionar `upstream=talitadeoa/gaia`.

Migração no mini (uma vez):
```bash
mkdir -p ~/work
mv ~/pr-workspace/gaia ~/work/gaia
mv ~/pr-workspace/agenda ~/work/agenda
mv ~/pr-workspace/abin-suite ~/work/abin-suite
rm -rf ~/pr-workspace/talitadeoa-gaia
```

Atualizar TOML:
```toml
[pi]
workspace = "~/work"

[backlog]
workspace = "~/work"
```

### 4. `/backlog/run` devolver 409 quando motor ocupado

Hoje a rota devolve 503 para qualquer `RuntimeError`. O 409 deveria ser usado quando o worker já está processando um item.

Padrão do projeto (rotas de automação, `gaia/serve/server.py:1005`):
- 409 = motor ocupado.
- 503 = motor sem Control Center.

Implementar:
- `backlog_enqueue` levanta exceção específica (ex.: `BacklogBusy`) quando `service.running_count() >= max_running`.
- `gaia/serve/server.py::_backlog_run` captura e devolve 409.
- `index.html:949` já trata 409 corretamente.

---

## Dados já coletados

- `cc4a13b` (gate fix) já está no mini. Não refazer.
- Item #65 no mini: `status=failed`, `error="pi: pi excedeu 900s e foi encerrado"`, `started_at=2026-11-24T10:51:50-03:00`, `finished_at=2026-11-24T11:06:53-03:00`.
- `~/pr-workspace/talitadeoa-gaia/` foi criado pelo backlog; `origin=https://github.com/talitadeoa/gaia.git`; branch `gaia/backlog/65-vamos-criar-um-novo-icon-...`.
- `~/pr-workspace` é clone do `gaia` com 253 arquivos versionados; `origin=https://github.com/piratistemica/gaia.git`.
- `origin/HEAD`: `gaia` → main, `agenda` → master, `abin-suite` → main.
- Testes atuais: `tests/test_backlog.py` passa 100%.

---

## Testes a adicionar/verificar

- `tests/test_backlog.py`:
  - `_tick` só pega item `queued` quando nenhum `running`.
  - `_tick` não pega nada quando há um `running`.
  - Worker reiniciado reexecuta item que estava `running` (simular `start_backlog` requeue).
  - Layout de clone: `clone_dir` gera nome curto e valida `origin`.
- `tests/test_serve_web.py`:
  - `/backlog/run` devolve 409 quando motor ocupado.
  - `/backlog/run` devolve 503 quando backlog desligado.

---

## Verificação obrigatória

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

---

## Não fazer

- Não reimplementar o fix `running_count`.
- Não mudar a unidade de trabalho do backlog (ainda é item -> branch -> PR).
- Não tocar em Telegram/workspace/base_branch sem confirmar com o usuário.
