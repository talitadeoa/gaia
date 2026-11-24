/* Control Center: fala com /control/api/* usando uma sessao de cookie.
 *
 * Sem framework e sem build (mesma escolha do chat). A credencial e a mesma
 * logica do chat, agora em `/control/static/shared.js`:
 *   1. `POST /control/api/session` com `Authorization: Bearer <token>` troca o
 *      token por um cookie HttpOnly (`gaia_control`). O JS nunca ve o nonce.
 *   2. As chamadas seguintes usam `credentials: "same-origin"` — cookie no
 *      navegador, nada na URL.
 *   3. Guardar o token no localStorage e opt-in ("lembrar token neste
 *      navegador"). Desmarcado, a sessao vale ate o cookie vencer e a tela pede
 *      o token de novo — nenhum segredo fica no navegador.
 *
 * Todo numero na tela vem de uma chamada real: se a API falhar, a tela mostra
 * o erro em vez de inventar estado.
 */
(() => {
  "use strict";

  const { api: apiCompartilhada, auth, header, instances, tema } = window.Gaia;
  const API = "/control/api";
  const estado = { aba: "overview", dados: {}, sessao: false, engine: null };

  const $ = (sel) => document.querySelector(sel);
  const el = window.Gaia.el;

  const token = () => auth.token("control");

  /** Troca o token pelo cookie de sessao. Devolve true se a sessao existe. */
  async function autenticar(segredo = token()) {
    const entrou = await auth.entrar("control", segredo);
    estado.sessao = entrou;
    return entrou;
  }

  /** Existe prova valida agora? (pergunta ao servidor, sem confiar em cache) */
  async function sessaoValida() {
    estado.sessao = await auth.sessao("control");
    return estado.sessao;
  }

  function api(caminho, opcoes = {}) {
    return apiCompartilhada(caminho, { ...opcoes, base: API, perfil: "control" });
  }

  function status(texto, classe = "") {
    const no = $("#status");
    no.textContent = texto;
    no.className = classe;
  }

  function falha(erro) {
    if (erro.status === 401) {
      estado.sessao = false;
      status("sessao expirada", "aviso");
      abrirModal();
      return;
    }
    if (erro.status === 503) {
      status("control center desativado: defina GAIA_CONTROL_TOKEN", "aviso");
      return;
    }
    status(erro.message, "erro");
  }

  function tag(texto, classe) {
    return el("span", { class: `tag ${classe || ""}` }, texto);
  }

  function classeStatus(valor) {
    const mapa = {
      ok: "ok", active: "ok", completed: "ok",
      warn: "warn", scheduled: "warn", running: "warn", paused: "warn",
      error: "err", failed: "err", missed: "err", cancelled: "err",
      off: "off", disabled: "off",
    };
    return mapa[valor] || "off";
  }

  function data(iso) {
    if (!iso) return "—";
    const quando = new Date(iso);
    if (Number.isNaN(quando.getTime())) return iso;
    return quando.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  }

  function duracao(ms) {
    if (!ms && ms !== 0) return "—";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
    return `${Math.round(ms / 60000)} min`;
  }

  /* -------------------------------------------------------------- navegacao */
  function trocarAba(nome) {
    estado.aba = nome;
    document.querySelectorAll("nav button").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === nome);
    });
    document.querySelectorAll(".tab").forEach((s) => {
      s.classList.toggle("active", s.id === `tab-${nome}`);
    });
    carregar();
  }

  async function carregar(tentarDeNovo = true) {
    if (!estado.sessao) {
      // O token lembrado so serve para refazer a troca: sem ele (opt-out) a
      // sessao vale ate o cookie vencer e o modal volta, como deve ser.
      const lembrado = token();
      if (!(await sessaoValida()) && !(lembrado && (await autenticar(lembrado)))) {
        status("informe o token para carregar", "aviso");
        abrirModal();
        return;
      }
    }
    status("carregando…");
    try {
      await carregarEngine();
      const carga = {
        overview: () => api("/overview"),
        models: async () => ({ providers: await api("/providers"), models: await api("/models") }),
        skills: () => api("/skills"),
        automations: () => api("/automations"),
        activity: () => api("/runs?limit=80"),
        errors: () => api("/errors?limit=80"),
        settings: async () => ({ settings: await api("/settings"), secrets: await api("/secrets") }),
      }[estado.aba];
      estado.dados[estado.aba] = await carga();
      desenhar();
      status(`atualizado ${new Date().toLocaleTimeString("pt-BR")}`, "dim");
    } catch (erro) {
      // Cookie expirado (12 h) ou serve reiniciado: refaz a troca com o token
      // guardado antes de incomodar. Uma tentativa so, para nao fazer laco.
      if (erro.status === 401 && tentarDeNovo && (await autenticar())) {
        return carregar(false);
      }
      falha(erro);
    }
  }

  function desenhar() {
    const dados = estado.dados[estado.aba];
    if (!dados) return;
    const alvo = $(`#tab-${estado.aba}`);
    alvo.replaceChildren();
    const pintor = {
      overview: desenharOverview,
      models: desenharModels,
      skills: desenharSkills,
      automations: desenharAutomations,
      activity: desenharActivity,
      errors: desenharErrors,
      settings: desenharSettings,
    }[estado.aba];
    pintor(alvo, dados);
  }

  /* ------------------------------------------------------------------ header */
  async function carregarEngine() {
    try {
      // `/control/api/engine` e o mesmo cabecalho do chat, mas dentro do path
      // do cookie do painel (Path=/control).
      estado.engine = await api("/engine");
    } catch (erro) {
      // 401 no /engine e o mesmo do resto: a sessao morreu.
      if (erro.status === 401) throw erro;
      estado.engine = null;
      return;
    }
    pintarCabecalho(estado.engine);
  }

  function pintarCabecalho(engine) {
    if (!engine) return;
    header.pintar(engine, {
      modelo: "#pill-model",
      provedor: "#pill-provider",
      chave: "#pill-key",
    });
    $("#pill-provider").hidden = !engine.provider;
    $("#pill-key").hidden = !engine.has_key;
    const slot = $("#slot-instancia");
    const seletor = instances.seletor();
    if (slot && seletor) slot.replaceChildren(seletor);
  }

  async function carregarInstancias() {
    await instances.carregar();
    if (estado.engine) pintarCabecalho(estado.engine);
  }

  /* ---------------------------------------------------------------- overview */
  function desenharOverview(alvo, dados) {
    const saude = dados.status || "ok";
    $("#pill-status").textContent = saude;
    $("#pill-status").className = `pill ${classeStatus(saude)}`;
    const motor = estado.engine || {};
    alvo.append(
      el("div", { class: "cards" },
        cartao("Modelo ativo", motor.model || dados.engine.model,
          `${motor.provider || dados.engine.provider} · ${dados.engine.busy ? "ocupado" : "ocioso"}`),
        cartao("Credencial", motor.masked || (motor.has_key ? "(oculta)" : "sem chave"),
          motor.auth_source || "defina a chave no .env"),
        cartao("Saúde", saude, `${(dados.health || []).filter((i) => i.status === "error").length} componente(s) com erro`),
        cartao("Skills", `${dados.skills.active}/${dados.skills.total}`, `${dados.skills.error} com erro`),
        cartao("Automações", `${dados.automations.active}/${dados.automations.total}`, `${(dados.automations.missed || []).length} não executaram`),
        cartao("24 h", `${(dados.summary || {}).total || 0} execuções`, JSON.stringify((dados.summary || {}).by_status || {})),
      ),
    );

    if ((dados.alerts || []).length) {
      alvo.append(
        el("div", { class: "panel" },
          el("header", {}, el("h2", {}, "Precisa de atenção")),
          el("div", { class: "panel-body" },
            el("ul", {}, dados.alerts.map((a) => el("li", { class: "aviso" }, `${a.message}`))),
          ),
        ),
      );
    }

    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Componentes"), el("span", { class: "dim" }, "system health")),
        el("div", { class: "panel-body" },
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "componente"), el("th", {}, "estado"), el("th", {}, "detalhe"))),
            el("tbody", {}, (dados.health || []).map((item) =>
              el("tr", {},
                el("td", {}, item.label || item.id),
                el("td", {}, tag(item.status, classeStatus(item.status))),
                el("td", { class: "dim" }, item.detail),
              ))),
          ),
        ),
      ),
    );

    alvo.append(tabelaExecucoes("Últimas execuções", dados.recent_runs || []));
  }

  function cartao(titulo, valor, sub) {
    return el("div", { class: "card" },
      el("h3", {}, titulo),
      el("div", { class: "big" }, valor ?? "—"),
      el("div", { class: "sub" }, sub ?? ""),
    );
  }

  function tabelaExecucoes(titulo, runs) {
    if (!runs.length) {
      return el("div", { class: "panel" }, el("header", {}, el("h2", {}, titulo)),
        el("div", { class: "panel-body vazio" }, "nenhuma execução registrada ainda."));
    }
    return el("div", { class: "panel" },
      el("header", {}, el("h2", {}, titulo)),
      el("div", { class: "panel-body" },
        el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "quando"), el("th", {}, "skill"), el("th", {}, "automação"),
            el("th", {}, "status"), el("th", {}, "duração"), el("th", {}, "detalhe"))),
          el("tbody", {}, runs.map((run) =>
            el("tr", {},
              el("td", { class: "mono" }, data(run.started_at)),
              el("td", {}, run.kind),
              el("td", { class: "mono dim" }, run.automation || "—"),
              el("td", {}, tag(run.status, classeStatus(run.status))),
              el("td", { class: "mono" }, duracao(run.duration_ms)),
              el("td", { class: "dim" }, run.error || run.detail || "—"),
            ))),
        ),
      ),
    );
  }

  /* ------------------------------------------------------------------ models */
  function desenharModels(alvo, dados) {
    const agent = dados.providers.agent;
    const lista = dados.models.models || [];

    const seletor = el("select", {},
      lista.length
        ? lista.map((m) => el("option", { value: m.slug, selected: m.current }, `${m.slug}${m.current ? " (atual)" : ""}`))
        : [el("option", { value: agent.slug }, `${agent.slug} (atual)`)]);

    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Modelo do agente")),
        el("div", { class: "panel-body" },
          el("div", { class: "linha-campos" },
            campoTexto("provider", agent.provider, "somente leitura"),
            campoTexto("modelo", agent.model, "somente leitura"),
            campoTexto("rápido (fast)", agent.fast_model || "—", "pedidos curtos"),
            campoTexto("resumo", agent.history_summary_model || "—", "resumo do histórico"),
            campoTexto("fallback", (agent.fallback_models || []).join(", ") || "—", "só 401/402/403"),
            campoTexto("temperatura", agent.temperature ?? "—", ""),
            campoTexto("máx. tokens", agent.max_tokens, ""),
          ),
          el("div", { class: "filtros", style: "margin-top:6px" },
            seletor,
            el("button", {
              class: "acao primary",
              onclick: async () => {
                try {
                  const resposta = await api("/model", {
                    method: "POST",
                    body: JSON.stringify({ model: seletor.value }),
                  });
                  status((resposta.messages || []).join(" · ") || `modelo: ${resposta.model}`);
                  carregar();
                } catch (erro) {
                  falha(erro);
                }
              },
            }, "trocar modelo"),
            el("span", { class: "dim" }, "a troca vale no próximo turno"),
          ),
        ),
      ),
    );

    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Provedores"), el("span", { class: "dim" }, "credencial e conexão")),
        el("div", { class: "panel-body" },
          el("table", {},
            el("thead", {}, el("tr", {},
              el("th", {}, "provider"), el("th", {}, "credencial"), el("th", {}, "origem"),
              el("th", {}, "modelo"), el("th", {}, "conexão"))),
            el("tbody", {}, dados.providers.providers.map((p) =>
              el("tr", {},
                el("td", {}, p.name, p.current ? tag("atual", "ok") : null),
                el("td", { class: "mono" }, p.has_key ? (p.masked || "sem chave") : "ausente"),
                el("td", { class: "dim" }, p.auth_source || (p.local ? "local, sem chave" : "—")),
                el("td", { class: "mono dim" }, p.model || p.default_model || "—"),
                el("td", {}, botaoTestar(p.name)),
              ))),
          ),
        ),
      ),
    );
  }

  function botaoTestar(nome) {
    const saida = el("span", { class: "dim" });
    const botao = el("button", {
      class: "acao",
      onclick: async () => {
        botao.disabled = true;
        saida.textContent = "testando…";
        saida.className = "dim";
        try {
          const r = await api(`/providers/${encodeURIComponent(nome)}/test`, { method: "POST", body: "{}" });
          saida.textContent = `${r.ok ? "ok" : "falhou"} · ${r.message} · ${r.elapsed_ms} ms`;
          saida.className = r.ok ? "ok" : "erro";
        } catch (erro) {
          saida.textContent = erro.message;
          saida.className = "erro";
          if (erro.status === 401) falha(erro);
        } finally {
          botao.disabled = false;
        }
      },
    }, "testar");
    return el("div", { class: "filtros" }, botao, saida);
  }

  function campoTexto(rotulo, valor, dica) {
    return el("div", { class: "campo" },
      el("label", {}, rotulo),
      el("input", { value: valor ?? "", disabled: true }),
      dica ? el("span", { class: "origem" }, dica) : null,
    );
  }

  /* ------------------------------------------------------------------ skills */
  function desenharSkills(alvo, dados) {
    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Catálogo"), el("span", { class: "dim" }, "descritores do núcleo")),
        el("div", { class: "panel-body" },
          el("table", {},
            el("thead", {}, el("tr", {},
              el("th", {}, "skill"), el("th", {}, "tipo"), el("th", {}, "status"),
              el("th", {}, "quando roda"), el("th", {}, "última execução"), el("th", {}, "módulo"))),
            el("tbody", {}, dados.skills.map((s) =>
              el("tr", {},
                el("td", {},
                  el("div", {}, s.name),
                  el("div", { class: "dim" }, s.description),
                  s.reason ? el("div", { class: "aviso" }, s.reason) : null,
                ),
                el("td", { class: "dim" }, s.kind),
                el("td", {}, tag(s.status, classeStatus(s.status))),
                el("td", { class: "dim" }, s.schedule_hint || "—"),
                el("td", { class: "dim" }, s.last_run
                  ? el("div", {},
                      tag(s.last_run.status, classeStatus(s.last_run.status)),
                      el("div", { class: "mono" }, data(s.last_run.started_at)),
                      el("div", {}, s.last_run.error || s.last_run.detail || ""))
                  : "nunca"),
                el("td", { class: "mono dim" }, s.module || "—"),
              ))),
          ),
        ),
      ),
    );
  }

  /* ------------------------------------------------------------- automations */
  function desenharAutomations(alvo, dados) {
    const lista = dados.automations || [];
    const naoRodaram = lista.filter((a) => a.status === "missed");
    const proximas = lista
      .filter((a) => a.next_run)
      .sort((a, b) => String(a.next_run).localeCompare(String(b.next_run)))
      .slice(0, 5);
    alvo.append(
      el("div", { class: "cards" },
        cartao("Ativas", `${lista.filter((a) => a.enabled).length}/${lista.length}`, "ligadas agora"),
        cartao("Não executaram", String(naoRodaram.length),
          naoRodaram.map((a) => a.name).join(", ") || "nada atrasado"),
        cartao("Próxima", proximas.length ? proximas[0].name : "—",
          proximas.length ? data(proximas[0].next_run) : "sem agenda"),
      ),
    );
    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Automações"),
          el("span", { class: "dim" }, "agenda governada pela tabela automations")),
        el("div", { class: "panel-body" },
          el("table", {},
            el("thead", {}, el("tr", {},
              el("th", {}, "automação"), el("th", {}, "dias"), el("th", {}, "janela"),
              el("th", {}, "entrega"), el("th", {}, "status"), el("th", {}, "próxima"), el("th", {}, "última"))),
            el("tbody", {}, lista.map((a) =>
              el("tr", {},
                el("td", {}, a.name, el("div", { class: "mono dim" }, a.id)),
                el("td", { class: "dim" }, a.days_label),
                el("td", { class: "mono" }, `${a.at}–${a.until}`),
                el("td", { class: "dim" }, a.delivery),
                el("td", {}, tag(a.status, classeStatus(a.status))),
                el("td", { class: "mono dim" }, a.next_run ? data(a.next_run) : "—"),
                el("td", { class: "dim" }, a.last_run
                  ? `${a.last_run.status} · ${data(a.last_run.started_at)}`
                  : "nunca"),
              ))),
          ),
        ),
      ),
    );
    const rotinas = dados.routines || [];
    if (rotinas.length) {
      alvo.append(
        el("div", { class: "panel" },
          el("header", {}, el("h2", {}, "Rotinas contínuas"),
            el("span", { class: "dim" }, "boot · laço · evento · varredura")),
          el("div", { class: "panel-body" },
            el("table", {},
              el("thead", {}, el("tr", {},
                el("th", {}, "rotina"), el("th", {}, "gatilho"), el("th", {}, "status"),
                el("th", {}, "motivo"), el("th", {}, "última (manual)"))),
              el("tbody", {}, rotinas.map((r) =>
                el("tr", {},
                  el("td", {}, r.name, el("div", { class: "mono dim" }, r.id)),
                  el("td", { class: "dim" }, r.trigger_label || r.trigger),
                  el("td", {}, tag(r.status, classeStatus(r.status))),
                  el("td", { class: "dim" }, r.reason || r.description || "—"),
                  el("td", { class: "dim" }, r.last_run
                    ? `${r.last_run.status} · ${data(r.last_run.started_at)}`
                    : "nunca"),
                ))),
            ),
          ),
        ),
      );
    }
  }

  /* ---------------------------------------------------------------- activity */
  function desenharActivity(alvo, dados) {
    const kinds = [...new Set(dados.runs.map((r) => r.kind))].sort();
    const filtroSkill = el("select", {},
      el("option", { value: "" }, "todas as skills"),
      kinds.map((k) => el("option", { value: k }, k)));
    const filtroStatus = el("select", {},
      ["", "completed", "failed", "running", "cancelled"].map((s) =>
        el("option", { value: s }, s || "todos os status")));

    const filtrar = async () => {
      try {
        const params = new URLSearchParams({ limit: "80" });
        if (filtroSkill.value) params.set("kind", filtroSkill.value);
        if (filtroStatus.value) params.set("status", filtroStatus.value);
        estado.dados.activity = await api(`/runs?${params}`);
        desenhar();
      } catch (erro) {
        falha(erro);
      }
    };
    filtroSkill.addEventListener("change", filtrar);
    filtroStatus.addEventListener("change", filtrar);

    const resumo = (dados.summary || {}).by_status || {};
    alvo.append(
      el("div", { class: "filtros" },
        filtroSkill, filtroStatus,
        el("span", { class: "dim" }, Object.entries(resumo).map(([k, v]) => `${k}: ${v}`).join(" · ") || "sem execuções"),
      ),
      tabelaExecucoes(`Execuções (${dados.runs.length})`, dados.runs),
    );
  }

  /* ------------------------------------------------------------------ errors */
  function desenharErrors(alvo, dados) {
    if (!dados.errors.length) {
      alvo.append(el("div", { class: "panel" }, el("header", {}, el("h2", {}, "Error Center")),
        el("div", { class: "panel-body vazio" }, "nenhuma falha registrada.")));
      return;
    }
    if ((dados.recurring || []).length) {
      alvo.append(
        el("div", { class: "panel" },
          el("header", {}, el("h2", {}, "Falhas recorrentes")),
          el("div", { class: "panel-body" },
            el("ul", {}, dados.recurring.map((r) =>
              el("li", {}, `${r.kind} · ${r.error_kind}: ${r.count} ocorrência(s)`)))),
        ),
      );
    }
    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Falhas recentes")),
        el("div", { class: "panel-body" },
          el("table", {},
            el("thead", {}, el("tr", {},
              el("th", {}, "quando"), el("th", {}, "skill"), el("th", {}, "origem"),
              el("th", {}, "mensagem"), el("th", {}, "tentativa"), el("th", {}, ""))),
            el("tbody", {}, dados.errors.map((erro) =>
              el("tr", {},
                el("td", { class: "mono" }, data(erro.started_at)),
                el("td", {}, erro.kind),
                el("td", {}, tag(erro.error_kind || "unknown", erro.error_kind === "auth" ? "err" : "warn")),
                el("td", {}, el("div", {}, erro.error),
                  el("details", {}, el("summary", {}, "detalhes técnicos"),
                    el("pre", {}, JSON.stringify(erro, null, 2)))),
                el("td", { class: "mono" }, erro.attempt),
                el("td", {}, tag(erro.status, classeStatus(erro.status))),
              ))),
          ),
        ),
      ),
    );
  }

  /* ---------------------------------------------------------------- settings */
  function desenharSettings(alvo, dados) {
    const { settings, secrets } = dados;

    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Arquivo de configuração"),
          el("span", { class: "dim" }, settings.path)),
        el("div", { class: "panel-body dim" },
          settings.exists
            ? "Editar aqui grava no TOML (com .bak) e relê para conferir. Campo com override no .env fica travado."
            : "Arquivo ainda não existe: a primeira edição cria."),
      ),
    );

    for (const secao of settings.sections) {
      const campos = secao.fields.map((campo) => campoSettings(secao.section, campo));
      alvo.append(
        el("div", { class: "panel" },
          el("header", {}, el("h2", {}, secao.label), el("span", { class: "mono dim" }, `[${secao.section}]`)),
          el("div", { class: "panel-body" }, el("div", { class: "linha-campos" }, campos)),
        ),
      );
    }

    alvo.append(
      el("div", { class: "panel" },
        el("header", {}, el("h2", {}, "Credenciais"),
          el("span", { class: "dim" }, secrets.path)),
        el("div", { class: "panel-body" },
          el("p", { class: "dim" },
            "As chaves ficam no .env (chmod 600) e nunca são exibidas por inteiro. Gravar faz backup (.env.bak)."),
          el("table", {},
            el("thead", {}, el("tr", {},
              el("th", {}, "chave"), el("th", {}, "valor"), el("th", {}, "origem"), el("th", {}, "ações"))),
            el("tbody", {}, secrets.secrets.map(linhaSecret)),
          ),
          novaCredencial(secrets.allowed || []),
        ),
      ),
    );
  }

  // Rotulo humano de valor de enum (o TOML guarda o identificador).
  const ROTULOS_OPCAO = {
    browser: "microfone do navegador",
    server: "microfone do servidor",
  };

  function rotuloOpcao(chave, valor) {
    return ROTULOS_OPCAO[valor] || String(valor);
  }

  function campoSettings(secao, campo) {
    const aviso = campo.source === "env"
      ? el("span", { class: "origem env" }, `definido por ${campo.env_key} no .env`)
      : el("span", { class: "origem" }, `origem: ${campo.source}`);

    // `options` vem do motor para enums disfarcados de str (ex.: fonte do audio
    // do modo voz = browser|server): vira <select>, nunca texto livre.
    const opcoes = Array.isArray(campo.options) ? campo.options : [];
    let entrada;
    if (campo.type === "bool") {
      entrada = el("select", { disabled: !campo.editable },
        el("option", { value: "true", selected: campo.value === true }, "ligado"),
        el("option", { value: "false", selected: campo.value === false }, "desligado"));
    } else if (opcoes.length) {
      entrada = el("select", { disabled: !campo.editable },
        opcoes.map((valor) => el("option", {
          value: valor,
          selected: campo.value === valor,
        }, rotuloOpcao(campo.key, valor))));
    } else if (campo.type === "list") {
      entrada = el("input", {
        value: (campo.value || []).join(", "),
        disabled: !campo.editable,
        placeholder: "0, 2, 4",
      });
    } else if (campo.type === "time") {
      entrada = el("input", { type: "time", value: campo.value || "", disabled: !campo.editable });
    } else {
      entrada = el("input", {
        value: campo.value ?? "",
        disabled: !campo.editable,
        type: campo.type === "int" || campo.type === "float" ? "number" : "text",
        step: campo.type === "float" ? "any" : null,
      });
    }

    const salvar = el("button", {
      class: "acao",
      disabled: !campo.editable,
      onclick: async () => {
        let valor = entrada.value;
        if (campo.type === "bool") valor = valor === "true";
        else if (campo.type === "list") {
          valor = valor.split(",").map((v) => v.trim()).filter(Boolean).map((v) => Number(v));
        } else if (campo.type === "int") valor = Number.parseInt(valor, 10);
        else if (campo.type === "float") valor = Number.parseFloat(valor);
        try {
          const r = await api("/settings", {
            method: "PATCH",
            body: JSON.stringify({ section: secao, key: campo.key, value: valor }),
          });
          status(`${secao}.${campo.key} = ${JSON.stringify(r.value)} (backup em ${r.backup || "—"})`, "ok");
          carregar();
        } catch (erro) {
          falha(erro);
        }
      },
    }, "salvar");

    const removerOverride = campo.source === "env"
      ? el("button", {
          class: "acao",
          onclick: async () => {
            try {
              const r = await api("/settings/override", {
                method: "POST",
                body: JSON.stringify({ section: secao, key: campo.key }),
              });
              status(r.note || "override removido", "aviso");
              carregar();
            } catch (erro) {
              falha(erro);
            }
          },
        }, "remover override")
      : null;

    return el("div", { class: "campo" },
      el("label", {}, campo.key),
      entrada,
      el("div", { class: "filtros" }, salvar, removerOverride),
      aviso,
    );
  }

  function linhaSecret(item) {
    const entrada = el("input", { type: "password", placeholder: "novo valor", autocomplete: "off" });
    return el("tr", {},
      el("td", { class: "mono" }, item.key),
      el("td", { class: "mono" }, item.masked || "—"),
      el("td", { class: "dim" }, item.source === "env" ? "ambiente" : item.source || "—"),
      el("td", {},
        el("div", { class: "filtros" },
          entrada,
          el("button", {
            class: "acao",
            onclick: async () => {
              if (!entrada.value) {
                status("informe o valor", "aviso");
                return;
              }
              try {
                const r = await api(`/secrets/${encodeURIComponent(item.key)}`, {
                  method: "PUT",
                  body: JSON.stringify({ value: entrada.value }),
                });
                entrada.value = "";
                status(`${r.key} gravada (${r.masked})`, "ok");
                carregar();
              } catch (erro) {
                falha(erro);
              }
            },
          }, "gravar"),
          el("button", {
            class: "acao",
            onclick: async () => {
              try {
                const r = await api(`/secrets/${encodeURIComponent(item.key)}`, { method: "DELETE" });
                status(r.note || "credencial removida", "aviso");
                carregar();
              } catch (erro) {
                falha(erro);
              }
            },
          }, "remover"),
        ),
      ),
    );
  }

  function novaCredencial(permitidas) {
    const chave = el("input", { placeholder: "GAIA_AGENT_API_KEY" });
    const valor = el("input", { type: "password", placeholder: "valor", autocomplete: "off" });
    if (permitidas.length) chave.setAttribute("list", "chaves-permitidas");
    const lista = el("datalist", { id: "chaves-permitidas" },
      permitidas.map((k) => el("option", { value: k })));
    return el("div", { class: "filtros", style: "margin-top:12px" },
      chave, valor, lista,
      el("button", {
        class: "acao primary",
        onclick: async () => {
          if (!chave.value || !valor.value) {
            status("informe chave e valor", "aviso");
            return;
          }
          try {
            const r = await api(`/secrets/${encodeURIComponent(chave.value)}`, {
              method: "PUT",
              body: JSON.stringify({ value: valor.value }),
            });
            status(`${r.key} gravada (${r.masked})`, "ok");
            carregar();
          } catch (erro) {
            falha(erro);
          }
        },
      }, "adicionar"));
  }

  /* ------------------------------------------------------------------- modal */
  function abrirModal() {
    // `type=password`: nao preenche o campo com o token guardado. Quem ja tem
    // sessao nem ve este modal; quem nao tem digita uma vez por navegador.
    $("#input-token").value = "";
    const lembrar = $("#input-lembrar");
    if (lembrar) lembrar.checked = auth.lembrado("control");
    $("#modal").hidden = false;
  }
  function fecharModal() {
    $("#modal").hidden = true;
  }

  /** Encerra a sessao no servidor e esquece o token local. */
  async function sair() {
    await auth.sair("control");
    estado.sessao = false;
    $("#input-token").value = "";
    status("sessao encerrada", "aviso");
    abrirModal();
  }

  /* ------------------------------------------------------------------- boot */
  document.querySelectorAll("nav button").forEach((botao) => {
    botao.addEventListener("click", () => trocarAba(botao.dataset.tab));
  });
  $("#btn-token").addEventListener("click", abrirModal);
  tema.montar($("#btn-tema"));
  $("#btn-refresh").addEventListener("click", carregar);
  $("#btn-close-modal").addEventListener("click", fecharModal);
  $("#btn-save-token").addEventListener("click", async () => {
    const valor = $("#input-token").value.trim();
    const lembrar = Boolean($("#input-lembrar") && $("#input-lembrar").checked);
    // Sem token nao ha o que trocar: so fecha e mostra o aviso.
    if (!valor) {
      auth.lembrar("control", false, "");
      fecharModal();
      status("informe o token para carregar", "aviso");
      return;
    }
    estado.sessao = await autenticar(valor);
    if (!estado.sessao) {
      auth.lembrar("control", false, "");
      status("token recusado", "erro");
      return;
    }
    // Opt-in: so grava o segredo se a pessoa marcou. Desmarcado apaga o que
    // houvesse guardado de uma sessao anterior.
    auth.lembrar("control", lembrar, lembrar ? valor : "");
    fecharModal();
    carregar();
  });
  $("#btn-clear-token").addEventListener("click", sair);
  $("#input-token").addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") $("#btn-save-token").click();
  });

  carregarInstancias();
  trocarAba(estado.aba);
  setInterval(() => {
    if (document.visibilityState === "visible" && estado.aba === "overview") carregar();
  }, 30000);
})();
