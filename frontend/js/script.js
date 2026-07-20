(function () {
    const $ = (s) => document.querySelector(s);

    function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

    // --- Estado da Aplicação ---
    const state = { 
        novoPedido: { itens: [] },
        pedidoEmEdicao: { id: null, itens: [] },
        loja: sessionStorage.getItem("loja_codigo") || ""
    };

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
        const clientes = await jfetch(`/clientes?search=${encodeURIComponent(searchTerm)}`);
        
        if (Array.isArray(clientes)) {
            const optionsHtml = clientes.map(c => `<option value="${c.id}">${esc(c.nome)}</option>`).join("");
            $("#clienteId").innerHTML = optionsHtml;
            $("#editClienteId").innerHTML = optionsHtml;

            const clientesWrap = $("#clientesWrap");
            if (clientesWrap) {
                clientesWrap.innerHTML = clientes.length ? `
                <table class="table">
                    <thead><tr><th>Nome</th><th>Telefone</th><th>Email</th><th>Ação</th></tr></thead>
                    <tbody>${clientes.map(c => `
                    <tr>
                        <td>${esc(c.nome)}</td>
                        <td>${esc(c.telefone || '')}</td>
                        <td>${esc(c.email || '')}</td>
                        <td class="actions">
                            <button class="btn-secondary btn-sm adjust-cliente-btn" data-id="${c.id}" data-telefone="${esc(c.telefone || '')}" data-email="${esc(c.email || '')}" data-nome="${esc(c.nome)}">Ajustar</button>
                            <button class="btn-secondary btn-danger btn-sm remove-cliente-btn" data-id="${c.id}">Remover</button>
                        </td>
                    </tr>`).join('')}
                    </tbody>
                </table>` : '<p class="muted">Nenhum cliente encontrado.</p>';
            }
        }
    }
    
    async function loadSellers() {
        const vendedores = await jfetch("/vendedores");
        if (Array.isArray(vendedores)) {
            const optionsHtml = '<option value="">Selecione...</option>' + vendedores.map(v => `<option value="${v.id}">${esc(v.nome)}</option>`).join("");
            $("#vendedorId").innerHTML = optionsHtml;
            $("#editVendedorId").innerHTML = optionsHtml;
            
            const vendedoresWrap = $("#vendedoresWrap");
            if (vendedoresWrap) {
                vendedoresWrap.innerHTML = vendedores.length ? `
                    <table class="table">
                        <thead><tr><th>Nome</th><th>Ação</th></tr></thead>
                        <tbody>
                            ${vendedores.map(v => `
                                <tr>
                                    <td>${esc(v.nome)}</td>
                                    <td class="actions"><button class="btn-secondary btn-danger btn-sm remove-vendedor-btn" data-id="${v.id}">Remover</button></td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                ` : '<p class="muted">Nenhum vendedor cadastrado.</p>';
            }
        }
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
                <thead><tr><th>ID</th><th>Cliente</th><th>Data</th><th>Total</th><th>Ações</th></tr></thead>
                <tbody>${pedidos.map(p => `
                    <tr>
                        <td>${p.id}</td>
                        <td>${esc(p.cliente_nome)}</td>
                        <td>${new Date(p.data_iso).toLocaleString('pt-BR')}</td>
                        <td>${fmtBRL(p.total)}</td>
                        <td class="actions">
                            <button class="btn-secondary btn-sm edit-pedido-btn" data-id="${p.id}">Editar</button>
                            <button class="btn-secondary btn-danger btn-sm remove-pedido-btn" data-id="${p.id}">Remover</button>
                            <button class="btn-secondary btn-sm png-pedido-btn" data-id="${p.id}">PNG</button>
                            <button class="btn-secondary btn-sm pdf-pedido-btn" data-id="${p.id}">PDF</button>
                        </td>
                    </tr>`).join('')}
                </tbody>
            </table>` : '<p class="muted">Nenhum pedido cadastrado.</p>';
            }
        }
    }

    function renderEstoque(estoque) {
        const estoqueWrap = $("#estoqueWrap");
        if (!estoqueWrap) return;
        if (estoque.length === 0) {
            estoqueWrap.innerHTML = `<p class="muted">Nenhum tipo de tecido cadastrado. Comece adicionando um acima.</p>`;
            return;
        }

        estoqueWrap.innerHTML = estoque.map(tecido => `
            <div class="estoque-card">
                <div class="estoque-header">
                    <h4>${esc(tecido.nome_tecido)}</h4>
                    <button class="btn-secondary btn-danger btn-sm remove-tecido-btn" data-id="${tecido.id}">Remover Tecido</button>
                </div>
                <div class="table-responsive">
                    <table class="table">
                        <thead><tr><th>Cor</th><th>Peso (kg)</th><th>Peças</th><th>Ação</th></tr></thead>
                        <tbody>
                            ${tecido.cores.length ? tecido.cores.map(cor => `
                                <tr>
                                    <td>${esc(cor.nome_cor)}</td>
                                    <td>${esc(numberToPtbr(cor.peso_kg))}</td>
                                    <td>${esc(cor.qtd_pecas)}</td>
                                    <td class="actions">
                                        <button class="btn-secondary btn-sm adjust-cor-btn" data-id="${cor.id}" data-nome="${esc(cor.nome_cor)}" data-peso="${esc(cor.peso_kg)}" data-pecas="${esc(cor.qtd_pecas)}">Ajustar</button>
                                        <button class="btn-secondary btn-danger btn-sm remove-cor-btn" data-id="${cor.id}">X</button>
                                    </td>
                                </tr>
                            `).join('') : `<tr><td colspan="4" class="muted" style="text-align:center;">Nenhuma cor adicionada.</td></tr>`}
                        </tbody>
                    </table>
                </div>
                <form class="form-add-cor" data-tecido-id="${tecido.id}">
                    <div class="item-row">
                        <input name="nome_cor" placeholder="Nova Cor" required>
                        <input name="peso_kg" placeholder="Peso (kg)" inputmode="decimal" required>
                        <input name="qtd_pecas" placeholder="Nº Peças" type="number" value="1">
                        <button type="submit" class="btn-primary btn-sm" style="width:auto;">Adicionar/Somar Cor</button>
                    </div>
                </form>
            </div>
        `).join('');
    }

    async function loadStock() {
        try {
            const estoque = await jfetch("/api/estoque");
            renderEstoque(estoque);
        } catch (err) {
            $("#estoqueWrap").innerHTML = `<p class="msg erro">Erro ao carregar estoque: ${esc(err.message)}</p>`;
        }
    }

    async function loadInitialData() {
        $("#lojaBadge").textContent = state.loja;
        try {
            await Promise.all([
                loadClients(),
                loadOrders(),
                loadSellers(),
                loadStock()
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
            $("#editVendedorId").value = pedido.vendedor_id;
            
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

    // ===== Event Listeners =====
    document.addEventListener("DOMContentLoaded", () => {
        const formPix = $("#formPix"), formCliente = $("#formCliente"), formVendedor = $("#formVendedor");
        const formPedido = $("#formPedido"), pedidosWrap = $("#pedidosWrap"), clientesWrap = $("#clientesWrap");
        const vendedoresWrap = $("#vendedoresWrap"), clienteSearchInput = $("#clienteSearch"), orderSortSelect = $("#orderSort");
        const inpPreco = $("#preco"), inpDesconto = $("#desconto"), totalPreviewEl = $("#totalPreview");
        const btnAddItem = $("#btnAddItem"), itensWrap = $("#itensWrap");
        const logoutBtn = $("#logoutBtn");
        const editModal = $("#editModal");
        const estoqueWrap = $("#estoqueWrap");
        const formAddTecido = $("#formAddTecido");

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
      
        formVendedor?.addEventListener("submit", async e => {
            e.preventDefault();
            const data = { nome: e.target.elements.nome.value.trim() };
            if (!data.nome) return;
            try {
                await jfetch("/vendedores", { method: "POST", body: JSON.stringify(data) });
                e.target.reset();
                loadSellers();
            } catch (err) { alert(err.message); }
        });

        formPedido?.addEventListener("submit", async e => {
            e.preventDefault();
            if (state.novoPedido.itens.length === 0) return alert("Adicione pelo menos um item ao pedido.");
            const payload = {
                cliente_id: $("#clienteId").value, vendedor_id: $("#vendedorId").value,
                tecido: $("#tecido").value, preco_unitario: $("#preco").value,
                desconto: $("#desconto").value, itens: state.novoPedido.itens,
                descontar_estoque: $("#descontar_estoque").checked
            };
            try {
                await jfetch("/pedidos", { method: "POST", body: JSON.stringify(payload) });
                state.novoPedido.itens = [];
                formPedido.reset();
                renderItens(state.novoPedido.itens, itensWrap, 'remove-item');
                updateTotalPreview(state.novoPedido.itens, inpPreco, inpDesconto, totalPreviewEl);
                loadOrders();
                if (payload.descontar_estoque) loadStock();
                alert('Pedido salvo com sucesso!');
            } catch (err) { alert(`Erro ao salvar pedido: ${err.message}`); }
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
            }
        });

        vendedoresWrap?.addEventListener('click', async e => {
            const btn = e.target.closest('.remove-vendedor-btn');
            if (btn && confirm('Tem certeza?')) {
                try {
                    await jfetch(`/vendedores/${btn.dataset.id}`, { method: 'DELETE' });
                    loadSellers();
                } catch (err) { alert(`Erro: ${err.message}`); }
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
                    } catch (err) { alert(`Erro: ${err.message}`); }
                }
            }
            else if (target.classList.contains('pdf-pedido-btn')) {
                window.open(`/exportar/${pedidoId}?type=pdf`, '_blank');
            }
            else if (target.classList.contains('png-pedido-btn')) {
                window.open(`/exportar/${pedidoId}?type=png`, '_blank');
            }
        });

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
                    cliente_id: $("#editClienteId").value, vendedor_id: $("#editVendedorId").value,
                    tecido: $("#editTecido").value, preco_unitario: $("#editPreco").value,
                    desconto: $("#editDesconto").value, itens: state.pedidoEmEdicao.itens,
                };
                // NOTA: A edição não envia `descontar_estoque` e o backend não vai alterar o estoque.
                try {
                    await jfetch(`/pedidos/${pedidoId}`, { method: 'PUT', body: JSON.stringify(payload) });
                    closeEditModal();
                    loadOrders();
                    alert('Pedido atualizado com sucesso!');
                } catch (err) { alert(`Erro ao atualizar pedido: ${err.message}`); }
            });
        }
    });
})();