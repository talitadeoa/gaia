# Plano — proatividade muda e ambiente partido (chave Command Code)

> **Status: plano ativo, nada aplicado.** Diagnóstico verificado em 22/11/2026
> 00:10 (MacBook + mini por SSH). Referência atemporal: `../../AGENTS.md`.

**Sintoma relatado:** "agora voltou a ser proativa com a nova api key da
Command Code?"

**Resposta curta:** a chave está boa, mas a proatividade **não** voltou. Há
dois problemas independentes da chave: (1) no mini, o `gaia serve` entrega o
texto proativo no **TTS local de uma máquina headless**, nunca no Telegram —
e o provedor dele está sem saldo (`402`); (2) no Mac, o dono do Telegram
morreu no rename `zaya → gaia` e o `~/.zshrc` aponta para um config de 4
linhas sem `.env`.

---

## 1. O que foi verificado (e o que NÃO é a causa)

| Verificação | Comando | Resultado |
|---|---|---|
| Chave `GAIA_AGENT_API_KEY` (`~/.config/gaia/.env`, `user_2i2…`) | POST `/provider/v1/chat/completions` com `glm-5.3-flash`, `deepseek/deepseek-v4.1-flash`, `…-flash-fast` | **200 OK** nos três |
| Chave `~/.commandcode/auth.json` (`apiKey`) | idem | **200 OK** nos três |
| `auth.json → deepseek.key` (`sk-8db1…`) | idem | 401 — é chave de outra API, ignorada pelo preset |
| Turno real no Mac (config canônica) | `gaia ask "Responda apenas: ok"` | **`ok`** |
| `gaia doctor` (config canônica) | `gaia doctor` | `modelo deepseek/deepseek-v4.1-flash disponível`, 104 modelos |
| Provedor efetivo do mini | `ssh gaia 'gaia doctor'` | `deepseek/deepseek-chat` — **não é a Command Code** |
| Saldo do mini | `curl api.deepseek.com/user/balance` | `is_available: false`, `total_balance: "-0.00"` |

**Conclusão:** a chave nova funciona e resolve o 402 **se** o processo que
importa (o do mini) for apontado para ela. Hoje não é.

---

## 2. Topologia encontrada (quatro "Gaias" concorrentes)

| # | Onde | Config | `.env` | Dados | Estado |
|---|---|---|---|---|---|
| A | `~/.config/gaia/` (padrão do código) | 143 linhas, sem `[career]`/`[telegram]` | sim, completo | `~/.local/share/gaia/zaya.db` (664 turns, parado 12/11) | **usada só quando `ZAYA_*` não está no shell** |
| B | `./config` + `./data` do repo (`Abrir Gaia.command`) | 160 linhas, com `[career]`/`[telegram]` | sim, só `DEEPSEEK_API_KEY` + token | `data/gaia.db` (1086 turns, último 17/11 22:26) | TUI local antiga |
| C | `~/zaya/config` + `~/zaya/data` (**para onde o `.zshrc` aponta**) | **4 linhas**, só `[agent]` | **não existe** | `~/zaya/data/gaia.db` (1 conversa, 0 turns) | **terminal hoje: sem Telegram, sem briefing, sem proatividade** |
| D | mini `gaia` (`100.x.y.z`) | `~/.config/gaia/config.toml` | sim | `/home/talita/.local/share/gaia/gaia.db` (540 KB, vivo) | **dono real**: `gaia-serve.service` ativo |

`~/.zshrc:164-166`:

```sh
export ZAYA_CONFIG="$HOME/zaya/config/config.toml"
export ZAYA_DOTENV="$HOME/zaya/config/.env"
export ZAYA_DATA_DIR="$HOME/zaya/data"
```

`config.py` espelha `ZAYA_*` → `GAIA_*` (`_alias_legacy_env`), então **o shell
vence o padrão**: no terminal, `gaia` lê o config de 4 linhas. Prova:

```
$ .venv/bin/python -c "from gaia.config import Config; c=Config.load(); print(c.config_file, c.agent.model, c.telegram.enabled)"
/Users/talita/zaya/config/config.toml z-ai/glm-5.3-flash False
```

(`z-ai/glm-5.3-flash` veio de um `/modelo` antigo; `fast_model` continua
`deepseek/deepseek-v4.1-flash-fast`, de outro provedor.)

No Mac, a chave sobrevive só porque o preset `commandcode` é `ambient=True` e
lê `~/.commandcode/auth.json` — não porque o `.env` esteja sendo lido.

---

## 3. As cinco causas, com evidência

### 3.1 (a principal) No `serve`, proatividade não vai para o Telegram

`gaia/controller/initiative.py:301`:

```python
async def _say_proactive(self, text: str, count: bool = True) -> None:
    self.ui.proactive(text)          # no serve = RemoteUI = evento SSE (ninguém escuta)
    ...
    if self.channel == "telegram":   # <-- serve nunca cai aqui
        await self._notify_telegram(text)
        return
    await self._speak(text)          # <-- TTS local em máquina headless
```

O cutover de 20/11 moveu o dono do Telegram **para dentro** do `gaia serve`,
que roda com `channel="serve"` (`gaia/cli_serve.py:45`). Resultado: briefing e
fecho da noite chegam (usam `self.telegram.send_message` direto, em
`controller/briefing.py`), mas **lembrete, agenda, foco, estudos, check-in de
ociosidade e saudação** — que passam por `_say_proactive` — vão para o
alto-falante do mini e para um SSE sem cliente.

Evidência no mini:

```
21/11 11:01  WARNING gaia: iniciativa via LLM falhou: 402 Insufficient Balance
21/11 12:00  INFO    gaia: briefing noon de 2026-11-21 enviado
21/11 18:00  INFO    gaia: fecho da noite de 2026-11-21 enviado
21/11 21:39  WARNING gaia.telegram: getUpdates falhou: Too Many Requests
```

Briefing (caminho direto) entrega; iniciativa (caminho `_say_proactive`) só
loga o erro do LLM e cai no fallback `_idle_message()` → `_speak()` → áudio
local. O ciclo das 45 min rodou o dia inteiro e a Talita não recebeu nada.

O mesmo vale para `_speak` (`controller/speech.py:199`): só pula o áudio em
`channel == "telegram"`. O `serve` tem `tts.enabled = true` (edge), então tenta
tocar em host sem saída de áudio.

### 3.2 Mini: provedor errado e sem saldo

```
$ ssh gaia 'gaia doctor'  → provedor do agente deepseek/deepseek-chat
$ curl api.deepseek.com/user/balance → is_available: false
```

O mini não tem `~/.commandcode/auth.json` nem `COMMAND_CODE_API_KEY`/`GAIA_AGENT_API_KEY`
no `.env`. Enquanto isso, todo texto gerado por LLM (iniciativa, insight da
noite, consolidação por LLM) falha com 402.

### 3.3 Mac: o dono do Telegram morreu no rename

- `launchctl print-disabled gui/501` → `"com.zaya.telegram" => disabled`
  (mesmo se habilitado, o plist chama `/Users/talita/.local/bin/zaya`, que
  quebra: `ModuleNotFoundError: No module named 'zaya'` — o pacote virou `gaia`).
- `getUpdates` do bot responde e **ninguém** faz polling no Mac: confirmado por
  chamada direta à API (`pending_update_count: 0`, sem 409).
- `~/.local/bin/zmin` → `/Users/talita/zaya/scripts/zmin.sh` (diretório não
  existe; o script real é `~/gaia/scripts/gmin.sh`). **Corrigido em 2026-11-23**:
  o link morto foi removido e `gmin` aponta para `~/gaia/scripts/gmin.sh`
  (`~/.local/bin` está no `PATH`). O `~/.local/bin/zaya` (que chamava o pacote
  `zaya`, já renomeado para `gaia`) também saiu; o comando é `.venv/bin/gaia`.
- `.venv` tem `zaya_assistant` editable apontando para `file:///Users/talita/zaya`.

### 3.4 Mac: `serve` zumbi de sexta

```
PID 44382 · iniciado 20/11 18:59 · .venv/bin/python -m zaya serve --port 8798 --no-telegram
  ZAYA_DATA_DIR=/tmp/zaya-loc        (vazio: sem gaia.db)
  ZAYA_DOTENV=/Users/talita/zaya/config/.env   (não existe)
  ZAYA_TELEGRAM_ENABLED=false
```

`GET /health` → *empty reply* (código 000): o motor está travado. É anterior ao
rename, roda com `--no-telegram` e não serve proatividade a ninguém.

### 3.5 `.env` do Mac desliga a proatividade por cima do TOML

`~/.config/gaia/.env:8,10` (precedência: ambiente > TOML):

```
GAIA_PROACTIVE_IDLE_CHECKIN=false
GAIA_PROACTIVE_STARTUP_GREETING=false
```

Mesmo consertando o resto, a config canônica do Mac fica sem check-in e sem
saudação — enquanto o TOML diz `idle_checkin = true`.

---

## 4. Correção — ordem obrigatória

> Regra do repo que manda na ordem: **um único processo escritor**. Enquanto o
> mini estiver com o `gaia-serve` ativo, **nada** de poller no Mac. Toda
> alteração de código entra no Mac e desce por `gmin deploy`.

### Fase 0 — fix de código (Mac, branch, TDD)

**0.1 Teste primeiro** (`tests/test_orchestrator.py`, ao lado de
`test_proativo_no_canal_telegram_vai_para_o_chat`):

```python
@pytest.mark.asyncio
async def test_proativo_no_serve_dono_do_telegram_vai_para_o_chat(orch):
    telegram = FakeTelegram()
    orch.telegram = telegram
    orch.channel = "serve"
    orch.owns_telegram = True

    await orch._say_proactive("Bom dia")

    assert telegram.sent == ["Bom dia"]
    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_proativo_no_serve_sem_poller_nao_toca_audio(orch):
    orch.channel = "serve"
    orch.owns_telegram = False

    await orch._say_proactive("Bom dia")

    assert orch.tts.spoken == []
```

**0.2 `gaia/controller/initiative.py`** — o processo que é dono do poller
entrega no chat, independente do nome do canal:

```python
    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.ui.proactive(text)
        if count:
            self._proactive_count += 1
            self._last_proactive = time.monotonic()
        if self.channel == "telegram" or getattr(self, "owns_telegram", False):
            await self._notify_telegram(text)
            return
        await self._speak(text)
```

**0.3 `gaia/controller/speech.py`** — `_speak` não fala num `serve` headless:

```python
    async def _speak(self, text: str) -> None:
        if self.channel in {"telegram", "serve"}:
            return
```

**0.4 Marcar o dono do poller:**

- `gaia/controller/orchestrator.py`, no `__init__` (perto de `self.channel`):
  `self.owns_telegram = channel == "telegram"`
- `gaia/cli_serve.py`, em `_serve_command`, imediatamente antes de
  `servico_telegram = asyncio.create_task(_rodar_telegram(...))`:
  `orchestrator.owns_telegram = True`
- `gaia/telegram/service.py`, `run()`, junto de `start_proactive()`
  (cobre `gaia telegram serve`): `self.orchestrator.owns_telegram = True`

**0.5 Verificação da fase:**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

### Fase 1 — backup e deploy no mini

```bash
# backup do estado do mini antes de tocar
ssh gaia 'mkdir -p ~/gaia-backups/proatividade-20261122 && \
  cp -a ~/.config/gaia/config.toml ~/.config/gaia/.env \
        ~/.local/share/gaia/gaia.db ~/gaia-backups/proatividade-20261122/'

# corrigir o atalho do gmin (aplicado em 2026-11-23: o zmin morto saiu e o
# gmin aponta para ~/gaia/scripts/gmin.sh; ~/.local/bin já está no PATH)
ln -sf ~/gaia/scripts/gmin.sh ~/.local/bin/gmin

gmin deploy --dry-run      # confere o que sobe
gmin deploy                # rsync + restart da unidade ativa
```

O código do mini é de **16/11** (`aca6724`): não tem `SHORTCUTS`,
`default_model`, `save_agent_model(fast_model=)` nem `gaia/estudos/`. Sem o
deploy, os passos da Fase 2 não existem lá.

### Fase 2 — apontar o mini para a Command Code

**2.1 Chave no `.env` do mini** (nunca no TOML):

```bash
ssh gaia 'grep -q "^GAIA_AGENT_API_KEY=" ~/.config/gaia/.env || \
  printf "\nGAIA_AGENT_API_KEY=user_2i2...COLE_A_CHAVE_INTEIRA_AQUI\n" >> ~/.config/gaia/.env'
ssh gaia 'chmod 600 ~/.config/gaia/.env'
```

> A chave do Mac está em `~/.config/gaia/.env` (`GAIA_AGENT_API_KEY`,
> prefixo `user_2i2`, 93 chars). Não cole no TOML nem em commit.

**2.2 Provedor/modelo no TOML do mini** (`~/.config/gaia/config.toml`):

```toml
[agent]
provider = "commandcode"
model = "deepseek/deepseek-v4.1-flash"
fast_model = "deepseek/deepseek-v4.1-flash-fast"
```

Se preferir pelo próprio CLI (faz `.bak` e preserva comentários):

```bash
ssh gaia '/home/talita/gaia/.venv/bin/gaia ask "/modelo cc"'
```

**2.3 Conferir antes de reiniciar:**

```bash
ssh gaia 'cd ~/gaia && .venv/bin/gaia doctor'          # deve dizer commandcode/deepseek/deepseek-v4.1-flash disponível
ssh gaia 'cd ~/gaia && .venv/bin/gaia ask "Responda apenas: ok"'
```

**2.4 Reiniciar e ver o log:**

```bash
gmin restart
ssh gaia 'journalctl --user -u gaia-serve.service -f'
```

Critério: some o `402 Insufficient Balance`. Se aparecer
`iniciativa via LLM falhou`, o `.env` do mini ainda não foi lido.

### Fase 3 — limpar o Mac (para não duplicar poller nem confundir config)

Só depois de a Fase 2 estar verde.

**3.1 Matar o serve zumbi:**

```bash
kill 44382          # 20/11, /tmp/zaya-loc, --no-telegram, /health travado
```

**3.2 `/Users/talita/.zshrc`** — trocar o bloco `Zaya` por nada (o padrão do
código já é `~/.config/gaia/` + `~/.local/share/gaia/`):

```sh
# remover as três linhas:
# export ZAYA_CONFIG / ZAYA_DOTENV / ZAYA_DATA_DIR
```

Abrir shell novo e conferir:

```bash
.venv/bin/python -c "from gaia.config import Config; c=Config.load(); \
print(c.config_file, c.agent.model, c.telegram.enabled, c.proactive.idle_checkin)"
# esperado: /Users/talita/.config/gaia/config.toml  commandcode/...  True  (produto do .env)
```

**3.3 Alinhar o `.env` canônico** (decisão: proatividade ligada, como no mini):

```
GAIA_PROACTIVE_IDLE_CHECKIN=true
GAIA_PROACTIVE_STARTUP_GREETING=true
```

Deixe explícito: com o mini dono do Telegram, a TUI do Mac **não** deve fazer
polling nem enviar proativo. `GAIA_TELEGRAM_ENABLED=true` no Mac serve apenas
para leitura de config; se causar mensagem dupla, o caminho é
`GAIA_TELEGRAM_ENABLED=false` no Mac (a Fase 0 já impede áudio local).

**3.4 Aposentar o legado:**

```bash
# plist desabilitado e apontando para entrypoint morto
launchctl disable gui/501/com.zaya.telegram   # idempotente
mv ~/Library/LaunchAgents/com.zaya.telegram.plist ~/Library/LaunchAgents/com.zaya.telegram.plist.old

# entrypoint quebrado dentro do venv (editable apontando para ~/zaya)
.venv/bin/python -m pip uninstall -y zaya-assistant   # só se aparecer algo estranho no doctor
```

`~/zaya/` inteiro vira histórico: **arquivar** (não apagar). Os bancos divergem
de propósito e não devem ser sincronizados — ver `2026-11-20-handoff.md`.

| Banco | Turns | Último turno | Decisão |
|---|---|---|---|
| mini `~/.local/share/gaia/gaia.db` | vivo | 21/11 | **fonte da verdade** |
| Mac `data/gaia.db` | 1086 | 17/11 22:26 | arquivar (histórico) |
| Mac `~/.local/share/gaia/zaya.db` | 664 | 12/11 11:54 | arquivar (pré-rename) |
| Mac `~/zaya/data/gaia.db` | 0 | — | descartar |

---

## 5. Verificação de ponta a ponta

```bash
# 1) um único dono do Telegram
ssh gaia "ps aux | grep 'gaia serve' | grep -v grep | wc -l"   # 1
ps aux | grep -E 'gaia (serve|telegram)' | grep -v grep        # vazio no Mac

# 2) motor saudável
curl -s http://100.x.y.z:8787/health    # {"ok": true, "model": "commandcode/..."}

# 3) proatividade sai no chat (não no alto-falante)
ssh gaia 'journalctl --user -u gaia-serve.service -n 30 --no-pager | grep -i proativ'
ssh gaia 'sqlite3 ~/.local/share/gaia/gaia.db "select key from meta where key like \"briefing:%\" order by key desc limit 3;"'

# 4) prova viva no Telegram
#    mande "me lembre de beber agua em 2 minutos" para o @talitasgaiabot
#    e confirme a mensagem de confirmação e o disparo 2 min depois
```

**Critério de pronto:** uma mensagem proativa espontânea (lembrete, agenda ou
check-in) chega no Telegram com o mini sendo o único processo e o log sem
`402`.

---

## 6. Riscos e rollback

| Risco | Mitigação |
|---|---|
| Dois pollers → 409 `getUpdates` e briefing duplicado | Fase 3 só com a Fase 2 verde; checar `ps aux` nas duas pontas |
| `gmin deploy` reinicia o serve e paga ~13 s de SIGTERM (worker de memória com `shield`) | esperar o restart terminar antes de testar |
| `save_agent_model` grava `.bak` ao lado do `config.toml` | guardar o backup da Fase 1 |
| Fase 0 muda comportamento da TUI local (proativo deixa de tocar áudio no `channel="serve"`) | testes cobrem os dois canais; TUI usa `channel="cli"` e continua falando |
| `GAIA_TELEGRAM_ENABLED=true` no Mac e no mini | se aparecer mensagem dupla, desligar no Mac |

**Rollback:** `cp ~/gaia-backups/proatividade-20261122/config.toml
~/.config/gaia/config.toml && gmin restart`; para o código,
`git revert` do commit da Fase 0 + `gmin deploy`.

---

## 7. Pendências descobertas (não bloqueiam este plano)

- ~~`~/.local/bin/zmin` e `~/.local/bin/zaya` são links mortos/renomeados;
  `gmin` não está no `PATH` (só o `zmin` quebrado).~~ **Resolvido em
  2026-11-23**: os dois links mortos saíram e `~/.local/bin/gmin` →
  `~/gaia/scripts/gmin.sh`. O mini já tinha esse mesmo link desde 21/11.
- `AGENTS.md` manda usar `gmin`, mas `scripts/gmin.sh` chama
  `gaia telegram serve` na unidade do mini — vale conferir se a unidade ativa
  ainda é a de 16/11 (`gaia-telegram.service` inativo, `gaia-serve` ativo).
- `docs/README.md` não lista este plano (adicionar na tabela de planos).
- O caminho `_say_proactive` não tem teste para `channel="serve"` antes deste
  plano — a lacuna é justamente a causa 3.1.
