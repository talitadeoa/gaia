"""O web do `gaia serve`: paleta de comandos, sessoes e eco de idiomas.

Tres contratos que a tela depende e que quebram em silencio:

* o ``index.html`` nao pode ter ``id`` repetido nem ``$("#x")`` apontando para
  elemento que nao existe (isso foi o bug do botao "sessoes", que pegava o
  ``<dialog>`` e chamava ``showModal`` num ``<button>``);
* ``GET /commands`` serve o mesmo catalogo da ajuda do terminal;
* ``POST /chinese`` liga o eco no motor (qualquer idioma) e o evento ``zh`` so
  e emitido com ele ligado; ``GET /languages``/``POST /language`` sao o dialog
  "idiomas", que oferece 中文, English e Français e manda o idioma escolhido
  no `lang` do evento (o `zh` fixo do passado nao existe mais).
"""

from __future__ import annotations

import asyncio
import http.client
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from gaia.controller import command_catalog
from gaia.interface.remote import RemoteUI
from gaia.serve.app import OrchestratorTarget
from gaia.serve.server import ServeServer

RAIZ = Path(__file__).resolve().parent.parent
INDEX = RAIZ / "gaia" / "serve" / "static" / "index.html"


# ------------------------------------------------------------------ html ---


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.soma: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for nome, valor in attrs:
            if nome == "id" and valor:
                self.ids.append(valor)


def _html() -> tuple[str, _Ids]:
    texto = INDEX.read_text(encoding="utf-8")
    coletor = _Ids()
    coletor.feed(texto)
    return texto, coletor


def test_nenhum_id_repetido_no_index():
    """`#sessoes` era o botao *e* o dialog: `$("#sessoes")` devolvia o botao."""
    _, coletor = _html()
    repetidos = {item for item in coletor.ids if coletor.ids.count(item) > 1}
    assert repetidos == set()


def test_todo_seletor_de_id_existe_no_html():
    texto, coletor = _html()
    usados = set(re.findall(r'\$\("#([a-z0-9-]+)"\)', texto))
    faltando = usados - set(coletor.ids)
    assert faltando == set(), f"$() aponta para id inexistente: {faltando}"


def test_botoes_novos_do_header():
    texto, _ = _html()
    for botao in ("btn-sessoes", "btn-nova", "btn-idiomas"):
        assert f'id="{botao}"' in texto
    # O atual de cada lista continua sendo o alvo do `showModal`.
    assert 'dialog id="sessoes"' in texto
    assert 'dialog id="idiomas"' in texto
    # O botao 中文 fixo saiu: quem escolhe o idioma e o dialog.
    assert 'id="btn-chines"' not in texto


def test_tela_de_automacoes_no_html():
    """Botao do header NAVEGA para a tela propria (`#automacoes`), sem dialog.

    O desenho mudou de "dialog somente leitura" para tela com checkbox: o
    contrato agora e o hash (F5/voltar do navegador), os alvos da tela e a
    leitura/escrita das mesmas rotas do motor.
    """
    texto, coletor = _html()
    assert 'id="btn-automacoes"' in texto
    assert 'id="view-automacoes"' in texto
    assert 'id="view-chat"' in texto
    assert 'dialog id="automacoes-dialog"' not in texto
    assert "automacoes" in coletor.ids
    # O botao chama a navegacao (pelo hash), nao `showModal`.
    assert '"#btn-automacoes").addEventListener("click", () => irParaTela("#automacoes"))' in texto
    assert '"#automacoes": "automacoes"' in texto
    assert 'hashchange' in texto
    # Le o mesmo JSON do painel e nao inventa status.
    assert 'api("/automations")' in texto
    for estado in ("scheduled", "missed", "paused", "failed"):
        assert estado in texto
    # Checkbox de cada rotina e da proatividade gravam no motor.
    assert 'api("/automations/set"' in texto
    assert 'api("/proactive"' in texto
    # Status em portugues (o pedido era "se falhou ou se deu certo").
    assert '"deu certo"' in texto and '"não rodou"' in texto
    # Agrupamento por bot: o dono vem do servidor (`render` devolve `bot`).
    assert "item.bot || \"gaia\"" in texto


def test_rotinas_continuas_na_tela_de_automacoes():
    """A mesma tela mostra as duas familias: automacao (janela) e rotina (gatilho).

    O contrato: a secao existe, cada rotina tem checkbox (ativar/desativar) e,
    quando `runnable`, o "rodar agora"; e as duas familias escrevem pelas MESMAS
    rotas do motor (`/automations/set` e `/automations/run`).
    """
    texto, coletor = _html()
    assert 'id="auto-rotinas"' in texto
    assert "auto-rotinas" in coletor.ids
    # Uma linha por rotina, com o gatilho vindo do servidor (nao do JS).
    assert "function linhaRotina(item)" in texto
    assert "trigger_label" in texto
    # Checkbox de ativar/desativar para TODAS as rotinas.
    assert "onchange: (evento) => alternarRotina(item, evento.target)" in texto
    # "rodar agora" so nas que tem handler.
    assert "item.runnable" in texto
    assert "onclick: (evento) => rodarRotina(item, evento.currentTarget)" in texto
    # Mesmas rotas: rotina e automacao compartilham a escrita.
    assert 'api("/automations/set"' in texto
    assert 'api("/automations/run"' in texto
    # Status novos existem no mapa da tela (sem "undefined" na etiqueta).
    for estado in ("active", "paused", "disabled", "error"):
        assert estado in texto
    # O motivo do servidor aparece (config desligada / worker parado).
    assert "item.reason" in texto
    # O resumo do cabecalho conta as duas familias.
    assert "routines_summary" in texto
    assert "rotinas ativas" in texto


def test_tela_de_backlog_propria():
    """O backlog tem tela propria (`#backlog`), como a de automacoes.

    O card do aside ja existia; o que faltava era o espaco para ler o backlog
    inteiro (todos os estados, o repo de cada projeto, o campo de item novo)
    sem o aperto de 320px. A tela e uma VIEW, nao um dialog: o hash e a fonte
    de verdade (F5 e o voltar do navegador continuam nela) e o corpo vem do
    MESMO componente do card (`backlogCorpo`), sem duplicar regra.
    """
    texto, coletor = _html()
    assert 'id="btn-backlog"' in texto
    assert 'id="view-backlog"' in texto
    assert 'id="backlog-tela"' in texto
    assert "view-backlog" in coletor.ids
    assert "automacoes" in coletor.ids
    assert 'dialog id="backlog"' not in texto
    # Navegacao pelo hash, sem showModal.
    assert '"#btn-backlog").addEventListener("click"' in texto
    assert '"#backlog": "backlog"' in texto
    assert 'function irParaTela(hash)' in texto
    assert '$("#view-backlog").hidden = tela !== "backlog"' in texto
    # A tela nao duplica a regra: cabecalho de la, corpo compartilhado aqui.
    assert 'alvo.replaceChildren(backlogCorpo(dados, { local: "agenda" }))' in texto
    assert '"#backlog-chat").addEventListener("click", voltarAoChat)' in texto
    assert '"#backlog-atualizar").addEventListener("click"' in texto
    # Clicar no cabecalho do card do aside abre a tela; "recolher"/"expandir"
    # e que mexe na lista dentro do aside.
    assert 'title: "abrir o backlog na tela inteira"' in texto
    assert 'onclick: () => irParaTela("#backlog")' in texto
    assert '"recolher" : "expandir"' in texto


def test_card_de_backlog_expande_ao_clicar():
    """O card do aside nasce recolhido e abre no clique, como o de automacoes.

    O bug que isto guarda: o card despejava a lista crua no aside (com
    `[object HTMLDetailsElement]` no lugar de cada projeto) e o cabecalho
    repetia ``7 item(ns) · 3 pendente(s)``. Agora o mesmo corpo desenha os
    dois lugares (aside recolhido, tela `#backlog` aberta) e o cabecalho
    do aside NAVEGA para a tela; quem abre/fecha a lista ali e o "recolher".
    """
    texto, _ = _html()
    assert 'id="backlog-card"' in texto and 'id="backlog-tela"' in texto
    # Um componente so, com o estado de aberto/fechado por lugar.
    assert "function backlogCard(dados, { local })" in texto
    assert "function backlogCorpo(dados, { local })" in texto
    assert "function pintarBacklog(dados)" in texto
    # O toggle redesenha so o card (a tela nao perde o foco do campo).
    assert "function pintarBacklogCard()" in texto
    assert "backlogDados: null" in texto
    assert "backlogAbertura: { card: false }" in texto
    assert 'class: "topo abrir"' in texto and 'onclick: () => irParaTela("#backlog")' in texto
    # Cada projeto e um collapsible de verdade (`details`/`summary`).
    assert 'window.Gaia.el("details", { class: "projeto"' in texto
    assert 'window.Gaia.el("summary", {' in texto and 'class: chaveGrupo' not in texto
    # Projeto sem repo avisa na propria linha (o despacho recusa).
    assert 'class: "repo falta"' in texto and '"sem repositório"' in texto
    # O estado do collapsible do usuario sobrevive ao redesenho do SSE.
    assert "estado.backlogGrupos" in texto and '"toggle"' in texto
    # O resumo nao repete singular/plural entre parenteses.
    assert 'total + " item(ns)' not in texto
    assert 'pendentes + " pendente(s)"' not in texto
    # O aside e a tela leem a mesma rota e o mesmo evento.
    assert 'api("/backlog")' in texto and 'api("/backlog/sync"' in texto
    assert 'api("/backlog/run"' in texto and 'api("/backlog/add"' in texto
    assert 'case "backlog"' in texto
    # Status do pedido: pendente -> enviado ao mini -> em execucao -> PR/erro.
    for rotulo in ('"pendente"', '"enviado ao mini"', '"em execução no pi"',
                   '"PR aberto"', '"erro"'):
        assert rotulo in texto
    # Limpeza de 7 dias visivel na tela (o servidor e que filtra).
    assert "prune_days" in texto


def test_backlog_pede_o_repo_no_ponto_de_adicionar():
    """Pagina nova sem mapeamento: o campo de repo aparece onde a pessoa esta.

    O problema: ``x backlog`` sem ``[backlog.repos]`` aparecia no card e o item
    nunca despachava. Agora o grupo diz "sem repositório" e o formulario de
    item novo ganha o campo que grava no TOML (``POST /backlog/repo``).
    """
    texto, _ = _html()
    assert 'api("/backlog/repo"' in texto
    assert 'id: "backlog-repo-" + local' in texto
    assert 'placeholder: "dono/repo deste projeto"' in texto
    assert '"salvar repositório"' in texto
    assert "needs_repo" in texto
    # O rascunho guarda o repo por pele (o redesenho do SSE nao apaga).
    assert 'card: { texto: "", projeto: "", repo: "" }' in texto
    assert 'agenda: { texto: "", projeto: "", repo: "" }' in texto


def test_js_das_duas_telas_tem_sintaxe_valida(tmp_path):
    """Erro de sintaxe no JS nao aparece no pytest: `node --check` pega.

    O `control.js` ja nasceu com uma chave a mais (o panel das rotinas ficou
    fora da funcao) e o contrato textual continuou verde. Sem node instalado o
    teste e pulado: o CI nao depende dele.
    """
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node nao instalado")

    from gaia.serve import web

    arquivos: list[tuple[str, str]] = []
    texto, _ = _html()
    for indice, bloco in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", texto, re.S)):
        arquivos.append((f"index.html:{indice}", bloco))
    for nome in ("control.js", "shared.js"):
        bruto, _tipo = web.control_asset(nome)  # type: ignore[misc]
        arquivos.append((nome, bruto.decode("utf-8")))

    for nome, conteudo in arquivos:
        # `node --check` decide o parser pela extensao: sempre `.js`.
        caminho = tmp_path / (nome.replace(":", "_").replace("/", "_") + ".js")
        caminho.write_text(conteudo, encoding="utf-8")
        resultado = subprocess.run(
            [node, "--check", str(caminho)], capture_output=True, text=True
        )
        assert resultado.returncode == 0, f"{nome}: {resultado.stderr[:600]}"


def test_el_achata_listas_de_filhos():
    """`el("div", {}, [a, b])` precisa virar filhos, nao `[object ...]`."""
    from gaia.serve import web

    bruto, _ = web.static_asset("shared.js")  # type: ignore[misc]
    js = bruto.decode("utf-8")
    assert "filhos.flat(Infinity)" in js


def test_botao_de_voz_no_html():
    """O modo voz e um contrato entre HTML, JS e rotas `/voice/*`."""
    texto, _ = _html()
    assert 'id="btn-voz"' in texto and 'hidden' in texto.split('id="btn-voz"')[1][:200]
    # O botao so aparece depois de `GET /voice`; o Enter passa por `vozEnter`.
    assert 'carregarVoz()' in texto
    assert 'vozEnter()' in texto
    assert '/voice/listen' in texto and '/voice/speak' in texto and '/voice/stop' in texto
    # Ao terminar o turno falado, a resposta e falada e o mic volta.
    assert 'vozFalar(' in texto and 'vozSilenciar(' in texto
    # `M` e o atalho de mudo (como na TUI), nao o Enter.
    assert 'vozAlternarMudo()' in texto and 'teclaMudo(' in texto
    assert '"m"' in texto and '"M"' in texto
    # O estado "transcrevendo" aparece de verdade: a tela sonda `GET /voice`,
    # que publica a fase da escuta — antes ela so existia no rotulo.
    assert 'vozSondar(' in texto and 'dados.state === "transcrevendo"' in texto
    assert 'state' in texto


def test_modo_voz_grava_wav_no_navegador():
    """Microfone do navegador: getUserMedia -> WAV 16-bit -> /voice/upload."""
    texto, _ = _html()
    assert "navigator.mediaDevices.getUserMedia" in texto
    assert 'api("/voice/upload"' in texto
    assert '"Content-Type": "audio/wav"' in texto
    # WAV de verdade: cabecalho RIFF/WAVE e PCM 16-bit mono 16 kHz.
    assert 'texto(0, "RIFF")' in texto and 'texto(8, "WAVE")' in texto
    assert "VOZ_WAV_HZ = 16000" in texto and "new Int16Array" in texto
    # A origem do audio vem do motor (`GET /voice` -> `source`).
    assert 'dados.source === "server" ? "server" : "browser"' in texto
    assert 'voz.fonte === "browser"' in texto
    # A rota antiga continua existindo (microfone do servidor).
    assert '/voice/listen' in texto


def test_enter_solta_a_gravacao_do_navegador():
    """No modo navegador o Enter fecha a gravacao local, sem abortar o upload."""
    texto, _ = _html()
    trecho = texto.split("function vozEnter()", 1)[1].split("function ", 1)[0]
    assert 'voz.fonte === "browser"' in trecho
    assert "voz.pararGravacao()" in trecho
    assert 'vozEstado("transcrevendo")' in trecho


def test_enter_do_modo_voz_nao_silencia():
    """Enter envia; quem cala o microfone e o `M` (bug: Enter ficava mudo)."""
    texto, _ = _html()
    trecho = texto.split("function vozEnter()", 1)[1].split("function ", 1)[0]
    # No estado silenciado o Enter so avisa do atalho, nunca religa nem cala.
    assert "vozAlternarMudo(" not in trecho
    assert 'aviso(' in trecho and "M" in trecho
    # O unico caminho que pausa o microfone e o atalho/erro, nao o Enter.
    assert "voz.manual = true" in trecho


def test_paleta_de_comandos_no_html():
    texto, _ = _html()
    assert 'id="comandos"' in texto
    assert " filtrarComandos()" in texto.replace("\n", " ") or "filtrarComandos()" in texto
    # Enter/Tab/setas tratados antes do envio.
    for tecla in ("ArrowDown", "ArrowUp", "Tab", "Escape"):
        assert f'"{tecla}"' in texto


def test_tema_claro_escuro_no_html():
    """Tema do web: botao no header, `data-tema` no <html> e paleta unica.

    O contrato que nao pode voltar atras: o CSS NAO fala de
    `prefers-color-scheme` (quem resolve "sistema" e o JS, senao a paleta tem
    duas fontes de verdade e o botao mente sobre o que esta na tela), a
    resolucao acontece no <head> antes da primeira pintura (senao a tela pisca
    escura ao abrir em tema claro) e o mesmo botao existe nas duas telas.
    """
    texto, coletor = _html()
    assert "btn-tema" in coletor.ids
    assert 'tema.montar($("#btn-tema"))' in texto
    # Resolucao antes do <style>: nada de flash de tema errado.
    cabeca = texto.split("<style>", 1)[0]
    assert 'localStorage.getItem("gaia.tema")' in cabeca
    assert "document.documentElement.dataset.tema" in cabeca
    # A paleta e declarada por atributo, nao por media query.
    assert ':root[data-tema="claro"]' in texto
    assert "@media (prefers-color-scheme" not in texto
    # Catppuccin: Mocha no escuro, Latte no claro.
    for cor in ("#11111b", "#1e1e2e", "#cdd6f4", "#e6e9ef", "#eff1f5", "#4c4f69"):
        assert cor in texto
    # O botao primario le o token de "texto sobre o accent" (o Latte precisa de
    # branco; o Mocha, do crust) em vez do verde fixo do escuro.
    assert "color: var(--on-accent)" in texto


def test_tema_no_painel_e_no_shared():
    """O Control Center tem o mesmo botao e o modulo `tema` e compartilhado."""
    from gaia.serve import web

    painel = (RAIZ / "gaia" / "serve" / "static" / "control.html").read_text(encoding="utf-8")
    assert 'id="btn-tema"' in painel
    assert 'localStorage.getItem("gaia.tema")' in painel
    css = (RAIZ / "gaia" / "serve" / "static" / "control.css").read_text(encoding="utf-8")
    assert ':root[data-tema="claro"]' in css
    assert "@media (prefers-color-scheme" not in css
    js_painel = (RAIZ / "gaia" / "serve" / "static" / "control.js").read_text(encoding="utf-8")
    assert 'tema.montar($("#btn-tema"))' in js_painel

    bruto, _ = web.static_asset("shared.js")  # type: ignore[misc]
    js = bruto.decode("utf-8")
    assert "const tema" in js and "tema," in js
    assert 'const CHAVE_TEMA = "gaia.tema"' in js
    # Tres escolhas, com "sistema" seguindo o aparelho ao vivo.
    assert 'const TEMAS = ["sistema", "escuro", "claro"]' in js
    assert "addEventListener(\"change\", aoTrocar)" in js


def test_icone_da_gaia_nas_duas_telas():
    """O mesmo PNG e o favicon do chat e do painel (sem arquivo duplicado)."""
    from gaia.serve import web

    assert web.ICON_PATH.is_file()
    assert "icon.png" in web.STATIC_ASSETS
    assert "icon.png" in web.CONTROL_ASSETS
    conteudo, tipo = web.icon()
    assert tipo == "image/png"
    assert conteudo.startswith(b"\x89PNG")
    for pagina in (INDEX, RAIZ / "gaia" / "serve" / "static" / "control.html"):
        texto = pagina.read_text(encoding="utf-8")
        assert 'rel="icon"' in texto
        assert "/static/icon.png" in texto


# -------------------------------------------------------------- catalogo ---


class FakeTarget:
    """Target minimo para as rotas novas (catalogo, idiomas e chines)."""

    def __init__(self) -> None:
        self.chinese = False
        self.language = "zh"

    def health(self) -> dict[str, Any]:
        return {"model": "deepseek/flash", "session": "s1", "chinese": self.chinese}

    def sessions(self) -> list[dict[str, Any]]:
        return []

    def models(self) -> list[dict[str, Any]]:
        return []

    def switch_model(self, slug: str) -> list[str]:
        return []

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    def turn(self, text: str) -> str:
        return "r_1"

    def decide(self, request_id: str, decision: str) -> None:
        return None

    def abort(self) -> None:
        return None

    def set_chinese(self, enabled: bool) -> bool:
        self.chinese = bool(enabled)
        return self.chinese

    def set_language(self, key: str, enabled: bool | None = None) -> bool:
        chave = str(key or "").lower()
        if chave not in {"zh", "en", "fr"}:
            raise ValueError(f"idioma desconhecido: {key}")
        self.language = chave
        return self.set_chinese(True if enabled is None else bool(enabled))

    def echo_language(self) -> str:
        return self.language


def _pedir(
    base: str, metodo: str, caminho: str, *, corpo: dict[str, Any] | None = None
) -> tuple[int, Any]:
    host, porta = base.removeprefix("http://").split(":")
    conexao = http.client.HTTPConnection(host, int(porta), timeout=10)
    dados = json.dumps(corpo).encode() if corpo is not None else None
    cabecalhos = {"Content-Type": "application/json"} if dados else {}
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    bruto = resposta.read()
    conexao.close()
    try:
        return resposta.status, json.loads(bruto)
    except ValueError:
        return resposta.status, bruto


@pytest.fixture()
def servidor():
    alvo = FakeTarget()
    srv = ServeServer(alvo, RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        yield alvo, base
    finally:
        srv.shutdown()


def test_commands_serve_o_catalogo(servidor):
    _, base = servidor
    status, corpo = _pedir(base, "GET", "/commands")
    assert status == 200
    comandos = corpo["commands"]
    assert [item["usage"] for item in comandos] == [
        item["usage"] for item in command_catalog.as_dicts()
    ]
    ajuda = next(item for item in comandos if item["usage"] == "/ajuda")
    assert ajuda["names"] == ["/ajuda", "/help"]
    assert ajuda["argument"] is False


def test_chinese_liga_e_desliga_no_motor(servidor):
    alvo, base = servidor
    status, corpo = _pedir(base, "POST", "/chinese", corpo={"enabled": True})
    assert status == 200
    assert corpo == {"ok": True, "chinese": True}
    assert alvo.chinese is True

    status, corpo = _pedir(base, "GET", "/engine")
    assert status == 200
    assert corpo["chinese"] is True

    status, corpo = _pedir(base, "POST", "/chinese", corpo={"enabled": False})
    assert status == 200 and corpo["chinese"] is False
    assert alvo.chinese is False


def test_languages_lista_os_idiomas_de_eco(servidor):
    _alvo, base = servidor
    status, corpo = _pedir(base, "GET", "/languages")
    assert status == 200
    idiomas = corpo["languages"]
    assert [item["key"] for item in idiomas] == ["zh", "en", "fr"]
    assert [item["label"] for item in idiomas] == ["中文", "English", "Français"]
    assert idiomas[0]["current"] is True
    assert idiomas[0]["enabled"] is False
    # O idioma escolhido vem junto: a tela nao precisa cruzar `current`.
    assert corpo["language"] == "zh"


def test_engine_traz_a_lista_de_idiomas(servidor):
    """O botao "idiomas" so aparece se `/engine` disser que ha idioma.

    Antes ele era preenchido por `pintarMotor` sem `languages` e ficava
    escondido ate alguem abrir o dialog — que era o proprio botao.
    """
    _, base = servidor
    status, corpo = _pedir(base, "GET", "/engine")
    assert status == 200
    assert [item["key"] for item in corpo["languages"]] == ["zh", "en", "fr"]
    assert corpo["language"] == "zh"
    assert corpo["chinese"] is False


def test_language_escolhe_o_idioma_e_liga_o_eco(servidor):
    """Dialog "idiomas": `{key: "en"}` escolhe e liga; `enabled: false` desliga."""
    alvo, base = servidor
    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "zh"})
    assert status == 200
    assert corpo["key"] == "zh" and corpo["chinese"] is True
    assert alvo.chinese is True
    assert corpo["languages"][0]["enabled"] is True

    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "en"})
    assert status == 200 and corpo["language"] == "en"
    assert alvo.language == "en"
    current = [item for item in corpo["languages"] if item["current"]]
    assert [item["key"] for item in current] == ["en"]

    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "en", "enabled": False})
    assert status == 200 and corpo["chinese"] is False
    assert alvo.chinese is False
    assert corpo["language"] == "en"


def test_language_frances_troca_o_idioma_do_eco(servidor):
    """O dialog tambem oferece frances (nao e mais so o chines)."""
    alvo, base = servidor
    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "fr"})
    assert status == 200
    assert corpo["language"] == "fr" and corpo["chinese"] is True
    assert alvo.language == "fr"


def test_language_desconhecido_e_400(servidor):
    _, base = servidor
    status, corpo = _pedir(base, "POST", "/language", corpo={"key": "de"})
    assert status == 400
    assert corpo["error"] == "bad_language"

    status, corpo = _pedir(base, "POST", "/language", corpo={})
    assert status == 400
    assert corpo["error"] == "empty_language"


def test_language_aceita_motor_antigo_sem_set_language(servidor):
    """Target antigo (so `set_chinese`) continua atendendo o dialog."""

    class Antigo(FakeTarget):
        set_language = None  # type: ignore[assignment]

    srv = ServeServer(Antigo(), RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        status, corpo = _pedir(base, "POST", "/language", corpo={"key": "zh"})
        assert status == 200 and corpo["chinese"] is True
        # Motor antigo so conhece o chines: a lista cai nos tres do transporte,
        # mas trocar para ingles/frances continua 400.
        status, corpo = _pedir(base, "POST", "/language", corpo={"key": "fr"})
        assert status == 400
    finally:
        srv.shutdown()


def test_chinese_sem_suporte_no_motor_e_400():
    class SemChines(FakeTarget):
        set_chinese = None  # type: ignore[assignment]

    srv = ServeServer(SemChines(), RemoteUI(), host="127.0.0.1", port=0)  # type: ignore[arg-type]
    base = srv.start()
    try:
        status, corpo = _pedir(base, "POST", "/chinese", corpo={"enabled": True})
        assert status == 400
        assert corpo["error"] == "chinese_failed"
    finally:
        srv.shutdown()


# ------------------------------------------------------- motor de verdade ---


class _Cfg:
    def __init__(self) -> None:
        self.interface = type(
            "I", (), {"chinese_echo": False, "echo_language": "zh"}
        )()
        self.agent = type("A", (), {"model_slug": lambda self: "deepseek/flash"})()


class _Orch:
    """Orchestrator falso so com o que o eco de idiomas precisa."""

    def __init__(self, traducao: str = "你好 | nǐ hǎo") -> None:
        self.cfg = _Cfg()
        self.session_id = "s1"
        self.traducao = traducao
        self.chamadas: list[str] = []
        self.pedidos: list[tuple[str, str]] = []
        self.mode_name = "assistente"

    async def translate_zh(self, text: str) -> str:
        self.chamadas.append(text)
        return self.traducao

    async def translate_echo(self, text: str, lang: str | None = None) -> str:
        self.pedidos.append((text, lang or ""))
        return f"[{lang or 'zh'}] {text}"

    async def answer_once(self, text: str, **_: Any) -> str:
        return f"eco: {text}"


def _loop_em_thread() -> tuple[asyncio.AbstractEventLoop, Any]:
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _drenar(ui: RemoteUI, limite: float = 3.0) -> list[dict[str, Any]]:
    """Todos os eventos emitidos em `limite` segundos (nada se perde)."""
    import time

    achados: list[dict[str, Any]] = []
    fim = time.time() + limite
    while time.time() < fim:
        while not ui.events.empty():
            achados.append(ui.events.get_nowait())
    return achados


def _de(eventos: list[dict[str, Any]], tipo: str) -> list[dict[str, Any]]:
    return [evento for evento in eventos if evento["type"] == tipo]


def test_eco_zh_so_com_o_interruptor_ligado():
    loop, thread = _loop_em_thread()
    try:
        orch = _Orch()
        ui = RemoteUI()
        alvo = OrchestratorTarget(orch, ui)
        alvo.attach_loop(loop)

        alvo.turn("bom dia")
        antes = _drenar(ui, limite=1.5)
        assert _de(antes, "run_finished")
        assert _de(antes, "zh") == []
        assert orch.chamadas == []

        alvo.set_chinese(True)
        alvo.turn("bom dia")
        depois = _drenar(ui, limite=2.0)
        ecos = _de(depois, "zh")
        assert [eco["role"] for eco in ecos] == ["user", "gaia"]
        assert ecos[0]["traducao"] == "[zh] bom dia"
        # `lang` rotula a linha do eco na tela.
        assert {eco["lang"] for eco in ecos} == {"zh"}
        # A pergunta e a resposta passaram pelo tradutor do idioma escolhido.
        assert orch.pedidos == [("bom dia", "zh"), ("eco: bom dia", "zh")]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_eco_zh_nao_derruba_o_turno_quando_a_traducao_falha():
    loop, thread = _loop_em_thread()

    class Quebrado(_Orch):
        async def translate_echo(self, text: str, lang: str | None = None) -> str:
            raise RuntimeError("sem rede")

    try:
        ui = RemoteUI()
        alvo = OrchestratorTarget(Quebrado(), ui)
        alvo.attach_loop(loop)
        alvo.set_chinese(True)
        alvo.turn("oi")
        eventos = _drenar(ui, limite=2.0)
        fim = _de(eventos, "run_finished")
        assert fim and fim[0]["ok"] is True
        assert _de(eventos, "zh") == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_languages_e_set_language_no_motor_de_verdade():
    orch = _Orch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    idiomas = alvo.languages()
    assert [item["key"] for item in idiomas] == ["zh", "en", "fr"]
    assert idiomas[0]["enabled"] is False
    assert alvo.echo_language() == "zh"
    assert alvo.set_language("zh") is True
    assert orch.cfg.interface.chinese_echo is True
    assert alvo.languages()[0]["enabled"] is True
    assert alvo.set_language("en") is True
    assert orch.cfg.interface.echo_language == "en"
    assert alvo.echo_language() == "en"
    assert [item["key"] for item in alvo.languages() if item["current"]] == ["en"]
    assert alvo.set_language("en", False) is False
    with pytest.raises(ValueError):
        alvo.set_language("de")


def test_motor_antigo_com_echo_language_invalido_cai_no_chines():
    orch = _Orch()
    orch.cfg.interface.echo_language = "klingon"
    alvo = OrchestratorTarget(orch, RemoteUI())
    assert alvo.echo_language() == "zh"


def test_eco_em_ingles_usa_translate_echo():
    """O idioma escolhido chega ao tradutor e volta no evento, nao fixo em zh."""
    loop, thread = _loop_em_thread()
    try:
        orch = _Orch()
        ui = RemoteUI()
        alvo = OrchestratorTarget(orch, ui)
        alvo.attach_loop(loop)
        alvo.set_language("en")

        alvo.turn("bom dia")
        ecos = _de(_drenar(ui, limite=2.0), "zh")
        assert [eco["lang"] for eco in ecos] == ["en", "en"]
        assert [eco["traducao"] for eco in ecos] == ["[en] bom dia", "[en] eco: bom dia"]
        # `zh` continua sendo enviado: telas antigas nao quebram.
        assert all(eco["zh"] for eco in ecos)
        assert orch.pedidos == [("bom dia", "en"), ("eco: bom dia", "en")]
        assert orch.chamadas == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_eco_em_frances_com_motor_antigo_nao_traduz():
    """Motor que so tem `translate_zh` nao inventa ingles/frances."""

    class SoChines(_Orch):
        translate_echo = None  # type: ignore[assignment]

    loop, thread = _loop_em_thread()
    try:
        orch = SoChines()
        ui = RemoteUI()
        alvo = OrchestratorTarget(orch, ui)
        alvo.attach_loop(loop)
        alvo.set_language("fr")
        alvo.turn("bom dia")
        eventos = _drenar(ui, limite=1.5)
        assert _de(eventos, "zh") == []
        assert orch.chamadas == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_set_chinese_le_a_configuracao_do_motor():
    orch = _Orch()
    alvo = OrchestratorTarget(orch, RemoteUI())
    assert alvo.chinese() is False
    assert alvo.set_chinese(True) is True
    assert orch.cfg.interface.chinese_echo is True
    assert alvo.chinese() is True
    assert alvo.set_chinese(False) is False
    assert alvo.chinese() is False


def test_painel_control_lista_as_rotinas():
    from gaia.serve import web

    bruto, _ = web.control_asset("control.js")  # type: ignore[misc]
    js = bruto.decode("utf-8")
    assert "Rotinas contínuas" in js
    assert "dados.routines" in js
    assert "trigger_label" in js


# ------------------------------------------------------- bots (fase 2) ---


def test_rail_de_bots_no_html():
    """Rail à esquerda: nome completo, badge e `#b/<bot>` como hash.

    O contrato que a fase 2 fecha: cinco bots montados a partir de
    `GET /bots` (nunca lista fixa), o clique navega por `#b/<id>` (F5 e o
    voltar do navegador continuam) e a thread troca por `POST /bot/select`.
    """
    texto, coletor = _html()
    assert 'id="rail-bots"' in texto
    assert "rail-bots" in coletor.ids
    # O rail e do chat (nao e uma tela propria): fica dentro de #view-chat.
    assert 'id="view-chat"' in texto
    assert texto.index('id="rail-bots"') > texto.index('id="view-chat"')
    # Nome completo visivel; id so no tooltip/data-bot.
    assert 'class: "nome-bot"' in texto and "bot.name || bot.id" in texto
    assert '"data-bot": bot.id' in texto
    # Montado a partir da rota (a tela nunca fixa a lista).
    assert 'api("/bots")' in texto
    assert "function pintarRail(bots)" in texto
    # Troca pelo hash e pela rota nova (nao por /mode).
    assert "/^#b\\/[a-z0-9_-]+$/" in texto
    assert "function botDoHash()" in texto
    assert 'api("/bot/select"' in texto
    assert "function trocarBot(chave)" in texto
    # O clique no rail NAVEGA (o hash e a fonte de verdade, nao um estado solto).
    assert 'location.hash = "#b/" + bot.id' in texto
    assert 'id="badge-"' not in texto  # o badge e por item, sem id fixo
    assert 'class: "badge"' in texto
    assert 'class: "rodando"' in texto
    # Hash trocado nao recarrega a pagina: hashchange pede a troca.
    assert 'const pedido = botDoHash();' in texto
    # O boot espera o motor antes de comparar o hash com o bot ativo.
    assert "await carregarBots();" in texto
    assert "pintarTelaAtual();\n  // `#b/<bot>` pede a thread daquele bot" in texto


def test_feed_de_atividade_no_aside():
    """O card do feed existe, e recolhido por padrao, e nao entra na conversa."""
    texto, coletor = _html()
    assert 'id="bot-atividade"' in texto
    assert "bot-atividade" in coletor.ids
    assert 'api("/bots/activity?limit=30")' in texto
    assert 'api("/bots/seen"' in texto
    assert "function carregarAtividade()" in texto
    assert "function pintarAtividade()" in texto
    # Evento de bot alheio nao vira bolha: o `case` so atualiza feed/rail.
    assert 'case "bot_activity":' in texto
    trecho = texto[texto.index('case "bot_activity":'):]
    trecho = trecho[: trecho.index("break;")]
    assert "carregarAtividade();" in trecho and "carregarBots();" in trecho
    assert "bolha(" not in trecho
    # Estado do recolhido sobrevive ao redesenho.
    assert "atividadeAberta: false" in texto


def test_rail_e_feed_nao_quebram_sem_motor_novo():
    """`/bots` e `/bots/activity` falhando = tela segue (sem rail, sem card)."""
    texto, _ = _html()
    trecho = texto[texto.index("async function carregarBots()"):]
    trecho = trecho[: trecho.index("async function trocarBot")]
    assert "pintarRail([])" in trecho
    assert "carregarAtividade();" in trecho
    trecho = texto[texto.index("async function carregarAtividade()"):]
    trecho = trecho[: trecho.index("function pintarAtividade")]
    assert "estado.atividade = null" in trecho
