# Handoff: troca da chave para o OpenDesign (Mac feito, mini pendente)

Estado: 2026-11-24, HEAD `4fc7140` no Mac e no mini. As mudanças do Mac estão
**no working tree, sem commit**: elas precisam ir para o `origin/main` para o
mini receber o preset pelo `gaia-deploy.timer`.

---

## Prompt (cole numa sessão nova)

Estamos no repo `~/gaia` (Python 3.12, `.venv`). Leia `AGENTS.md` primeiro.
Sessão anterior trocou a chave de API da Gaia da Command Code para o
**OpenDesign** (o gateway do pi). O Mac já roda com ela; **o mini não**, e o mini
é quem serve o web/tailnet (100.x.y.z:8787). Termine a propagação.

### O que já foi feito (Mac — não refazer)

Arquivos modificados no working tree (sem commit):

- `gaia/agent/providers/factory.py`: preset `opendesign`
  (`api="openai"`, `base_url="https://amr-link.open-design.ai/v1"`,
  `env_keys=("OPENDESIGN_API_KEY", "OPEN_DESIGN_API_KEY")`,
  `auth_file=("~/.pi/agent/auth.json",)` com
  `auth_key_path=("opendesign", "key")`, `ambient=True`,
  `default_model="deepseek-v4.1-flash"`,
  `fast_model="deepseek-v4.1-flash-eco"`), aliases `od`/`open-design` e atalho
  `SHORTCUTS["od"] = ("opendesign", "deepseek-v4.1-flash")`.
- `gaia/config/env.py`: `"opendesign"` em `_PROVIDER_API_KEYS`.
- `.env.example` + `README.md`: preset documentado.
- `tests/test_providers_presets.py`: 4 testes novos (base_url/aliases, chave
  lida de `auth.json`, chave por `OPENDESIGN_API_KEY`, fast model).

Estado de config no Mac (fora do repo):

- `~/zaya/config/config.toml` (é o `ZAYA_CONFIG` do `~/.zshrc`, o efetivo):
  `provider = "opendesign"`, `model = "deepseek-v4.1-flash"`,
  `fast_model = "deepseek-v4.1-flash-eco"`.
- `~/zaya/config/.env` (novo, 600): `OPENDESIGN_API_KEY` + `GAIA_AGENT_API_KEY`.
- `~/.config/gaia/.env`: `GAIA_AGENT_API_KEY` trocada pela do OpenDesign.
- `config/config.toml` (repo) e `~/.config/gaia/config.toml`: mesmos 3 campos,
  com `.bak-opendesign`. `config/.env` (repo) ganhou `OPENDESIGN_API_KEY`.

Verificado no Mac: `gaia doctor` → `✓ provedor opendesign/deepseek-v4.1-flash`,
`✓ 62 modelos`; `gaia ask` responde; fast model (`-eco`) responde; `pytest`,
`ruff check gaia tests` e `mypy` verdes (a suíte completa rodou antes do último
ajuste do `fast_model` do preset, que passou em `test_providers_presets` — rode
os três de novo antes do commit).

### O que falta (mini)

1. **Commit + push** no Mac
   (sugestão: `feat(providers): preset opendesign e troca da chave de API`).
   O mini faz `fetch` + `reset --hard origin/main` a cada 5 min
   (`gaia-deploy.timer`). Confira com `ssh gaia 'cd ~/gaia && git log --oneline -1'`
   e `grep -c opendesign gaia/agent/providers/factory.py` (precisa ser > 0).
2. **Config do mini** (`~/.config/gaia/config.toml`, hoje
   `provider = "deepseek"` / `model = "deepseek-chat"`): trocar para
   `provider = "opendesign"`, `model = "deepseek-v4.1-flash"`,
   `fast_model = "deepseek-v4.1-flash-eco"` (backup `.bak-opendesign`).
3. **Chave no mini** (`~/.config/gaia/.env`, 600). Tem três opções:
   - `OPENDESIGN_API_KEY=sk-amr-...` (recomendado; o mini **já alcança** o
     gateway — `curl -s -o /dev/null -w '%{http_code}'` sem chave deu 401, ou
     seja, resolve/DNS/rota ok);
   - `GAIA_AGENT_API_KEY=<mesma chave>` (funciona: `resolve_api_key` cai nela);
   - copiar o `~/.pi/agent/auth.json` do Mac para o mini — **atenção**: o
     `auth.json` do mini hoje é `{}` (vazio), então não há o que reaproveitar lá;
     e as **três** chaves do arquivo (deepseek, commandcode, opendesign) iriam
     junto. Prefira a variável.
   A chave sai de `~/.pi/agent/auth.json` do Mac (`["opendesign"]["key"]`,
   `sk-amr-...`, 39 chars). **Nunca** ecoar o valor em log/tela; mascarar como
   `sk-amr-…` nos relatórios.
4. **Reiniciar** `systemctl --user restart gaia-serve.service` e conferir:
   - `ssh gaia 'cd ~/gaia && .venv/bin/gaia doctor'` → `✓ provedor
     opendesign/deepseek-v4.1-flash` e `✓ API opendesign ... 62 modelos`;
   - `curl -s http://100.x.y.z:8787/health` → `"model":
     "opendesign/deepseek-v4.1-flash"`;
   - um turno real pelo web (`gaia --remote`) ou pelo Telegram.

### Extra (decisão do dono, perguntar antes)

No mini `[pi] enabled = true` e `model = "commandcode/deepseek/deepseek-v4.1-flash"`,
mas o `~/.pi/agent/auth.json` do mini está `{}` — a ferramenta `pi` de lá não
tem credencial. Os flags do pi (`--model commandcode/...`) não dizem de onde
vem a chave (o provider `opendesign` do bridge é registrado sem `apiKey`), então
**não transfira `~/.config/gh`/`auth.json` às cegas**: teste com um
`pi --print --mode json "diga ok"` no mini antes de afirmar que funciona.
Se funcionar com `OPENDESIGN_API_KEY` no ambiente da unidade, troque `[pi] model`
para `opendesign/deepseek-v4.1-flash`.

### Armadilhas confirmadas nesta sessão

- O `gaia` sem a variável `PI_*` usa `~/.config/gaia/...`, **mas** o `~/.zshrc`
  do Mac exporta `ZAYA_CONFIG`/`ZAYA_DOTENV`/`ZAYA_DATA_DIR` → o efetivo no
  shell é `~/zaya/config/*`. `gaia doctor` mostra o caminho real.
- `COMMAND_CODE_API_KEY` e `DEEPSEEK_API_KEY` **continuam** no `.env` do mini
  (e `DEEPSEEK_API_KEY` no `config/.env` do repo). São inofensivas com o
  provedor em `opendesign` (a chave é escolhida pelo provedor configurado), mas
  não são removidas por esta mudança.
- `EnvironmentFile=-%h/.config/gaia/.env` é lido **no start** da unidade: sem
  `systemctl --user restart`, a chave nova não entra no processo.
- Um restart do `gaia serve` derruba as sessões de cookie do painel/chat (nonce
  em memória); o painel refaz o login se tiver o token no `localStorage`.
- Não existe `instances.json` no Mac — o seletor de instância do web é opcional;
  "web no tailnet" = o `gaia serve` do mini.

### Verificação obrigatória (CI = exatamente isto)

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

Deploy: `git push origin main && gmin deploy`; o mini não usa `rsync`.
