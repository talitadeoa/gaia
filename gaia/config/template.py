"""Template TOML escrito a mao (`gaia config --init`).

Os comentarios sao o valor deste arquivo: um dump do `model_json_schema()`
perderia as explicacoes. Mantenha aqui, e nao no modelo.
"""

from __future__ import annotations

from pathlib import Path

from gaia.config.core import Config

_DEFAULT_CONFIG_TEMPLATE = """\
# Configuracao da Gaia. Segredos NAO devem ficar aqui: use ~/.config/gaia/.env

[agent]
# Trocar de provedor: rode /modelo (lista ao vivo + escolha numerada).
# Presets: commandcode | deepseek | openai | openrouter | ollama | anthropic |
#          opencode | groq | mistral | xai | custom (qualquer um com base_url)
# Atalhos do /modelo: cc = Command Code, ds = DeepSeek direta, glm, turbo.
provider = "commandcode"
model = "deepseek/deepseek-v4.1-flash"
# Vazio = endpoint padrao do provider.
base_url = ""
# Diretorio de trabalho (vazio = diretorio atual).
directory = ""
max_tokens = 4096
temperature = 0.3
max_steps = 32
resume_session = true
request_timeout = 900.0
# Auto-aprovar ferramentas (perigoso; padrao false).
auto_approve = false
tools = true
allow_outside_project = true
# Modelo pequeno para pedidos curtos (vazio = sempre o principal).
# Vale so dentro do provedor atual: id de outro provedor e descartado.
fast_model = "deepseek/deepseek-v4.1-flash-fast"
# Cacheia o prefixo estavel do prompt (persona + preferencias).
cache_prompt = true
# Depois disso o historico antigo vira um resumo compacto.
max_history_messages = 20
max_history_chars = 24000
summary_chars = 1200
# Mede tokens por turno (e mostra no /status).
usage = true
# Resultado de ferramenta no historico (o turno atual ve tudo; 0 = sem teto).
tool_result_chars = 4000
# Resumo do historico: local (extrativo) | llm (semantico, com fallback local).
history_summary = "local"
# Modelo do resumo (vazio = modelo principal).
history_summary_model = ""
# A chave da API NAO fica aqui: use GAIA_AGENT_API_KEY em ~/.config/gaia/.env

[stt]
model = "small"
language = "pt"
device = "cpu"
compute_type = "int8"
beam_size = 1
vad_filter = true
confirm_transcript = false      # envia a transcricao direto, sem pedir correcao
partial_transcription = true    # legendas parciais enquanto fala
partial_interval_seconds = 1.5
partial_window_seconds = 10.0

[tts]
enabled = true
provider = "edge"          # edge | say | none
edge_voice = "pt-BR-FranciscaNeural"
say_voice = "Luciana"
rate = "+0%"
max_chars = 1200
sentence_pause_ms = 120      # pausa extra entre frases (0 = nenhuma)

[audiobook]
enabled = true
inbox = ""                 # vazio => <data_dir>/audiobooks/inbox (coloque PDFs aqui)
outbox = ""                # vazio => <data_dir>/audiobooks/outbox (MP3 pronto)
tmp = ""                   # vazio => <data_dir>/audiobooks/tmp
voice = "pt-BR-ThalitaNeural"
rate = "-4%"
pitch = "+0Hz"
sentence_pause_ms = 220
chunk_chars = 3500
watch_minutes = 0.0        # >0 = checa a pasta de entrada sozinho (ex.: 5)
notify = true              # avisa no Telegram quando fica pronto
resume = true

[audio]
sample_rate = 16000
channels = 1
silence_ms = 5000               # pausa de 5s encerra a fala
min_speech_ms = 250
preroll_ms = 300
max_seconds = 0.0               # 0 = sem limite para uma fala
vad_threshold = 0.5             # probabilidade minima do Silero para contar fala
speech_threshold_db = -42.0     # reserva: limiar de RMS se faltar onnxruntime
input_device = ""
continuous = true               # microfone aberto; fala -> executa -> volta a ouvir
auto_mute_seconds = 30          # silencio que desliga o microfone (0 = nunca)

[secondbrain]
path = "~/SecondBrain"
# Embeddings para busca por sentido e para o sonho. Vazio = desligado.
embed_model = ""            # ex.: text-embedding-3-small | nomic-embed-text
embed_base_url = "https://api.openai.com/v1"
context_results = 2            # 2-3 trechos curtos bastam
context_chars = 480            # por trecho
context_max_chars = 1200       # teto do bloco inteiro
index_db = ""
auto_context = true
confirm_create = false          # criar nota nao pergunta (aditivo)
require_confirmation = true     # editar/excluir ainda pergunta
reindex_on_start = false        # ligue para reindexar a cada inicio
backup_before_write = true
default_folder = "Inbox"
preferences_file = ""          # vazio => <secondbrain.path>/preferences.md

[interface]
show_banner = true
speak_tool_activity = false
steering = true
concise = true
# Humor da persona: off | leve | medio | alto (alto = brinca e conta piadas).
humor = "medio"
show_reasoning = true
show_diffs = true
show_metas_on_start = true
dashboard_collapsed = true   # metas e lua começam recolhidos
ui = "textual"             # textual (padrão) | terminal
selection_mode = false     # true = seleção de texto ligada: arraste destaca e copia
fast = false               # modo rápido: sem busca automática, respostas curtas
fast_smalltalk = true      # saudações respondem na hora, sem chamar o modelo
chinese_echo = false       # mostra chinês + pinyin de cada mensagem
echo_language = "zh"       # idioma do eco: zh | en | fr
markdown_render = true     # respostas em Markdown (títulos, ênfase, código)
default_mode = "assistente"     # pesquisadora | trabalhadora | investidora | assistente

[whatsapp]
enabled = false
phone_number_id = ""
recipient = ""
api_version = "v21.0"
webhook_host = "127.0.0.1"
webhook_port = 8765
# access_token e verify_token vão apenas no .env/ambiente.

[browser]
enabled = true
profile_dir = ""          # vazio => <data_dir>/browser
headless = true
timeout_ms = 30000

[pi]
enabled = false           # liga a ferramenta `pi` (delega codigo ao harness)
binary = ""               # vazio => pi do PATH ou ~/.pi/agent/bin/pi
workspace = "~/pr-workspace"  # clone de trabalho do pi (nunca o repo do deploy)
protect = ["~/gaia"]      # fora do alcance: o deploy reseta esse clone
model = ""               # ex.: commandcode/deepseek/deepseek-v4.1-flash
timeout = 900.0
max_output_chars = 12000
extra_args = []

[backlog]
# Card "Backlog" do web: paginas da Agenda cujo titulo termina em "backlog"
# + itens adicionados pelo chat. Cada item e despachado ao pi num clone do
# repositorio e termina em PR.
enabled = true
agenda_suffix = "backlog"
agenda_scan = 50            # quantas paginas recentes varrer atras do sufixo
github_owner = "talitadeoa"     # dono usado quando nao ha mapeamento abaixo
workspace = "~/pr-workspace"    # clones do backlog (uma pasta por repo)
base_branch = "main"
branch_prefix = "gaia/backlog"
gh_binary = "gh"                # gh autenticado (gh auth login) no mini
prune_days = 7                  # concluidos somem da tela depois de N dias
max_items = 200
max_prompt_chars = 12000

# Projeto (prefixo do titulo da pagina) -> repositorio do GitHub.
# "gaia backlog" -> gaia, "agenda backlog" -> agenda, "suite backlog" -> suite.
[backlog.repos]
gaia = "talitadeoa/gaia"
agenda = "talitadeoa/agenda"
suite = "talitadeoa/abin-suite"

[x]
# Publicar no X (Twitter). A ferramenta `post_x` so aparece com isto ligado e
# com credencial no cofre: X_CLIENT_ID, X_CLIENT_SECRET e X_REFRESH_TOKEN em
# <cofre>/x.env (OAuth2 PKCE; o refresh token rotaciona a cada post).
# Publicar e escrita em nome publico: a ferramenta sempre pede aprovacao.
enabled = false
secrets_dir = ""            # vazio => a pasta do [secrets]

[secrets]
# Cofre de credenciais de terceiros (GitHub, Google, X): um arquivo por
# servico, lido sob demanda pela ferramenta que precisa. Fora do .env de
# proposito -- o .env e o EnvironmentFile do `gaia serve`, e o bash/pi herdam
# o ambiente inteiro do processo.
# Nada e exportado: `open_service("github").get("GH_TOKEN")` le do disco.
enabled = true
dir = ""                    # vazio => ~/.config/gaia/secrets

[focus]
enabled = true
path = "~/.config/gaia/focus.json"
prompt_on_moon = true

[agenda]
enabled = true
env_file = "~/agenda-git/agenda/.env"
poll_minutes = 30
max_items = 5
notify_pending = false
tasks_poll_seconds = 0      # 0 = nao checa sozinho

[estudos]
# Progresso dos simulados do Abin Suite (Supabase proprio, separado da Agenda).
# Credenciais no .env apontado por env_file: VITE_SUPABASE_URL,
# SUPABASE_SERVICE_ROLE_KEY e ESTUDOS_USER_ID.
enabled = true
env_file = "~/abin-suite/.env"
poll_minutes = 60
remind_when_late = true     # cobra quando nao estudou e a meta esta atrasada
remind_at = "20:00"         # so a partir deste horario, uma vez por dia
study_days = [6, 2, 4, 5]   # dias de estudo (0=seg..6=dom): dom, qua, sex, sab
max_items = 5

[telegram]
enabled = false
allowed_chat_id = ""
api_base_url = "https://api.telegram.org"
poll_timeout = 25
voice_replies = false
auto_approve = false      # true = aprova as ferramentas no chat autorizado
shell_access = false      # true = libera write/edit/bash no chat (evite)
show_reasoning = false    # mostra o raciocinio no chat (/pensando on|off)
# bot_token vai apenas no .env/ambiente.

[memory]
# Consolida o dia em uma nota de memoria no Second Brain.
enabled = true
at = "23:00"
on_exit = true
use_llm = true
folder = "Memoria"
# Sonho noturno: quando ociosa de madrugada, cruza o dia com memorias antigas.
dream_enabled = true
dream_at = "02:00"          # janela noturna: pode virar a meia-noite
dream_until = "08:00"
dream_folder = "Sonhos"     # pasta propria, fora do diario consolidado
dream_notify = true         # avisa no dia seguinte (uma linha)
dream_notify_at = "08:00"
retry_minutes = 10.0        # espaco entre tentativas se o aviso falhar

[briefing]
# Bom dia, meio-dia e fecho da noite no Telegram, direto da Agenda.
enabled = true
morning_at = "08:00"
noon_at = "12:00"
noon_until = "18:00"
morning_routine = "matinal"
max_tasks = 15
# Comentario pessoal do modelo em cima da lista (uma frase feita pra ela hoje).
personalize = false
# Briefings que ganham o comentario: morning | noon.
personalize_slots = ["morning"]
personalize_timeout_seconds = 20.0
# Fecho da noite: o que foi feito, o que ficou e um insight seu.
evening_enabled = true
evening_at = "18:00"
evening_until = "23:00"
evening_insight = true

[career]
# Rotina semanal de vagas: busca nas fontes abertas, filtra, pontua e
# entrega no Telegram. O metodo esta em ~/SecondBrain/cv/playbook-vagas.md.
enabled = true
days = [0]                  # 0=seg, 1=ter, 2=qua, 3=qui, 4=sex
at = "09:00"
until = "18:00"             # depois disso nao envia mais (recupera na proxima)
max_items = 8               # vagas por mensagem; o resto fica no estado

[proactive]
enabled = true
startup_greeting = true
greeting_via_llm = false   # true = saudação personalizada pelo modelo (mais lenta)
idle_checkin = true
idle_minutes = 45.0
min_interval_minutes = 60.0
daily_max = 4
quiet_hours = "23:00-08:00"
check_interval_seconds = 30.0
reminders = true

[serve]
# `gaia serve`: o mini processa e guarda tudo; as telas so desenham.
# Ver docs/plans/2026-11-19-serve-cliente-fino.md.
enabled = false
host = "127.0.0.1"    # loopback; para o tailnet use o IP 100.x e allow_remote
port = 8787
# Segredo: apenas no .env (GAIA_SERVE_TOKEN). Sem token nao escuta na rede.
allow_remote = false
telegram = true       # sobe o poller do Telegram junto (um processo so)
# Id desta maquina em <data_dir>/instances.json (ex.: "mini"). Vazio = casar
# pelo endereco pelo qual a pagina foi aberta.
instance_id = ""
# De onde vem o audio do modo voz: "browser" (o navegador grava e envia WAV)
# ou "server" (o microfone do proprio motor). Editavel em /control.
voice_source = "browser"
# Control Center (/control) usa GAIA_CONTROL_TOKEN no .env, NAO este token:
# o token do chat nao administra credenciais. Sem ele o painel fica off.
"""


def write_default_config(path: Path | None = None, force: bool = False) -> Path:
    target = path or Config.default_config_path()
    target = Path(target).expanduser()
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return target
