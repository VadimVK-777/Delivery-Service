const csrf = document.querySelector('meta[name="csrf-token"]').content;
function notice(message) { const n = document.querySelector('#notice'); n.textContent = message; n.classList.add('visible'); setTimeout(() => n.classList.remove('visible'), 7000); }
async function api(url, body, headers = {}) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...headers}, body: JSON.stringify(body)});
  const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Не удалось выполнить запрос'); return data;
}
const register = document.querySelector('#register-form');
if (register) {
  fetch('/api/v1/parcel-types').then(r => {if (!r.ok) throw new Error('Типы посылок недоступны'); return r.json();}).then(types => {
    const select = document.querySelector('#type-options'); select.replaceChildren(...types.map(t => new Option(t.name, t.id)));
  }).catch(e => notice(e.message));
  function pendingIds() {
    return JSON.parse(sessionStorage.getItem('pending') || '[]');
  }
  let requestKey = null, requestBody = null;
  register.addEventListener('submit', async event => {
    event.preventDefault(); const button = register.querySelector('button'); button.disabled = true;
    const body = Object.fromEntries(new FormData(register)); body.type_id = Number(body.type_id);
    const serialized = JSON.stringify(body); if (serialized !== requestBody) {requestKey = crypto.randomUUID(); requestBody = serialized;}
    try {
      const data = await api('/api/v1/parcels', body, {'Idempotency-Key': requestKey});
      const pending = pendingIds(); if (!pending.includes(data.id)) pending.push(data.id);
      sessionStorage.setItem('pending', JSON.stringify(pending)); register.reset(); requestKey = null; requestBody = null;
      notice('Посылка принята. Рассчитываем доставку.'); poll();
    } catch (e) {notice(e.message);} finally {button.disabled = false;}
  });
  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const pending = pendingIds();
      const finished = new Set();
      const items = document.createDocumentFragment();
      for (const id of pending) {
        try {
          const response = await fetch(`/api/v1/registrations/${id}`);
          if (!response.ok) {
            if (response.status === 404) finished.add(id);
            continue;
          }
          const {status} = await response.json();
          const item = document.createElement('a');
          item.textContent = `${id.slice(0, 8)} · ${status}`;
          if (['pending', 'calculated', 'failed'].includes(status)) item.href = `/parcels/${id}`;
          items.append(item);
          if (status === 'calculated' || status === 'failed') finished.add(id);
        } catch {
          // Keep this parcel for the next poll.
        }
      }
      if (finished.size) {
        // A registration may have added another parcel while the requests were in flight.
        sessionStorage.setItem('pending', JSON.stringify(pendingIds().filter(id => !finished.has(id))));
      }
      document.querySelector('#pending').replaceChildren(items);
      if (pending.length) htmx.trigger(document.body, 'parcelsChanged');
    } finally {
      polling = false;
    }
  }
  setInterval(poll, 3000); poll();
}
document.querySelector('#question-form')?.addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true;
  const question = form.question.value; const conversation = document.querySelector('#conversation');
  const user = document.createElement('p'); user.className = 'bubble user'; user.textContent = question; conversation.append(user);
  try {const result = await api('/api/v1/support/questions', {question}); const reply = document.createElement('p'); reply.className = 'bubble'; reply.textContent = result.answer; conversation.append(reply);
    const sources = document.createElement('p'); sources.className = 'sources'; sources.textContent = result.sources.join(' · '); conversation.append(sources); form.reset();
  } catch (e) {notice(e.message);} finally {button.disabled = false; conversation.scrollTop = conversation.scrollHeight;}
});
document.querySelector('#assignment-button')?.addEventListener('click', async event => {
  const button = event.currentTarget; button.disabled = true;
  try {const data = await api(`/api/v1/parcels/${button.dataset.id}/assignment-token`, {}); document.querySelector('#assignment-output').textContent = data.assignment_token;}
  catch (e) {notice(e.message);} finally {button.disabled = false;}
});
document.body.addEventListener('htmx:responseError', () => notice('Не удалось загрузить список посылок. Попробуйте позже.'));
