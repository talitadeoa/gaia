/* Lógica compartilhada do web do `gaia serve` (chat e Control Center).
 *
 * Sem framework e sem build. Exporta `window.Gaia` com:
 *
 *   - `auth`      : sessão de cookie (`POST /session`), logout, token lembrado
 *   - `api`       : fetch same-origin com cookie, `Bearer` opcional e erro tipado
 *   - `instances` : lista de `instances.json`, seletor no header e troca de máquina
 *   - `header`    : pinta instância/modelo/provedor/chave mascarada/modo
 *   - `tema`      : claro/escuro/sistema (Catppuccin Latte e Mocha), no <html>
 *
 * O tema e a excecao a regra "segredo nao fica no navegador": ele nao e
 * credencial, e preferencia de quem esta olhando a tela, entao mora no
 * `localStorage` (`gaia.tema`) e vale para o chat e o painel no mesmo
 * navegador. O CSS so conhece `data-tema="claro"` e `data-tema="escuro"`:
 * quem resolve "sistema" e este modulo (`matchMedia`), para a paleta existir
 * uma vez so em cada arquivo. O `<head>` das duas paginas repete a resolucao
 * num script inline minimo — sem ele a primeira pintura sai na paleta escura.
 *
 * Regra de credencial (a mesma nos dois lados):
 *   1. O token (`GAIA_SERVE_TOKEN` no chat, `GAIA_CONTROL_TOKEN` no painel) só é
 *      enviado no `Authorization` para TROCAR por sessão. Depois disso quem
 *      autentica é o cookie `HttpOnly` — o JS não lê o nonce.
 *   2. Guardar o token no `localStorage` é opt-in explícito ("lembrar token
 *      neste navegador"). Desmarcado (padrão), nada do segredo fica no
 *      navegador: quando o cookie vence, a tela pede de novo.
 *   3. O token lembrado só serve para refazer a troca em silêncio; ele nunca
 *      aparece na URL e nunca é o que autentica cada requisição.
 */
(() => {
  "use strict";

  const CHAT = {
    nome: "chat",
    base: "",
    sessao: "/session",
    chaveToken: "gaia.token",
    chaveLembrar: "gaia.lembrar.chat",
  };
  const CONTROL = {
    nome: "control",
    base: "/control/api",
    sessao: "/control/api/session",
    chaveToken: "gaia.control.token",
    chaveLembrar: "gaia.lembrar.control",
  };
  const PERFIS = { chat: CHAT, control: CONTROL };

  const texto = (valor) => String(valor === null || valor === undefined ? "" : valor);

  /** Elemento com atributos, eventos e filhos (o mesmo helper do painel). */
  function el(tag, attrs = {}, ...filhos) {
    const no = document.createElement(tag);
    for (const [chave, valor] of Object.entries(attrs)) {
      if (valor === null || valor === undefined || valor === false) continue;
      if (chave === "class") no.className = valor;
      else if (chave === "html") no.innerHTML = valor;
      else if (chave === "text") no.textContent = valor;
      else if (chave.startsWith("on")) no.addEventListener(chave.slice(2), valor);
      else no.setAttribute(chave, valor);
    }
    // `flat(Infinity)`: listas de listas (grupos do backlog) viram filhos, e
    // nunca o texto "[object HTMLDetailsElement]".
    for (const filho of filhos.flat(Infinity)) {
      if (filho === null || filho === undefined || filho === false) continue;
      no.append(filho instanceof Node ? filho : document.createTextNode(String(filho)));
    }
    return no;
  }

  /* ------------------------------------------------------------- armazenamento */
  const auth = {
    perfil(nome) {
      return PERFIS[nome] || CHAT;
    },

    /** O token foi guardado neste navegador? (opt-in) */
    lembrado(nome = "chat") {
      const p = auth.perfil(nome);
      return localStorage.getItem(p.chaveLembrar) === "1";
    },

    /** Token lembrado ("" quando não há ou quando a pessoa não quis lembrar). */
    token(nome = "chat") {
      const p = auth.perfil(nome);
      if (!auth.lembrado(nome)) return "";
      return localStorage.getItem(p.chaveToken) || "";
    },

    /** Lembra/esquece o token. `false` apaga também o que estava guardado. */
    lembrar(nome, ligado, segredo) {
      const p = auth.perfil(nome);
      if (!ligado) {
        localStorage.removeItem(p.chaveToken);
        localStorage.removeItem(p.chaveLembrar);
        return;
      }
      localStorage.setItem(p.chaveLembrar, "1");
      if (segredo !== undefined && segredo !== null) {
        localStorage.setItem(p.chaveToken, segredo);
      }
    },

    /** Esquece o token e encerra a sessão no servidor (idempotente). */
    async sair(nome = "chat") {
      const p = auth.perfil(nome);
      auth.lembrar(nome, false, "");
      try {
        await fetch(p.sessao, { method: "DELETE", credentials: "same-origin" });
      } catch (_) {
        /* sem rede: o cookie sai do navegador do mesmo jeito que o token local */
      }
    },

    /** Existe prova válida agora? Pergunta ao servidor (não confia em cache). */
    async sessao(nome = "chat") {
      const p = auth.perfil(nome);
      try {
        const resposta = await fetch(p.sessao, { credentials: "same-origin" });
        if (!resposta.ok) return false;
        const corpo = await resposta.json().catch(() => ({}));
        return Boolean(corpo && corpo.authenticated);
      } catch (_) {
        return false;
      }
    },

    /** Troca o token por cookie de sessão. Devolve true se deu certo. */
    async entrar(nome, segredo) {
      const p = auth.perfil(nome);
      const valor = texto(segredo).trim();
      if (!valor) return false;
      try {
        const resposta = await fetch(p.sessao, {
          method: "POST",
          credentials: "same-origin",
          headers: { Authorization: "Bearer " + valor, "Content-Type": "application/json" },
          body: "{}",
        });
        return resposta.ok;
      } catch (_) {
        return false;
      }
    },
  };

  /* ----------------------------------------------------------------- chamadas */
  /** fetch same-origin com cookie; `Bearer` só se houver token (fallback). */
  async function api(caminho, opcoes = {}) {
    // `base`/`perfil`/`bearer` são nossas, não do fetch: saem do init.
    const { base = "", perfil, bearer, ...init } = opcoes;
    const nome = perfil || (base.startsWith("/control") ? "control" : "chat");
    const segredo = bearer === false ? "" : auth.token(nome);
    const cabecalhos = {
      ...(segredo ? { Authorization: "Bearer " + segredo } : {}),
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    };
    const resposta = await fetch(base + caminho, {
      ...init,
      credentials: "same-origin",
      headers: cabecalhos,
    });
    let corpo = null;
    const tipo = resposta.headers.get("Content-Type") || "";
    try {
      corpo = tipo.includes("application/json") ? await resposta.json() : await resposta.text();
    } catch (_) {
      corpo = null;
    }
    if (!resposta.ok) {
      const detalhe = (corpo && (corpo.message || corpo.error)) || resposta.statusText;
      const erro = new Error(`${resposta.status}: ${detalhe}`);
      erro.status = resposta.status;
      erro.corpo = corpo;
      throw erro;
    }
    return corpo;
  }

  /* --------------------------------------------------------------- instâncias */
  const instances = {
    lista: [],

    /** Carrega `/instances` (público). Falha = lista vazia, nunca exceção. */
    async carregar() {
      try {
        const dados = await api("/instances");
        instances.lista = Array.isArray(dados && dados.instances) ? dados.instances : [];
      } catch (_) {
        instances.lista = [];
      }
      return instances.lista;
    },

    atual() {
      return instances.lista.find((item) => item.current) || null;
    },

    /** Trocar de instância é navegação: cookie é por origem (SameSite=Strict). */
    trocar(id) {
      const alvo = instances.lista.find((item) => item.id === id);
      if (!alvo || alvo.current) return false;
      window.location.href = alvo.url + (window.location.pathname === "/control" ? "/control" : "/");
      return true;
    },

    /**
     * Seletor do header. Sem `instances.json`, devolve null (a tela segue sem
     * seletor, mostrando só o nome de quem está respondendo).
     */
    seletor(onTrocar) {
      if (!instances.lista.length) return null;
      const atual = instances.atual();
      const select = el(
        "select",
        { class: "pill instancias", title: "trocar de instância (o cookie é por endereço)" },
        instances.lista.map((item) =>
          el(
            "option",
            { value: item.id, selected: item.current },
            `${item.online === false ? "○" : "●"} ${item.name}${item.online === false ? " (offline)" : ""}`,
          ),
        ),
        !atual ? el("option", { value: "", selected: true }, "instância atual") : null,
      );
      select.addEventListener("change", () => {
        if (onTrocar) onTrocar(select.value);
        else instances.trocar(select.value);
      });
      const aviso = el(
        "span",
        { class: "dica instancia-status", title: "status medido pelo servidor" },
        instances.lista
          .filter((item) => item.online === false)
          .map((item) => `${item.name}: offline`)
          .join(" · "),
      );
      return el("span", { class: "instancia" }, select, aviso);
    },
  };

  /* ------------------------------------------------------------------- header */
  const header = {
    /**
     * Pinta nome da instância, modelo, provedor, chave mascarada e modo.
     * `alvos` são ids/seletores; o que não existir é ignorado.
     */
    pintar(engine, alvos = {}) {
      const dados = engine || {};
      const instancia = dados.instance || {};
      const set = (seletor, valor, titulo) => {
        if (!seletor) return;
        const no = typeof seletor === "string" ? document.querySelector(seletor) : seletor;
        if (!no) return;
        no.textContent = texto(valor);
        if (titulo !== undefined) no.title = texto(titulo);
      };
      set(alvos.instancia, instancia.name || "", instancia.url || "");
      set(alvos.modelo, dados.model || "—", "modelo ativo");
      set(alvos.provedor, dados.provider || "", "provedor ativo");
      const chave = dados.masked || (dados.has_key ? "(chave oculta)" : "sem chave");
      set(alvos.chave, chave, "a chave nunca aparece inteira");
      set(alvos.modo, dados.mode || "", "modo (persona) ativo");
      return { instancia, chave };
    },

    /** Quadro lateral com o estado do motor (modelo, provedor, chave, modo). */
    cartao(engine) {
      const dados = engine || {};
      const linha = (rotulo, valor) =>
        el("div", { class: "linha-info" }, el("span", { class: "dica" }, rotulo), el("span", {}, valor || "—"));
      return el(
        "div",
        { class: "cartao-motor" },
        el("div", { class: "dica" }, "motor"),
        linha("modelo", dados.model),
        linha("provedor", dados.provider),
        linha("chave", dados.masked || (dados.has_key ? "(oculta)" : "sem chave")),
        linha("origem", dados.auth_source),
        linha("modo", dados.mode),
      );
    },
  };

  /* --------------------------------------------------------------------- tema */
  /** Preferencia de tema: `sistema` (segue o aparelho), `escuro` ou `claro`. */
  const CHAVE_TEMA = "gaia.tema";
  const TEMAS = ["sistema", "escuro", "claro"];
  const ROTULOS = { sistema: "sistema", escuro: "escuro", claro: "claro" };

  /** O aparelho esta em modo escuro agora? (sem `matchMedia`, assume escuro) */
  function aparelhoEscuro() {
    if (typeof window.matchMedia !== "function") return true;
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  const tema = {
    chave: CHAVE_TEMA,
    opcoes: TEMAS,

    /** Escolha salva (`sistema` quando nao ha nada, ou lixo no storage). */
    escolhido() {
      try {
        const salvo = texto(localStorage.getItem(CHAVE_TEMA)).toLowerCase();
        return TEMAS.includes(salvo) ? salvo : "sistema";
      } catch (_) {
        // storage bloqueado (modo privado, politica): a tela ainda funciona.
        return "sistema";
      }
    },

    /** Paleta efetiva para uma escolha: `sistema` vira `escuro` ou `claro`. */
    paleta(escolha = tema.escolhido()) {
      if (escolha === "claro" || escolha === "escuro") return escolha;
      return aparelhoEscuro() ? "escuro" : "claro";
    },

    /** Aplica no `<html>` (`data-tema`) e devolve a escolha usada. */
    aplicar(escolha) {
      const usado = TEMAS.includes(texto(escolha)) ? texto(escolha) : tema.escolhido();
      const raiz = document.documentElement;
      raiz.dataset.tema = tema.paleta(usado);
      raiz.dataset.temaEscolha = usado;
      return usado;
    },

    /** Grava a escolha (`sistema` apaga a chave) e aplica na hora. */
    definir(escolha) {
      const usado = TEMAS.includes(texto(escolha)) ? texto(escolha) : "sistema";
      try {
        if (usado === "sistema") localStorage.removeItem(CHAVE_TEMA);
        else localStorage.setItem(CHAVE_TEMA, usado);
      } catch (_) {
        /* sem storage: a escolha vale so nesta pagina, o que ja e melhor que nada */
      }
      return tema.aplicar(usado);
    },

    /** Proxima escolha do ciclo do botao: sistema -> escuro -> claro -> sistema. */
    proximo(escolha = tema.escolhido()) {
      const atual = TEMAS.indexOf(escolha);
      return TEMAS[(atual < 0 ? 0 : atual + 1) % TEMAS.length];
    },

    /** Texto/titulo do botao do header ("tema · claro", "tema · sistema"…). */
    rotulo(escolha = tema.escolhido()) {
      return "tema · " + (ROTULOS[escolha] || ROTULOS.sistema);
    },

    /**
     * Liga um botao do header ao ciclo de temas. Idempotente: o header das duas
     * telas e estatico, entao cada botao e montado uma vez so.
     */
    montar(botao) {
      if (!botao) return null;
      const pintar = (escolha) => {
        botao.textContent = tema.rotulo(escolha);
        botao.title =
          "tema da tela: " + ROTULOS[escolha]
          + " (clique para alternar sistema, escuro e claro)";
        botao.dataset.escolha = escolha;
      };
      const escolha = tema.aplicar(tema.escolhido());
      pintar(escolha);
      botao.addEventListener("click", () => pintar(tema.definir(tema.proximo())));
      // "sistema" precisa acompanhar o aparelho AO VIVO (o SO troca sozinho no
      // fim da tarde); so vale quando a escolha ainda e `sistema`.
      if (typeof window.matchMedia === "function") {
        const media = window.matchMedia("(prefers-color-scheme: dark)");
        const aoTrocar = () => {
          if (tema.escolhido() === "sistema") tema.aplicar("sistema");
        };
        if (typeof media.addEventListener === "function") {
          media.addEventListener("change", aoTrocar);
        } else if (typeof media.addListener === "function") {
          media.addListener(aoTrocar); // Safari antigo, sem `addEventListener`
        }
      }
      return botao;
    },
  };

  window.Gaia = { api, auth, el, header, instances, tema, texto };
})();
