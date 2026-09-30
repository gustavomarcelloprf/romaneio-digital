# Documento de ajustes: Romaneio Pronto

## Contexto para quem vai implementar

- **Stack:** backend Flask (`backend/app.py`) com PostgreSQL (psycopg 3); frontend HTML/CSS/JS puro, sem framework (`frontend/html/index.html`, `frontend/js/script.js`, `frontend/css/style.css`).
- **Navegação:** SPA com uma `<section class="view">` por tela. O menu é montado a partir do array `TELAS` em `script.js`, filtrado pelas permissões que o `/me` devolve.
- **Romaneio:** gerado no backend em PNG (`backend/services/render_png.py`) e PDF (`backend/services/render_pdf.py`). O download sai por `GET /exportar/<pedido_id>`.
- **Branch base:** `feat/ux-simplify`. As imagens anexadas vêm da versão antiga (`main`), e parte do que aparece nelas já mudou nessa branch. Quando for o caso, está indicado em cada item.
- **Público:** operadores de loja de tecidos, na maioria usando o **celular** (iOS/Android). Toda mudança precisa funcionar bem em tela pequena.

---

### A-01: Remover "Salvar como orçamento"

- **Tela:** Vender (`#view-vender`)
- **Imagem:** `print-01-vender.png` (botão "Salvar como orçamento", o texto explicativo ao lado e a seção "Orçamentos" embaixo)
- **Problema:** o conceito de orçamento confunde o operador e não agrega nada. Se o comprador quiser uma proposta, o operador monta um pedido normal e envia. Se o comprador aprovar, a venda está feita.
- **Comportamento esperado:** existe um único fluxo, que é montar o pedido e salvar. Não há status "orçamento", botão de orçamento, lista de orçamentos nem conversão de orçamento em pedido.
- **Status: ✅ já implementado na branch `feat/ux-simplify`** (commit `c8474e7`). Foram removidos o botão, a lista, a rota `POST /pedidos/<id>/converter` e os filtros `status='pedido'` dos relatórios. **Nada a fazer**, basta conferir que não sobrou nenhuma referência.
- **Critério de aceite:** nenhuma ocorrência de "orçamento" na interface. Todo pedido salvo baixa o estoque (quando marcado) e entra nas vendas e na comissão.
- **Prioridade:** alta (concluído)

---

### A-02: Pedidos recém-criados abaixo do formulário de Vender + compartilhamento nativo

- **Tela:** Vender (`#view-vender`) e Pedidos (`#view-pedidos`)
- **Imagem:** `print-01-vender.png` (área abaixo do formulário, onde hoje aparece "Orçamentos")
- **Problema:** depois de salvar, o pedido vai para a aba "Pedidos". O operador precisa trocar de tela para achar o que acabou de criar e mandar ao cliente.
- **Comportamento atual (branch `feat/ux-simplify`):**
  - Após salvar, aparece um aviso único (`#vendaSucesso`, função `mostrarVendaSalva`) com os botões "Enviar no WhatsApp", "Romaneio (imagem)" e "Romaneio (PDF)". Quando o aviso é fechado, o pedido some da tela.
  - O botão WhatsApp (`enviarWhatsApp` → link `wa.me`) manda **só texto**, porque o link do WhatsApp não anexa arquivo.
- **Comportamento esperado:**
  1. Adicionar abaixo do formulário de Vender uma seção **"Pedidos recentes"**, com os pedidos criados ali. Cada linha mostra nº, cliente, tecido, total, fiado/pago e as ações **PNG** e **PDF**.
  2. O pedido recém-salvo aparece no topo dessa lista imediatamente, sem recarregar a página.
  3. **Compartilhar via Web Share API:** os botões PNG/PDF baixam o arquivo como `Blob` e chamam `navigator.share({ files: [file] })` quando `navigator.canShare({ files })` for verdadeiro. Assim abre a folha de compartilhamento nativa do iOS/Android, que já oferece WhatsApp com o arquivo anexado. **Fallback** (desktop ou navegador sem suporte): download normal do arquivo.
  4. **Remover o botão "WhatsApp"** (link `wa.me` em texto) do aviso pós-venda e da tela Pedidos, junto com `montarMensagemWhatsApp` e `enviarWhatsApp`, se não forem usados em outro lugar. Remover também o texto explicativo sobre WhatsApp em `#view-pedidos`.
- **Em aberto (confirmar com o Gustavo):**
  - A aba **"Pedidos" some do menu** ou continua existindo para histórico, edição, exclusão e quitação de fiado?
  - Quais pedidos aparecem em "Pedidos recentes": só os **de hoje**, só os **do operador logado** ou os **últimos N** da loja?
- **Critério de aceite:** no celular, o operador salva a venda, vê o pedido logo abaixo, toca em PNG ou PDF e a folha de compartilhamento do sistema abre com o arquivo pronto para enviar no WhatsApp.
- **Prioridade:** alta

---

### A-03: Refazer o Dashboard

- **Tela:** Dashboard (`#view-dashboard`, função `renderDashboardLoja`, endpoint `GET /api/relatorio/loja`)
- **Imagem:** `print-02-dashboard.png`
- **Problema:** a tela não parece um dashboard. São só cartões de números e listas em texto, sem nenhum gráfico, e os filtros são limitados.
- **Comportamento atual:** filtros fixos de período (Hoje / 7 dias / 30 dias / Este mês / Tudo); cartões de Faturamento, Pedidos, Ticket médio, Comissões a pagar, Saídas e Lucro; listas "Por operador", "Tecidos mais vendidos", "Tecidos menos vendidos" e "Encalhados no período".
- **Comportamento esperado:** uma tela visual, com gráficos, que dê para entender em poucos segundos. Proposta inicial (a validar):
  - **Gráficos:** faturamento por dia no período (linha ou barras); vendas por operador (barras); tecidos mais vendidos em kg e R$ (barras horizontais).
  - **Filtros:** manter os atalhos de período e adicionar **intervalo de datas personalizado**, além de filtro por **operador** e por **tecido**.
  - **Cartões de KPI:** manter, com comparação ao período anterior (ex.: "+12% vs. 30 dias anteriores").
  - Biblioteca de gráficos leve via CDN (ex.: Chart.js), para não precisar de build. Layout responsivo para celular.
- **Em aberto (confirmar com o Gustavo):** quais gráficos e filtros são prioridade e o que da tela atual pode sair.
- **Critério de aceite:** a definir após confirmação.
- **Prioridade:** média
