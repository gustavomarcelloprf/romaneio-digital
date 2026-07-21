'use strict';

/** ========= helpers ========= **/
const $ = (id) => document.getElementById(id);

function showMsg(text, ok = false) {
  const el = $('msg');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = ok ? '#0a7c2f' : '#b00020';
}

function show(view) {
  const vLogin  = $('viewLogin');
  const vSignup = $('viewSignup');

  // Seletores para os links/botões de navegação
  const goLogin = $('goLogin');
  const goSignup = $('goSignup');

  if (vLogin)  vLogin.hidden = (view !== 'login');
  if (vSignup) vSignup.hidden = (view !== 'signup');

  // <<< ADICIONADO >>>: Renomeia os links de "Criar Loja" / "Entrar" para serem mais claros
  // (Ex: `toLogin` -> `goLogin` para corresponder ao ID no HTML)
  if (goLogin) goLogin.addEventListener('click', (e) => { e.preventDefault(); show('login');  showMsg('', true); });
  if (goSignup) goSignup.addEventListener('click', (e) => { e.preventDefault(); show('signup'); showMsg('', true); });
}


/** fetch JSON helper **/
async function jfetch(url, opts = {}) {
  const res = await fetch(url, {
    credentials: 'include', // importante para a sessão/cookies funcionar
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  let data = null;
  // Tenta parsear o JSON, mesmo em caso de erro, para obter a mensagem do servidor
  try { data = await res.json(); } catch (_) {}
  return { ok: res.ok, status: res.status, data };
}


document.addEventListener('DOMContentLoaded', () => {
  const formLogin  = $('formLogin');
  const formSignup = $('formSignup');

  // inicializa a view correta (login)
  show('login');
  showMsg('', true); // limpa mensagens iniciais

  /** ========= LOGIN ========= **/
  formLogin?.addEventListener('submit', async (e) => {
    e.preventDefault();
    showMsg('Entrando...', true);

    const payload = Object.fromEntries(new FormData(formLogin).entries());
    const r = await jfetch('/auth/login', { method: 'POST', body: JSON.stringify(payload) });

    if (r.ok) {
      showMsg('Login OK! Redirecionando...', true);
      setTimeout(() => { window.location.href = '/'; }, 400);
    } else {
      showMsg(r.data?.error || 'Erro ao entrar. Verifique o nome da loja e a senha.');
    }
  });


  /** ========= SIGNUP ========= **/
  // <<< MODIFICADO >>>: O signup agora também envia JSON, igual ao login.
  // Isso simplifica o backend (que só precisa esperar JSON) e corrige a incompatibilidade.
  formSignup?.addEventListener('submit', async (e) => {
    e.preventDefault();
    showMsg('Cadastrando loja...', true);

    const payload = Object.fromEntries(new FormData(formSignup).entries());

    // Validação simples no frontend
    if (!payload.nome_loja || !payload.nome || !payload.login || !payload.senha) {
        showMsg('Nome da loja, seu nome, login e senha são obrigatórios.');
        return;
    }

    const r = await jfetch('/auth/signup', { method: 'POST', body: JSON.stringify(payload) });

    if (r.ok) {
        show('login');
        // Pré-preenche loja e login no formulário de login para facilitar
        const lojaInput = formLogin?.querySelector('input[name="nome_loja"]');
        if (lojaInput) lojaInput.value = payload.nome_loja;
        const loginInput = formLogin?.querySelector('input[name="login"]');
        if (loginInput) loginInput.value = payload.login;

        showMsg('Loja criada! Agora você já pode entrar.', true);
        formSignup.reset();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
        showMsg(r.data?.error || `Erro ao cadastrar: ${r.status}`);
    }
  });
});
