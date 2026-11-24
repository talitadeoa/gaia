# Design — Redesign completo da web do `gaia serve`

## Contexto
A página `gaia/serve/static/index.html` concentra hoje chat, rail de bots, automações, backlog e painel lateral. O CSS cresceu organicamente e apresenta:
- header amontoado, sem agrupamento semântico;
- rail de bots estreito e sem destaque;
- chat e aside com padding pequeno e sem hierarquia;
- cards de automação/backlog sem separação visual;
- ausência de entrada para criar bot e automação diretamente na tela.

## Objetivo
1. Redesenhar a interface com **estilo liquid glass** (translucidez, blur, bordas suaves, brilho sutil).
2. Padronizar posicionamento, espaçamento e escala tipográfica.
3. Adicionar botão para **criar novo bot**.
4. Adicionar botão na tela de automações para **criar automação por texto ou voz (STT)**.
5. Manter todo o comportamento existente (SSE, permissões, modo voz, rotinas, backlog).

## Abordagem escolhida
**C — Redesign completo**, mantendo a estrutura HTML existente onde possível, mas reorganizando o CSS em um sistema de design coeso e adicionando os novos elementos de UI.

## Sistema visual Liquid Glass
- **Superfícies**: fundo translúcido com `backdrop-filter: blur()` e camadas de vidro.
- **Bordas**: 1px com alpha baixo; cantos arredondados consistentes (8px, 12px, 16px, 999px).
- **Luz**: gradientes sutis, sombras internas (`inset`) e brilho nas bordas para simular reflexo.
- **Cores**: mantém a paleta atual (`--bg`, `--panel`, `--accent`, etc.), mas com variações mais suaves e opacidades.
- **Escala**: 4, 8, 12, 16, 20, 24, 32px.
- **Tipografia**: tamanhos 11, 12, 13, 14, 16, 18, 20px; pesos 400, 500, 600.

## Estrutura de componentes

### 1. Header (topo)
- **Grupo esquerdo**: título/logo Gaia, seletor de instância, status de conexão.
- **Grupo central**: modelo, provedor, modo.
- **Grupo direito**: botões de navegação (automações, backlog, sessões, idiomas, voz), botão de novo bot, botão parar, botão token.
- Cada grupo envolto em "glass pill/bar" com blur e borda sutil.

### 2. Rail de bots (esquerda)
- Largura maior (`64px` ou `80px`), padding confortável.
- Cada bot como cápsula de vidro: avatar circular, nome opcional, indicador de atividade.
- Hover com brilho e leve elevação.
- Botão "+ bot" no final do rail para adicionar novo bot.

### 3. Área de chat
- Padding interno maior (`24px`).
- Bolhas com glass effect: bordas suaves, sombra sutil.
- Mensagens do usuário com acento próprio, mensagens da Gaia com glass padrão.
- Separadores de data/turno mais elegantes.

### 4. Aside (painel direito)
- Cards em glass: motor, automações, atividade, backlog, busca.
- Espaçamento consistente entre cards.
- Badges e tags com glass colorido.

### 5. Composer (footer)
- Glass bar, campo de texto com fundo translúcido.
- Botões com efeito liquid glass.
- Paleta de comandos em glass flutuante com sombra suave.

### 6. Telas próprias (`#automacoes`, `#backlog`)
- Header em glass com título e ações.
- Lista de cards em glass.
- Botão flutuante "+ automação" com microfone para criar por voz/texto.

### 7. Modais/dialogs
- Todos os `<dialog>` com glass, blur e sombra.
- Botões e inputs com o mesmo sistema visual.

## Funcionalidades novas

### A) Criar novo bot
- Botão "+ bot" no final do rail de bots.
- Abre modal com campo para nome, papel/diretriz e seleção de modelo/ferramentas.
- Envia `POST /bots` (ou rota equivalente) para criar o bot.
- Atualiza rail e recarrega `GET /bots`.

### B) Criar automação por texto/voz
- Botão flutuante "+ automação" na tela de automações.
- Ao clicar, expande um glass input com ícone de microfone.
- Campo aceita digitação ou gravação de voz (reusa a lógica existente de STT do modo voz).
- Texto transcrito é enviado para `POST /automation/create` (ou rota equivalente) para o `automation_builder` do motor.
- Feedback visual: escutando, transcrevendo, enviando.

## Fluxo de dados
- Novo bot: UI → `POST /bots` → backend registry → resposta com bot criado → UI recarrega rail.
- Nova automação: UI captura texto/voz → `POST /automation/create` → backend `automation_builder` → resposta com automação criada → UI recarrega lista.

## Restrições e cuidados
- Manter compatibilidade com os eventos SSE existentes.
- Não alterar IDs ou classes críticas usidas pelo JS.
- Preservar acessibilidade (ARIA, diálogos modais, contraste).
- Continuar funcionando sem Control Center (telas somem graciosamente).
- Testar em dark e light mode.

## Critérios de sucesso
- Layout visualmente organizado, com espaçamento consistente e hierarquia clara.
- Efeito liquid glass aplicado em header, rail, cards, composer e modais.
- Botão de novo bot funcional e integrado ao rail.
- Botão de nova automação com input de texto e voz funcional.
- Todos os testes existentes passando.
