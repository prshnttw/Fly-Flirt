(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$, el = FF.el;
  var socket = FF.socket();

  var S = {
    slot: boot.slot, room: null, parts: [], counts: { 0: 0, 1: 0 }, min: boot.min_for_verdict || 3,
    maxLen: boot.max_len || 400, status: 'active', bubbles: {}, latestSeq: -1, totals: { cells: 1350, edges: 50158 },
    typingTimer: null, lastTypingSent: 0, stateTimer: null, ready: false,
    lastAckedSeq: -1, partnerSeenUpTo: -1
  };
  var msgsBox = $('#msgs'), input = $('#input'), sendBtn = $('#send');

  /* ---------------- 3D brain ---------------- */
  var brain = null, tip = $('#tip-hover');
  var legend = $('#legend');
  ['brake', 'input', 'accumulator', 'output'].forEach(function (r) {
    legend.appendChild(el('span', {}, el('i', { style: { background: FlyBrain.ROLE_HEX[r] } }), FlyBrain.ROLE_LABEL[r].split(' (')[0]));
  });
  brain = new FlyBrain($('#brain'), {
    autoRotate: true, distance: 2.3, interactive: true, scale: boot.scale,
    onSlow: function (fps) {
      if (boot.scale === 'full') FF.toast('The full pathway is running slowly here (' + fps + ' fps). You can switch it off under the brain.', 'warn', 7000);
    },
    onHover: function (i, pos) {
      if (i == null) { tip.hidden = true; return; }
      var d = brain.describe(i);
      tip.textContent = d.type + ' · ' + d.role + ' · ' + d.nt + ' · ' + Math.round(d.level * 100) + '%';
      tip.style.left = pos.x + 'px'; tip.style.top = pos.y + 'px'; tip.hidden = false;
    }
  });
  var brainReady = brain.ready, pending = null;
  brainReady.then(function (ok) { if (ok && pending) { pending(); pending = null; } });
  function withBrain(fn) { if (brain.target) fn(); else pending = fn; }

  /* ---------------- participants / header ---------------- */
  function part(slot) { return S.parts.filter(function (p) { return p.slot === slot; })[0]; }
  function chip(node, p, isMe) {
    node.textContent = (isMe ? 'you · ' : '') + (p ? FF.labelFor(p) : '…');
    var tone = FF.tone(p);
    node.style.color = tone; node.style.borderColor = tone;
    node.style.background = 'color-mix(in srgb, ' + tone + ' 14%, transparent)';
  }
  function renderHeader() {
    chip($('#me-chip'), part(S.slot), true);
    var other = part(1 - S.slot);
    chip($('#you-chip'), other, false);
    if (!other) { $('#you-chip').textContent = 'waiting for a friend…'; }
    var pres = $('#presence');
    pres.textContent = '';
    S.parts.forEach(function (p) {
      pres.appendChild(el('span', {}, el('i', { class: 'dot ' + (p.connected ? 'live' : 'off') }), p.slot === S.slot ? 'you' : (p.nickname || p.label)));
    });
    var mode = S.room && S.room.mode;
    $('#room-title').textContent = mode === 'flirt' ? 'Flirt chat' : 'Friends chat';
    $('#meter-label').textContent = mode === 'flirt' ? 'Courtship chemistry (pC1 hub)' : 'Social resonance (pC1 hub)';
  }

  /* ---------------- messages ---------------- */
  function nearBottom() { return msgsBox.scrollHeight - msgsBox.scrollTop - msgsBox.clientHeight < 90; }
  function scrollDown(force) { if (force || nearBottom()) msgsBox.scrollTop = msgsBox.scrollHeight; }
  function addSystem(text) { msgsBox.appendChild(el('div', { class: 'sys', text: text })); scrollDown(true); }

  function addMessage(m) {
    if (S.bubbles[m.seq]) return S.bubbles[m.seq];
    var p = part(m.slot), mine = m.slot === S.slot;
    var followMessage = mine || nearBottom();
    var chips = el('div', { class: 'chips' });
    var node = el('div', { class: 'msg ' + (mine ? 'mine' : 'theirs') + (FF.toneIsLight(p) ? ' light' : ''), 'data-seq': m.seq, style: { '--tone': FF.tone(p) } },
      el('div', { class: 'meta' }, el('i'), el('span', { text: (mine ? 'you' : (p ? p.nickname : '?')) + ' · ' + FF.time(m.ts) })),
      el('div', { class: 'bubble', text: m.text }),
      chips);
    msgsBox.appendChild(node);
    S.bubbles[m.seq] = { node: node, chips: chips, slot: m.slot };
    if (m.analysed) fillChips(m.seq, m); else chips.appendChild(el('span', { class: 'chip wait', text: 'fly is reading…' }));
    scrollDown(followMessage);
    return S.bubbles[m.seq];
  }

  /* ---------------- delivery receipts ("seen") — live only, nothing here is ever stored ---------------- */
  function ackSeen() {
    if (document.hidden) return;
    var bestSeq = -1;
    Object.keys(S.bubbles).forEach(function (seq) {
      seq = +seq;
      if (S.bubbles[seq].slot !== S.slot && seq > bestSeq) bestSeq = seq;
    });
    if (bestSeq > S.lastAckedSeq) {
      S.lastAckedSeq = bestSeq;
      socket.emit('seen', { room_id: boot.room_id, seq: bestSeq });
    }
  }
  function updateSeenMark() {
    var prev = FF.$('.seen-mark', msgsBox);
    if (prev) prev.remove();
    var bestSeq = -1;
    Object.keys(S.bubbles).forEach(function (seq) {
      seq = +seq;
      if (S.bubbles[seq].slot === S.slot && seq <= S.partnerSeenUpTo && seq > bestSeq) bestSeq = seq;
    });
    if (bestSeq === -1) return;
    S.bubbles[bestSeq].node.appendChild(el('div', { class: 'seen-mark', text: 'Seen' }));
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) ackSeen(); });
  window.addEventListener('focus', ackSeen);

  var CHANNEL_SHORT = { brake_m1: 'mAL brake', brake_m8: 'mAL brake', banter: 'AVLP banter', attention: 'LC16 vision', confiding: 'lateral horn', vigor: 'FLA input', feedback: 'AN feedback' };
  function fillChips(seq, u) {
    var b = S.bubbles[seq];
    if (!b) return;
    b.chips.textContent = '';
    var c = u.counts || {};
    if (c.new_cells != null) b.chips.appendChild(el('span', { class: 'chip', text: '+' + FF.fmt(c.new_cells) + ' neurons' }));
    if (c.new_edges != null) b.chips.appendChild(el('span', { class: 'chip', text: '+' + FF.fmt(c.new_edges) + ' synapses' }));
    var ch = u.channels && Object.keys(u.channels).sort(function (a, z) { return Math.abs(u.channels[z]) - Math.abs(u.channels[a]); })[0];
    if (ch && Math.abs(u.channels[ch]) > 0.05) {
      var neg = u.channels[ch] < 0;
      b.chips.appendChild(el('span', { class: 'chip' + (neg ? ' warn' : ''), text: (neg ? '⏸ ' : '⚡ ') + CHANNEL_SHORT[ch] }));
    }
  }

  /* ---------------- cockpit ---------------- */
  var paramsBox = $('#params');
  FF.PARAMS.forEach(function (p) {
    paramsBox.appendChild(el('div', { class: 'param ' + p[0], 'data-p': p[0] },
      el('div', { class: 't' }, el('span', { text: p[1] }), el('b', { text: '0.00' })),
      el('div', { class: 'b' }, el('i'))));
  });

  function setState(label, key) {
    var s = $('#state'); s.textContent = label; s.setAttribute('data-s', key);
  }
  function setMeter(x) {
    var pct = Math.round((x || 0) * 100);
    $('#meter-val').textContent = pct + '%';
    $('#meter-fill').style.width = pct + '%';
    $('#meter').setAttribute('aria-valuenow', String(pct));
  }
  function setCounts(c) {
    if (!c) return;
    $('#k-cells').textContent = FF.fmt(c.cells_active);
    $('#k-edges').textContent = FF.fmt(c.edges_active);
    $('#k-new').textContent = '+' + FF.fmt((c.new_cells || 0) + (c.new_edges || 0));
    $('#hud-cells').textContent = FF.fmt(c.cells_active);
    $('#hud-edges').textContent = FF.fmt(c.edges_active);
    if (c.cells_total) $('#hud-total').textContent = FF.fmt(c.cells_total);
  }
  function setParams(params) {
    if (!params) return;
    FF.$$('.param', paramsBox).forEach(function (n) {
      var v = params[n.getAttribute('data-p')] || 0;
      n.querySelector('b').textContent = v.toFixed(2);
      n.querySelector('.b i').style.width = Math.round(v * 100) + '%';
    });
  }
  function setVerdict(ready, counts) {
    var cta = $('#verdict-cta');
    cta.classList.toggle('ready', !!ready);
    cta.setAttribute('aria-disabled', ready ? 'false' : 'true');
    cta.tabIndex = ready ? 0 : -1;
    if (ready) { cta.href = '/verdict/' + boot.room_id; $('#verdict-progress').textContent = ''; }
    else {
      cta.removeAttribute('href');
      var me = counts ? counts[S.slot] || 0 : 0, other = counts ? counts[1 - S.slot] || 0 : 0;
      $('#verdict-progress').textContent = '(you ' + Math.min(me, S.min) + '/' + S.min + ' · partner ' + Math.min(other, S.min) + '/' + S.min + ')';
    }
  }
  function setNarration(n) {
    if (!n) return;
    $('#narration').hidden = false;
    $('#n-region').textContent = n.region;
    $('#n-plain').textContent = n.plain;
    $('#n-tech').textContent = n.technical;
  }
  function setProvider(llm) {
    var box = $('#provider'); box.textContent = '';
    if (!llm) return;
    if (llm.degraded) {
      box.appendChild(el('span', { class: 'deg', text: '⚙ local scorer' }));
      box.appendChild(el('span', { text: 'AI is busy or rate-limited, so the fly used its built-in reader' }));
    } else {
      var model = String(llm.provider || '').replace(/^groq:/, '').replace(/^cache$/, 'cached');
      box.appendChild(el('span', { class: 'ok', text: '✓ AI: ' + model }));
      if (llm.latency_ms) box.appendChild(el('span', { text: llm.latency_ms + ' ms' }));
    }
  }
  function applyUpdate(u) {
    fillChips(u.seq, u);
    S.counts = { 0: +u.message_counts['0'], 1: +u.message_counts['1'] };
    setVerdict(u.verdict_ready, S.counts);
    if (u.seq < S.latestSeq) return;
    S.latestSeq = u.seq;
    setMeter(u.meter); setCounts(u.counts);
    if (u.private) {   // the fly's reading is shown to the author only
      $('#diary').textContent = '“' + (u.diary || '…') + '”';
      $('#topic').textContent = u.topic ? '#' + u.topic : '';
      setParams(u.params); setNarration(u.narration); setProvider(u.llm);
    } else {           // your partner's message: the fly's advice on how you could reply
      var tipEl = $('#nexttip');
      tipEl.hidden = !u.tip; tipEl.textContent = u.tip || '';
    }
    var hot = u.meter >= 0.6;
    setState(hot ? 'resonance' : 'firing', hot ? 'verdict' : 'firing');
    clearTimeout(S.stateTimer);
    S.stateTimer = setTimeout(function () { setState(hot ? 'resonance' : 'idle', hot ? 'verdict' : 'idle'); }, 2600);
  }

  /* ---------------- composer ---------------- */
  function setComposer(enabled, placeholder) {
    input.disabled = !enabled; sendBtn.disabled = !enabled;
    if (placeholder) input.placeholder = placeholder;
  }
  function updateCount() {
    var left = S.maxLen - input.value.length;
    $('#count').textContent = left < 80 ? left + ' left' : '';
    $('#count').style.color = left < 20 ? 'var(--red)' : '';
  }
  input.setAttribute('maxlength', String(S.maxLen));
  input.addEventListener('input', function () {
    updateCount();
    var now = Date.now();
    if (now - S.lastTypingSent > 1800) { socket.emit('typing', { room_id: boot.room_id, on: true }); S.lastTypingSent = now; }
    clearTimeout(S.typingTimer);
    S.typingTimer = setTimeout(function () { socket.emit('typing', { room_id: boot.room_id, on: false }); S.lastTypingSent = 0; }, 2200);
  });
  $('#composer').addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text || input.disabled) return;
    socket.emit('message', { room_id: boot.room_id, text: text });
    input.value = ''; updateCount();
    clearTimeout(S.typingTimer);
    socket.emit('typing', { room_id: boot.room_id, on: false }); S.lastTypingSent = 0;
    input.focus();
  });

  /* ---------------- socket events ---------------- */
  socket.on('connect', function () {
    $('#notice').hidden = true;
    socket.emit('room_join', { room_id: boot.room_id });
  });
  socket.on('disconnect', function () {
    setComposer(false, 'Reconnecting…');
    showNotice('Connection lost. Reconnecting…');
  });

  function showNotice(text, node) {
    var n = $('#notice'); n.textContent = ''; n.appendChild(document.createTextNode(text));
    if (node) n.appendChild(node);
    n.hidden = false;
  }

  socket.on('room_state', function (st) {
    S.room = st.room; S.parts = st.room.participants; S.slot = st.you; S.min = st.min_for_verdict; S.maxLen = st.max_len;
    S.status = st.room.status; S.bubbles = {}; S.latestSeq = -1;
    msgsBox.textContent = '';
    st.messages.forEach(function (m) { addMessage(m); });
    if (!st.messages.length) addSystem('Say hi! Each message lights up real neurons in the fly brain.');
    var last = st.messages.filter(function (m) { return m.analysed && m.slot === S.slot; }).pop();
    if (last) {
      $('#diary').textContent = '“' + last.diary + '”';
      setNarration(last.narration); setParams(last.params);
    }
    var theirs = st.messages.filter(function (m) { return m.analysed && m.slot !== S.slot; }).pop();
    $('#nexttip').hidden = !(theirs && theirs.tip); $('#nexttip').textContent = theirs && theirs.tip ? theirs.tip : '';
    var lastAny = st.messages.filter(function (m) { return m.analysed; }).pop();
    if (lastAny) S.latestSeq = lastAny.seq;
    S.counts = { 0: +st.message_counts['0'], 1: +st.message_counts['1'] };
    setMeter(st.meter); setCounts({ cells_active: st.totals.cells_active, edges_active: st.totals.edges_active, cells_total: st.totals.cells_total, new_cells: 0, new_edges: 0 });
    setVerdict(st.verdict_ready, S.counts);
    withBrain(function () { brain.applyState(st.state.idx, st.state.val, true); });
    renderHeader();
    applyStatus();
    S.ready = true;
    updateCount();
    if (S.status === 'active') input.focus();
    requestAnimationFrame(function () { scrollDown(true); });
    S.lastAckedSeq = -1; ackSeen();
    S.partnerSeenUpTo = (st.room.seen && st.room.seen[String(1 - S.slot)]) != null ? st.room.seen[String(1 - S.slot)] : -1;
    updateSeenMark();
    applyPrivacy(st.room.store_override, st.room.store_override == null ? st.retain_text_default : st.room.store_override);
    $('#nudge').hidden = true;
  });

  function applyStatus() {
    var st = S.status;
    if (st === 'ended') {
      setComposer(false, 'This chat has ended');
      showNotice('This chat has ended. You can still open your verdict.');
    } else if (st === 'waiting' || S.parts.length < 2) {
      setComposer(false, 'Waiting for your friend to join…');
      var code = S.room && S.room.code;
      showNotice(code ? 'Waiting for your friend. Invite code: ' + code : 'Waiting for your friend to join…');
    } else {
      setComposer(true, 'Write a message…');
      $('#notice').hidden = true;
    }
  }

  socket.on('presence', function (p) {
    S.parts = p.participants; if (p.status) S.status = p.status;
    if (S.room) S.room.participants = p.participants;
    renderHeader(); applyStatus();
  });
  socket.on('new_message', function (m) {
    addMessage({ seq: m.seq, slot: m.slot, text: m.text, ts: m.ts, analysed: false });
    S.counts = { 0: +m.message_counts['0'], 1: +m.message_counts['1'] };
    setVerdict(false, S.counts);
    if (m.slot !== S.slot) { $('#typing').textContent = ''; }
    $('#nudge').hidden = true;   // the conversation just moved again
    ackSeen();
  });
  socket.on('seen', function (p) {
    if (p.slot === 1 - S.slot) { S.partnerSeenUpTo = p.seq; updateSeenMark(); }
  });
  socket.on('verdict_nudge', function () { $('#nudge').hidden = false; });
  $('#nudge-dismiss').addEventListener('click', function () { $('#nudge').hidden = true; });
  socket.on('analysing', function () { setState('analysing', 'analysing'); });
  socket.on('fly_update', function (u) { applyUpdate(u); });
  socket.on('brain_wave', function (w) {
    withBrain(function () {
      brain.playActivity(w);
    });
  });
  socket.on('analysis_failed', function (m) {
    var b = S.bubbles[m.seq]; if (b) { b.chips.textContent = ''; b.chips.appendChild(el('span', { class: 'chip warn', text: 'could not analyse' })); }
    setState('idle', 'idle');
  });
  socket.on('typing', function (t) {
    var p = part(t.slot);
    $('#typing').textContent = t.on ? ((p ? p.nickname : 'Partner') + ' is typing…') : '';
  });
  socket.on('room_ended', function (e) {
    S.status = 'ended'; applyStatus();
    var who = part(e.by);
    addSystem((e.by === S.slot ? 'You' : (who ? who.nickname : 'Your partner')) + ' left the chat.');
  });
  socket.on('error_message', function (e) {
    if (e.code === 'forbidden' || e.code === 'not_found') {
      setComposer(false, 'Unavailable');
      showNotice(e.message + ' ', el('a', { href: '/', text: 'Back to home' }));
    } else FF.toast(e.message || 'Something went wrong.', e.code === 'slow_down' ? 'warn' : 'bad');
  });

  /* ---------------- brain size: standard vs full pathway ---------------- */
  if (boot.full_available) {
    var toggle = $('#scale-toggle'), note = $('#scale-note');
    $('#scale-row').hidden = false;
    toggle.checked = boot.scale === 'full';
    if (FlyBrain.weakDevice()) {
      note.hidden = false;
      note.textContent = 'Heads up: this looks like a phone or a low-power device. The bigger brain may run slowly and use more data (about 2 MB).';
    }
    toggle.addEventListener('change', function () {
      var want = toggle.checked ? 'full' : 'standard';
      if (want === 'full' && FlyBrain.weakDevice() && !confirm('This device may struggle with the full pathway (4,300 neurons). Try it anyway? You can switch back at any time.')) {
        toggle.checked = false; return;
      }
      toggle.disabled = true;
      note.hidden = false; note.textContent = 'Switching for both of you…';
      socket.emit('set_scale', { room_id: boot.room_id, scale: want });
    });
    socket.on('scale_changed', function () { window.location.reload(); });
    socket.on('scale_working', function () { FF.toast('Rebuilding the brain, one moment…'); });
    socket.on('error_message', function (e) {
      if (e.code === 'full_busy' || e.code === 'no_full' || e.code === 'bad_scale') { toggle.checked = boot.scale === 'full'; toggle.disabled = false; note.hidden = true; }
    });
  }

  /* ---------------- privacy: does this chat's text get saved? ---------------- */
  var privacyToggle = $('#privacy-toggle'), privacySub = $('#privacy-sub');
  function applyPrivacy(storeOverride, effective) {
    privacyToggle.checked = !!effective;
    privacySub.textContent = storeOverride == null
      ? (effective ? 'Not chosen: this server keeps chat text by default.' : 'Not chosen: this server does not keep chat text by default.')
      : (effective ? 'Chosen: this chat is being saved.' : 'Chosen: this chat is not being saved.');
  }
  privacyToggle.addEventListener('change', function () {
    socket.emit('set_privacy', { room_id: boot.room_id, store: privacyToggle.checked });
  });
  socket.on('privacy_changed', function (p) { applyPrivacy(p.store_override, p.effective); });

  /* Close the native disclosure after a choice, outside click, or Escape. */
  var options = $('#chat-options'), optionsSummary = options.querySelector('summary');
  options.addEventListener('click', function (e) {
    if (e.target.closest('button, a')) {
      options.open = false;
      optionsSummary.focus();
    }
  }, true);
  document.addEventListener('click', function (e) {
    if (!options.contains(e.target)) options.open = false;
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && options.open) {
      options.open = false;
      optionsSummary.focus();
    }
  });

  /* ---------------- safety: skip, block, report ---------------- */
  var prefs = FF.prefs.load();
  if (prefs && prefs.mode) $('#next').hidden = false;   // only for chats found by matching, not invites
  function goNext() {
    var q = prefs ? '/match?mode=' + encodeURIComponent(prefs.mode) + '&seeking=' + encodeURIComponent(prefs.seeking || 'any') +
      (prefs.nick ? '&nick=' + encodeURIComponent(prefs.nick) : '') : '/';
    window.location.href = q;
  }
  $('#next').addEventListener('click', function () {
    if (S.status === 'active' && !confirm('Skip this person and look for someone new?')) return;
    socket.emit('leave_room', { room_id: boot.room_id, skip: true });
    setTimeout(goNext, 250);
  });
  $('#block').addEventListener('click', function () {
    if (!confirm('Block this person? The chat ends and you will never be matched with them again.')) return;
    socket.emit('block_user', { room_id: boot.room_id });
  });
  $('#report').addEventListener('click', function () {
    FF.reportDialog(boot.room_id, function (alsoBlocked) {
      if (alsoBlocked) { socket.emit('leave_room', { room_id: boot.room_id }); setTimeout(function () { window.location.href = '/'; }, 400); }
    });
  });
  socket.on('blocked', function () {
    FF.toast('Blocked. You will not be matched with them again.');
    setTimeout(function () { window.location.href = '/'; }, 900);
  });
  socket.on('message_blocked', function (m) {
    var left = m.strikes_left;
    FF.toast(m.message + (left != null && left > 0 ? ' (' + left + ' warning' + (left === 1 ? '' : 's') + ' left before your chat is ended)' : ''), 'bad', 5200);
    input.focus();
  });
  socket.on('message_masked', function (m) {
    FF.toast(m.reason === 'contact' ? 'We hid contact details: keep chatting here, it is safer.' : 'A few words were starred out to keep things friendly.', 'warn', 3800);
  });

  $('#leave').addEventListener('click', function () {
    if (S.status === 'active' && !confirm('Leave this chat? Your partner will be told, and you can still see the verdict.')) return;
    socket.emit('leave_room', { room_id: boot.room_id });
    setTimeout(function () { window.location.href = '/'; }, 200);
  });

  // "How your words become neurons" legend
  var lt = $('#legend-table');
  lt.appendChild(el('tr', {}, el('td', { text: 'from' }), el('td', { text: 'drives real neurons' })));
  (boot.legend || []).forEach(function (row) {
    lt.appendChild(el('tr', {}, el('td', { text: row.drivers }), el('td', { text: row.population + ' (' + row.cells + ')' })));
  });

  setVerdict(false, S.counts);
  setComposer(false, 'Connecting…');
  updateCount();
})();
