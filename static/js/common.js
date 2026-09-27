/* Shared helpers. Everything is exposed on window.FF. No inline scripts anywhere (strict CSP). */
(function (global) {
  'use strict';

  var FF = {};

  FF.$ = function (sel, root) { return (root || document).querySelector(sel); };
  FF.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  FF.boot = function () {
    var node = document.getElementById('boot');
    try { return node ? JSON.parse(node.textContent) : {}; } catch (e) { return {}; }
  };

  /* A random nickname: one emoji (matches the server's allow-list in flyflirt/util.py) plus an
     adjective/noun pair, always short enough for the field (max combo below is 20 chars, field is 24). */
  var NICK_EMOJI = ['🦋', '🐝', '🦗', '🐛', '🦟', '🐞', '✨', '🌙', '🔥', '💫', '🌟', '🐜'];
  var NICK_ADJ = ['Curious', 'Velvet', 'Sneaky', 'Electric', 'Midnight', 'Golden', 'Quiet', 'Bold',
    'Lucky', 'Gentle', 'Restless', 'Cosmic', 'Dizzy', 'Fuzzy', 'Plucky', 'Nimble', 'Cheeky', 'Wild'];
  var NICK_NOUN = ['Firefly', 'Moth', 'Beetle', 'Cricket', 'Dragonfly', 'Comet', 'Nebula', 'Wasp',
    'Cicada', 'Lantern', 'Ember', 'Drift', 'Sprite', 'Pixel', 'Glowworm', 'Hornet'];
  function pick(list) { return list[Math.floor(Math.random() * list.length)]; }
  FF.randomNickname = function () {
    return pick(NICK_EMOJI) + ' ' + pick(NICK_ADJ) + ' ' + pick(NICK_NOUN);
  };
  /* Fill a nickname field with a random name, wire an optional dice button to reroll it, and make the
     default easy to throw away: the first time the field is focused, select it all so typing replaces
     it outright instead of needing a row of backspaces. */
  FF.wireNickname = function (input, diceBtn) {
    if (!input) return;
    if (!input.value.trim()) input.value = FF.randomNickname();
    input.addEventListener('focus', function () { input.select(); }, { once: true });
    if (diceBtn) diceBtn.addEventListener('click', function () { input.value = FF.randomNickname(); input.focus(); input.select(); });
  };
  /* A nickname is required everywhere: validate before letting a flow proceed. */
  FF.requireNickname = function (input) {
    var v = input.value.trim();
    if (!v) { input.focus(); FF.toast('Please enter a nickname first.', 'warn'); return null; }
    return v;
  };

  /* Build DOM safely: text is always set via textContent, never innerHTML. */
  FF.el = function (tag, attrs) {
    var node = document.createElement(tag), i, child;
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'style' && typeof v === 'object') Object.keys(v).forEach(function (s) { node.style.setProperty(s.replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); }), v[s]); });
      else if (k.indexOf('on') === 0 && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    });
    for (i = 2; i < arguments.length; i++) {
      child = arguments[i];
      if (child == null || child === false) continue;
      node.appendChild(typeof child === 'string' || typeof child === 'number' ? document.createTextNode(String(child)) : child);
    }
    return node;
  };

  FF.fmt = function (n) { return Number(n || 0).toLocaleString('en-US'); };
  FF.pct = function (x) { return Math.round((x || 0) * 100) + '%'; };
  FF.clock = function (s) { s = Math.max(0, Math.floor(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  FF.time = function (ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };

  FF.toast = function (msg, kind, ms) {
    var box = document.getElementById('toasts');
    if (!box) return;
    var t = FF.el('div', { class: 'toast ' + (kind || ''), text: msg });
    box.appendChild(t);
    setTimeout(function () { t.style.transition = 'opacity .3s'; t.style.opacity = '0'; setTimeout(function () { t.remove(); }, 320); }, ms || 3200);
  };

  FF.api = function (path, opts) {
    return fetch(path, Object.assign({ credentials: 'same-origin', headers: { Accept: 'application/json' } }, opts || {}))
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
      });
  };

  /* Remembered "who I am / who I want" so "Next person" can search again in one click. */
  FF.prefs = {
    save: function (p) { try { sessionStorage.setItem('ff_prefs', JSON.stringify(p)); } catch (e) { /* private mode */ } },
    load: function () { try { return JSON.parse(sessionStorage.getItem('ff_prefs') || 'null'); } catch (e) { return null; } }
  };

  /* Report dialog (used by the chat and verdict pages). */
  var REPORT_CATEGORIES = [
    ['harassment', 'Harassment or bullying'], ['sexual', 'Sexual or explicit content'], ['hate', 'Hate speech'],
    ['threat', 'Threats or self-harm'], ['spam', 'Spam or scams'], ['other', 'Something else']
  ];
  FF.reportDialog = function (roomId, done) {
    var el = FF.el, prev = document.activeElement;
    var select = el('select', { class: 'input', id: 'rep-cat', 'aria-label': 'What happened?' });
    REPORT_CATEGORIES.forEach(function (c) { select.appendChild(el('option', { value: c[0], text: c[1] })); });
    var note = el('textarea', { class: 'input', id: 'rep-note', maxlength: '300', rows: '3', placeholder: 'Anything else we should know? (optional)' });
    var block = el('input', { type: 'checkbox', id: 'rep-block', checked: true });
    var status = el('p', { class: 'muted', style: { 'font-size': '.8rem', 'min-height': '1.1em' }, role: 'alert' });
    var send = el('button', { class: 'btn btn-primary', type: 'button', text: 'Send report' });
    var cancel = el('button', { class: 'btn btn-ghost', type: 'button', text: 'Cancel' });
    var back = el('div', { class: 'modal-back' },
      el('div', { class: 'modal glass', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'rep-title' },
        el('h3', { id: 'rep-title', text: 'Report this conversation' }),
        el('p', { class: 'muted', style: { 'font-size': '.82rem' }, text: 'Reports are private. Your partner is never told who reported them.' }),
        select, note,
        el('label', { class: 'check' }, block, el('span', { text: 'Also block this person so we never match us again' })),
        status, el('div', { class: 'row', style: { 'justify-content': 'flex-end', gap: '.5rem' } }, cancel, send)));
    function close() { back.remove(); document.removeEventListener('keydown', onKey); if (prev && prev.focus) prev.focus(); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    cancel.addEventListener('click', close);
    back.addEventListener('click', function (e) { if (e.target === back) close(); });
    document.addEventListener('keydown', onKey);
    send.addEventListener('click', function () {
      send.disabled = true; status.textContent = 'Sending…';
      FF.api('/api/rooms/' + roomId + '/report', {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ category: select.value, reason: note.value.trim(), block: block.checked })
      }).then(function (r) {
        if (r.ok) { close(); FF.toast('Thanks. Your report was sent.'); if (done) done(block.checked); }
        else { send.disabled = false; status.textContent = r.data.message || 'Could not send the report.'; }
      }).catch(function () { send.disabled = false; status.textContent = 'Could not reach the server.'; });
    });
    document.body.appendChild(back);
    select.focus();
  };

  FF.copy = function (text, okMsg) {
    var done = function () { FF.toast(okMsg || 'Copied!'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { FF._legacyCopy(text, done); });
    } else FF._legacyCopy(text, done);
  };
  FF._legacyCopy = function (text, done) {
    var ta = FF.el('textarea', { style: { position: 'fixed', opacity: '0' } });
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { FF.toast('Press Ctrl+C to copy', 'warn'); }
    ta.remove();
  };

  FF.share = function (data) {
    if (navigator.share) { navigator.share(data).catch(function () {}); return true; }
    return false;
  };

  FF.socket = function () {
    return global.io({ transports: ['websocket', 'polling'], reconnection: true, reconnectionDelay: 500, reconnectionDelayMax: 5000 });
  };

  /* Golden-angle hues give any number of messages well separated colours. */
  FF.msgColor = function (i) { return 'hsl(' + Math.round((i * 137.508 + 200) % 360) + ', 82%, 62%)'; };
  FF.msgHex = function (i) {
    var h = ((i * 137.508 + 200) % 360) / 360, s = 0.82, l = 0.62;
    var a = s * Math.min(l, 1 - l);
    function f(n) { var k = (n + h * 12) % 12; var c = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1); return Math.round(255 * c).toString(16).padStart(2, '0'); }
    return '#' + f(0) + f(8) + f(4);
  };

  /* Participant colour: label-based in flirt chats, slot-based between friends. */
  FF.tone = function (p) {
    if (!p) return 'var(--cyan)';
    if (p.label === 'Male') return 'var(--tone-male)';
    if (p.label === 'Female') return 'var(--tone-female)';
    return p.slot === 0 ? 'var(--tone-friend0)' : 'var(--tone-friend1)';
  };
  FF.toneIsLight = function (p) { return !!p && (p.label === 'Female' || (p.label === 'Friend' && p.slot === 1)); };

  FF.labelFor = function (p) { return p ? (p.nickname && p.nickname !== p.label ? p.nickname + ' · ' + p.label : p.label) : '?'; };

  /* (i) popovers: <button data-info> followed by <span class="info-pop" hidden>. */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-info]');
    var all = document.querySelectorAll('.info-pop');
    if (btn) {
      var pop = btn.parentElement.querySelector('.info-pop'), open = pop.hasAttribute('hidden');
      all.forEach(function (p) { p.setAttribute('hidden', ''); });
      document.querySelectorAll('[data-info]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
      if (open) { pop.removeAttribute('hidden'); btn.setAttribute('aria-expanded', 'true'); }
      return;
    }
    if (!(e.target.closest && e.target.closest('.info-pop'))) {
      all.forEach(function (p) { p.setAttribute('hidden', ''); });
      document.querySelectorAll('[data-info]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
    }
  });

  FF.PARAMS = [
    ['warmth', 'warmth'], ['humor', 'humor'], ['reciprocity', 'reciprocity'], ['curiosity', 'curiosity'],
    ['disclosure', 'sharing'], ['energy', 'energy'], ['tension', 'tension']
  ];

  global.FF = FF;
})(window);
