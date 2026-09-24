(function () {
    const $ = (s) => document.querySelector(s);

    function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

    // --- Estado da Aplicação ---
    const state = {
        novoPedido: { itens: [] },
        pedidoEmEdicao: { id: null, itens: [] },
        entrada: { linhas: [], encomendaId: null },
        novaEncomenda: { itens: [] },
        loja: sessionStorage.getItem("loja_codigo") || "",
        me: null,
        permissoes: {},
        periodo: "30d",
        view: null
    };

    // --- Navegação: uma tela por vez, sem recarregar a página ---
    // Cada tela declara a capacidade que a habilita; o menu é montado a
    // partir das permissões que o /me devolve, então o que o papel não pode
    // ver simplesmente não existe no menu.
    const TELAS = [
        { id: "vender",        label: "Vender",        perm: "vender" },
        { id: "pedidos",       label: "Pedidos",       perm: "pedidos_ver" },
        { id: "dashboard",     label: "Dashboard",     perm: "relatorio_loja" },
        { id: "minhas-vendas", label: "Minhas Vendas", perm: null },
        { id: "estoque",       label: "Estoque",       perm: "estoque_ver" },
        { id: "clientes",      label: "Clientes",      perm: "clientes_gerir" },
        { id: "equipe",        label: "Equipe",        perm: "usuarios_gerir" },
        // Despesas são dinheiro do dono, como as comissões a pagar.
        { id: "despesas",      label: "Despesas",      perm: "despesas_gerir" },
        { id: "config",        label: "Configurações", perm: "config_editar" },
    ];

    function pode(permissao) {
        return !permissao || state.permissoes[permissao] === true;
    }

    function telasVisiveis() {
        return TELAS.filter(t => pode(t.perm));
    }

    function montarMenu() {
        const nav = $("#mainNav");
        if (!nav) return;
        nav.innerHTML = telasVisiveis().map(t => `
            <button type="button" class="nav-item" data-view="${esc(t.id)}">${esc(t.label)}</button>
        `).join('');
    }

    function fecharMenuMobile() {
        document.body.classList.remove("nav-open");
        $("#menuToggle")?.setAttribute("aria-expanded", "false");
        $("#navOverlay")?.setAttribute("hidden", "");
    }

    // Carregamento sob demanda: ao abrir uma tela, seus dados são
    // reatualizados (o resto continua como está).
    function aoAbrirTela(id) {
        if (id === "vender") loadOrcamentos();
        else if (id === "pedidos") loadOrders();
        else if (id === "clientes") loadClients();
        else if (id === "estoque") loadStock();
        else if (id === "dashboard" || id === "minhas-vendas") loadRelatorios();
        else if (id === "equipe") loadUsuarios();
        else if (id === "despesas") loadDespesas();
    }

    function irPara(id, { atualizarHash = true } = {}) {
        const permitidas = telasVisiveis().map(t => t.id);
        // Tela inexistente ou fora do alcance do papel: cai na inicial.
        if (!permitidas.includes(id)) id = permitidas[0] || "vender";

        document.querySelectorAll(".view").forEach(sec => {
            sec.hidden = sec.id !== `view-${id}`;
        });
        document.querySelectorAll(".nav-item").forEach(btn => {
            btn.classList.toggle("is-active", btn.dataset.view === id);
        });

        state.view = id;
        if (atualizarHash && window.location.hash !== `#${id}`) {
            window.location.hash = id;
        }
        fecharMenuMobile();
        window.scrollTo(0, 0);
        aoAbrirTela(id);
    }

    // --- Funções Utilitárias ---
    function ptbrToNumber(v) {
        if (typeof v === "number") return v;
        if (!v) return null;
        const s = String(v).replace(/[^\d,.-]/g, "").replace(/\./g, "").replace(",", ".");
        const n = parseFloat(s);
        return isNaN(n) ? null : n;
    }

    function numberToPtbr(n) {
        if (typeof n !== 'number') return '';
        return String(n).replace('.', ',');
    }

    function fmtBRL(n) {
        return (n || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
    }

    function fmtDataBR(iso) {
        const d = new Date(iso);
        return isNaN(d) ? String(iso || '') : d.toLocaleString('pt-BR');
    }

    function isoDate(d) {
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }

    // Converte o preset selecionado (Hoje, 7d, 30d, mês, tudo) na querystring
    // ?de=YYYY-MM-DD&ate=YYYY-MM-DD esperada pelos endpoints de relatório.
    function periodoQuery() {
        const hoje = new Date();
        const ate = isoDate(hoje);
        let de;
        switch (state.periodo) {
            case 'hoje': de = ate; break;
            case '7d': { const d = new Date(hoje); d.setDate(d.getDate() - 7); de = isoDate(d); break; }
            case 'mes': de = `${ate.slice(0, 7)}-01`; break;
            case 'tudo': de = '1970-01-01'; break;
            default: { const d = new Date(hoje); d.setDate(d.getDate() - 30); de = isoDate(d); break; }
        }
        return `de=${de}&ate=${ate}`;
    }

    async function jfetch(url, opts = {}) {
        try {
            const res = await fetch(url, {
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                ...opts,
            });
            if (res.status === 401) {
                window.location.href = "/acesso";
                return null; 
            }
            if (res.status === 200 && res.headers.get('content-length') === '0') {
                return { ok: true };
            }
            const json = await res.json();
            if (!res.ok) throw new Error(json.error || `Erro HTTP ${res.status}`);
            return json;
        } catch (err) {
            console.error(`Falha no fetch para ${url}:`, err);
            throw err;
        }
    }

    // --- Funções de Renderização e Lógica de Negócio ---
    function updateTotalPreview(itens, precoEl, descontoEl, totalEl) {
        if (!totalEl || !precoEl || !descontoEl) return;
        const preco = ptbrToNumber(precoEl.value) || 0;
        const desconto = ptbrToNumber(descontoEl.value) || 0;
        const subtotal = itens.reduce((acc, it) => acc + (it.peso || 0), 0) * preco;
        const total = subtotal - desconto;
        totalEl.textContent = fmtBRL(total);
    }

    function renderItens(itens, wrapEl, className) {
        if (!wrapEl) return;
        if (itens.length === 0) {
            wrapEl.innerHTML = '<p class="muted">Nenhum item adicionado.</p>';
        } else {
            wrapEl.innerHTML = `
                <table class="table">
                    <thead><tr><th>Cor</th><th>Peso (kg)</th><th>Ação</th></tr></thead>
                    <tbody>
                        ${itens.map((it, i) => `
                            <tr>
                                <td>${esc(it.cor)}</td>
                                <td>${esc(numberToPtbr(it.peso))}</td>
                                <td><button type="button" class="btn-secondary btn-sm ${className}" data-index="${i}">Remover</button></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>`;
        }
    }
    
    function addItem(itens, corEl, pesoEl, renderFn, updateFn) {
        if(!corEl || !pesoEl) return;
        const cor = corEl.value.trim();
        const peso = ptbrToNumber(pesoEl.value);
        if (cor && peso > 0) {
            itens.push({ cor, peso }); 
            renderFn();
            updateFn();
            corEl.value = "";
            pesoEl.value = "";
            corEl.focus();
        }
    }

    async function loadClients() {
        const clienteSearchInput = $("#clienteSearch");
        if(!clienteSearchInput) return;
        const searchTerm = clienteSearchInput.value;
        const podeFiado = pode("fiado_gerir");
        const promessas = [jfetch(`/clientes?search=${encodeURIComponent(searchTerm)}`)];
        if (podeFiado) promessas.push(jfetch("/api/fiado"));
        const [clientes, fiado] = await Promise.all(promessas);

        if (Array.isArray(clientes)) {
            const optionsHtml = clientes.map(c => `<option value="${c.id}">${esc(c.nome)}</option>`).join("");
            $("#clienteId").innerHTML = optionsHtml;
            $("#editClienteId").innerHTML = optionsHtml;

            const saldoPorCliente = new Map((fiado?.devedores || []).map(d => [d.cliente_id, d.saldo]));

            const clientesWrap = $("#clientesWrap");
            if (clientesWrap) {
                clientesWrap.innerHTML = clientes.length ? `
                <table class="table">
                    <thead><tr><th>Nome</th><th>Telefone</th><th>Email</th>${podeFiado ? '<th>Saldo devedor</th>' : ''}<th>Ação</th></tr></thead>
                    <tbody>${clientes.map(c => `
                    <tr>
                        <td>${esc(c.nome)}</td>
                        <td>${esc(c.telefone || '')}</td>
                        <td>${esc(c.email || '')}</td>
                        ${podeFiado ? `<td>${fmtBRL(saldoPorCliente.get(c.id) || 0)}</td>` : ''}
                        <td class="actions">
                            <button class="btn-secondary btn-sm adjust-cliente-btn" data-id="${c.id}" data-telefone="${esc(c.telefone || '')}" data-email="${esc(c.email || '')}" data-nome="${esc(c.nome)}">Ajustar</button>
                            ${podeFiado ? `<button class="btn-secondary btn-sm pagamento-cliente-btn" data-id="${c.id}" data-nome="${esc(c.nome)}">Registrar pagamento</button>` : ''}
                            <button class="btn-secondary btn-danger btn-sm remove-cliente-btn" data-id="${c.id}">Remover</button>
                        </td>
                    </tr>`).join('')}
                    </tbody>
                </table>` : '<p class="muted">Nenhum cliente encontrado.</p>';
            }

            const fiadoBlock = $("#fiadoBlock");
            if (fiadoBlock) {
                fiadoBlock.toggleAttribute("hidden", !podeFiado);
                if (podeFiado && fiado) renderFiado(fiado);
            }
        }
    }

    // Bloco "A receber": só lê o que /api/fiado já devolve (clientes com
    // saldo > 0 e o total geral), sem endpoint próprio de listagem.
    function renderFiado(fiado) {
        const resumo = $("#fiadoResumo");
        if (resumo) resumo.innerHTML = statCard("Total a receber", fmtBRL(fiado.total));
        const wrap = $("#fiadoWrap");
        if (!wrap) return;
        const lista = fiado.devedores || [];
        wrap.innerHTML = lista.length ? `
            <table class="table">
                <thead><tr><th>Cliente</th><th>Saldo devedor</th></tr></thead>
                <tbody>${lista.map(d => `
                    <tr><td>${esc(d.nome)}</td><td>${fmtBRL(d.saldo)}</td></tr>`).join('')}
                </tbody>
            </table>` : '<p class="muted">Nenhum cliente devedor no momento.</p>';
    }
    
    async function loadMe() {
        const me = await jfetch("/me");
        if (!me) return;
        state.me = me;
        state.permissoes = me.permissoes || {};
        // O /me é a fonte de verdade do nome da loja (o sessionStorage
        // serve só como palpite inicial e nem sempre está preenchido).
        state.loja = me.loja || state.loja;

        const lojaBadge = $("#lojaBadge");
        if (lojaBadge) lojaBadge.textContent = state.loja;
        const userBadge = $("#userBadge");
        if (userBadge) userBadge.textContent = `${me.nome} (${me.papel})`;

        montarMenu();
        // O form de cadastro de tecido só faz sentido para quem pode mutar
        // o estoque (o backend recusa o resto com 403).
        $("#formAddTecido")?.toggleAttribute("hidden", !pode("estoque_mutar"));
        $("#entradaMercadoria")?.toggleAttribute("hidden", !pode("estoque_mutar"));
        $("#encomendasWrap")?.toggleAttribute("hidden", !pode("estoque_mutar"));

        if (pode("usuarios_gerir")) await loadUsuarios();
        if (pode("estoque_mutar")) await loadEncomendas();
    }

    // --- Dashboards (Fase 1) ---
    function statCard(label, value, extraClass = '') {
        return `<div class="stat-card"><span class="stat-label">${esc(label)}</span><span class="stat-value ${esc(extraClass)}">${esc(value)}</span></div>`;
    }

    // Barra proporcional feita só com divs/CSS (sem libs de gráfico).
    function barCell(valor, maximo) {
        const pct = maximo > 0 ? Math.max(2, Math.round((valor / maximo) * 100)) : 0;
        return `<div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>`;
    }

    function tabelaTecidos(lista) {
        if (!lista.length) return '<p class="muted" style="padding:12px;">Nenhuma venda no período.</p>';
        const max = Math.max(...lista.map(t => t.faturamento || 0));
        return `
            <table class="table">
                <thead><tr><th>Tecido</th><th>Faturamento</th><th>Peso (kg)</th><th></th></tr></thead>
                <tbody>${lista.map(t => `
                    <tr>
                        <td>${esc(t.tecido)}</td>
                        <td>${fmtBRL(t.faturamento)}</td>
                        <td>${esc(numberToPtbr(t.peso_total))}</td>
                        <td>${barCell(t.faturamento || 0, max)}</td>
                    </tr>`).join('')}
                </tbody>
            </table>`;
    }

    function renderDashboardLoja(rel) {
        // Comissão é o que a loja tem A PAGAR, e só o dono (admin) vê esse
        // número: o backend simplesmente não manda o campo para o gerente.
        const veComissoes = rel.comissoes_a_pagar !== undefined;
        // Despesas e lucro seguem a mesma regra: só chegam para o admin.
        const veLucro = rel.lucro !== undefined;
        const cards = $("#dashCards");
        if (cards) {
            cards.innerHTML =
                statCard("Faturamento", fmtBRL(rel.faturamento_total)) +
                statCard("Pedidos", rel.num_pedidos) +
                statCard("Ticket médio", fmtBRL(rel.ticket_medio)) +
                (veComissoes ? statCard("Comissões a pagar", fmtBRL(rel.comissoes_a_pagar)) : '') +
                (veLucro ? statCard("Despesas", fmtBRL(rel.despesas_total)) : '') +
                (veLucro ? statCard("Lucro", fmtBRL(rel.lucro), rel.lucro < 0 ? 'is-negativo' : '') : '');
        }
        const opWrap = $("#dashOperadoresWrap");
        if (opWrap) {
            const ops = rel.por_operador || [];
            const max = Math.max(...ops.map(o => o.faturamento || 0), 0);
            opWrap.innerHTML = ops.length ? `
                <table class="table">
                    <thead><tr><th>Operador</th><th>Pedidos</th><th>Faturamento</th>${veComissoes ? '<th>Comissão a pagar</th>' : ''}<th></th></tr></thead>
                    <tbody>${ops.map(o => `
                        <tr>
                            <td>${esc(o.nome)}</td>
                            <td>${esc(o.num_pedidos)}</td>
                            <td>${fmtBRL(o.faturamento)}</td>
                            ${veComissoes ? `<td>${fmtBRL(o.comissao)}</td>` : ''}
                            <td>${barCell(o.faturamento || 0, max)}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>` : '<p class="muted" style="padding:12px;">Nenhum pedido no período.</p>';
        }
        const maisWrap = $("#dashMaisWrap");
        if (maisWrap) maisWrap.innerHTML = tabelaTecidos(rel.tecidos_mais_vendidos || []);
        const menosWrap = $("#dashMenosWrap");
        if (menosWrap) menosWrap.innerHTML = tabelaTecidos(rel.tecidos_menos_vendidos || []);
        const encWrap = $("#dashEncalhadosWrap");
        if (encWrap) {
            const enc = rel.tecidos_encalhados || [];
            encWrap.innerHTML = enc.length
                ? `<div class="tag-list">${enc.map(t => `<span class="pill">${esc(t)}</span>`).join('')}</div>`
                : '<p class="muted">Nenhum tecido encalhado: tudo em estoque vendeu no período.</p>';
        }
    }

    function renderMinhasVendas(rel) {
        // O admin é o dono: não recebe comissão, então a coluna some da
        // visão dele em vez de mostrar uma fileira de zeros.
        const minhaComissao = rel.mostra_comissao !== false;
        const cards = $("#meuCards");
        if (cards) {
            cards.innerHTML =
                statCard("Faturamento", fmtBRL(rel.faturamento)) +
                statCard("Pedidos", rel.num_pedidos) +
                (minhaComissao ? statCard("Comissão", fmtBRL(rel.comissao)) : '');
        }
        const wrap = $("#meusPedidosWrap");
        if (wrap) {
            const pedidos = rel.ultimos_pedidos || [];
            wrap.innerHTML = pedidos.length ? `
                <table class="table">
                    <thead><tr><th>ID</th><th>Data</th><th>Cliente</th><th>Total</th>${minhaComissao ? '<th>Comissão</th>' : ''}</tr></thead>
                    <tbody>${pedidos.map(p => `
                        <tr>
                            <td>${esc(p.id)}</td>
                            <td>${esc(fmtDataBR(p.data_iso))}</td>
                            <td>${esc(p.cliente_nome)}</td>
                            <td>${fmtBRL(p.total)}</td>
                            ${minhaComissao ? `<td>${fmtBRL(p.comissao_valor)}</td>` : ''}
                        </tr>`).join('')}
                    </tbody>
                </table>` : '<p class="muted" style="padding:12px;">Nenhum pedido seu no período.</p>';
        }
    }

    async function loadRelatorios() {
        const qs = periodoQuery();
        try {
            const promessas = [jfetch(`/api/relatorio/meu?${qs}`)];
            if (pode("relatorio_loja")) promessas.push(jfetch(`/api/relatorio/loja?${qs}`));
            const [meu, loja] = await Promise.all(promessas);
            if (meu) renderMinhasVendas(meu);
            if (loja) renderDashboardLoja(loja);
        } catch (err) {
            console.error("Erro ao carregar relatórios:", err);
        }
    }

    // --- Despesas (somente admin) ---
    function fmtDataCurta(iso) {
        // "YYYY-MM-DD" -> "DD/MM/YYYY" sem passar por Date (evita fuso).
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
        return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso || '');
    }

    function renderDespesas(res) {
        const cards = $("#despesasCards");
        if (cards) {
            const cats = res.por_categoria || [];
            cards.innerHTML =
                statCard("Total do período", fmtBRL(res.total)) +
                cats.slice(0, 3).map(c => statCard(c.categoria, fmtBRL(c.total))).join('');
        }
        const wrap = $("#despesasWrap");
        if (!wrap) return;
        const lista = res.despesas || [];
        wrap.innerHTML = lista.length ? `
            <table class="table">
                <thead><tr><th>Data</th><th>Descrição</th><th>Categoria</th><th>Valor</th><th>Ação</th></tr></thead>
                <tbody>${lista.map(d => `
                    <tr>
                        <td>${esc(fmtDataCurta(d.data))}</td>
                        <td>${esc(d.descricao)}</td>
                        <td>${esc(d.categoria)}</td>
                        <td>${fmtBRL(d.valor)}</td>
                        <td><button type="button" class="btn-secondary btn-danger btn-sm remove-despesa-btn" data-id="${esc(d.id)}">Remover</button></td>
                    </tr>`).join('')}
                </tbody>
            </table>` : '<p class="muted" style="padding:12px;">Nenhuma despesa no período.</p>';
    }

    async function loadDespesas() {
        if (!pode("despesas_gerir")) return;
        try {
            const res = await jfetch(`/api/despesas?${periodoQuery()}`);
            if (res) renderDespesas(res);
        } catch (err) {
            const wrap = $("#despesasWrap");
            if (wrap) wrap.innerHTML = `<p class="msg erro">Erro ao carregar despesas: ${esc(err.message)}</p>`;
        }
    }

    function renderResultadoImportacao(res) {
        const msg = $("#despesasImportMsg");
        if (!msg) return;
        const erros = res.erros || [];
        msg.className = `msg ${res.importadas ? 'ok' : 'erro'}`;
        msg.innerHTML =
            `${esc(res.importadas)} despesa(s) importada(s), total ${esc(fmtBRL(res.total))}.` +
            (erros.length ? ` ${esc(erros.length)} linha(s) com erro:
                <ul class="import-erros">${erros.map(e => `<li>Linha ${esc(e.linha)}: ${esc(e.erro)}</li>`).join('')}</ul>` : '');
    }

    async function loadUsuarios() {
        const usuarios = await jfetch("/usuarios");
        const usuariosWrap = $("#usuariosWrap");
        if (!usuariosWrap || !Array.isArray(usuarios)) return;
        usuariosWrap.innerHTML = usuarios.length ? `
            <table class="table">
                <thead><tr><th>Nome</th><th>Login</th><th>Papel</th><th>Comissão (%)</th><th>Status</th><th>Ações</th></tr></thead>
                <tbody>
                    ${usuarios.map(u => `
                        <tr>
                            <td>${esc(u.nome)}</td>
                            <td>${esc(u.login)}</td>
                            <td>${esc(u.papel)}</td>
                            <td>${esc(numberToPtbr(u.taxa_comissao))}</td>
                            <td>${u.ativo ? (u.convite_pendente ? '<span class="muted">Convite pendente</span>' : 'Ativo') : '<span class="muted">Inativo</span>'}</td>
                            <td class="actions">
                                <button class="btn-secondary btn-sm edit-usuario-btn" data-id="${esc(u.id)}" data-nome="${esc(u.nome)}" data-taxa="${esc(u.taxa_comissao)}">Editar taxa</button>
                                <button class="btn-secondary btn-sm toggle-usuario-btn ${u.ativo ? 'btn-danger' : ''}" data-id="${esc(u.id)}" data-ativo="${u.ativo ? 1 : 0}">${u.ativo ? 'Desativar' : 'Ativar'}</button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        ` : '<p class="muted">Nenhum usuário cadastrado.</p>';
    }

    async function loadOrders() {
        const orderSortSelect = $("#orderSort");
        if(!orderSortSelect) return;
        const sortBy = orderSortSelect.value;
        const pedidos = await jfetch(`/pedidos?sort=${sortBy}`);
        const pedidosWrap = $("#pedidosWrap");
        if (Array.isArray(pedidos)) {
            if (pedidosWrap) {
                pedidosWrap.innerHTML = pedidos.length ? `
            <table class="table">
                <thead><tr><th>ID</th><th>Cliente</th><th>Vendedor</th><th>Data</th><th>Total</th><th>Comissão</th><th>Ações</th></tr></thead>
                <tbody>${pedidos.map(p => `
                    <tr>
                        <td>${p.id}</td>
                        <td>${esc(p.cliente_nome)}</td>
                        <td>${esc(p.vendedor_nome || '')}</td>
                        <td>${new Date(p.data_iso).toLocaleString('pt-BR')}</td>
                        <td>${fmtBRL(p.total)}</td>
                        <td>${fmtBRL(p.comissao_valor)}</td>
                        <td class="actions">
                            <button class="btn-secondary btn-sm edit-pedido-btn" data-id="${p.id}">Editar</button>
                            <button class="btn-secondary btn-danger btn-sm remove-pedido-btn" data-id="${p.id}">Remover</button>
                            <button class="btn-secondary btn-sm png-pedido-btn" data-id="${p.id}">PNG</button>
                            <button class="btn-secondary btn-sm pdf-pedido-btn" data-id="${p.id}">PDF</button>
                            <button class="btn-secondary btn-sm whatsapp-pedido-btn" data-id="${p.id}"
                                title="Envia o romaneio em texto pelo WhatsApp. O WhatsApp não anexa arquivo pelo link: use PNG ou PDF para anexar manualmente.">WhatsApp</button>
                        </td>
                    </tr>`).join('')}
                </tbody>
            </table>` : '<p class="muted">Nenhum pedido cadastrado.</p>';
            }
        }
    }

    // Orçamentos: pedidos com status 'orcamento' (sem baixa de estoque).
    async function loadOrcamentos() {
        const wrap = $("#orcamentosWrap");
        if (!wrap || !pode("vender")) return;
        try {
            const orcamentos = await jfetch("/pedidos?status=orcamento");
            wrap.innerHTML = orcamentos.length ? `
            <table class="table">
                <thead><tr><th>ID</th><th>Cliente</th><th>Tecido</th><th>Vendedor</th><th>Data</th><th>Total</th><th>Ações</th></tr></thead>
                <tbody>${orcamentos.map(o => `
                    <tr>
                        <td>${esc(o.id)}</td>
                        <td>${esc(o.cliente_nome)}</td>
                        <td>${esc(o.tecido)}</td>
                        <td>${esc(o.vendedor_nome || '')}</td>
                        <td>${esc(new Date(o.data_iso).toLocaleString('pt-BR'))}</td>
                        <td>${esc(fmtBRL(o.total))}</td>
                        <td class="actions">
                            <button class="btn-primary btn-sm converter-orcamento-btn" data-id="${esc(o.id)}" style="width:auto;">Converter em pedido</button>
                            <button class="btn-secondary btn-danger btn-sm remove-orcamento-btn" data-id="${esc(o.id)}">Remover</button>
                        </td>
                    </tr>`).join('')}
                </tbody>
            </table>` : '<p class="muted">Nenhum orçamento em aberto.</p>';
        } catch (err) {
            wrap.innerHTML = `<p class="msg erro">Erro ao carregar orçamentos: ${esc(err.message)}</p>`;
        }
    }

    function renderEstoque(estoque) {
        const estoqueWrap = $("#estoqueWrap");
        if (!estoqueWrap) return;
        // Operador consulta o estoque, mas não mexe: os controles de
        // mutação não são renderizados para ele (o backend também recusa).
        const podeMutar = pode("estoque_mutar");
        if (estoque.length === 0) {
            estoqueWrap.innerHTML = podeMutar
                ? `<p class="muted">Nenhum tipo de tecido cadastrado. Comece adicionando um acima.</p>`
                : `<p class="muted">Nenhum tipo de tecido cadastrado.</p>`;
            return;
        }

        const colunas = podeMutar ? 5 : 4;
        const emAlerta = estoque.reduce((n, t) => n + t.cores.filter(c => c.abaixo_minimo).length, 0);
        const resumo = emAlerta
            ? `<p class="msg estoque-alerta-resumo"><strong>${esc(emAlerta)} cor(es) no ou abaixo do estoque mínimo.</strong></p>`
            : '';
        estoqueWrap.innerHTML = resumo + estoque.map(tecido => `
            <div class="estoque-card">
                <div class="estoque-header">
                    <h4>${esc(tecido.nome_tecido)}</h4>
                    ${podeMutar ? `<button class="btn-secondary btn-danger btn-sm remove-tecido-btn" data-id="${tecido.id}">Remover Tecido</button>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table">
                        <thead><tr><th>Cor</th><th>Peso (kg)</th><th>Peças</th><th>Mínimo (kg)</th>${podeMutar ? '<th>Ação</th>' : ''}</tr></thead>
                        <tbody>
                            ${tecido.cores.length ? tecido.cores.map(cor => `
                                <tr class="${cor.abaixo_minimo ? 'estoque-baixo' : ''}">
                                    <td>${esc(cor.nome_cor)}${cor.abaixo_minimo ? '<span class="tag-alerta">Repor</span>' : ''}</td>
                                    <td>${esc(numberToPtbr(cor.peso_kg))}</td>
                                    <td>${esc(cor.qtd_pecas)}</td>
                                    <td>${podeMutar ? `
                                        <form class="form-minimo-cor item-row" data-id="${esc(cor.id)}">
                                            <input name="estoque_minimo" inputmode="decimal" value="${esc(cor.estoque_minimo ? numberToPtbr(cor.estoque_minimo) : '')}" placeholder="0 = sem alerta" style="max-width:110px;">
                                            <button type="submit" class="btn-secondary btn-sm">Definir</button>
                                        </form>` : esc(cor.estoque_minimo ? numberToPtbr(cor.estoque_minimo) : '—')}</td>
                                    ${podeMutar ? `<td class="actions">
                                        <button class="btn-secondary btn-sm adjust-cor-btn" data-id="${cor.id}" data-nome="${esc(cor.nome_cor)}" data-peso="${esc(cor.peso_kg)}" data-pecas="${esc(cor.qtd_pecas)}">Ajustar</button>
                                        <button class="btn-secondary btn-danger btn-sm remove-cor-btn" data-id="${cor.id}">X</button>
                                    </td>` : ''}
                                </tr>
                            `).join('') : `<tr><td colspan="${colunas}" class="muted" style="text-align:center;">Nenhuma cor adicionada.</td></tr>`}
                        </tbody>
                    </table>
                </div>
                ${podeMutar ? `
                <form class="form-add-cor" data-tecido-id="${tecido.id}">
                    <div class="item-row">
                        <input name="nome_cor" placeholder="Nova Cor" required>
                        <input name="peso_kg" placeholder="Peso (kg)" inputmode="decimal" required>
                        <input name="qtd_pecas" placeholder="Nº Peças" type="number" value="1">
                        <button type="submit" class="btn-primary btn-sm" style="width:auto;">Adicionar/Somar Cor</button>
                    </div>
                </form>` : ''}
            </div>
        `).join('');
    }

    async function loadStock() {
        try {
            const estoque = await jfetch("/api/estoque");
            renderEstoque(estoque);
            // Sugestões de tecido na entrada: o backend recusa tecido não cadastrado.
            const dl = $("#entradaTecidos");
            if (dl) dl.innerHTML = estoque.map(t => `<option value="${esc(t.nome_tecido)}">`).join('');
            const dlEnc = $("#encTecidos");
            if (dlEnc) dlEnc.innerHTML = estoque.map(t => `<option value="${esc(t.nome_tecido)}">`).join('');
        } catch (err) {
            $("#estoqueWrap").innerHTML = `<p class="msg erro">Erro ao carregar estoque: ${esc(err.message)}</p>`;
        }
    }

    // ===== Encomendas (previsto) =====
    // O previsto nunca credita o estoque; "Receber" carrega o previsto na
    // entrada abaixo (mesmo mecanismo/endpoint de crédito), com o item_id de
    // cada linha preservado para o backend calcular a variação.
    function linhaEncomendaVazia() {
        return { tecido: "", cor: "", peso_previsto: "", rolos_previstos: "" };
    }

    function renderEncomendaItens() {
        const tbody = $("#encItensLinhas");
        if (!tbody) return;
        const itens = state.novaEncomenda.itens;
        tbody.innerHTML = itens.length ? itens.map((it, i) => `
            <tr data-index="${i}">
                <td><input data-campo="tecido" list="encTecidos" value="${esc(it.tecido)}" placeholder="Tecido"></td>
                <td><input data-campo="cor" value="${esc(it.cor)}" placeholder="Cor"></td>
                <td><input data-campo="peso_previsto" inputmode="decimal" value="${esc(it.peso_previsto)}" placeholder="0,000"></td>
                <td><input data-campo="rolos_previstos" inputmode="numeric" value="${esc(it.rolos_previstos)}" placeholder="Opcional"></td>
                <td><button type="button" class="btn-secondary btn-danger btn-sm remove-enc-linha">X</button></td>
            </tr>
        `).join('') : `<tr><td colspan="5" class="muted" style="text-align:center;">Nenhum item. Adicione o que está previsto chegar.</td></tr>`;
    }

    function msgEncomenda(html) {
        const el = $("#encomendaMsg");
        if (el) el.innerHTML = html;
    }

    async function criarEncomenda() {
        const itens = state.novaEncomenda.itens.filter(it => it.tecido || it.cor || it.peso_previsto || it.rolos_previstos);
        if (!itens.length) return msgEncomenda(`<p class="msg erro">Adicione ao menos um item previsto.</p>`);
        try {
            await jfetch("/api/encomendas", {
                method: "POST",
                body: JSON.stringify({
                    fornecedor: $("#encFornecedor")?.value.trim() || "",
                    data_prevista: $("#encDataPrevista")?.value || "",
                    itens: itens.map(it => ({
                        tecido: it.tecido, cor: it.cor,
                        peso_previsto: it.peso_previsto,
                        rolos_previstos: it.rolos_previstos || null,
                    })),
                }),
            });
            state.novaEncomenda.itens = [linhaEncomendaVazia()];
            renderEncomendaItens();
            if ($("#encFornecedor")) $("#encFornecedor").value = "";
            if ($("#encDataPrevista")) $("#encDataPrevista").value = "";
            msgEncomenda(`<p class="msg">Encomenda criada.</p>`);
            loadEncomendas();
        } catch (err) {
            msgEncomenda(`<p class="msg erro">Erro ao criar encomenda: ${esc(err.message)}</p>`);
        }
    }

    function renderEncomendasLista(lista) {
        const tbody = $("#encomendasLista");
        if (!tbody) return;
        tbody.innerHTML = lista.length ? lista.map(e => `
            <tr>
                <td>#${esc(e.id)}</td>
                <td>${esc(e.fornecedor || '-')}</td>
                <td>${esc(e.data_prevista || '-')}</td>
                <td>${esc(e.status)}</td>
                <td class="actions">${e.status === 'aberta'
                    ? `<button type="button" class="btn-secondary btn-sm receber-encomenda-btn" data-id="${e.id}">Receber</button>`
                    : ''}</td>
            </tr>
        `).join('') : `<tr><td colspan="5" class="muted" style="text-align:center;">Nenhuma encomenda.</td></tr>`;
    }

    async function loadEncomendas() {
        try {
            const lista = await jfetch("/api/encomendas");
            if (Array.isArray(lista)) renderEncomendasLista(lista);
        } catch (err) {
            msgEncomenda(`<p class="msg erro">Erro ao carregar encomendas: ${esc(err.message)}</p>`);
        }
    }

    // Carrega o previsto da encomenda na entrada, um item por linha, mantendo
    // o item_id de cada um para o backend calcular a variação ao confirmar.
    async function iniciarRecebimento(encomendaId) {
        try {
            const encomenda = await jfetch(`/api/encomendas/${encomendaId}`);
            state.entrada.encomendaId = encomendaId;
            state.entrada.linhas = encomenda.itens.map(it => ({
                item_id: it.id, tecido: it.tecido, cor: it.cor,
                peso: numberToPtbr(it.peso_previsto), id_rolo: "",
            }));
            renderEntrada();
            if ($("#entradaFornecedor")) $("#entradaFornecedor").value = encomenda.fornecedor || "";
            if ($("#entradaData") && !$("#entradaData").value) {
                const d = new Date();
                $("#entradaData").value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
            }
            const titulo = $("#entradaTitulo");
            if (titulo) titulo.textContent = `Recebendo encomenda #${encomendaId} — ajuste ao peso real`;
            const btnConfirmar = $("#btnConfirmarEntrada");
            if (btnConfirmar) btnConfirmar.textContent = "Confirmar recebimento";
            $("#btnCancelarRecebimento")?.removeAttribute("hidden");
            msgEntrada("");
            $("#entradaMercadoria")?.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (err) {
            msgEncomenda(`<p class="msg erro">Erro ao abrir a encomenda: ${esc(err.message)}</p>`);
        }
    }

    function cancelarRecebimento() {
        state.entrada.encomendaId = null;
        state.entrada.linhas = [linhaEntradaVazia()];
        renderEntrada();
        if ($("#entradaFornecedor")) $("#entradaFornecedor").value = "";
        const titulo = $("#entradaTitulo");
        if (titulo) titulo.textContent = "Entrada de mercadoria";
        const btnConfirmar = $("#btnConfirmarEntrada");
        if (btnConfirmar) btnConfirmar.textContent = "Confirmar entrada";
        $("#btnCancelarRecebimento")?.setAttribute("hidden", "");
        msgEntrada("");
    }

    // ===== Entrada de mercadoria (lote digitado ou planilha) =====
    // Peso da entrada: com vírgula é pt-BR ("1.234,5"); sem vírgula o ponto é
    // decimal ("12.5"), como o backend interpreta.
    function pesoEntrada(v) {
        if (typeof v === "number") return v;
        const s = String(v ?? "").trim();
        if (!s) return null;
        if (s.includes(",")) return ptbrToNumber(s);
        const n = parseFloat(s.replace(/[^\d.-]/g, ""));
        return isNaN(n) ? null : n;
    }

    function linhaEntradaVazia() {
        return { tecido: "", cor: "", peso: "", id_rolo: "" };
    }

    function renderEntrada() {
        const tbody = $("#entradaLinhas");
        if (!tbody) return;
        const linhas = state.entrada.linhas;
        tbody.innerHTML = linhas.length ? linhas.map((l, i) => `
            <tr data-index="${i}">
                <td><input data-campo="tecido" list="entradaTecidos" value="${esc(l.tecido)}" placeholder="Tecido"></td>
                <td><input data-campo="cor" value="${esc(l.cor)}" placeholder="Cor"></td>
                <td><input data-campo="peso" inputmode="decimal" value="${esc(typeof l.peso === "number" ? numberToPtbr(l.peso) : l.peso)}" placeholder="0,000"></td>
                <td><input data-campo="id_rolo" value="${esc(l.id_rolo ?? "")}" placeholder="Opcional"></td>
                <td><button type="button" class="btn-secondary btn-danger btn-sm remove-entrada-linha">X</button></td>
            </tr>
        `).join('') : `<tr><td colspan="5" class="muted" style="text-align:center;">Nenhuma linha. Adicione linhas ou leia uma planilha.</td></tr>`;
        atualizarResumoEntrada();
    }

    function atualizarResumoEntrada() {
        const el = $("#entradaResumo");
        if (!el) return;
        const linhas = state.entrada.linhas;
        const kg = linhas.reduce((acc, l) => acc + (pesoEntrada(l.peso) || 0), 0);
        el.textContent = linhas.length
            ? `${linhas.length} linha(s) · ${numberToPtbr(Math.round(kg * 1000) / 1000)} kg`
            : "";
    }

    function msgEntrada(html) {
        const el = $("#entradaMsg");
        if (el) el.innerHTML = html;
    }

    function listaErrosEntrada(erros) {
        if (!erros || !erros.length) return "";
        return `<ul>${erros.map(e => `<li>Linha ${esc(e.linha)}: ${esc(e.erro)}</li>`).join('')}</ul>`;
    }

    // fetch direto (não jfetch): o upload precisa de multipart e a confirmação
    // devolve a lista de linhas com erro, que o jfetch descartaria.
    async function fetchEntrada(url, opts) {
        const res = await fetch(url, { credentials: "include", ...opts });
        if (res.status === 401) { window.location.href = "/acesso"; return null; }
        const json = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, json };
    }

    async function lerPlanilhaEntrada() {
        const arquivo = $("#entradaArquivo")?.files?.[0];
        if (!arquivo) return msgEntrada(`<p class="msg erro">Escolha um arquivo .csv ou .xlsx.</p>`);
        const fd = new FormData();
        fd.append("arquivo", arquivo);
        msgEntrada(`<p class="muted">Lendo planilha...</p>`);
        try {
            const r = await fetchEntrada("/api/estoque/entradas/importar", { method: "POST", body: fd });
            if (!r) return;
            if (!r.ok) return msgEntrada(`<p class="msg erro">${esc(r.json.error || `Erro HTTP ${r.status}`)}</p>`);
            const { linhas, erros } = r.json;
            // Substitui a tabela pelas linhas lidas: o admin confere e confirma.
            state.entrada.linhas = linhas.map(l => ({ ...l, id_rolo: l.id_rolo || "" }));
            renderEntrada();
            msgEntrada(
                `<p class="msg">${esc(linhas.length)} linha(s) lida(s) de "${esc(arquivo.name)}". Confira e clique em Confirmar entrada.</p>` +
                (erros.length ? `<p class="msg erro">${esc(erros.length)} linha(s) ignorada(s):</p>${listaErrosEntrada(erros)}` : "")
            );
        } catch (err) {
            msgEntrada(`<p class="msg erro">Erro ao ler planilha: ${esc(err.message)}</p>`);
        }
    }

    function listaVariacaoEncomenda(variacao) {
        if (!variacao || !variacao.length) return "";
        return `<ul>${variacao.map(v => {
            const sinal = v.variacao > 0 ? '+' : '';
            return `<li>${esc(v.tecido)} / ${esc(v.cor)}: previsto ${esc(numberToPtbr(v.peso_previsto))} kg, recebido ${esc(numberToPtbr(v.peso_recebido))} kg (${sinal}${esc(numberToPtbr(v.variacao))} kg)</li>`;
        }).join('')}</ul>`;
    }

    async function confirmarEntrada() {
        const linhas = state.entrada.linhas.filter(l => l.tecido || l.cor || l.peso || l.id_rolo);
        if (!linhas.length) return msgEntrada(`<p class="msg erro">Adicione ao menos uma linha.</p>`);
        const kg = linhas.reduce((acc, l) => acc + (pesoEntrada(l.peso) || 0), 0);
        const encomendaId = state.entrada.encomendaId;
        const rotulo = encomendaId ? "recebimento" : "entrada";
        if (!confirm(`Confirmar ${rotulo} de ${linhas.length} linha(s), total ${numberToPtbr(Math.round(kg * 1000) / 1000)} kg?`)) return;
        const btn = $("#btnConfirmarEntrada");
        if (btn) btn.disabled = true;
        try {
            const url = encomendaId ? `/api/encomendas/${encomendaId}/receber` : "/api/estoque/entradas";
            const r = await fetchEntrada(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    fornecedor: $("#entradaFornecedor")?.value.trim() || "",
                    data: $("#entradaData")?.value || "",
                    linhas: linhas.map(l => ({
                        item_id: l.item_id ?? null, tecido: l.tecido, cor: l.cor,
                        peso: l.peso, id_rolo: l.id_rolo || null,
                    })),
                }),
            });
            if (!r) return;
            if (!r.ok) {
                return msgEntrada(`<p class="msg erro">${esc(r.json.error || `Erro HTTP ${r.status}`)}</p>${listaErrosEntrada(r.json.erros)}`);
            }
            const { linhas: n, peso_total, cores_criadas, variacao } = r.json;
            cancelarRecebimento();
            if ($("#entradaArquivo")) $("#entradaArquivo").value = "";
            msgEntrada(
                `<p class="msg">${encomendaId ? 'Recebimento' : 'Entrada'} registrado: ${esc(n)} linha(s), ${esc(numberToPtbr(peso_total))} kg.</p>` +
                (cores_criadas.length ? `<p class="muted">Cores novas: ${cores_criadas.map(c => `${esc(c.tecido)} / ${esc(c.cor)}`).join(', ')}</p>` : "") +
                listaVariacaoEncomenda(variacao)
            );
            loadStock();
            if (encomendaId) loadEncomendas();
        } catch (err) {
            msgEntrada(`<p class="msg erro">Erro ao registrar entrada: ${esc(err.message)}</p>`);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function loadInitialData() {
        $("#lojaBadge").textContent = state.loja;
        try {
            // loadMe primeiro: o menu e os relatórios dependem do papel.
            await loadMe();
            // Com o menu já montado, abre a tela pedida na URL (ou a inicial).
            irPara((window.location.hash || "").replace("#", "") || "vender",
                   { atualizarHash: false });
            await Promise.all([
                loadClients(),
                loadOrders(),
                loadOrcamentos(),
                loadStock(),
                loadRelatorios()
            ]);
        } catch (err) {
            alert(`Erro ao carregar dados: ${err.message}`);
        }
    }
    
    async function openEditModal(pedidoId) {
        const editModal = $("#editModal");
        if(!editModal) return;

        try {
            const { pedido, itens } = await jfetch(`/pedidos/${pedidoId}`);
            state.pedidoEmEdicao.id = pedido.id;
            state.pedidoEmEdicao.itens = itens.map(it => ({ cor: it.cor, peso: it.peso_kg }));
    
            $("#editClienteId").value = pedido.cliente_id;
            $("#editTecido").value = pedido.tecido;
            $("#editPreco").value = numberToPtbr(pedido.preco_unitario);
            $("#editDesconto").value = numberToPtbr(pedido.desconto);
    
            renderItens(state.pedidoEmEdicao.itens, $("#editItensWrap"), 'remove-edit-item');
            updateTotalPreview(state.pedidoEmEdicao.itens, $("#editPreco"), $("#editDesconto"), $("#editTotalPreview"));
            
            editModal.classList.add('is-visible');
        } catch (err) {
            alert(`Erro ao carregar dados do pedido: ${err.message}`);
        }
    }
    
    function closeEditModal() {
        const editModal = $("#editModal");
        if(editModal) {
            editModal.classList.remove('is-visible');
        }
        state.pedidoEmEdicao = { id: null, itens: [] };
    }

    // ===== WhatsApp (link wa.me, sem API) =====
    // Só dígitos, sem o 0 de operadora/DDD; com DDD (10–11 dígitos) ganha o 55.
    // Número curto demais vira "sem telefone": o vendedor escolhe o contato.
    function normalizarTelefoneBR(tel) {
        let d = String(tel || '').replace(/\D/g, '').replace(/^0+/, '');
        if (d.length === 10 || d.length === 11) d = '55' + d;
        return d.length >= 12 ? d : '';
    }

    function montarMensagemWhatsApp(pedido, itens) {
        const peso = n => Number(n || 0).toLocaleString('pt-BR', { maximumFractionDigits: 3 });
        const linhas = [
            `*${pedido.loja_nome || state.loja}*`,
            `Romaneio #${pedido.id}`,
            `Cliente: ${pedido.cliente_nome || '-'}`,
            '',
            'Itens:',
            ...itens.map(it => `- ${it.cor || 'Sem cor'}: ${peso(it.peso_kg)} kg`),
            '',
            `*Total: ${fmtBRL(pedido.total)}*`,
        ];
        if (pedido.loja_pix_chave) linhas.push(`Chave PIX: ${pedido.loja_pix_chave}`);
        return linhas.join('\n');
    }

    async function enviarWhatsApp(pedidoId) {
        // A aba abre já no clique: aberta depois do await o navegador bloqueia o pop-up.
        const aba = window.open('', '_blank');
        try {
            const { pedido, itens } = await jfetch(`/pedidos/${pedidoId}`);
            const texto = encodeURIComponent(montarMensagemWhatsApp(pedido, itens || []));
            const fone = normalizarTelefoneBR(pedido.cliente_telefone);
            const url = `https://wa.me/${fone}?text=${texto}`;
            if (aba) { aba.opener = null; aba.location.href = url; }
            else window.open(url, '_blank', 'noopener');
        } catch (err) {
            aba?.close();
            alert(`Erro ao montar mensagem do WhatsApp: ${err.message}`);
        }
    }

    // ===== Event Listeners =====
    document.addEventListener("DOMContentLoaded", () => {
        const formPix = $("#formPix"), formCliente = $("#formCliente"), formUsuario = $("#formUsuario");
        const formPedido = $("#formPedido"), pedidosWrap = $("#pedidosWrap"), clientesWrap = $("#clientesWrap");
        const usuariosWrap = $("#usuariosWrap"), clienteSearchInput = $("#clienteSearch"), orderSortSelect = $("#orderSort");
        const inpPreco = $("#preco"), inpDesconto = $("#desconto"), totalPreviewEl = $("#totalPreview");
        const btnAddItem = $("#btnAddItem"), itensWrap = $("#itensWrap");
        const logoutBtn = $("#logoutBtn");
        const editModal = $("#editModal");
        const estoqueWrap = $("#estoqueWrap");
        const formAddTecido = $("#formAddTecido");

        // ===== Navegação entre telas (client-side, sem reload) =====
        $("#mainNav")?.addEventListener("click", e => {
            const btn = e.target.closest(".nav-item");
            if (btn) irPara(btn.dataset.view);
        });

        // Voltar/avançar do navegador e links com #hash continuam funcionando.
        window.addEventListener("hashchange", () => {
            const id = (window.location.hash || "").replace("#", "");
            if (id && id !== state.view) irPara(id, { atualizarHash: false });
        });

        // No celular o menu é uma gaveta; o overlay fecha ao tocar fora.
        $("#menuToggle")?.addEventListener("click", () => {
            const aberto = document.body.classList.toggle("nav-open");
            $("#menuToggle").setAttribute("aria-expanded", String(aberto));
            $("#navOverlay")?.toggleAttribute("hidden", !aberto);
        });
        $("#navOverlay")?.addEventListener("click", fecharMenuMobile);
        document.addEventListener("keydown", e => {
            if (e.key === "Escape") fecharMenuMobile();
        });

        // Presets de período dos relatórios: um clique atualiza AMBAS as barras
        // (Dashboard e Minhas Vendas) e recarrega os dois relatórios.
        document.querySelectorAll("[data-relatorio-periodo]").forEach(bar => {
            bar.addEventListener("click", e => {
                const btn = e.target.closest(".periodo-btn");
                if (!btn) return;
                state.periodo = btn.dataset.preset;
                document.querySelectorAll(".periodo-btn").forEach(b =>
                    b.classList.toggle("is-active", b.dataset.preset === state.periodo));
                loadRelatorios();
                loadDespesas();
            });
        });

        // Upload de planilha: multipart, então sem o Content-Type JSON do jfetch.
        $("#formImportarDespesas")?.addEventListener("submit", async e => {
            e.preventDefault();
            const msg = $("#despesasImportMsg");
            msg.className = "msg";
            msg.textContent = "Importando...";
            try {
                const res = await fetch("/api/despesas/importar", {
                    method: "POST",
                    credentials: "include",
                    body: new FormData(e.target),
                });
                if (res.status === 401) { window.location.href = "/acesso"; return; }
                const json = await res.json();
                if (!res.ok) throw new Error(json.error || `Erro HTTP ${res.status}`);
                renderResultadoImportacao(json);
                e.target.reset();
                loadDespesas();
                loadRelatorios();
            } catch (err) {
                msg.className = "msg erro";
                msg.textContent = `Erro: ${err.message}`;
            }
        });

        $("#despesasWrap")?.addEventListener("click", async e => {
            const btn = e.target.closest(".remove-despesa-btn");
            if (!btn || !confirm("Remover esta despesa?")) return;
            try {
                await jfetch(`/api/despesas/${btn.dataset.id}`, { method: "DELETE" });
                loadDespesas();
                loadRelatorios();
            } catch (err) { alert(err.message); }
        });

        logoutBtn?.addEventListener("click", async () => {
            await jfetch("/auth/logout", { method: "POST" });
            sessionStorage.removeItem("loja_codigo");
            window.location.href = "/acesso";
        });
        
        loadInitialData();
        
        formCliente?.addEventListener("submit", async e => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(e.target).entries());
            try {
                await jfetch("/clientes", { method: "POST", body: JSON.stringify(data) });
                e.target.reset();
                loadClients();
            } catch (err) { alert(err.message); }
        });
      
        formUsuario?.addEventListener("submit", async e => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(e.target).entries());
            const msgEl = $("#usuarioMsg");
            try {
                const criado = await jfetch("/usuarios", { method: "POST", body: JSON.stringify(data) });
                e.target.reset();
                if (msgEl) msgEl.textContent = "Operador criado! Envie o link abaixo para ele definir a própria senha.";
                mostrarConvite(criado);
                loadUsuarios();
            } catch (err) {
                if (msgEl) msgEl.textContent = `Erro: ${err.message}`;
            }
        });

        // O link é montado com a origem que o admin está usando (ex.: IP da LAN),
        // que é a mesma pela qual o operador vai acessar o sistema.
        function mostrarConvite(criado) {
            const box = $("#conviteBox");
            if (!box || !criado?.convite_path) return;
            const link = `${window.location.origin}${criado.convite_path}`;
            box.innerHTML = `
                <label>Link de convite de ${esc(criado.nome)} (vale por 7 dias, uso único)
                    <input id="conviteLink" type="text" readonly value="${esc(link)}">
                </label>
                <button type="button" id="copiarConviteBtn" class="btn-secondary btn-sm">Copiar link</button>`;
            box.hidden = false;
        }

        $("#conviteBox")?.addEventListener('click', async e => {
            if (!e.target.closest('#copiarConviteBtn')) return;
            const input = $("#conviteLink");
            if (!input) return;
            const btn = e.target.closest('#copiarConviteBtn');
            let ok = false;
            // navigator.clipboard só existe em contexto seguro (https/localhost);
            // pela LAN em http cai no execCommand.
            try { await navigator.clipboard.writeText(input.value); ok = true; }
            catch (_) { input.select(); try { ok = document.execCommand('copy'); } catch (_) {} }
            btn.textContent = ok ? "Copiado!" : "Selecione e copie (Ctrl+C)";
            if (!ok) input.select();
            setTimeout(() => { btn.textContent = "Copiar link"; }, 2000);
        });

        usuariosWrap?.addEventListener('click', async e => {
            const btn = e.target.closest('button');
            if (!btn) return;
            const usuarioId = btn.dataset.id;
            if (!usuarioId) return;

            if (btn.classList.contains('edit-usuario-btn')) {
                const taxaAtual = numberToPtbr(parseFloat(btn.dataset.taxa));
                const novaTaxaStr = prompt(`Nova taxa de comissão (%) para "${btn.dataset.nome}":\n\n(A mudança vale só para pedidos NOVOS; os anteriores mantêm a comissão congelada.)`, taxaAtual);
                if (novaTaxaStr === null) return;
                const novaTaxa = ptbrToNumber(novaTaxaStr);
                if (novaTaxa === null || novaTaxa < 0) return alert('Taxa inválida.');
                try {
                    await jfetch(`/usuarios/${usuarioId}`, { method: 'PUT', body: JSON.stringify({ taxa_comissao: novaTaxa }) });
                    loadUsuarios();
                } catch (err) { alert(`Erro: ${err.message}`); }
            } else if (btn.classList.contains('toggle-usuario-btn')) {
                const ativar = btn.dataset.ativo !== '1';
                if (!ativar && !confirm('Desativar este usuário? Ele não conseguirá mais entrar no sistema.')) return;
                try {
                    await jfetch(`/usuarios/${usuarioId}`, { method: 'PUT', body: JSON.stringify({ ativo: ativar ? 1 : 0 }) });
                    loadUsuarios();
                } catch (err) { alert(`Erro: ${err.message}`); }
            }
        });

        formPedido?.addEventListener("submit", async e => {
            e.preventDefault();
            if (state.novoPedido.itens.length === 0) return alert("Adicione pelo menos um item ao pedido.");
            // "Salvar como orçamento" é o segundo botão submit do form.
            const isOrcamento = e.submitter?.dataset.orcamento === "1";
            const payload = {
                cliente_id: $("#clienteId").value,
                tecido: $("#tecido").value, preco_unitario: $("#preco").value,
                desconto: $("#desconto").value, itens: state.novoPedido.itens,
                descontar_estoque: !isOrcamento && $("#descontar_estoque").checked,
                pago: !$("#fiado").checked,
                is_orcamento: isOrcamento
            };
            try {
                await jfetch("/pedidos", { method: "POST", body: JSON.stringify(payload) });
                state.novoPedido.itens = [];
                formPedido.reset();
                renderItens(state.novoPedido.itens, itensWrap, 'remove-item');
                updateTotalPreview(state.novoPedido.itens, inpPreco, inpDesconto, totalPreviewEl);
                if (isOrcamento) {
                    loadOrcamentos();
                    alert('Orçamento salvo! O estoque NÃO foi descontado.');
                } else {
                    loadOrders();
                    loadRelatorios();
                    if (payload.descontar_estoque) loadStock();
                    alert('Pedido salvo com sucesso!');
                }
            } catch (err) { alert(`Erro ao salvar: ${err.message}`); }
        });

        $("#orcamentosWrap")?.addEventListener('click', async e => {
            const btn = e.target.closest('button');
            if (!btn?.dataset.id) return;
            if (btn.classList.contains('converter-orcamento-btn')) {
                if (!confirm('Converter este orçamento em pedido? O estoque será descontado agora.')) return;
                btn.disabled = true;
                try {
                    await jfetch(`/pedidos/${btn.dataset.id}/converter`, { method: 'POST' });
                    loadOrcamentos();
                    loadOrders();
                    loadStock();
                    loadRelatorios();
                    alert('Orçamento convertido em pedido.');
                } catch (err) {
                    btn.disabled = false;
                    alert(`Não foi possível converter: ${err.message}`);
                }
            } else if (btn.classList.contains('remove-orcamento-btn')) {
                if (!confirm('Remover este orçamento?')) return;
                try {
                    await jfetch(`/pedidos/${btn.dataset.id}`, { method: 'DELETE' });
                    loadOrcamentos();
                } catch (err) { alert(`Erro: ${err.message}`); }
            }
        });

        formPix?.addEventListener("submit", async e => {
            e.preventDefault();
            const pixChaveInput = e.target.elements.pix_chave;
            $("#pixMsg").textContent = "Salvando...";
            try {
                await jfetch("/pix", { method: "POST", body: JSON.stringify({ pix_chave: pixChaveInput.value.trim() }) });
                $("#pixMsg").textContent = "Chave Pix salva com sucesso!";
            } catch (err) { $("#pixMsg").textContent = `Erro: ${err.message}`; }
        });

        btnAddItem?.addEventListener("click", () => {
            addItem(state.novoPedido.itens, $("#cor"), $("#peso"), 
                () => renderItens(state.novoPedido.itens, itensWrap, 'remove-item'),
                () => updateTotalPreview(state.novoPedido.itens, inpPreco, inpDesconto, totalPreviewEl)
            );
        });
        
        itensWrap?.addEventListener("click", e => {
            if (e.target.classList.contains("remove-item")) {
                state.novoPedido.itens.splice(parseInt(e.target.dataset.index, 10), 1);
                renderItens(state.novoPedido.itens, itensWrap, 'remove-item');
                updateTotalPreview(state.novoPedido.itens, inpPreco, inpDesconto, totalPreviewEl);
            }
        });

        [inpPreco, inpDesconto].forEach(el => el?.addEventListener("input", () => updateTotalPreview(state.novoPedido.itens, inpPreco, inpDesconto, totalPreviewEl)));
        
        clienteSearchInput?.addEventListener('keyup', loadClients);
        orderSortSelect?.addEventListener('change', loadOrders);
        
        clientesWrap?.addEventListener('click', async e => {
            const btn = e.target.closest('button');
            if (!btn) return;

            if (btn.classList.contains('remove-cliente-btn')) {
                if (confirm('Tem certeza?')) {
                    try {
                        await jfetch(`/clientes/${btn.dataset.id}`, { method: 'DELETE' });
                        loadClients();
                    } catch (err) { alert(`Erro: ${err.message}`); }
                }
            } else if (btn.classList.contains('adjust-cliente-btn')) {
                const { id, nome, telefone, email } = btn.dataset;
                
                const novoTelefone = prompt(`Ajustar cliente "${nome}"\n\nTelefone atual: ${telefone}\n\nDigite o NOVO telefone:`, telefone);
                if (novoTelefone === null) return;

                const novoEmail = prompt(`Ajustar cliente "${nome}"\n\nEmail atual: ${email}\n\nDigite o NOVO email:`, email);
                if (novoEmail === null) return;
                
                try {
                    await jfetch(`/clientes/${id}`, {
                        method: 'PUT',
                        body: JSON.stringify({ telefone: novoTelefone.trim(), email: novoEmail.trim() })
                    });
                    loadClients();
                } catch (err) {
                    alert(`Erro ao atualizar cliente: ${err.message}`);
                }
            } else if (btn.classList.contains('pagamento-cliente-btn')) {
                const { id, nome } = btn.dataset;
                const valorStr = prompt(`Registrar pagamento de "${nome}"\n\nValor (R$):`, '');
                if (valorStr === null) return;
                const valor = ptbrToNumber(valorStr);
                if (valor === null || valor <= 0) return alert('Valor inválido.');
                try {
                    await jfetch(`/api/clientes/${id}/pagamentos`, {
                        method: 'POST',
                        body: JSON.stringify({ valor, data: isoDate(new Date()) })
                    });
                    loadClients();
                } catch (err) {
                    alert(`Erro ao registrar pagamento: ${err.message}`);
                }
            }
        });

        pedidosWrap?.addEventListener('click', async e => {
            const target = e.target.closest('button');
            if (!target) return;
            const pedidoId = target.dataset.id;
            if (!pedidoId) return;

            if (target.classList.contains('edit-pedido-btn')) {
                openEditModal(pedidoId);
            }
            else if (target.classList.contains('remove-pedido-btn')) {
                if (confirm(`Tem certeza que deseja remover o pedido #${pedidoId}? Esta ação não devolverá itens ao estoque.`)) {
                    try {
                        await jfetch(`/pedidos/${pedidoId}`, { method: 'DELETE' });
                        loadOrders();
                        loadRelatorios();
                    } catch (err) { alert(`Erro: ${err.message}`); }
                }
            }
            else if (target.classList.contains('pdf-pedido-btn')) {
                window.open(`/exportar/${pedidoId}?type=pdf`, '_blank');
            }
            else if (target.classList.contains('png-pedido-btn')) {
                window.open(`/exportar/${pedidoId}?type=png`, '_blank');
            }
            else if (target.classList.contains('whatsapp-pedido-btn')) {
                enviarWhatsApp(pedidoId);
            }
        });

        // ===== Encomendas (previsto) =====
        if ($("#encomendasWrap")) {
            state.novaEncomenda.itens = [linhaEncomendaVazia()];
            renderEncomendaItens();

            $("#btnEncAddLinha")?.addEventListener('click', () => {
                state.novaEncomenda.itens.push(linhaEncomendaVazia());
                renderEncomendaItens();
                $("#encItensLinhas tr:last-child input")?.focus();
            });
            $("#encItensLinhas")?.addEventListener('input', e => {
                const campo = e.target.dataset.campo;
                const tr = e.target.closest('tr[data-index]');
                if (!campo || !tr) return;
                state.novaEncomenda.itens[parseInt(tr.dataset.index, 10)][campo] = e.target.value;
            });
            $("#encItensLinhas")?.addEventListener('click', e => {
                if (!e.target.classList.contains('remove-enc-linha')) return;
                const tr = e.target.closest('tr[data-index]');
                state.novaEncomenda.itens.splice(parseInt(tr.dataset.index, 10), 1);
                renderEncomendaItens();
            });
            $("#formNovaEncomenda")?.addEventListener('submit', e => {
                e.preventDefault();
                criarEncomenda();
            });
            $("#encomendasLista")?.addEventListener('click', e => {
                const btn = e.target.closest('.receber-encomenda-btn');
                if (!btn) return;
                iniciarRecebimento(parseInt(btn.dataset.id, 10));
            });
        }

        // ===== Entrada de mercadoria =====
        if ($("#entradaMercadoria")) {
            const entradaData = $("#entradaData");
            if (entradaData && !entradaData.value) {
                const d = new Date();
                entradaData.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
            }
            state.entrada.linhas = [linhaEntradaVazia()];
            renderEntrada();

            $("#btnEntradaAddLinha")?.addEventListener('click', () => {
                state.entrada.linhas.push(linhaEntradaVazia());
                renderEntrada();
                $("#entradaLinhas tr:last-child input")?.focus();
            });
            // Digitação só atualiza o estado (sem re-render, para não perder o foco).
            $("#entradaLinhas")?.addEventListener('input', e => {
                const campo = e.target.dataset.campo;
                const tr = e.target.closest('tr[data-index]');
                if (!campo || !tr) return;
                state.entrada.linhas[parseInt(tr.dataset.index, 10)][campo] = e.target.value;
                atualizarResumoEntrada();
            });
            $("#entradaLinhas")?.addEventListener('click', e => {
                if (!e.target.classList.contains('remove-entrada-linha')) return;
                const tr = e.target.closest('tr[data-index]');
                state.entrada.linhas.splice(parseInt(tr.dataset.index, 10), 1);
                renderEntrada();
            });
            $("#btnLerPlanilha")?.addEventListener('click', lerPlanilhaEntrada);
            $("#btnConfirmarEntrada")?.addEventListener('click', confirmarEntrada);
            $("#btnCancelarRecebimento")?.addEventListener('click', cancelarRecebimento);
        }

        // ===== Event Listeners do Estoque =====
        formAddTecido?.addEventListener('submit', async e => {
            e.preventDefault();
            const nome_tecido = e.target.elements.nome_tecido.value.trim();
            if (!nome_tecido) return;
            try {
                await jfetch('/api/estoque/tecidos', { method: 'POST', body: JSON.stringify({ nome_tecido }) });
                e.target.reset();
                loadStock();
            } catch (err) {
                alert(`Erro: ${err.message}`);
            }
        });

        estoqueWrap?.addEventListener('submit', async e => {
            if (e.target.classList.contains('form-minimo-cor')) {
                e.preventDefault();
                const valor = e.target.elements.estoque_minimo.value.trim() || '0';
                const n = ptbrToNumber(valor);
                if (n === null || n < 0) return alert('Mínimo inválido. Use 0 para desligar o alerta.');
                try {
                    await jfetch(`/api/estoque/cores/${e.target.dataset.id}/minimo`, {
                        method: 'PUT', body: JSON.stringify({ estoque_minimo: valor })
                    });
                    loadStock();
                } catch (err) { alert(`Erro ao definir mínimo: ${err.message}`); }
                return;
            }
            if (e.target.classList.contains('form-add-cor')) {
                e.preventDefault();
                const form = e.target;
                const tecido_id = form.dataset.tecidoId;
                const payload = {
                    tecido_id: parseInt(tecido_id),
                    nome_cor: form.elements.nome_cor.value.trim(),
                    peso_kg: form.elements.peso_kg.value,
                    qtd_pecas: parseInt(form.elements.qtd_pecas.value, 10)
                };
                try {
                    await jfetch('/api/estoque/cores', { method: 'POST', body: JSON.stringify(payload) });
                    loadStock();
                } catch (err) {
                    alert(`Erro: ${err.message}`);
                }
            }
        });

        estoqueWrap?.addEventListener('click', async e => {
            const target = e.target.closest('button');
            if (!target) return;

            if (target.classList.contains('remove-tecido-btn')) {
                if (confirm('Tem certeza? Remover o tecido removerá TODAS as suas cores do estoque.')) {
                    try {
                        await jfetch(`/api/estoque/tecidos/${target.dataset.id}`, { method: 'DELETE' });
                        loadStock();
                    } catch(err) { alert(`Erro: ${err.message}`); }
                }
            } else if (target.classList.contains('remove-cor-btn')) {
                if (confirm('Tem certeza?')) {
                    try {
                        await jfetch(`/api/estoque/cores/${target.dataset.id}`, { method: 'DELETE' });
                        loadStock();
                    } catch(err) { alert(`Erro: ${err.message}`); }
                }
            } else if (target.classList.contains('adjust-cor-btn')) {
                const { id, nome, peso, pecas } = target.dataset;

                const novoPesoStr = prompt(`Ajustar ESTOQUE para a cor "${nome}"\n\nPeso atual: ${numberToPtbr(parseFloat(peso))} kg\n\nDigite o NOVO peso total (kg):`, numberToPtbr(parseFloat(peso)));
                if (novoPesoStr === null) return;

                const novasPecasStr = prompt(`Ajustar ESTOQUE para a cor "${nome}"\n\nPeças atuais: ${pecas}\n\nDigite a NOVA quantidade total de peças:`, pecas);
                if (novasPecasStr === null) return;

                const payload = {
                    peso_kg: novoPesoStr.trim(),
                    qtd_pecas: parseInt(novasPecasStr.trim(), 10)
                };
                
                if (isNaN(payload.qtd_pecas) || payload.qtd_pecas < 0 || ptbrToNumber(payload.peso_kg) === null || ptbrToNumber(payload.peso_kg) < 0) {
                    return alert('Valores inválidos. O peso e a quantidade não podem ser negativos.');
                }
                
                try {
                    await jfetch(`/api/estoque/cores/${id}`, {
                        method: 'PUT',
                        body: JSON.stringify(payload)
                    });
                    loadStock();
                } catch(err) {
                    alert(`Erro ao ajustar estoque: ${err.message}`);
                }
            }
        });


        // --- Event Listeners do Modal de Edição ---
        if (editModal) {
            $("#closeModal")?.addEventListener('click', closeEditModal);
            
            $("#btnEditAddItem")?.addEventListener('click', () => {
                addItem(state.pedidoEmEdicao.itens, $("#editCor"), $("#editPeso"), 
                    () => renderItens(state.pedidoEmEdicao.itens, $("#editItensWrap"), 'remove-edit-item'),
                    () => updateTotalPreview(state.pedidoEmEdicao.itens, $("#editPreco"), $("#editDesconto"), $("#editTotalPreview"))
                );
            });
            
            $("#editItensWrap")?.addEventListener('click', e => {
                if (e.target.classList.contains('remove-edit-item')) {
                    state.pedidoEmEdicao.itens.splice(parseInt(e.target.dataset.index, 10), 1);
                    renderItens(state.pedidoEmEdicao.itens, $("#editItensWrap"), 'remove-edit-item');
                    updateTotalPreview(state.pedidoEmEdicao.itens, $("#editPreco"), $("#editDesconto"), $("#editTotalPreview"));
                }
            });
              
            [$("#editPreco"), $("#editDesconto")].forEach(el => el?.addEventListener("input", () => updateTotalPreview(state.pedidoEmEdicao.itens, $("#editPreco"), $("#editDesconto"), $("#editTotalPreview"))));

            $("#formEditPedido")?.addEventListener('submit', async e => {
                e.preventDefault();
                const pedidoId = state.pedidoEmEdicao.id;
                if (!pedidoId) return;
                const payload = {
                    cliente_id: $("#editClienteId").value,
                    tecido: $("#editTecido").value, preco_unitario: $("#editPreco").value,
                    desconto: $("#editDesconto").value, itens: state.pedidoEmEdicao.itens,
                };
                // NOTA: A edição não envia `descontar_estoque` e o backend não vai alterar o estoque.
                try {
                    await jfetch(`/pedidos/${pedidoId}`, { method: 'PUT', body: JSON.stringify(payload) });
                    closeEditModal();
                    loadOrders();
                    loadRelatorios();
                    alert('Pedido atualizado com sucesso!');
                } catch (err) { alert(`Erro ao atualizar pedido: ${err.message}`); }
            });
        }
    });
})();