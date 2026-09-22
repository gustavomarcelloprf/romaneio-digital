'use strict';

/** ========= helpers ========= **/
const $ = (id) => document.getElementById(id);

function showMsg(text, ok = false) {
  const el = $('msg');
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('ok', Boolean(text) && ok);
  el.classList.toggle('erro', Boolean(text) && !ok);
}

function clearFieldErrors(form) {
  form?.querySelectorAll('.field.invalid').forEach((f) => f.classList.remove('invalid'));
  form?.querySelectorAll('.field-error').forEach((p) => { p.textContent = ''; });
}

function setFieldError(input, message) {
  const field = input.closest('.field');
  if (!field) return;
  field.classList.toggle('invalid', Boolean(message));
  const errEl = field.querySelector('.field-error');
  if (errEl) errEl.textContent = message || '';
}

// Validação inline: marca cada campo obrigatório vazio e foca o primeiro.
function validateForm(form) {
  let firstInvalid = null;
  form.querySelectorAll('input[required]').forEach((input) => {
    const vazio = !input.value.trim();
    setFieldError(input, vazio ? 'Campo obrigatório.' : '');
    if (vazio && !firstInvalid) firstInvalid = input;
  });
  firstInvalid?.focus();
  return !firstInvalid;
}

// Desabilita o botão durante o submit para evitar duplo envio.
function setLoading(form, loading) {
  const btn = form?.querySelector('button[type="submit"]');
  if (!btn) return;
  btn.disabled = loading;
  btn.classList.toggle('is-loading', loading);
}

function show(view) {
  const vLogin  = $('viewLogin');
  const vSignup = $('viewSignup');
  if (vLogin)  vLogin.hidden = (view !== 'login');
  if (vSignup) vSignup.hidden = (view !== 'signup');

  // Estado visual das abas (aria-selected acompanha para leitores de tela).
  const tabLogin = $('tabLogin');
  const tabSignup = $('tabSignup');
  tabLogin?.classList.toggle('is-active', view === 'login');
  tabLogin?.setAttribute('aria-selected', String(view === 'login'));
  tabSignup?.classList.toggle('is-active', view === 'signup');
  tabSignup?.setAttribute('aria-selected', String(view === 'signup'));
}

function switchTo(view) {
  show(view);
  showMsg('', true);
  clearFieldErrors($('formLogin'));
  clearFieldErrors($('formSignup'));
  const alvo = view === 'login' ? $('formLogin') : $('formSignup');
  alvo?.querySelector('input')?.focus();
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

  // Alternância login <-> cadastro: abas e links inferiores fazem o mesmo.
  $('tabLogin')?.addEventListener('click', () => switchTo('login'));
  $('tabSignup')?.addEventListener('click', () => switchTo('signup'));
  $('goLogin')?.addEventListener('click', (e) => { e.preventDefault(); switchTo('login'); });
  $('goSignup')?.addEventListener('click', (e) => { e.preventDefault(); switchTo('signup'); });

  // Mostrar/ocultar senha.
  document.querySelectorAll('.toggle-senha').forEach((btn) => {
    btn.addEventListener('click', () => {
      const input = btn.closest('.senha-wrap')?.querySelector('input');
      if (!input) return;
      const mostrar = input.type === 'password';
      input.type = mostrar ? 'text' : 'password';
      btn.textContent = mostrar ? 'Ocultar' : 'Mostrar';
      btn.setAttribute('aria-label', mostrar ? 'Ocultar senha' : 'Mostrar senha');
      btn.setAttribute('aria-pressed', String(mostrar));
      input.focus();
    });
  });

  // Erro de campo some assim que o usuário volta a digitar.
  document.querySelectorAll('#formLogin input, #formSignup input').forEach((input) => {
    input.addEventListener('input', () => setFieldError(input, ''));
  });

  /** ========= LOGIN ========= **/
  formLogin?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!validateForm(formLogin)) return;
    showMsg('Entrando...', true);
    setLoading(formLogin, true);

    const payload = Object.fromEntries(new FormData(formLogin).entries());
    try {
      const r = await jfetch('/auth/login', { method: 'POST', body: JSON.stringify(payload) });

      if (r.ok) {
        showMsg('Login OK! Redirecionando...', true);
        setTimeout(() => { window.location.href = '/'; }, 400);
        return; // mantém o botão desabilitado até o redirect
      }
      showMsg(r.data?.error || 'Erro ao entrar. Verifique o nome da loja e a senha.');
    } catch (_) {
      showMsg('Falha de conexão. Tente novamente.');
    }
    setLoading(formLogin, false);
  });


  /** ========= SIGNUP ========= **/
  // O signup também envia JSON, igual ao login (contrato do backend).
  formSignup?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!validateForm(formSignup)) {
      showMsg('Nome da loja, seu nome, login e senha são obrigatórios.');
      return;
    }
    showMsg('Cadastrando loja...', true);
    setLoading(formSignup, true);

    const payload = Object.fromEntries(new FormData(formSignup).entries());
    try {
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
        formLogin?.querySelector('input[name="senha"]')?.focus();
      } else {
        showMsg(r.data?.error || `Erro ao cadastrar: ${r.status}`);
      }
    } catch (_) {
      showMsg('Falha de conexão. Tente novamente.');
    }
    setLoading(formSignup, false);
  });
});
