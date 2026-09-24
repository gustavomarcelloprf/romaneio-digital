'use strict';

// Página do convite: o operador define a própria senha. O token está no
// próprio caminho (/convite/<token>), então o POST vai para a mesma URL.
const $ = (id) => document.getElementById(id);
const SENHA_MIN = 6;

function showMsg(text, ok = false) {
  const el = $('msg');
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('ok', Boolean(text) && ok);
  el.classList.toggle('erro', Boolean(text) && !ok);
}

function setFieldError(input, message) {
  const field = input.closest('.field');
  if (!field) return;
  field.classList.toggle('invalid', Boolean(message));
  const errEl = field.querySelector('.field-error');
  if (errEl) errEl.textContent = message || '';
}

document.addEventListener('DOMContentLoaded', () => {
  const form = $('formConvite');
  const senha = $('conviteSenha');
  const senha2 = $('conviteSenha2');
  if (!form || !senha || !senha2) return;

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

  [senha, senha2].forEach((input) => input.addEventListener('input', () => setFieldError(input, '')));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (senha.value.trim().length < SENHA_MIN) {
      setFieldError(senha, `Mínimo de ${SENHA_MIN} caracteres.`);
      senha.focus();
      return;
    }
    if (senha.value !== senha2.value) {
      setFieldError(senha2, 'As senhas não conferem.');
      senha2.focus();
      return;
    }

    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.classList.add('is-loading');
    try {
      const res = await fetch(window.location.pathname, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ senha: senha.value }),
      });
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (res.ok) {
        form.hidden = true;
        showMsg(`Senha definida! Entre com a loja "${data?.loja ?? ''}" e o login "${data?.login ?? ''}".`, true);
        $('irLogin').hidden = false;
        return;
      }
      showMsg(data?.error || `Erro ao definir a senha (${res.status}).`);
      // Convite morto (expirou ou já usado): não adianta tentar de novo.
      if (res.status === 404) { form.hidden = true; $('irLogin').hidden = false; }
    } catch (_) {
      showMsg('Falha de conexão. Tente novamente.');
    }
    btn.disabled = false;
    btn.classList.remove('is-loading');
  });
});
