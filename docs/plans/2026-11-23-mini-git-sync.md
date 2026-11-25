# Prompt — o mini passa a receber todo push (git, não rsync)

> **Como usar:** cole o bloco "PROMPT" numa sessão nova, na raiz do repo
> (`~/gaia`), no MacBook. O estado abaixo foi verificado em 23/11/2026, no
> commit `34d4138`, com `1018 passed`, `ruff` e `mypy` verdes.

---

## PROMPT

```
Objetivo: o mini (host SSH "gaia", IP do tailnet 100.x.y.z, repo em
/home/talita/gaia, serviço gaia-serve.service) deve ficar sincronizado com o
GitHub SEMPRE que houver push na main. Hoje o transporte é `gmin deploy`
(rsync -a, sem --delete) e o .git do mini está corrompido. Troque por git de
verdade e deixe automático.

Verificadores obrigatórios antes de cada commit (os três do CI):

    .venv/bin/python -m pytest -q
    .venv/bin/ruff check gaia tests
    .venv/bin/mypy

ANTES DE QUALQUER CÓDIGO

1. Leia AGENTS.md inteiro (invariantes: um único processo escritor, dados fora
   do repo, segredos só no .env/ambiente).
2. Leia docs/acesso-mini.md (seção `gmin`) e scripts/gmin.sh (cmd_deploy,
   cmd_restart, cmd_serve).
3. Confirme o estado real do mini — não confie neste relatório:

       ssh gaia 'cd ~/gaia && cat .git/refs/heads/main; ls .git/config;
                  git status --porcelain | awk "{print \$1}" | sort | uniq -c;
                  git ls-files zaya | wc -l; git stash list | head -3'

ESTADO VERIFICADO EM 23/11/2026 (o que motivou esta tarefa)

- `.git/config` AUSENTE no mini: o repo não tem remote nem tracking. Isso não
  é acidente isolado — `rsync` copia arquivos, nunca o `.git`, então o índice
  do mini ficou preso em `aca6724` enquanto o disco recebia o código novo.
- `HEAD` = `aca6724` (75 commits atrás de `34d4138`); 47 modificados + ~195
  não rastreados só na árvore do mini; `zaya/` (93 arquivos do pacote antigo)
  ainda rastreado; `gaia/` não rastreado; `stash@{0}` com 143 arquivos.
- `.venv` do mini tem dois `.pth`: `_editable_impl_gaia_assistant.pth`
  (-> /home/talita/gaia) e `_editable_impl_zaya_assistant.pth`
  (-> /home/talita/zaya, que NÃO existe). Persiste no `.venv/bin/zaya`.
- Zero commits locais no mini fora do remoto (nada se perde com reset).
- Dependências NÃO mudaram desde `aca6724` (só rename zaya->gaia no
  pyproject): reinstalar o venv é higiene, não necessidade.
- O repo GitHub é PÚBLICO (api.github.com/repos/talitadeoa/gaia -> "private":
  false) e o mini já consegue `git ls-remote https://...` sem chave.
- A árvore do mini, fora `.git/.venv/data/config/__pycache__`, é IDÊNTICA
  byte a byte à do Mac (rsync -c). Ou seja: hoje o rsync é o transporte e está
  funcionando; o problema é só o `.git` e a falta de automação.
- Arquivos que existem SÓ no mini (não versionados): .commandcode/taste/taste.md,
  .opencode/skills/procurar-vagas/SKILL.md, "Abrir Zaya.command",
  agentes-chat-export.json, images.jpeg, 3 docs de plano antigos (zaya-*) e
  docs/plans/2026-11-23-estudos-automacao-painel.md. Nenhum é código.

DECISÃO DE DESENHO (siga; se discordar, argumente no commit, não no meio)

- O mini é um CLONE git normal: `origin` = https://github.com/talitadeoa/gaia,
  branch main, `git fetch` + `git reset --hard origin/main`. É o único caminho
  que dá auditoria (`git log`), rollback (`reset --hard HEAD~1`) e um estado
  que não apodrece.
- `rsync` NÃO é o mecanismo principal. Ele fica, no máximo, como fallback
  manual documentado — e nunca sem `--dry-run` antes.
- `data/`, `config/`, `.env` estão no `.gitignore`: `reset --hard` não toca
  neles. Ainda assim o primeiro reset deve ter backup (passo 2).
- O `gmin deploy` passa a ser um `git pull` remoto. Mantenha o nome do
  subcomando (as docs e o hábito apontam para ele) e o `--dry-run`.

ENTREGAS (nesta ordem, commits pequenos)

1. deploy/gaia-deploy.sh (novo) — script que roda NO MINI:
     - `set -euo pipefail`; falha se $HOME/gaia não for repo git;
     - `git fetch --prune origin main` e compara; se já está em origin/main,
       sai 0 sem reiniciar nada (isso é o caso comum: push que não mudou o
       mini, ou timer rodando em loop);
     - se há código novo: registra o SHA antigo, `git reset --hard
       origin/main`, e `git clean -fd` LIMITADO ao que o rsync costumava
       deixar (nunca remover data/, config/, .venv/ — confira o .gitignore
       antes de usar -x, e prefira não usar -x);
     - valida o código: `.venv/bin/python -c 'import gaia'`; se falhar,
       `git reset --hard <SHA antigo>` e sai != 0 SEM reiniciar o serviço;
     - reinicia a unidade ativa (gaia-serve.service se ativa, senão
       gaia-telegram.service — mesma regra de scripts/gmin.sh:unit_ativo);
     - `--dry-run` mostra o plano e não mexe em nada;
     - escreve uma linha de log por execução (o journal já serve; não crie
       arquivo de log próprio).

2. deploy/gaia-deploy.service + deploy/gaia-deploy.timer (novos), no mesmo
   molde de deploy/gaia-sb-sync.* (que já funciona):
     - timer: OnBootSec=3min, OnUnitActiveSec=5min, Persistent=false;
     - service: Type=oneshot, ExecStart=%h/.local/bin/deploy-gaia,
       TimeoutStartSec=300, SuccessExitStatus=2 para "conflito/estado sujo".
   Escolha 5 min e JUSTIFIQUE no commit: o web do serve é servido do disco a
   cada request, mas código novo (Python) só vale depois do restart, e cada
   restart custa ~13 s de SIGTERM (AGENTS.md, "armadilhas").

3. scripts/gmin.sh:
     - `cmd_deploy` passa a: (a) garantir o clone saudável no mini
       (`git -C $REMOTE_REPO fetch` + `reset --hard`), com `--dry-run` só
       imprimindo; (b) chamar o restart como hoje. Mantenha a checagem
       `python -c 'import gaia'` entre reset e restart;
     - subcomando novo `gmin deploy install` que copia deploy/gaia-deploy.sh
       para ~/.local/bin/deploy-gaia, as unidades para ~/.config/systemd/user,
       faz daemon-reload e `enable --now` do timer (espelhe cmd_sb install);
     - `gmin deploy status` (novo): mostra HEAD local, origin/main, se o timer
       está ativo e a próxima execução (systemctl --user list-timers);
     - atualize o cabeçalho de ajuda do script (linhas 1-22) e README/docs.

4. Migração do mini (executada por você, uma vez, com backup):
     - backup sem o repo: tar de ~/gaia menos .git/.venv/data/config;
     - `git init -b main && git remote add origin ... && git fetch &&
       git reset --hard FETCH_HEAD` (o `zaya/` rastreado morre aqui);
     - decidir e registrar o destino dos 8 arquivos não versionados: os 3
       docs zaya-* e "Abrir Zaya.command" são lixo do rename -> mover para
       ~/gaia-backups/; agentes-chat-export.json e images.jpeg idem, se você
       confirmar que não são insumo de nada; .commandcode/ e .opencode/ são
       config de ferramenta -> manter (estão no .gitignore? confira; se não,
       adicione ao .gitignore, não ao repo);
     - `.venv`: `rm -rf .venv && uv venv --python 3.12 .venv && uv pip install
       --python .venv/bin/python -e ".[dev]"` (mata o pth órfão de zaya);
     - instalar o timer (passo 3) e provar o ciclo.

5. Testes (sem rede, no CI):
     - tests/test_gmin_deploy.py (novo) com pytest: o script é shell, então
       teste o que for Python/shell puro por subprocess com um repo tmp
       (git init, dois commits, remote local via `git init --bare` em
       tmp_path) e verifique: (a) sem mudança -> sai 0 e não reinicia;
       (b) com commit novo -> HEAD muda para origin/main; (c) código que
       quebra (`import gaia` falha) -> volta ao SHA antigo e sai != 0;
       (d) `--dry-run` não muda HEAD. Marque com @pytest.mark.skipif(not
       shutil.which("git")) e NUNCA toque no repo real nem em SSH.
     - se scripts/gmin.sh ganhar lógica testável em Python, prefira extrair
       para um módulo pequeno em vez de testar bash.

6. Docs (mesma mudança, regra do AGENTS.md):
     - docs/acesso-mini.md: substituir o parágrafo "gmin deploy sincroniza o
       repo menos o que não deve sair do MacBook" pela regra nova (clone git +
       timer de 5 min + `gmin deploy` = fetch/reset/restart);
     - AGENTS.md: armadilha nova ("rsync nunca copia .git: o índice do mini
       fica preso e o `git status` mente") e receita "mudança no fluxo de
       deploy";
     - README.md, seção de operação, se ela citar o deploy;
     - docs/README.md: registrar este plano com status "implementado".

CRITÉRIOS DE ACEITE (prove com saída de comando, não com afirmação)

- `ssh gaia 'cd ~/gaia && git status --porcelain'` -> vazio, e
  `git rev-parse HEAD` == `git rev-parse origin/main` == o SHA do Mac.
- Push novo no Mac: em <= 6 min o mini tem o novo HEAD, o serviço reiniciou e
  `curl -s http://100.x.y.z:8787/health` responde `ok: true`.
- Push que NÃO muda nada para o mini (ex.: só um doc) continua reiniciando?
  Decida: se o SHA mudou, reinicia (simples e previsível); documente.
- Commit com erro de import: o timer NÃO derruba o serviço e o HEAD do mini
  volta ao SHA anterior (prove forçando um import quebrado num clone de teste
  ou num commit real e revertendo).
- `gmin deploy --dry-run` não toca no mini.
- `1018+ passed`, ruff e mypy verdes.

NÃO FAÇA

- Não use `rsync --delete` em /home/talita/gaia: apagaria o `.venv` se ele não
  estivesse excluído, e qualquer exclusão esquecida vira perda silenciosa.
- Não rode dois processos no mini (invariante do AGENTS.md): o timer reinicia
  a unidade ATIVA, nunca liga as duas.
- Não coloque token no remote HTTPS: o repo é público, leitura anônima basta.
  Se um dia virar privado, use deploy key só de leitura (molde: sb_deploy).
- Não commite data/, config/ nem .env — nem "só para sincronizar".
```
