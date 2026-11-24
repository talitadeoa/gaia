# Web Redesign Liquid Glass + Novo Bot + Automação por Voz — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesenhar a web do `gaia serve` com estilo liquid glass, adicionar botão para criar novo bot e permitir criar automação por texto/voz na tela de automações.

**Architecture:** Manter HTML/JS existentes, reescrever o CSS como um design system de glassmorphism e adicionar elementos de UI novos (botão + modal + input de voz). Backend será ajustado nas rotas necessárias para criação de bot e automação por voz.

**Tech Stack:** HTML5, CSS3 (variables, backdrop-filter, grid, flexbox), Vanilla JS, SSE, Web Audio API (STT já existente), Python backend FastAPI-like (server.py).

---

### Task 1: Criar mock visual estático do redesign

**Files:**
- Create: `mock/web-mock.html` (mock visual local, não parte do produto)
- Reference: `gaia/serve/static/index.html`

**Step 1: Extrair estrutura HTML base**
Copiar `<header>`, `<main>` (rail/chat/aside), `<footer>` e telas próprias para o mock.

**Step 2: Aplicar CSS liquid glass no mock**
- Variáveis CSS glass.
- Header glass com blur.
- Rail de bots com avatares glass.
- Chat com bolhas glass.
- Aside cards glass.
- Composer glass.
- Modais glass.

**Step 3: Visualizar e iterar**
Abrir no navegador (via `python -m http.server`) e ajustar.

**Run:**
```bash
cd mock && python -m http.server 8080
```

**Expected:** Página estática bonita com efeito liquid glass.

**Step 4: Commit do mock**
```bash
git add mock/web-mock.html
git commit -m "docs(mock): liquid glass redesign preview"
```

---

### Task 2: Reescrever CSS de `index.html` com design system liquid glass

**Files:**
- Modify: `gaia/serve/static/index.html` (todo o bloco `<style>`)
- Test: `tests/test_serve_web.py` (verificar elementos existentes)

**Step 1: Definir variáveis CSS glass**
Incluir no `:root`:
- `--glass-bg`, `--glass-border`, `--glass-shadow`, `--glass-highlight`.
- Cores semitransparentes e opacidades.

**Step 2: Aplicar estilos por componente**
- `header` → glass bar, grupos com gap e padding.
- `#rail-bots` → largura maior, avatares glass, hover com brilho.
- `#chat` → padding maior, bolhas glass.
- `aside` → cards glass espaçados.
- `footer` → glass composer.
- `.auto-card`, `.backlog-card`, `.atividade`, `.cartao-motor` → glass.
- `dialog` → glass modal.

**Step 3: Preservar classes e IDs críticos**
Garantir que o JS continua encontrando elementos.

**Step 4: Testes visuais**
Rodar `gaia serve` e verificar se página carrega sem erros.

**Run:**
```bash
cd /Users/talita/gaia
.venv/bin/gaia serve &
```

**Expected:** Página carrega, eventos SSE funcionam, layout glass aplicado.

**Step 5: Commit**
```bash
git add gaia/serve/static/index.html
git commit -m "feat(serve): redesign web com estilo liquid glass"
```

---

### Task 3: Adicionar botão "+ bot" no rail de bots

**Files:**
- Modify: `gaia/serve/static/index.html` (HTML + JS `pintarRail`)
- Create: `gaia/serve/static/novo-bot.html` (modal para criar bot)
- Modify: `gaia/serve/server.py` (nova rota `POST /bots`)
- Modify: `gaia/bots/registry.py` (função para adicionar bot dinamicamente ou validar)
- Test: `tests/test_bots.py`, `tests/test_serve_web.py`

**Step 1: Adicionar botão no rail**
Após listar bots, adicionar botão "+ bot" com classe glass.

**Step 2: Criar modal/diálogo para novo bot**
Campos: nome, id, role/diretriz, modelo, ferramentas permitidas.

**Step 3: Implementar `POST /bots` no backend**
- Validar token.
- Criar/validar bot via registry.
- Persistir na sessão/configuração (discutir persistência real — pode ser em memória + TOML).

**Step 4: Conectar frontend com backend**
Ao salvar, chamar `POST /bots`, recarregar rail com `carregarBots()`.

**Run:**
```bash
.venv/bin/pytest tests/test_bots.py tests/test_serve_web.py -v
```

**Expected:** Botão aparece, cria bot, rail atualiza.

**Step 5: Commit**
```bash
git add gaia/serve/static/index.html gaia/serve/server.py gaia/bots/registry.py tests/
git commit -m "feat(serve): botão para criar novo bot"
```

---

### Task 4: Adicionar criação de automação por texto/voz na tela de automações

**Files:**
- Modify: `gaia/serve/static/index.html` (HTML + JS)
- Modify: `gaia/serve/server.py` (nova rota `POST /automation/create`)
- Modify: `gaia/controller/automation_builder.py` (expor função de criação)
- Test: `tests/test_automation_builder.py`, `tests/test_serve_web.py`

**Step 1: Adicionar botão flutuante "+ automação" na tela de automações**
Botão glass com microfone no `#view-automacoes`.

**Step 2: Criar input glass expandido**
- Campo de texto.
- Ícone de microfone para gravar (reusa `vozGravarAteEnviar` / lógica de STT).
- Botão enviar.

**Step 3: Implementar JS de captura**
- Texto: envia direto.
- Voz: reusa funções de STT existentes, transcreve e envia texto.

**Step 4: Implementar `POST /automation/create` no backend**
- Receber texto/frase.
- Chamar `automation_builder.parse(text)`.
- Retornar automação criada ou erros de validação.

**Step 5: Atualizar tela após criação**
Recarregar `carregarTelaAutomacoes()`.

**Run:**
```bash
.venv/bin/pytest tests/test_automation_builder.py tests/test_serve_web.py -v
```

**Expected:** Botão aparece, cria automação via texto e voz, lista atualiza.

**Step 6: Commit**
```bash
git add gaia/serve/static/index.html gaia/serve/server.py gaia/controller/automation_builder.py tests/
git commit -m "feat(serve): criar automação por texto ou voz na tela"
```

---

### Task 5: Ajustar backend para criação de bot e automação

**Files:**
- Modify: `gaia/bots/registry.py`
- Modify: `gaia/controller/automation_builder.py`
- Modify: `gaia/serve/app.py` (métodos do target se necessário)
- Modify: `gaia/serve/server.py`

**Step 1: Expor funções de criação no registry e automation_builder**
Garantir que sejam chamadas de forma limpa pelas rotas HTTP.

**Step 2: Adicionar validação e feedback de erro**
- Nome duplicado.
- ID inválido.
- Frase de automação sem entidades suficientes.

**Step 3: Testes unitários**
- Criar bot com dados válidos/inválidos.
- Criar automação com frases de exemplo.

**Run:**
```bash
.venv/bin/pytest tests/test_bots.py tests/test_automation_builder.py -v
```

**Expected:** Testes passam.

---

### Task 6: Integrar e testar end-to-end

**Files:**
- Modify: `gaia/serve/static/index.html`
- Test: `tests/test_serve_web.py`, manual com `gaia serve`

**Step 1: Subir servidor local**
```bash
.venv/bin/gaia serve
```

**Step 2: Testar manualmente**
- Login.
- Rail de bots.
- Criar novo bot.
- Abrir tela de automações.
- Criar automação por texto.
- Criar automação por voz.
- Verificar SSE e eventos.

**Step 3: Rodar suite de testes**
```bash
.venv/bin/pytest -q
```

**Expected:** Todos os testes passam.

---

### Task 7: Commit final e revisão

**Run:**
```bash
git add -A
git commit -m "feat(serve): redesign liquid glass, novo bot e automação por voz"
```

**Expected:** Branch limpa, commits organizados, documentação atualizada.
