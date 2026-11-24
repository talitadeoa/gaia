"""Extrai itens do conteudo HTML de uma pagina da Agenda.

A Agenda guarda o texto da pagina como HTML do ``contentEditable`` (``<div>``
por linha, ``<ul><li>``, ``<br>``). Nao existe editor de blocos: o que vale e
o texto visivel. Este modulo transforma o HTML em linhas, sem dependencia nova
(``html.parser`` da stdlib):

* tags de bloco viram quebra de linha (``div``, ``p``, ``li``, ``br``, titulos,
  ``tr``);
* ``<script>``/``<style>`` sao ignorados;
* marcador de lista/conclusao no inicio da linha e removido;
* linha vazia nao vira item.

Conclusao: a linha comecando com ``[x]``, ``[X]``, ``✅`` ou ``☑`` (ou o texto
inteiro riscado com ``~~...~~``) conta como feita. Isso e o que permite marcar
um item como concluido direto na pagina e a limpeza de 7 dias agir.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags que fecham a linha atual quando aparecem/terminam.
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "template"})
# Marcador de lista/checkbox no comeco da linha: "-", "*", "•", "1.", "[ ]".
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
_DONE_MARK = re.compile(r"^\s*(?:\[[xX]\]|✅|☑|✔)\s*")
_OPEN_MARK = re.compile(r"^\s*\[ \]\s*")
_STRIKE = re.compile(r"^\s*~~.+~~\s*$")


class _Linhas(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.linhas: list[str] = []
        self._atual: list[str] = []
        self._pular = 0

    def _fechar(self) -> None:
        texto = "".join(self._atual)
        self._atual = []
        texto = re.sub(r"[ \t\r\f\v]+", " ", texto).strip()
        if texto:
            self.linhas.append(texto)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._pular += 1
            return
        if tag in _BLOCK_TAGS:
            self._fechar()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._pular = max(0, self._pular - 1)
            return
        if tag in _BLOCK_TAGS:
            self._fechar()

    def handle_data(self, data: str) -> None:
        if not self._pular and data:
            self._atual.append(data)

    def close(self) -> None:
        super().close()
        self._fechar()


def lines_from_html(html: str) -> list[str]:
    """Linhas visiveis do HTML (na ordem, sem vazias). Texto puro tambem vale."""
    bruto = str(html or "")
    if not bruto.strip():
        return []
    if "<" not in bruto:  # pagina antiga em texto puro
        return [linha.strip() for linha in bruto.splitlines() if linha.strip()]
    parser = _Linhas()
    try:
        parser.feed(bruto)
        parser.close()
    except Exception:  # HTML quebrado nao pode derrubar a sincronizacao
        return [linha.strip() for linha in re.sub(r"<[^>]+>", "\n", bruto).splitlines()
                if linha.strip()]
    return parser.linhas


def normalize_item(raw: str) -> tuple[str, bool]:
    """``(texto, concluido)`` de uma linha. Remove marcador de lista/checkbox."""
    texto = " ".join(str(raw or "").split())
    if not texto:
        return "", False
    concluido = bool(_DONE_MARK.match(texto) or _STRIKE.match(texto))
    texto = _DONE_MARK.sub("", texto)
    texto = _OPEN_MARK.sub("", texto)
    texto = _BULLET.sub("", texto)
    texto = texto.strip()
    if texto.startswith("~~") and texto.endswith("~~") and len(texto) > 4:
        texto = texto[2:-2].strip()
    return texto, concluido


def items_from_content(html: str) -> list[tuple[str, bool]]:
    """Itens ``(texto, concluido)`` de uma pagina, na ordem, sem vazios."""
    itens: list[tuple[str, bool]] = []
    for linha in lines_from_html(html):
        texto, concluido = normalize_item(linha)
        if texto:
            itens.append((texto, concluido))
    return itens


__all__ = ["items_from_content", "lines_from_html", "normalize_item"]
