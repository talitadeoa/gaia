"""Constantes e sub-configs pydantic (`[agent]`, `[serve]`, `[audio]`, ...).

A classe `Config` (que junta tudo, resolve paths e le o TOML) fica em
`core.py`, para nenhum arquivo deste pacote passar de ~500 linhas.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

APP_NAME = "gaia"
_LEGACY_PREFIX = "ZAYA_"
_ENV_PREFIX = f"{APP_NAME.upper()}_"

# Idiomas do eco (traducao embaixo de cada mensagem): chines, ingles e frances.
# A chave e o codigo ISO usado na tela e no prompt do tradutor; o rotulo e o
# que aparece no dialog "idiomas" do web e na linha do eco.
ECHO_LANGUAGES: tuple[str, ...] = ("zh", "en", "fr")
ECHO_LABELS: dict[str, str] = {"zh": "中文", "en": "English", "fr": "Français"}
# Nome em portugues (avisos e status da TUI).
ECHO_NAMES: dict[str, str] = {"zh": "chinês", "en": "inglês", "fr": "francês"}

class AgentConfig(BaseModel):
    """Motor proprio da Gaia: provedor, modelo e ferramentas."""

    model_config = ConfigDict(extra="ignore")

    # Presets: commandcode | deepseek | openai | openrouter | ollama | anthropic
    # | opencode | groq | mistral | xai | custom. `/modelo cc` = Command Code,
    # `/modelo ds` = DeepSeek direta (V4.1 Flash).
    provider: str = "commandcode"
    model: str = "deepseek/deepseek-v4.1-flash"
    # Vazio => usa o endpoint padrao do provider.
    base_url: str = ""
    # Segredo: prefira GAIA_AGENT_API_KEY no ambiente/.env.
    api_key: str = ""
    # Diretorio em que o agente trabalha (vazio = diretorio atual).
    directory: str = ""
    max_tokens: int = 4096
    temperature: float | None = 0.3
    max_steps: int = 32
    resume_session: bool = True
    request_timeout: float = 900.0
    # Sem nenhum evento nesse tempo (e ainda em execucao) => considera travado.
    stall_timeout: float = 90.0
    auto_approve: bool = False
    # Ferramentas de leitura e escrita no computador.
    tools: bool = True
    allow_outside_project: bool = True
    # Modelo pequeno para pedidos curtos (vazio = sempre o modelo principal).
    # Vale so dentro do provedor atual: id de outro provedor e descartado.
    fast_model: str = "deepseek/deepseek-v4.1-flash-fast"
    # Marca o prefixo estavel do prompt para cache (Anthropic).
    cache_prompt: bool = True
    # Depois desse numero de mensagens, o historico antigo vira um resumo.
    max_history_messages: int = 20
    # Teto de caracteres do historico que vai ao modelo (0 = sem teto).
    max_history_chars: int = 24000
    summary_chars: int = 1200
    # Medir tokens por turno (usa stream_options em APIs compativeis).
    usage: bool = True
    # Teto de caracteres de um resultado de ferramenta guardado no historico.
    # 0 = sem teto. O modelo ve o resultado inteiro no turno atual.
    tool_result_chars: int = 4000
    # Como resumir o historico antigo: local (extrativo) ou llm (semantico).
    # O modo llm cai automaticamente no local se a chamada falhar.
    history_summary: str = "local"
    # Modelo do resumo (vazio = modelo principal).
    history_summary_model: str = ""
    # Cadeia curta de fallback: usada SO quando o provedor recusa a credencial
    # (401/402/403, ex.: sem saldo). Nao e retry de rede. Cada item e
    # ``provider/model``; o modelo ativo no config nao muda.
    fallback_models: list[str] = Field(default_factory=list)

    def model_slug(self) -> str:
        return f"{self.provider}/{self.model}"

    def apply_model(self, provider: str, model: str) -> None:
        """Aponta a configuracao para outro modelo (vale no proximo turno)."""
        self.provider = provider.strip()
        self.model = model.strip()


class STTConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "small"
    language: str = "pt"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 1
    vad_filter: bool = True
    # Se false, a transcricao e enviada direto, sem pedir correcao.
    confirm_transcript: bool = False
    # Legendas parciais enquanto a pessoa fala (transcricao final continua completa).
    partial_transcription: bool = True
    partial_interval_seconds: float = 1.5
    partial_window_seconds: float = 10.0


class TTSConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    provider: str = "edge"  # edge | say | none
    edge_voice: str = "pt-BR-FranciscaNeural"
    say_voice: str = "Luciana"
    rate: str = "+0%"
    max_chars: int = 1200
    # Pausa extra entre frases (ms). 0 = sem pausa adicional.
    sentence_pause_ms: int = 120


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sample_rate: int = 16000
    channels: int = 1
    # Silencio necessario para encerrar uma fala (5s = pausa confortavel).
    silence_ms: int = 5000
    min_speech_ms: int = 250
    preroll_ms: int = 300
    # 0 = sem limite de duracao para uma fala.
    max_seconds: float = 0.0
    # Probabilidade do Silero VAD acima da qual o bloco conta como fala.
    # So vale quando o onnxruntime esta disponivel (o normal).
    vad_threshold: float = 0.5
    # Reserva sem Silero: limiar de RMS em dB.
    speech_threshold_db: float = -42.0
    input_device: str = ""
    # Escuta continua: microfone aberto, fala -> executa -> responde -> volta a ouvir.
    continuous: bool = True
    # Depois desse tempo sem fala, desliga o microfone (0 = nunca).
    auto_mute_seconds: float = 30.0


class SecondBrainConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = "~/SecondBrain"
    index_db: str = ""
    auto_context: bool = True
    # 2 a 3 trechos curtos bastam: mais que isso infla o prompt sem ganho.
    context_results: int = 2
    # Limite de caracteres por nota injetada no contexto.
    context_chars: int = 480
    # Teto total do bloco de contexto recuperado.
    context_max_chars: int = 1200
    # Criar nota e aditivo: por padrao nao pergunta (mais rapido).
    confirm_create: bool = False
    # Editar/excluir sao destrutivos: por padrao continuam exigindo confirmacao.
    require_confirmation: bool = True
    # Reindexar a cada inicio pode custar caro em bases grandes.
    reindex_on_start: bool = False
    backup_before_write: bool = True
    default_folder: str = "Inbox"
    # Embeddings: vazio = desligado, a busca segue lexical (padrao).
    # Ex.: "text-embedding-3-small" (OpenAI) ou "nomic-embed-text" (Ollama).
    embed_model: str = ""
    embed_base_url: str = "https://api.openai.com/v1"
    # A chave vai no .env como GAIA_SB_EMBED_API_KEY, nunca no TOML.
    embed_api_key: str = ""
    # Cosseno minimo para considerar que "ressoa". Depende do modelo: medido
    # com paraphrase-multilingual no mini, relacionado ~0.40-0.60 e sem
    # relacao ~0.08-0.37. Ajuste se trocar de modelo de embedding.
    embed_min_score: float = 0.35
    # CPU (mini PC) demora: lote pequeno e timeout folgado evitam timeouts.
    embed_batch_size: int = 8
    embed_timeout: float = 120.0
    # Vazio => <secondbrain.path>/preferences.md
    preferences_file: str = ""
    exclude: list[str] = Field(
        default_factory=lambda: [".obsidian", ".git", ".trash", ".gaia", "node_modules"]
    )


class InterfaceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    show_banner: bool = True
    speak_tool_activity: bool = False
    steering: bool = True
    # Pede respostas diretas ao agente, reduzindo exploracao desnecessaria.
    concise: bool = True
    # Nivel de humor da persona: off | leve | medio | alto.
    humor: str = "medio"
    # Mostrar raciocinio e diffs das ferramentas no terminal.
    show_reasoning: bool = True
    show_diffs: bool = True
    # Mostrar o painel de metas ao iniciar.
    show_metas_on_start: bool = True
    # Painéis de metas e lua começam recolhidos (F5 continua ocultando).
    dashboard_collapsed: bool = True
    # Interface: terminal (padrão) ou textual.
    ui: str = "textual"
    # Selecao de texto ligada ao abrir: arrastar destaca e copia (F4 alterna).
    selection_mode: bool = False
    # Modo rápido: sem busca automática e respostas mais curtas.
    fast: bool = False
    # Saudações/small talk respondem na hora, sem chamar o modelo.
    fast_smalltalk: bool = True
    # Eco em chinês (caracteres + pinyin) para cada mensagem.
    chinese_echo: bool = False
    # Idioma do eco: zh | en | fr (vazio herda o padrao do chines). A tela
    # escolhe em `POST /language`; o TOML guarda a escolha.
    echo_language: str = "zh"
    # Respostas renderizadas como Markdown (títulos, ênfase, código).
    markdown_render: bool = True
    # Modo inicial: pesquisadora | trabalhadora | investidora | assistente.
    default_mode: str = "assistente"

    @field_validator("echo_language")
    @classmethod
    def _echo_language_valida(cls, valor: str) -> str:
        escolha = str(valor or "").strip().lower()
        if not escolha:
            return "zh"
        if escolha not in ECHO_LANGUAGES:
            raise ValueError("echo_language deve ser zh, en ou fr")
        return escolha


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Consolida os turnos do dia numa nota de memoria no Second Brain.
    enabled: bool = True
    # Horario local (HH:MM) em que o dia e consolidado enquanto a Gaia roda.
    at: str = "23:00"
    # Ao encerrar depois desse horario, consolida o que ainda estiver pendente.
    on_exit: bool = True
    # Resumo pelo modelo; se falhar, cai no resumo local deterministico.
    use_llm: bool = True
    folder: str = "Memoria"
    # Sonho noturno: quando ociosa de madrugada, cruza a memoria do dia com
    # as memorias antigas e escreve uma nota de sonho no Second Brain.
    dream_enabled: bool = True
    dream_at: str = "02:00"
    # Fim da janela do sonho (a janela pode virar a meia-noite).
    dream_until: str = "08:00"
    # Os sonhos ficam em pasta propria, separada do diario consolidado.
    dream_folder: str = "Sonhos"
    # Notas de andaime/exemplo que nunca devem entrar no sonho: elas falam de
    # tudo e de nada, e a busca por sentido as traz para o topo sempre.
    dream_ignore: list[str] = Field(
        default_factory=lambda: ["README.md", "Inbox/Boas-vindas.md"]
    )
    # Aviso no dia seguinte: uma linha sobre o sonho, sem segunda chamada ao
    # modelo. Vai pelo canal em uso (TUI ou Telegram), fora das quiet hours.
    dream_notify: bool = True
    dream_notify_at: str = "08:00"
    # Espaco entre tentativas quando o aviso do sonho nao consegue sair.
    retry_minutes: float = 10.0
    max_turns: int = 120
    max_chars: int = 16000
    llm_timeout_seconds: float = 90.0


class BriefingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Bom dia com tarefas/habitos e resumo do meio-dia, sem usar o modelo.
    enabled: bool = True
    morning_at: str = "08:00"
    noon_at: str = "12:00"
    # Depois desse horario o resumo do meio-dia nao e mais enviado.
    noon_until: str = "18:00"
    # Trecho do nome da rotina da Agenda (sem acento, case-insensitive).
    morning_routine: str = "matinal"
    max_tasks: int = 15
    check_interval_seconds: float = 30.0
    retry_minutes: float = 10.0
    # Comentario pessoal do modelo no briefing (uma frase feita pra ela hoje).
    personalize: bool = False
    # Quais briefings ganham o comentario: morning | noon.
    personalize_slots: list[str] = Field(default_factory=lambda: ["morning"])
    personalize_timeout_seconds: float = 20.0
    # Fecho da noite no Telegram: o que foi feito, o que nao foi e um insight.
    evening_enabled: bool = True
    evening_at: str = "18:00"
    # Depois desse horario nao envia mais (quiet hours comecam as 23:00).
    evening_until: str = "23:00"
    # Insight curto gerado pelo modelo junto com a lista (false = so a lista).
    evening_insight: bool = True
    evening_timeout_seconds: float = 60.0
    # Resumo da carteira (watchlist) nos dias escolhidos, sem usar o modelo.
    market_enabled: bool = True
    # Dias da semana com 0 = segunda (seg, qua, sex por padrao).
    market_days: list[int] = Field(default_factory=lambda: [0, 2, 4])
    market_at: str = "12:00"
    # Depois desse horario o resumo da carteira nao e mais enviado.
    market_until: str = "18:00"


class CareerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Rotina semanal de vagas: busca nas fontes abertas, filtra, pontua e
    # entrega no Telegram. O metodo vive em ~/SecondBrain/cv/playbook-vagas.md.
    enabled: bool = True
    # Dias da semana com 0 = segunda (segunda por padrao).
    days: list[int] = Field(default_factory=lambda: [0])
    at: str = "09:00"
    # Depois desse horario a rotina nao e mais enviada.
    until: str = "18:00"
    check_interval_seconds: float = 30.0
    retry_minutes: float = 10.0
    # Vagas por mensagem; o resto fica registrado no estado.
    max_items: int = 8
    # Vazio => <data_dir>/career/state.json
    state_file: str = ""


class ProactiveConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    startup_greeting: bool = True
    # Saudacao inicial instantanea (sem LLM). Ligue para personalizar via modelo.
    greeting_via_llm: bool = False
    idle_checkin: bool = True
    idle_minutes: float = 45.0
    min_interval_minutes: float = 60.0
    daily_max: int = 4
    quiet_hours: str = "23:00-08:00"
    check_interval_seconds: float = 30.0
    reminders: bool = True


class WhatsAppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    phone_number_id: str = ""
    # Segredos: apenas via ambiente/.env, nunca no TOML.
    access_token: str = ""
    verify_token: str = ""
    recipient: str = ""
    api_version: str = "v21.0"
    graph_base_url: str = "https://graph.facebook.com"
    # Porta local do webhook (recebe mensagens da Meta).
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8765


class SecretsConfig(BaseModel):
    """Cofre de credenciais de terceiros (`~/.config/gaia/secrets/`).

    Separado do `.env` de proposito: o `.env` e o `EnvironmentFile` do
    `gaia serve`, entao o que esta la entra no ambiente do processo e o
    `bash`/`pi` herdam tudo. Aqui cada servico tem arquivo proprio, lido sob
    demanda (`gaia/secrets.py`).
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Vazio => ~/.config/gaia/secrets (GAIA_SECRETS_DIR tambem vale).
    dir: str = ""


class XConfig(BaseModel):
    """Publicacao no X (Twitter). Credencial no cofre, nao no `.env`."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    # Cofre de onde saem X_CLIENT_ID/X_CLIENT_SECRET/X_REFRESH_TOKEN.
    # Vazio => a pasta do `[secrets]`.
    secrets_dir: str = ""


class BacklogConfig(BaseModel):
    """Card "Backlog" do web: paginas da Agenda + itens do chat -> PR no pi.

    Cada projeto aponta para um repositorio do GitHub (``owner/repo``). O item
    e implementado pelo pi num clone em ``workspace`` e termina em PR; o card
    mostra o estado. Ver ``gaia/backlog/``.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Fonte Agenda: paginas cujo titulo termina com este sufixo (sem caixa).
    # A PAGINA e a fonte: apagar/renomear na Agenda apaga/move o grupo aqui.
    agenda_suffix: str = "backlog"
    # Quantas paginas recentes varrer atras do sufixo. A purga usa a mesma
    # janela: pagina antiga fora dela sumiria por engano, entao ela e larga.
    agenda_scan: int = 200
    # Apagar do backlog o grupo cuja pagina nao esta mais na Agenda.
    remove_orphans: bool = True
    # Projeto da Agenda -> repositorio GitHub (owner/repo).
    repos: dict[str, str] = Field(
        default_factory=lambda: {
            "gaia": "talitadeoa/gaia",
            "agenda": "talitadeoa/agenda",
            "suite": "talitadeoa/abin-suite",
        }
    )
    # Owner usado quando a pagina nao tem mapeamento explicito.
    github_owner: str = "talitadeoa"
    # Raiz dos clones que o pi usa (uma subpasta por repo).
    workspace: str = "~/pr-workspace"
    # Branch base e prefixo do branch de trabalho.
    base_branch: str = "main"
    branch_prefix: str = "gaia/backlog"
    # Ferramenta de PR (`gh` autenticado no mini).
    gh_binary: str = "gh"
    # Itens concluidos ha mais de N dias somem da tela (mas ficam no banco).
    prune_days: int = 7
    # Teto de itens por pagina sincronizada (pagina imensa nao domina a tela).
    max_items: int = 200
    # Teto do texto enviado ao pi (o resto e cortado com aviso).
    max_prompt_chars: int = 12000

    def repo_for(self, project: str) -> str:
        """``owner/repo`` do projeto, ou ``""`` quando nao ha mapeamento.

        Nunca chuta ``owner/<projeto>``: abrir PR no repositorio errado (ou
        num que nao existe) e pior que recusar o item. Projeto sem repo
        aparece no card como "sem repositorio" e o despacho recusa com o
        motivo; o mapeamento mora em ``[backlog.repos]`` do TOML.
        """
        chave = project.strip().lower()
        mapeado = ""
        for nome, repo in self.repos.items():
            if str(nome).strip().lower() == chave:
                mapeado = str(repo).strip()
                break
        if mapeado and "/" in mapeado:
            return mapeado
        if mapeado:  # config parcial: so o nome do repo
            return f"{self.github_owner}/{mapeado}"
        return ""


class FocusConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    path: str = "~/.config/gaia/focus.json"
    # Perguntar sobre o foco nas fases principais da lua.
    prompt_on_moon: bool = True


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Perfil persistente: guarda cookies/logins entre execucoes.
    profile_dir: str = ""
    headless: bool = True
    timeout_ms: int = 30000


class AgendaConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # .env do projeto Agenda (Supabase). Nada e copiado para o repo da Gaia.
    env_file: str = "~/agenda-git/agenda/.env"
    poll_minutes: float = 30.0
    max_items: int = 5
    # Avisar proativamente sobre tarefas pendentes do dia.
    notify_pending: bool = False
    # Checagem automatica da pagina "tarefas pra gaia" (0 = desligada).
    tasks_poll_seconds: float = 0.0


class EstudosConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # .env do projeto Abin Suite (Supabase proprio, separado da Agenda).
    env_file: str = "~/abin-suite/.env"
    poll_minutes: float = 60.0
    # Cobrar proativamente quando nao estudou e a meta esta atrasada.
    remind_when_late: bool = True
    # So cobra a partir deste horario (HH:MM), uma vez por dia.
    remind_at: str = "20:00"
    # Dias de estudo (0=seg, 1=ter, ..., 6=dom). Padrao: dom/qua/sex/sab.
    study_days: list[int] = Field(default_factory=lambda: [6, 2, 4, 5])
    max_items: int = 5


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    # Segredo: apenas via ambiente/.env.
    bot_token: str = ""
    # Chat autorizado (você). Vazio => o primeiro chat que falar com o bot.
    allowed_chat_id: str = ""
    api_base_url: str = "https://api.telegram.org"
    poll_timeout: int = 25
    # Responder também com áudio (gera mp3 local e envia como áudio).
    voice_replies: bool = False
    # O Telegram não tem terminal interativo para aprovar ferramentas. Com
    # isto ligado, as ferramentas que pedem confirmação são aprovadas
    # automaticamente (exige allowed_chat_id fixado). Desligado, toda
    # ferramenta que pede confirmação é negada.
    auto_approve: bool = False
    # Shell e escrita de arquivo pelo chat remoto: opt-in explícito, e só vale
    # junto de auto_approve + allowed_chat_id. Desligado (padrão) o bot acessa
    # a Agenda pelas ferramentas agenda_* e não consegue rodar comando na
    # máquina.
    shell_access: bool = False
    # Mostra o raciocínio do modelo no chat (uma mensagem editada ao vivo).
    # Alterna no próprio chat com /pensando on|off.
    show_reasoning: bool = False


class ServeConfig(BaseModel):
    """`gaia serve`: uma inteligencia, varias telas (ver docs/plans/2026-11-19).

    O servidor e o dono unico do estado: banco, Second Brain e proatividade. Os
    clientes (`gaia --remote`, web) so desenham.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    # Loopback por padrao: nada escuta na rede sem pedido explicito.
    host: str = "127.0.0.1"
    port: int = 8787
    # Segredo: apenas via ambiente/.env (GAIA_SERVE_TOKEN).
    token: str = ""
    # Segredo do Control Center: apenas via ambiente/.env (GAIA_CONTROL_TOKEN).
    # Separado do token do chat de proposito — o token de conversa nao pode
    # administrar credenciais e configuracao. Sem ele, /control/api nao existe.
    control_token: str = ""
    # Bind fora do loopback (ex.: IP do tailnet) exige token. Ver allow_remote.
    allow_remote: bool = False
    # Id desta maquina em ``<data_dir>/instances.json``. Vazio = casa pelo Host
    # header (quem abriu a pagina pelo endereco da instancia ve ela como atual).
    instance_id: str = ""
    # Sobe o poller do Telegram junto do servidor (um processo so).
    telegram: bool = True
    # De onde vem o audio do modo voz do web: "browser" (o navegador grava e
    # envia WAV para `POST /voice/upload`) ou "server" (o motor grava com o
    # proprio microfone, `POST /voice/listen`). Padrao browser: o mini headless
    # normalmente nao tem microfone util. Editavel no Control Center.
    voice_source: str = "browser"

    @field_validator("voice_source")
    @classmethod
    def _voice_source_valida(cls, valor: str) -> str:
        escolha = str(valor or "").strip().lower()
        if escolha not in {"browser", "server"}:
            raise ValueError("voice_source deve ser 'browser' ou 'server'")
        return escolha

    def remote_bind_without_token(self) -> bool:
        """True = escutaria na rede sem segredo; o servidor deve recusar."""
        if self.host in {"", "127.0.0.1", "localhost", "::1"}:
            return False
        return not self.allow_remote or not self.token.strip()
