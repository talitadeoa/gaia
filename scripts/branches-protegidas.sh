#!/usr/bin/env bash
# branches-protegidas — poe a regra minima de protecao na branch padrao.
#
#   scripts/branches-protegidas.sh            # aplica nos repos da lista
#   scripts/branches-protegidas.sh --check    # so mostra o estado, nao muda nada
#   OWNER=alguem scripts/branches-protegidas.sh
#
# A regra (ruleset de repositorio, enforcement=active, sem bypass) e:
#   - deletion            -> a branch padrao nao pode ser apagada
#   - non_fast_forward    -> nada de force push (historia so cresce)
#
# O ruleset segue a branch padrao do repo (`~DEFAULT_BRANCH`), entao `main` e
# `master` recebem a regra sem mudar nada aqui.
#
# Exige `gh` autenticado com admin no repo (`gh auth status`). Nada de token
# aqui: a credencial vem do gh, nunca do .env.
set -euo pipefail

NAME="protege-branch-padrao"
OWNER="${OWNER:-talitadeoa}"
REPOS="${REPOS:-gaia second-brain agenda abin-suite estudos}"
CHECK=0

for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf 'uso: %s [--check]\n' "$0" >&2; exit 2 ;;
  esac
done

die() { printf 'branches-protegidas: %s\n' "$*" >&2; exit 1; }
info() { printf '\033[1m%s\033[0m\n' "$*"; }

command -v gh >/dev/null || die "gh nao esta no PATH (veja docs/acesso-mini.md)"
gh auth status >/dev/null 2>&1 || die "gh sem autenticacao: rode 'gh auth login'"

BODY='{"name":"'"$NAME"'","target":"branch","enforcement":"active",
  "bypass_actors":[],
  "conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},
  "rules":[{"type":"deletion"},{"type":"non_fast_forward"}]}'

for repo in $REPOS; do
  full="$OWNER/$repo"
  branch=$(gh api "repos/$full" --jq '.default_branch' 2>/dev/null) \
    || { printf '%-28s SKIP (sem acesso)\n' "$full"; continue; }

  atual=$(gh api "repos/$full/rules/branches/$branch" --jq '[.[].type]|join(",")' 2>/dev/null || true)
  if [ "$atual" = "deletion,non_fast_forward" ]; then
    printf '%-28s ja protegida (%s)\n' "$full" "$branch"
    continue
  fi

  if [ "$CHECK" = 1 ]; then
    printf '%-28s FALTA (%s: %s)\n' "$full" "$branch" "${atual:-vazio}"
    continue
  fi

  # Idempotente: reaproveita o ruleset pelo nome e so cria se nao existir.
  id=$(gh api "repos/$full/rulesets" --jq ".[] | select(.name == \"$NAME\") | .id" 2>/dev/null | head -1)
  if [ -n "$id" ]; then
    printf '%s' "$BODY" | gh api -X PUT "repos/$full/rulesets/$id" --input - --silent
  else
    printf '%s' "$BODY" | gh api -X POST "repos/$full/rulesets" --input - --silent
  fi
  printf '%-28s protegida (%s)\n' "$full" "$branch"
done

[ "$CHECK" = 1 ] && info 'apenas checagem: nada foi alterado'
exit 0
