#!/usr/bin/env bash
# gmin — administra a Gaia do Mac mini a partir do MacBook, sem ir até ele.
#
#   gmin doctor            # ssh, serviço, agenda, IP vs alias
#   gmin logs [-f]         # journal do serviço (sem sudo, é unidade de usuário)
#   gmin restart           # reinicia o serviço do Telegram
#   gmin deploy [--dry-run]# leva o mini ao origin/main e reinicia
#   gmin deploy install    # timer de 5 min: o mini puxa o push sozinho
#   gmin deploy status     # HEAD do mini vs origin/main + estado do timer
#   gmin deploy logs       # journal do timer de deploy
#   gmin test              # smoke test das ferramentas/agenda no mini
#   gmin agenda [AAAA-MM-DD]# o que a Gaia vê na Agenda
#   gmin ask "pergunta"    # conversa com a Gaia do mini, sem Telegram
#   gmin serve install [--tailnet] # instala a unidade (NAO liga)
#   gmin serve cutover     # para o Telegram e liga o serve (um processo so)
#   gmin serve rollback    # volta o dono do Telegram para o gaia-telegram
#   gmin serve logs [-f]   # journal do gaia-serve
#   gmin serve status      # estado do gaia-serve e /health
#   gmin sb sync           # sincroniza o SecondBrain agora (Mac <-> mini)
#   gmin sb install        # instala o timer de 15 min no mini (systemd)
#   gmin sb install-mac     # instala o agente launchd no Mac
#   gmin sb status         # estado do sync nos dois lados
#   gmin shell             # sessão persistente (screen) no mini
#   gmin sudo <comando…>   # roda com sudo; a senha é digitada AQUI
#
# Tudo por SSH com chave: nada de expor porta, nada de senha por comando.
set -euo pipefail

HOST="${GMIN_HOST:-gaia}"
LOCAL_REPO="${GMIN_LOCAL_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
REMOTE_REPO="${GMIN_REMOTE_REPO:-/home/talita/gaia}"
REMOTE_PY="$REMOTE_REPO/.venv/bin/python"
UNIT="gaia-telegram.service"
SERVE_UNIT="gaia-serve.service"
SB_UNIT="gaia-sb-sync"
DEPLOY_UNIT="gaia-deploy"
LOCAL_SB="${GAIA_SECONDBRAIN:-$HOME/SecondBrain}"
REMOTE_SB="${GMIN_REMOTE_SB:-/home/talita/SecondBrain}"

SSH=(ssh -o ConnectTimeout=8 "$HOST")

die() { printf 'gmin: %s\n' "$*" >&2; exit 1; }
info() { printf '\033[1m%s\033[0m\n' "$*"; }

need_ssh() {
  "${SSH[@]}" true 2>/dev/null || die "sem SSH em '$HOST' — rede caiu ou o IP mudou (gmin doctor)"
}

cmd_doctor() {
  need_ssh
  info "== ssh =="
  "${SSH[@]}" 'printf "host=%s  ip=%s  uptime=%s\n" "$(hostnamectl hostname)" \
    "$(hostname -I | awk "{print \$1}")" "$(uptime -p)"'
  local alias_ip
  alias_ip=$(awk '/^Host gaia$/{f=1} f&&/HostName/{print $2; exit}' ~/.ssh/config)
  local real_ip
  real_ip=$("${SSH[@]}" "hostname -I | awk '{print \$1}'")
  if [ "$alias_ip" = "$real_ip" ]; then
    printf 'alias: %s (ok)\n' "$alias_ip"
  else
    printf 'alias: %s — mini está em %s (atualize ~/.ssh/config ou use gaia.local)\n' \
      "$alias_ip" "$real_ip"
  fi

  if ping -c1 -W1 gaia.local >/dev/null 2>&1; then
    printf 'mDNS: gaia.local responde (alternativa ao IP)\n'
  else
    printf 'mDNS: gaia.local nao responde (da para reiniciar o avahi no mini)\n'
  fi

  info "== serviço =="
  "${SSH[@]}" "systemctl --user is-active $UNIT; systemctl --user show -p ActiveEnterTimestamp --value $UNIT"

  info "== agenda + ferramentas =="
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -" <<'PY'
from pathlib import Path
from gaia.config import Config
from gaia.agenda.client import AgendaClient
from gaia.agent.tools import ToolContext, build_tools

cfg = Config.load()
tools = build_tools(ToolContext(Path.cwd(), True, cfg.agenda))
agenda = AgendaClient(cfg.agenda)
print("agenda:", "ok" if agenda.enabled else "DESABILITADA", "| env:", cfg.agenda.env_file)
print("ferramentas de agenda:", [n for n in tools.names() if n.startswith("agenda_")] or "NENHUMA")
print("telegram: auto_approve=%s shell_access=%s" % (
    cfg.telegram.auto_approve, cfg.telegram.shell_access))
PY

  info "== última linha do log =="
  "${SSH[@]}" "tail -n 3 ~/.local/share/gaia/logs/gaia.log 2>/dev/null || echo '(sem log ainda)'"
}

cmd_logs() {
  need_ssh
  if [ "${1:-}" = "-f" ]; then
    "${SSH[@]}" journalctl --user -u "$UNIT" -f -n 40
  else
    "${SSH[@]}" journalctl --user -u "$UNIT" --no-pager -n 40
  fi
}

# Qual unidade é a dona agora: o serve (novo) ou o telegram (antigo).
unit_ativo() {
  "${SSH[@]}" "systemctl --user is-active $SERVE_UNIT >/dev/null 2>&1 \
    && echo $SERVE_UNIT || echo $UNIT"
}

cmd_restart() {
  need_ssh
  local alvo
  alvo="$(unit_ativo)"
  "${SSH[@]}" "systemctl --user restart $alvo && sleep 3 && systemctl --user is-active $alvo"
  printf 'serviço reiniciado em %s (%s)\n' "$HOST" "$alvo"
}

cmd_serve() {
  local acao="${1:-status}"
  need_ssh
  case "$acao" in
    install)
      local origem="$LOCAL_REPO/deploy/gaia-serve.service"
      local temporario=""
      if [ "${2:-}" = "--tailnet" ]; then
        origem="$LOCAL_REPO/deploy/gaia-serve-tailnet.service.example"
        # Escutar no tailnet so faz sentido com token: sem ele o serve nega
        # o bind (e o systemd entraria em loop de reinicio).
        "${SSH[@]}" "grep -q '^GAIA_SERVE_TOKEN=.\\+' ~/.config/gaia/.env" \
          || die "sem GAIA_SERVE_TOKEN em ~/.config/gaia/.env (a variante tailnet exige token)"
        # O molde do repo nao carrega IP (o repo e publico): o IP do tailnet
        # desta maquina entra aqui, e o que vai para o mini e a versao cheia.
        local ip_tailnet
        ip_tailnet=$("${SSH[@]}" 'tailscale ip -4 | head -1')
        [ -n "$ip_tailnet" ] || die "tailnet sem IPv4 no mini (rode 'tailscale up')"
        temporario="$(mktemp -d)"
        sed "s/__TAILNET_IP__/$ip_tailnet/g" "$origem" > "$temporario/$SERVE_UNIT" \
          || die "falha ao preencher o IP na unidade"
        grep -q '__TAILNET_IP__' "$temporario/$SERVE_UNIT" \
          && die "unidade com __TAILNET_IP__ sobrando: $origem"
        origem="$temporario/$SERVE_UNIT"
      fi
      info "instalando $SERVE_UNIT (sem ligar)"
      "${SSH[@]}" "mkdir -p ~/.config/systemd/user" || die "sem ~/.config/systemd/user"
      scp -q "$origem" "$HOST:~/.config/systemd/user/$SERVE_UNIT" \
        || die "falha ao copiar a unidade"
      [ -z "$temporario" ] || rm -rf "$temporario"
      "${SSH[@]}" "systemctl --user daemon-reload" || die "daemon-reload falhou"
      printf 'unidade instalada (inativa). Para trocar de dono do Telegram:\n'
      printf '  gmin serve cutover\n'
      ;;
    cutover)
      info "parando o Telegram ($UNIT) e ligando o serve ($SERVE_UNIT)"
      "${SSH[@]}" "systemctl --user disable --now $UNIT 2>/dev/null || true; \
        systemctl --user enable --now $SERVE_UNIT"
      sleep 3
      "${SSH[@]}" "systemctl --user is-active $SERVE_UNIT; \
        systemctl --user is-active $UNIT || echo \"$UNIT parado (esperado)\""
      printf 'dono do Telegram agora: %s em %s\n' "$SERVE_UNIT" "$HOST"
      printf 'conferir: gmin serve logs -f  (409 = dois pollers) e gmin serve status\n'
      ;;
    rollback)
      info "voltando o dono do Telegram para $UNIT"
      "${SSH[@]}" "systemctl --user disable --now $SERVE_UNIT 2>/dev/null || true; \
        systemctl --user enable --now $UNIT"
      sleep 3
      "${SSH[@]}" "systemctl --user is-active $UNIT; \
        systemctl --user is-active $SERVE_UNIT || echo \"$SERVE_UNIT parado (esperado)\""
      ;;
    logs)
      shift || true
      "${SSH[@]}" "journalctl --user -u $SERVE_UNIT -n 80 --no-pager $*"
      ;;
    status)
      # A unidade pode escutar no loopback (teste) ou no IP do tailnet
      # (clientes de fora). /health e publico: nao pede token.
      "${SSH[@]}" "systemctl --user is-active $SERVE_UNIT; \
        systemctl --user is-enabled $SERVE_UNIT; \
        H=\$(tailscale ip -4 | head -1); \
        for U in http://127.0.0.1:8787 \${H:+http://\$H:8787}; do \
          case \"\$U\" in http://127.0.0.1*) D=loopback ;; *) D=tailnet ;; esac; \
          printf '%s: ' \"\$D\"; \
          curl -sf -m 3 \"\$U/health\" && { echo; exit 0; }; \
          echo 'sem resposta'; \
        done"
      ;;
    *) die "use: gmin serve install [--tailnet]|cutover|rollback|logs|status" ;;
  esac
}

# O mini e um clone git (o rsync nunca copiou .git — foi assim que o indice
# dele ficou 75 commits atras). O transporte agora e o proprio GitHub.
cmd_deploy() {
  case "${1:-}" in
    install)
      need_ssh
      info "instalando deploy-gaia + timer de 5 min no mini"
      "${SSH[@]}" 'mkdir -p ~/.local/bin ~/.config/systemd/user' \
        || die "falha ao preparar os diretorios"
      scp -q "$LOCAL_REPO/deploy/gaia-deploy.sh" "$HOST:~/.local/bin/deploy-gaia" \
        || die "falha ao copiar deploy-gaia"
      "${SSH[@]}" 'chmod +x ~/.local/bin/deploy-gaia' || die "falha no chmod"
      scp -q "$LOCAL_REPO/deploy/gaia-deploy.service" \
        "$HOST:~/.config/systemd/user/$DEPLOY_UNIT.service" || die "falha ao copiar a unidade"
      scp -q "$LOCAL_REPO/deploy/gaia-deploy.timer" \
        "$HOST:~/.config/systemd/user/$DEPLOY_UNIT.timer" || die "falha ao copiar o timer"
      "${SSH[@]}" "systemctl --user daemon-reload && systemctl --user enable --now $DEPLOY_UNIT.timer" \
        || die "falha ao ativar o timer"
      "${SSH[@]}" "systemctl --user list-timers $DEPLOY_UNIT.timer --no-pager"
      printf 'ativo: no maximo 5 min depois de um push na main o mini atualiza e reinicia.\n'
      ;;
    status)
      need_ssh
      "${SSH[@]}" "cd $REMOTE_REPO && \
        printf 'HEAD:        %s\n' \"\$(git rev-parse --short HEAD)\" && \
        printf 'origin/main: %s\n' \"\$(git rev-parse --short origin/main 2>/dev/null || echo '(sem fetch ainda)')\" && \
        printf 'sujo:        %s arquivo(s)\n' \"\$(git status --porcelain | wc -l)\" && \
        git log -1 --format='ultimo commit: %h %s (%cr)'; \
        echo; systemctl --user is-active $DEPLOY_UNIT.timer; \
        systemctl --user list-timers $DEPLOY_UNIT.timer --no-pager | head -3"
      ;;
    logs)
      need_ssh
      "${SSH[@]}" "journalctl --user -u $DEPLOY_UNIT.service --no-pager -n 40"
      ;;
    ''|--dry-run)
      need_ssh
      local dry=0
      if [ "${1:-}" = "--dry-run" ]; then dry=1; fi
      info "$HOST: fetch origin/main em $REMOTE_REPO"
      local saida
      saida=$("${SSH[@]}" "cd $REMOTE_REPO || exit 1; \
        git fetch --prune origin main >/dev/null 2>&1 || { echo 'fetch falhou (rede?)'; exit 1; }; \
        A=\$(git rev-parse HEAD); B=\$(git rev-parse origin/main); \
        if [ \"\$A\" = \"\$B\" ]; then echo SEM_MUDANCA; exit 0; fi; \
        echo \"\$(git rev-parse --short \$A) -> \$(git rev-parse --short \$B): \$(git log -1 --format=%s \$B)\"; \
        if [ $dry = 1 ]; then exit 0; fi; \
        git reset --hard \$B >/dev/null && git clean -fdq \
          || { echo 'reset falhou (estado sujo?)'; exit 1; }; \
        $REMOTE_PY -c 'import gaia' \
          || { echo 'codigo novo nao importa — voltando para' \$A; git reset --hard \$A >/dev/null; exit 1; }" \
        ) || die "deploy abortado no mini — serviço NÃO reiniciado"
      printf '%s\n' "$saida"
      if [ "$saida" = SEM_MUDANCA ]; then
        info "mini ja estava em origin/main — nada reiniciado"
        return 0
      fi
      if [ $dry = 1 ]; then
        info "dry-run: nada foi alterado nem reiniciado"
        return 0
      fi
      # cmd_restart escolhe a unidade ativa, então o deploy não reinicia um
      # serviço parado nem deixa o serve rodando código velho.
      cmd_restart
      ;;
    *) die "use: gmin deploy [--dry-run]|install|status|logs" ;;
  esac
}

cmd_test() {
  need_ssh
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -" <<'PY'
"""Smoke test: o venv do mini não tem pytest, então conferimos o essencial."""
import asyncio
from pathlib import Path
from gaia.config import Config
from gaia.agenda.client import AgendaClient
from gaia.agent.tools import ToolContext, build_tools
from gaia.agent.tools.agenda import build_agenda_tools
from gaia.briefing.compose import collect_tasks
from gaia.telegram.service import telegram_permission

cfg = Config.load()
ctx = ToolContext(Path.cwd(), True, cfg.agenda)
tools = build_tools(ctx)
assert {"read", "bash"} <= set(tools.names()), tools.names()
if cfg.agenda.enabled:
    assert "agenda_dia" in tools.names(), "ferramentas de agenda não apareceram"
    rows = asyncio.run(AgendaClient(cfg.agenda).notes_on(__import__("gaia.agenda.client", fromlist=["x"])._today_local()))
    blanks = [r for r in rows if r.get("is_checkbox") and not str(r.get("text") or "").strip()]
    itens = collect_tasks(rows)
    assert not any(not i.text for i in itens), "tarefa vazia no briefing"
    print("agenda ok: %d notas, %d em branco ignoradas, %d tarefas" % (len(rows), len(blanks), len(itens)))

pol = telegram_permission(cfg.telegram)
print("telegram: bash=%s agenda_dia=%s" % (pol("bash", []), pol("agenda_dia", [])))
print("smoke ok")
PY
}

cmd_agenda() {
  need_ssh
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -" "$1" <<'PY'
import sys
from pathlib import Path
from gaia.config import Config
from gaia.agent.tools import ToolContext
from gaia.agent.tools.agenda import build_agenda_tools

cfg = Config.load()
ctx = ToolContext(Path.cwd(), True, cfg.agenda)
dia = {tool.name: tool for tool in build_agenda_tools()}["agenda_dia"]
args = {"data": sys.argv[1]} if len(sys.argv) > 1 and sys.argv[1] else {}
print(dia.handler(args, ctx).content)
PY
}

cmd_ask() {
  need_ssh
  [ $# -ge 1 ] || die 'uso: gmin ask "sua pergunta"'
  local pergunta="$*"
  "${SSH[@]}" -t "cd $REMOTE_REPO && $REMOTE_PY -m gaia ask --no-voice $(printf '%q' "$pergunta")"
}

cmd_shell() {
  need_ssh
  printf 'entrando em %s (screen: Ctrl+A D sai sem matar a sessão)\n' "$HOST"
  "${SSH[@]}" -t 'screen -Rd gmin || screen -S gmin'
}

cmd_sudo() {
  need_ssh
  [ $# -ge 1 ] || die 'uso: gmin sudo <comando…>'
  # ssh junta os argumentos com espaço e o shell remoto separa de novo: sem
  # citar cada um, comando com espaço (sed, por exemplo) chega cortado.
  local citado=""
  local arg
  for arg in "$@"; do citado+=" $(printf '%q' "$arg")"; done
  printf 'senha do %s (digitada aqui):\n' "$HOST"
  "${SSH[@]}" -t "sudo$citado"
}

cmd_sb() {
  local acao="${1:-sync}"
  case "$acao" in
    sync)
      need_ssh
      [ -d "$LOCAL_SB/.git" ] || die "$LOCAL_SB nao e um repositorio git"
      info "sincronizando nos dois lados"
      # Mac primeiro (empurra o que foi escrito aqui), mini depois (recebe e
      # empurra os sonhos da madrugada) e Mac de novo (recebe o que o mini
      # escreveu). Duas passadas sao baratas: sem mudanca, o script so faz
      # fetch/pull.
      "$LOCAL_REPO/scripts/sb-sync.sh" || die "sb-sync falhou no Mac (conflito? veja git status)"
      "${SSH[@]}" 'PATH=$HOME/.local/bin:$PATH sb-sync' \
        || die "sb-sync falhou no mini (conflito? resolva la)"
      "$LOCAL_REPO/scripts/sb-sync.sh" || die "sb-sync falhou no Mac (conflito? veja git status)"
      ;;
    install)
      need_ssh
      info "instalando $SB_UNIT.service + .timer no mini"
      scp -q "$LOCAL_REPO/scripts/sb-sync.sh" "$HOST:~/.local/bin/sb-sync" \
        || die "falha ao copiar sb-sync"
      "${SSH[@]}" 'mkdir -p ~/.config/systemd/user && chmod +x ~/.local/bin/sb-sync' \
        || die "falha ao preparar ~/.local/bin"
      scp -q "$LOCAL_REPO/deploy/gaia-sb-sync.service" \
        "$HOST:~/.config/systemd/user/$SB_UNIT.service" || die "falha ao copiar a unidade"
      scp -q "$LOCAL_REPO/deploy/gaia-sb-sync.timer" \
        "$HOST:~/.config/systemd/user/$SB_UNIT.timer" || die "falha ao copiar o timer"
      "${SSH[@]}" "systemctl --user daemon-reload && systemctl --user enable --now $SB_UNIT.timer" \
        || die "falha ao ativar o timer"
      "${SSH[@]}" "systemctl --user list-timers $SB_UNIT.timer --no-pager"
      printf 'ativo: o mini sincroniza sozinho a cada 15 min.\n'
      printf 'no Mac, use: gmin sb install-mac\n'
      ;;
    install-mac)
      # O Mac nao tem systemd: a unidade e um launchd agent do usuario.
      local plist="$HOME/Library/LaunchAgents/com.gaia.sbsync.plist"
      mkdir -p "$HOME/Library/Logs" || die "sem ~/Library/Logs"
      cp "$LOCAL_REPO/deploy/com.gaia.sbsync.plist" "$plist" || die "falha ao copiar o plist"
      plutil -lint "$plist" >/dev/null || die "plist invalido"
      launchctl bootout "gui/$(id -u)/com.gaia.sbsync" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$plist" || die "falha ao carregar o launchd agent"
      printf 'ativo no Mac: com.gaia.sbsync a cada 15 min\n'
      printf 'logs: ~/Library/Logs/gaia-sbsync.log\n'
      ;;
    status)
      info "== Mac =="
      "$LOCAL_REPO/scripts/sb-sync.sh" status || printf 'Mac: falhou (veja o erro acima)\n'
      info "== mini =="
      if "${SSH[@]}" true 2>/dev/null; then
        "${SSH[@]}" 'PATH=$HOME/.local/bin:$PATH sb-sync status' \
          || printf 'mini: falhou (veja o erro acima)\n'
      else
        printf 'mini: sem SSH em %s (Tailscale parado? use GMIN_HOST=gaia-lan)\n' "$HOST"
      fi
      exit 0
      ;;
    logs)
      need_ssh
      "${SSH[@]}" "journalctl --user -u $SB_UNIT.service --no-pager -n 40"
      ;;
    *) die "use: gmin sb sync|install|install-mac|status|logs" ;;
  esac
}

main() {
  local sub="${1:-doctor}"
  shift || true
  case "$sub" in
    doctor|status) cmd_doctor ;;
    logs) cmd_logs "$@" ;;
    restart) cmd_restart ;;
    deploy) cmd_deploy "$@" ;;
    test|testes) cmd_test ;;
    agenda) cmd_agenda "${1:-}" ;;
    ask) cmd_ask "$@" ;;
    serve) cmd_serve "$@" ;;
    sb) cmd_sb "$@" ;;
    shell|ssh) cmd_shell ;;
    sudo) cmd_sudo "$@" ;;
    -h|--help|help) sed -n '2,26p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' ;;
    *) die "subcomando desconhecido: $sub (use: gmin help)" ;;
  esac
}

main "$@"
