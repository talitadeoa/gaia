# Acesso ao mini a partir do MacBook

O mini (`gaia`) é headless na prática. Em produção o `gaia serve` é o
**único** processo dono do banco, do Second Brain, dos workers e do Telegram;
Mac, navegador e celular são telas (`gaia --remote` ou a web em `GET /`). Para
administrar, tudo sai do MacBook por SSH com chave — sem senha, sem abrir porta
no roteador.

## Control Center: o dia a dia é o navegador

O painel roda **no mini** (mesmo processo do motor) e é acessado de qualquer
tela do tailnet:

```
http://gaia:8787/control          # MagicDNS, funciona fora de casa
http://100.x.y.z:8787/control    # IP do tailnet (veja 'Fora de casa')
```

A tela pede o token do painel (`GAIA_CONTROL_TOKEN`, separado do token do chat)
uma vez por navegador e o troca por uma sessão: cookie `HttpOnly` com
`Path=/control` e `SameSite=Strict`, emitido por `POST /control/api/session`.
Depois disso o token não vai na URL nem a cada requisição. Sem ele, `/control`
mostra a instrução e `/control/api/*` responde 503.

O chat (`/`) segue a mesma ideia com o token do chat: `POST /session` emite o
cookie `gaia_chat` (`Path=/`) e o `Bearer` continua valendo para `curl` e
`gaia --remote`.

Guardar o token para "não digitar nunca mais" é **opt-in**: a caixa "lembrar o
token neste navegador" no modal. Desmarcada (padrão), nada do segredo fica no
navegador — a sessão vale até o cookie vencer (12 h) e o modal volta. Marcada,
o token fica no `localStorage` daquele endereço e a tela refaz a troca quando o
cookie morre. Como cookie é por origem, MagicDNS e IP do tailnet são duas
sessões (e dois "lembrar"): cada um pede o token uma vez, se você quiser.
O botão **sair** revoga a sessão no servidor e apaga o token guardado.

Por que aqui e não no MacBook: o painel lê o `gaia.db`, a agenda
(`automations`) e os workers **do processo que é dono deles**. Um `gaia serve`
no MacBook mostraria o banco do MacBook — outra Gaia, outro histórico, outra
agenda. A regra "um único escritor" vale também para o painel.

O chat web é a outra tela: `http://gaia:8787/` (mesmo endereço, token do chat).

Para alternar entre mini e Mac pela própria tela, crie
`~/.local/share/gaia/instances.json` (no mini) com as duas máquinas:

```json
[
  {"id": "mini", "name": "Mini PC", "url": "http://gaia:8787",
   "serve_token": "...", "control_token": "..."},
  {"id": "mac", "name": "MacBook", "url": "http://100.x.y.z:8787",
   "serve_token": "...", "control_token": "..."}
]
```

`chmod 600` nele (tem segredos). O seletor do header mostra online/offline
(sondado pelo servidor) e trocar de instância navega para o outro endereço —
o MacBook precisa ter o próprio `gaia serve` rodando, com o banco dele.

## O básico que já existe

`~/.ssh/config` no MacBook:

```sshconfig
Host gaia
    HostName 192.168.0.10   # IP do mini na LAN; descubra com `gmin doctor`
    User talita
    IdentityFile ~/.ssh/id_ed25519
```

Daí sai `ssh gaia` e mais nada é necessário para entrar.

## `gmin` — o kit de administração

`scripts/gmin.sh`, symlinkado em `~/.local/bin/gmin` (que já está no `PATH`).

```bash
gmin doctor              # ssh, alias vs IP real, serviço, agenda, ferramentas, log
gmin logs -f             # journal do serviço (unidade de usuário: sem sudo)
gmin restart             # reinicia a unidade ativa (serve ou telegram)
gmin serve status        # /health no loopback e no tailnet
gmin deploy --dry-run    # mostra o que iria; depois `gmin deploy` atualiza e reinicia
gmin deploy status       # HEAD do mini vs origin/main + estado do timer
gmin deploy install      # timer de 5 min: o mini puxa o push sozinho
gmin test                # smoke test: importa tudo, confere agenda e permissões
gmin agenda [AAAA-MM-DD] # exatamente o que a Gaia vê na Agenda
gmin ask "pergunta"      # conversa com a Gaia do mini sem passar pelo Telegram
gmin shell               # sessão `screen` persistente (sobrevive a queda de wifi)
gmin sudo <comando…>     # roda com sudo; a senha é digitada AQUI, no MacBook
gmin sb sync             # sincroniza o SecondBrain agora (Mac <-> mini)
gmin sb status           # estado do sync nos dois lados
gmin sb install          # timer de 15 min no mini (systemd)
gmin sb install-mac      # agente launchd de 15 min no Mac
```

`gmin deploy` **não copia arquivo**: o mini é um clone git normal
(`origin` = GitHub). O comando faz `git fetch` + `reset --hard origin/main` e
só reinicia o serviço se o código novo importar; se não importar, volta ao SHA
anterior e não reinicia.

O caminho normal é automático: `gmin deploy install` instala
`gaia-deploy.timer` (a cada 5 min), e **todo push na `main` chega no mini em até
5 min**, com reset para o SHA novo e restart da unidade ativa. Cinco minutos, e
não um, porque cada restart custa ~13 s de SIGTERM; sem commit novo o script sai
0 sem reiniciar nada. `data/`, `config/` e `.env` estão no `.gitignore`, então o
reset não toca neles.

`rsync` não é mais o transporte. Ele nunca copiou `.git` — foi assim que o
índice do mini ficou preso 75 commits atrás, com o disco já no código novo.

## Second Brain: um repo, duas máquinas

`~/SecondBrain` é um repositório git nas duas pontas
(`github.com/talitadeoa/second-brain`, privado). O que sincroniza é **texto**:
`Memoria/`, `Sonhos/`, `Inbox/`, `Pesquisa/`, `planilhas/`, `cv/`, `preferences.md`.
O que **nunca** sincroniza: `gaia.db`, `sb_index.db` (ambos no `.gitignore`) e
`.gaia/`, `.zaya/` (backups locais). Sincronizar banco SQLite corrompe — é a
mesma regra do "um único processo escritor".

```bash
sb-sync                  # nas duas máquinas: commita, puxa, empurra
gmin sb sync             # faz os dois lados de uma vez, de fora
```

Agendamento (uma vez só):

```bash
gmin sb install          # mini: gaia-sb-sync.timer, a cada 15 min
gmin sb install-mac      # Mac: launchd com.gaia.sbsync, a cada 15 min
```

Regras do `scripts/sb-sync.sh`, que valem para qualquer sincronização manual:

- **Conflito não é resolvido sozinho.** O script aborta com exit 2, deixa a
  árvore limpa e imprime o que resolver. Perder um sonho ou uma memória porque
  "o git resolveu" é pior que ficar um tempo sem sincronizar.
- **Identidade explícita.** O mini não tem `git config --global`; o script falha
  se `user.name`/`user.email` não estiverem no repo local.
- **Credencial própria.** O mini empurra com a chave `~/.ssh/sb_deploy` (deploy
  key só neste repo, via `core.sshCommand`), não com a chave pessoal do Mac.
- **Sem `--force`, sem `--delete`.** Nada de espelhar pasta: o que existe nas
  duas pontas só muda por commit.

O que **não** se resolve por aqui: memória/sonho que já divergiram no texto
(aconteceu em 17–21/11, quando o Mac e o mini consolidavam o mesmo dia). A
unificação de 22/11 está em `~/gaia-backups/sb-unificacao-20261122/`, com
`conflitos-mac/` e `conflitos-mini/` de cada arquivo divergente.

## Uma vez só: senha do sudo

`sudo` no mini pede senha interativa — mas isso **não** obriga a ir até lá:

```bash
gmin sudo -v                                   # valida o sudo por 15 min
gmin sudo systemctl restart avahi-daemon        # conserta o nome .local
gmin sudo apt install -y tmux fail2ban         # opcionais
```

## Itens de uma vez (e por que)

| Comando | Por quê |
|---|---|
| `sudo systemctl restart avahi-daemon` | o mDNS ainda anuncia o nome antigo (`talita-default-string.local`); depois disso `ssh gaia.local` funciona e o IP deixa de importar |
| `sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && sudo systemctl restart ssh` | fecha login por senha (hoje ninguém usa: 113 logins por chave, 0 por senha) |
| `sudo apt install -y tmux` | `screen` funciona, `tmux` é mais confortável para sessões longas |
| `sudo apt install -y fail2ban` | só faz sentido se algum dia o SSH ficar exposto |

## IP dinâmico

`enp1s0` está em DHCP, então o alias pode quebrar um dia.
`gmin doctor` compara o alias com o IP real e avisa. Três saídas, em ordem de
preferência:

1. **mDNS**: depois do restart do avahi, `HostName gaia.local` no
   `~/.ssh/config` — imune a mudança de IP.
2. **Reserva DHCP** no roteador, amarrando o MAC do mini ao IP atual.
3. `gmin doctor` + editar o `HostName` na mão quando avisar.

## Fora de casa

O SSH é só LAN. O acesso remoto é por VPN de malha (Tailscale), **já instalada**
no mini, no MacBook e no iPhone. O IP do tailnet nao vai no repo (ele e
publico): descubra com `tailscale ip -4` ou `gmin doctor`, e o `gaia serve`
escuta nesse IP com token. O MagicDNS (`<host>.<tailnet>.ts.net`) dispensa o
numero. O SSH e a web deixam de depender de estar em casa, com ACL por maquina
— nada de `iptables` manual nem de expor a 22 na internet.

Se precisar reinstalar em outra máquina:

```bash
brew install --cask tailscale      # macOS
gmin sudo apt install -y tailscale && gmin sudo tailscale up
gmin serve install --tailnet       # exige GAIA_SERVE_TOKEN no .env
```

## GitHub e o pi no mini (a Gaia com conta propria)

O mini nao tem desktop: nenhuma sessao grafica, `DRM` desconectado. Isso nao
impede login nenhum dos tres servicos, porque so um deles precisaria de tela:

| Servico | Como autentica no mini | Onde fica o segredo |
|---|---|---|
| GitHub | device flow (`piratistemica`) | `~/.config/gh/hosts.yml` (600) — **nunca** no `.env` |
| Google | OAuth com `--no-launch-browser` + `ssh -L` do redirect | `~/.config/gcloud/` |
| X | OAuth2 PKCE com `redirect_uri=localhost` + mesmo `ssh -L` | json de token do cliente |

O truque que dispensa instalar desktop: o fluxo OAuth escuta em
`127.0.0.1:PORTA` **do mini**; do MacBook, `ssh -N -L PORTA:localhost:PORTA gaia`,
e o consentimento abre no Safari do Mac. Nenhuma janela no mini e o `code` cai
direto no processo de la.

### GitHub: conta propria `piratistemica` (estado atual)

Feito por **device flow** — nao precisou de PAT nem de senha no terminal. O
codigo de 8 digitos e digitado em `github.com/login/device` no navegador do
Mac, numa **janela privada** logada na conta da Gaia (sessao normal
autorizaria a sua).

```bash
ssh gaia 'gh auth login --hostname github.com --git-protocol https \
  --scopes repo,read:org,workflow'
```

Escopos do token: `gist`, `read:org`, `repo`, `workflow`. `gh auth setup-git`
depois disso escreve o helper em `~/.gitconfig`:

```
[credential "https://github.com"]
    helper = !/usr/bin/gh auth git-credential
```

**O segredo fica em `~/.config/gh/hosts.yml` (600) e em mais lugar nenhum.**
Nada de `GH_TOKEN` no `~/.config/gaia/.env`: aquele arquivo e o
`EnvironmentFile=` do `gaia-serve.service`, entao o token entraria no
`os.environ` e o `bash` do agente o leria. O subprocesso do `pi` herda a
credencial por `HOME` (o servico roda com `HOME=/home/talita`), que e o
caminho desejado. Se um dia o `GH_TOKEN` for necessario no ambiente, use o
cofre (`~/.config/gaia/secrets/github.env`, lido sob demanda por
`gaia/secrets.py`) — nunca o `.env`.

Identidade do git no mini (global e no `~/pr-workspace`):
`piratistemica <piratistemica@users.noreply.github.com>`.

### `~/pr-workspace`: fork como `origin`, upstream como `upstream`

`~/gaia` e resetado (`git reset --hard origin/main`) a cada 5 min pelo
`gaia-deploy.timer`. Branch de PR aberta ali e apagada. O clone de trabalho do
pi e outro, e ele usa o **fork** como `origin` — a conta da Gaia nao tem push
no repositorio da `talitadeoa` (`403`):

```bash
ssh gaia 'cd ~/pr-workspace && \
  git remote set-url origin https://github.com/piratistemica/gaia.git && \
  git remote add upstream https://github.com/talitadeoa/gaia.git'
```

Mesmo esquema em `~/pr-workspace/agenda` e `~/pr-workspace/abin-suite`
(`origin` = fork `piratistemica/*`, `upstream` = `talitadeoa/*`). Os clones
vizinhos ficam fora do indice da `gaia` por `.git/info/exclude` (nao no
`.gitignore`, que e versionado).

O fluxo de PR e: commitar num branch do fork, `git push origin <branch>`, e
abrir contra o upstream nomeando o dono do fork:

```bash
gh pr create --repo talitadeoa/agenda --base master \
  --head piratistemica:<branch> --title "..." --body "..."
gh pr create --repo talitadeoa/gaia   --base main   \
  --head piratistemica:<branch> --title "..." --body "..."
```

`--repo`/`--head` sao obrigatorios: dentro do fork o `gh pr create` sem eles
tenta abrir PR do fork para o fork (ou aborta pedindo `--head`). Validado de
ponta a ponta: PR `talitadeoa/agenda#44` criado e fechado pela conta da Gaia.

### Branch padrao protegida (`scripts/branches-protegidas.sh`)

O `main` do `gaia` (e a branch padrao de `second-brain`, `agenda`, `abin-suite`,
`estudos`) tem um **ruleset** `protege-branch-padrao`: `deletion` bloqueada e
`non_fast_forward` bloqueado (force push nao passa). Push normal continua
livre — nao ha PR obrigatorio nem status check exigido — porque o
`gaia-deploy.timer` faz `reset --hard origin/main` e o `sb-sync` empurra commit
direto na `main`.

```bash
scripts/branches-protegidas.sh --check   # mostra o que falta (nao muda nada)
scripts/branches-protegidas.sh           # aplica; rode de novo a vontade
OWNER=piratistemica REPOS=gaia scripts/branches-protegidas.sh
```

O ruleset segue a branch padrao do repo (`~DEFAULT_BRANCH`), entao `master`
recebe a mesma regra sem ajuste. `bypass_actors` e vazio: nem admin escapa, e
editar a regra exige desligar o ruleset. O script **nao** guarda token — usa o
`gh` ja autenticado (o fork `piratistemica/*` pode rodar em modo `--check`, mas
so tem admin onde a conta da Gaia e dona).

> `allow_force_pushes`/`allow_deletions` da API antiga de
> `/branches/{branch}/protection` foram **removidos** em favor do ruleset:
> manter os dois ligados so duplicava a regra em telas diferentes.

### pi instalado e apontado para o Command Code

```bash
ssh gaia 'curl -fsSL https://pi.dev/install.sh | sh'   # deixa ~/.pi/agent/bin/pi
ssh gaia 'export PATH=$HOME/.pi/agent/bin:$PATH; pi --version'
```

O pi nao herda `GAIA_AGENT_API_KEY`: ele quer o nome do provedor
(`DEEPSEEK_API_KEY` etc.). No mini a chave DeepSeek esta **sem saldo** (`402`),
entao o `~/.pi/agent/models.json` aponta o provedor `commandcode` para
`https://api.commandcode.ai/provider/v1` com `apiKey: "${COMMAND_CODE_API_KEY}"`
— a mesma chave que ja esta no `.env` da Gaia. `settings.json` escolhe
`defaultProvider`/`defaultModel` para o pi funcionar sem `--model`.

## O que **não** fazer

- Port forwarding de SSH no roteador: é o caminho mais curto para brute force
  contínuo.
- `NOPASSWD: ALL` no sudoers: com `auto_approve` no agente, isso é root a um
  comando de distância. Se quiser tirar a senha de alguns comandos, use um
  drop-in em `/etc/sudoers.d/` com lista explícita (`systemctl restart ssh`,
  `journalctl`) — nunca `ALL`.
