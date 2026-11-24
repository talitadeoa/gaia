# Handoff: OpenDesign em tudo (Mac feito, falta commit/push + mini)

Estado: 2026-11-24. HEAD `4fc7140` no Mac e no mini. Nada commitado ainda.
Leia `AGENTS.md` primeiro. Substitui o `2026-11-24-handoff-opendesign-api-key.md`
(que ficou desatualizado: a troca do Mac ja foi feita, e a decisao agora e
"opendesign em TUDO, com as outras chaves guardadas").

---

## PROMPT (colar numa sessao nova)

```
Repo ~/gaia (Python 3.12, .venv). Objetivo: terminar a migracao para o
OpenDesign (gateway do pi) e deixar as chaves DeepSeek/Command Code prontas
para trocar de volta quando quiser.

FEITO no Mac (nao refazer):
- preset `opendesign` em gaia/agent/providers/factory.py (+ aliases od,
  open-design e atalho /modelo od); OPENDESIGN_API_KEY em gaia/config/env.py;
  .env.example e README; 4 testes em tests/test_providers_presets.py.
- Config efetiva do Mac migrada para ~/.config/gaia + ~/.local/share/gaia:
  o bloco ZAYA_* do ~/.zshrc esta COMENTADO (backup ~/.zshrc.bak-*), entao a
  config efetiva e ~/.config/gaia + ~/.local/share/gaia. O banco que vale e o
  do launcher `~/gaia/data` (45 conversas, 1092 turns) copiado para
  ~/.local/share/gaia/gaia.db; o antigo ficou `gaia.db.old-zaya-20261124`.
  A config efetiva foi completada com o que so existia no repo: show_reasoning,
  selection_mode, dashboard_collapsed, personalize/slots/timeout (em [briefing]),
  [career] e [telegram]; `tool_filter` (chave morta, nao existe no modelo) saiu.
  Agora `diff` ignorando comentarios entre as duas configs da vazio.
  ~/zaya continua no disco como backup congelado (fora do repo).
- Backups: ~/backups/pre-fix-db-config-*.tgz e ~/backups/zaya-e-config-*.tgz.
- `ruff check` estava com 16 F401 em tests/: corrigido (tests/conftest.py
  ganhou o fixture `store` e o `cfg` passou a usar o GAIA_CONFIG isolado).
- pytest, ruff e mypy verdes no working tree.

FALTA (ordem):
1. Commit + push (4 commits separados, ver secao Commits abaixo). O mini faz
   fetch + reset --hard origin/main a cada 5 min (gaia-deploy.timer).
2. Mini (`ssh gaia`), config: ~/.config/gaia/config.toml ->
   provider = "opendesign", model = "deepseek-v4.1-flash",
   fast_model = "deepseek-v4.1-flash-eco" (backup .bak-opendesign).
3. Mini, chave: ja tem o bridge instalado (`pi install
   npm:@lokeraar/pi-opendesign-bridge`) mas:
   - ~/.pi/agent/auth.json esta `{}` -> gravar
     {"opendesign": {"type": "api_key", "key": "<chave>"}} com chmod 600;
   - ~/.pi/agent/settings.json -> defaultProvider "opendesign",
     defaultModel "deepseek-v4.1-flash";
   - ~/.config/gaia/.env (600) -> OPENDESIGN_API_KEY=<chave> e
     GAIA_AGENT_API_KEY=<chave>.
   A chave sai de ~/.pi/agent/auth.json do Mac (["opendesign"]["key"],
   sk-amr-...). NUNCA ecoar o valor; mascarar como sk-amr-…
4. Mini, [pi] no config.toml: model = "opendesign/deepseek-v4.1-flash".
   Hoje esta "commandcode/deepseek/deepseek-v4.1-flash" e o auth.json vazio
   fazia o pi cair no COMMAND_CODE_API_KEY do ambiente.
5. Reiniciar `systemctl --user restart gaia-serve.service` (EnvironmentFile so
   e lido no start) e provar com:
   ssh gaia 'cd ~/gaia && .venv/bin/gaia doctor'
     -> "provedor opendesign/deepseek-v4.1-flash" e "API opendesign ... 62 modelos"
   curl -s http://100.x.y.z:8787/health -> model opendesign/deepseek-v4.1-flash
   ssh gaia 'export PATH=$HOME/.pi/agent/bin:$PATH; pi --print --mode json "diga ok"'
     -> provider "opendesign" (nao "commandcode")
   um turno real pelo web (gaia --remote) ou Telegram.
6. Limpeza opcional no mini: DEEPSEEK_API_KEY e COMMAND_CODE_API_KEY ficam
   COMENTADOS (nao apagados) em ~/.config/gaia/.env, com o valor guardado, para
   voltar rapido: e so descomentar e trocar provider/model no TOML.
   Mesmo tratamento em ~/gaia/config/.env (repo; ja esta fora do versionamento).

NAO fazer:
- Nao transferir ~/.config/gh nem o auth.json inteiro do Mac ao mini (o
  auth.json do Mac tem 3 chaves; so a opendesign vai).
- Nao remover a migracao ZAYA_* / zaya.db do codigo (AGENTS.md manda manter).
- Nao rodar rsync no mini.

VERIFICACAO (CI = exatamente isto):
  .venv/bin/python -m pytest -q
  .venv/bin/ruff check gaia tests
  .venv/bin/mypy
Deploy: git push origin main && gmin deploy
```

---

## Commits preparados (working tree atual)

1. `feat(providers): preset opendesign e chave do gateway do pi`
   `gaia/agent/providers/factory.py`, `gaia/config/env.py`, `.env.example`,
   `README.md`, `tests/test_providers_presets.py`
2. `chore(privacidade): IP do tailnet fora do repo e __TAILNET_IP__ no gmin`
   `deploy/gaia-serve-tailnet.service.example`, `scripts/gmin.sh`,
   `docs/acesso-mini.md`, `docs/README.md`, `docs/plans/2026-11-19-*`,
   `2026-11-20-handoff.md`, `2026-11-22-proatividade-e-ambiente.md`,
   `2026-11-23-mini-git-sync.md`, `tests/test_cli.py`, `tests/test_config.py`,
   `tests/test_serve.py`, `tests/test_serve_app.py`
3. `test(control): fixtures isolados e F401 do ruff`
   `tests/conftest.py` + os 12 arquivos de teste do Control Center/automacoes
4. `docs(plans): handoffs de opendesign, backlog, pi e privacidade`
   os 4 arquivos novos em `docs/plans/2026-11-24-*`

## Estado do ambiente (referencia)

- Mac: config efetiva `~/.config/gaia/config.toml` + `~/.local/share/gaia`
  (`GAIA_CONFIG`/`GAIA_DATA_DIR` nao estao no shell; o padrao do codigo e esse).
  `gaia doctor` confirma, e um `gaia ask` de teste gravou turns no banco novo.
  `~/zaya/` sobrou como backup congelado; `~/Library/LaunchAgents/com.zaya.telegram.plist`
  esta disabled (nao mexer sem decidir).
  ATENCAO: um terminal ja aberto ainda tem os ZAYA_* antigos no ambiente;
  `exec zsh` (ou terminal novo) para pegar o `.zshrc` corrigido.
- Mini: `gaia-serve.service` ativo no tailnet, config e `.env` em
  `~/.config/gaia/`, banco em `~/.local/share/gaia/`.
- Chaves de volta: DeepSeek (`DEEPSEEK_API_KEY`) e Command Code
  (`COMMAND_CODE_API_KEY`) so precisam de descomentar + trocar
  provider/model/fast_model no TOML.
