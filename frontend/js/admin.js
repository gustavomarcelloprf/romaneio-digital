(function() {
    const $ = (s) => document.querySelector(s);

    function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

    const adminTokenInput = $('#adminToken');
    const loadButton = $('#loadButton');
    const pendingListDiv = $('#pendingList');
    const allStoresListDiv = $('#allStoresList');
    const adminMsg = $('#adminMsg');

    function showMsg(text, isError = false) {
        adminMsg.textContent = text;
        adminMsg.style.color = isError ? 'red' : 'green';
        setTimeout(() => showMsg(''), 4000); // Limpa a mensagem após 4 segundos
    }

    async function apiCall(url, opts = {}) {
        const token = adminTokenInput.value;
        if (!token) {
            alert('Por favor, insira o Admin Token.');
            throw new Error('Token não fornecido.');
        }
        const res = await fetch(url, {
            ...opts,
            headers: { 'Authorization': token, ...(opts.headers || {}) }
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || `Erro HTTP ${res.status}`);
        return result;
    }

    async function approveStore(codigoLoja) {
        try {
            const result = await apiCall(`/admin/aprovar-loja/${codigoLoja}`, { method: 'POST' });
            showMsg(result.message);
            loadAllData();
        } catch (err) {
            showMsg(err.message, true);
        }
    }

    async function revokeStoreAccess(codigoLoja) {
        try {
            const result = await apiCall(`/admin/revogar-loja/${codigoLoja}`, { method: 'POST' });
            showMsg(result.message);
            loadAllData();
        } catch (err) {
            showMsg(err.message, true);
        }
    }

    // <<< NOVA FUNÇÃO PARA REATIVAR >>>
    async function reactivateStore(codigoLoja) {
        try {
            const result = await apiCall(`/admin/reativar-loja/${codigoLoja}`, { method: 'POST' });
            showMsg(result.message);
            loadAllData();
        } catch (err) {
            showMsg(err.message, true);
        }
    }
    
    function renderStores(stores) {
        const pendingStores = stores.filter(s => s.status === 'pendente');
        
        // Renderiza lojas pendentes
        if (pendingStores.length === 0) {
            pendingListDiv.innerHTML = '<p class="muted">Nenhuma loja pendente encontrada.</p>';
        } else {
            pendingListDiv.innerHTML = `
                <table class="table">
                    <thead><tr><th>Nome da Loja</th><th>Data de Cadastro</th><th>Ação</th></tr></thead>
                    <tbody>
                        ${pendingStores.map(loja => `
                            <tr>
                                <td>${esc(loja.nome)}</td>
                                <td>${new Date(loja.created_at).toLocaleString('pt-BR')}</td>
                                <td><button class="approve-btn" data-codigo="${esc(loja.codigo)}">Aprovar</button></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>`;
        }
        
        // Renderiza todas as lojas
        if (stores.length === 0) {
            allStoresListDiv.innerHTML = '<p class="muted">Nenhuma loja registrada encontrada.</p>';
        } else {
            allStoresListDiv.innerHTML = `
                <table class="table">
                    <thead><tr><th>Nome da Loja</th><th>Status</th><th>Ação</th></tr></thead>
                    <tbody>
                        ${stores.map(loja => `
                            <tr>
                                <td>${esc(loja.nome)}</td>
                                <td><span class="status-${esc(loja.status)}">${esc(loja.status)}</span></td>
                                <td>
                                    ${loja.status === 'aprovado' ? `<button class="revoke-btn" data-codigo="${esc(loja.codigo)}">Revogar Acesso</button>` : ''}
                                    ${loja.status === 'pendente' ? `<button class="approve-btn" data-codigo="${esc(loja.codigo)}">Aprovar</button>` : ''}
                                    ${loja.status === 'revogado' ? `<button class="reactivate-btn" data-codigo="${esc(loja.codigo)}">Reativar</button>` : ''}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>`;
        }
    }

    async function loadAllData() {
        showMsg('Carregando...', false);
        try {
            const allStores = await apiCall('/admin/lojas');
            renderStores(allStores);
            showMsg('');
        } catch (err) {
            showMsg(err.message, true);
        }
    }

    loadButton.addEventListener('click', loadAllData);

    // Delegação de eventos para os botões
    document.addEventListener('click', (e) => {
        const target = e.target;
        const codigo = target.dataset.codigo;

        if (target.classList.contains('approve-btn')) {
            if (confirm(`Tem certeza que deseja APROVAR a loja "${codigo}"?`)) {
                approveStore(codigo);
            }
        }
        if (target.classList.contains('revoke-btn')) {
            if (confirm(`Tem certeza que deseja REVOGAR o acesso da loja "${codigo}"?`)) {
                revokeStoreAccess(codigo);
            }
        }
        // <<< NOVO EVENTO PARA REATIVAR >>>
        if (target.classList.contains('reactivate-btn')) {
            if (confirm(`Tem certeza que deseja REATIVAR o acesso da loja "${codigo}"?`)) {
                reactivateStore(codigo);
            }
        }
    });

})();