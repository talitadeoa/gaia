# Handoff: pi no Telegram, workspace do backlog e timeout

Estado: 2026-11-24, HEAD `4fc7140` no Mac e no mini.
Sessão atual conversou com o usuário, diagnosticou e decidiu o caminho. Nenhum código foi alterado ainda (apenas leituras/SSH).

---

## O que já foi decidido

### 1. Telegram sem a ferramenta `pi`

O canal Telegram não deve ter acesso direto à ferramenta `pi`.
Quem quiser despachar código pelo celular usa `/backlog add` + `/backlog run`, que vai para a fila durável e termina em PR.

Consequência: `_SHELL_TOOLS` em `gaia/telegram/service.py` precisa incluir `pi`, ou — melhor — a regra deve usar a capacidade da ferramenta (`requires_approval`/`shell`).
A forma rápida e segura é adicionar `"pi"` ao `_SHELL_TOOLS` e testar.

### 2. Migrar o workspace para fora de `~/pr-workspace`

Hoje `~/pr-workspace` é um clone do `gaia` (origin `piratistemica/gaia`) que também abriga `agenda/` e `abin-suite/` no `.git/info/exclude`. Isso cria repo dentro de repo e confunde o backlog, que cria `talitadeoa-gaia/`.

Decisão: usar uma pasta vazia separada (ex.: `~/work`) para os clones do backlog e do pi.
Tanto `[pi] workspace` quanto `[backlog] workspace` apontarão para o mesmo lugar.

Migração no mini (uma vez):
```bash
mkdir -p ~/work
mv ~/pr-workspace/gaia ~/work/gaia
mv ~/pr-workspace/agenda ~/work/agenda
mv ~/pr-workspace/abin-suite ~/work/abin-suite
# opcional: apagar o clone talitadeoa-gaia criado pelo backlog
rm -rf ~/pr-workspace/talitadeoa-gaia
```

Depois ajustar no `~/.config/gaia/config.toml`:
```toml
[pi]
workspace = "~/work"

[backlog]
workspace = "~/work"
```

### 3. `base_branch` por repo

`talitadeoa/agenda` é `master`; `talitadeoa/gaia` e `talitadeoa/abin-suite` são `main`. A config global `backlog.base_branch = "main"` quebra o checkout do `agenda`.

Decisão: detectar a branch padrão via `git symbolic-ref refs/remotes/origin/HEAD` depois do `fetch`.
`backlog.base_branch` passa a ser **override** (vazio = detecta).

### 4. Timeout: repensar para o backlog

O item #65 (“novo icon a partir da imagem em data/imagem.jpg”) morreu com `pi excedeu 900s`. O timeout fixo é cego e mata tarefas longas.

Decisão aprofundar na próxima sessão: watchdog + progresso no painel.
- O `PiRunner` do backlog deve usar `Popen` e ler o JSONL linha a linha.
- Atualizar o campo `detail` (ou novo `progress`) do item `running`.
- O painel web já tem SSE `backlog`; é possível mostrar “pi: editando x · 12 ferramentas · 4m32s”.
- Não matar o processo por silêncio; matar só quando o processo morre ou o turno/chat aborta.

---

## Tarefas para a próxima sessão

1. **Telegram**
   - Adicionar `"pi"` a `_SHELL_TOOLS` em `gaia/telegram/service.py`.
   - Testar em `tests/test_telegram.py` que `pi` é negado sem `shell_access`.
   - Garantir que `/backlog add` + `/backlog run` continuem funcionando pelo Telegram.

2. **Workspace `~/work`**
   - Validar que `pi.workspace` e `backlog.workspace` apontam para a mesma raiz.
   - Se o diretório de destino do clone (`<workspace>/<owner>-<repo>`) já existe mas aponta para outro repo, recusar com mensagem clara.
   - Atualizar `docs/acesso-mini.md` com o novo layout.

3. **`base_branch` por repo**
   - Em `BacklogJob.prepare_clone`, depois do `fetch`, detectar `origin/HEAD`.
   - `backlog.base_branch` como override.
   - Teste: repo com `master` funciona sem config.

4. **Timeout/watchdog do backlog**
   - Substituir o `subprocess.run` do `PiRunner` por `Popen` + leitura incremental do JSONL.
   - Expor progresso no `BacklogJob` e gravar no banco.
   - Tratar SIGTERM/cancelamento do worker: matar o subprocesso `pi` e resetar `running -> queued` no boot.
   - Decidir entre: (a) timeout longo para backlog, (b) sem timeout, só watchdog, ou (c) acompanhar pelo botão “atualizar” do painel.

---

## Dados de verificação já coletados

- `cc4a13b` (fix `running_count`) já está commitado e no mini. Não refazer.
- Item #65 no mini: `status=failed`, `error="pi: pi excedeu 900s e foi encerrado"`.
- `~/pr-workspace/talitadeoa-gaia/` foi criado pelo backlog e aponta para `https://github.com/talitadeoa/gaia.git`.
- `~/pr-workspace` é clone do `gaia` (253 arquivos), com `agenda/`, `abin-suite/` e `talitadeoa-gaia/` dentro.
- `origin/HEAD` dos clones: `gaia` → main, `agenda` → master, `abin-suite` → main.

---

## O que NÃO fazer

- Não mudar a unidade de trabalho do backlog (ainda é item -> branch -> PR). O handoff da página como unidade (`docs/plans/2026-11-24-backlog-proximo-passo-prompt.md`) é outra pauta; resolver primeiro a infra.
- Não tocar no modelo de permissões do `serve`/web sem decidir com o usuário.
- Não alterar o `clone_dir` sem testes em `tests/test_backlog.py`.

---

## Verificação obrigatória antes de commit

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

Testes alvo:
- `tests/test_telegram.py` (permissão do `pi`)
- `tests/test_backlog.py` (worker, job, workspace, base_branch)
- `tests/test_serve_web.py` (rotas de backlog, se tocar no server)

---

## Pergunta em aberto para o usuário

Sobre timeout, a próxima sessão deve escolher:

1. **Watchdog simples**: manter timeout longo para backlog (ex.: 30 min), sem mudar a arquitetura.
2. **Sem deadline**: `Popen` + leitura linha a linha, matar só por aborto do turno ou morte do processo; progresso no SSE.
3. **Botão “atualizar” do painel**: o worker continua em background, o botão traz o estado atual e o usuário acompanha manualmente.

Recomendação técnica: implementar 2 + watchdog de processo, com progresso no SSE.
