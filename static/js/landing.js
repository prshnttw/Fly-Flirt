(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot();

  // Ambient 3D brain behind the card. It stays dark until it receives
  // genuine activity from every live conversation (anonymous: only cell indices, never text).
  var brain = null, socket = null;
  if (window.FlyBrain) {
    brain = new FlyBrain(FF.$('#bg'), { interactive: false, autoRotate: true, rotateSpeed: 0.0014, distance: 2.5, rest: 0.3 });
    brain.ready.then(function (ok) {
      if (!ok) return;
      try {
        socket = FF.socket();
        socket.on('connect', function () { socket.emit('lab_subscribe'); });
        socket.on('lab_pulse', function (p) {
          brain.playWave((p.frames || []).map(function (f) { return { cells: f.cells, acts: f.acts, edges: [] }; }), { gap: 220 });
        });
      } catch (e) { /* the page works fine without the live feed */ }
    });
  }

  // Nickname is required everywhere: pre-fill a random one so it's never actually empty, and offer a reroll.
  var nickInput = FF.$('#nick');
  FF.wireNickname(nickInput, FF.$('#nick-dice'));

  // "Enter chat": ask who you are, then who you'd like to meet, then start scanning.
  var steps = ['home-step', 'gender-step', 'seek-step'], picked = { gender: null };
  function show(id) { steps.forEach(function (s) { FF.$('#' + s).hidden = s !== id; }); }
  function start(gender, seeking) {
    var nick = FF.requireNickname(nickInput);
    if (!nick) return;
    window.location.href = '/match?mode=' + gender + '&seeking=' + seeking + '&nick=' + encodeURIComponent(nick);
  }
  FF.$('#go-enter').addEventListener('click', function () { show('gender-step'); });
  FF.$('#go-invite').addEventListener('click', function (e) {
    e.preventDefault();
    var nick = FF.requireNickname(nickInput);
    if (!nick) return;
    window.location.href = '/invite?nick=' + encodeURIComponent(nick);
  });
  FF.$$('[data-gender]').forEach(function (b) {
    b.addEventListener('click', function () {
      picked.gender = b.getAttribute('data-gender');
      if (picked.gender === 'friends') return start('friends', 'any');   // no label, so no preference to ask
      show('seek-step');
    });
  });
  FF.$$('[data-seek]').forEach(function (b) {
    b.addEventListener('click', function () { start(picked.gender, b.getAttribute('data-seek')); });
  });
  FF.$$('[data-back]').forEach(function (b) {
    b.addEventListener('click', function () { show(b.getAttribute('data-back')); });
  });

  // Live counters
  function refresh() {
    FF.api('/api/stats').then(function (r) {
      if (!r.ok) return;
      FF.$('#s-online').textContent = FF.fmt(r.data.online);
      FF.$('#s-waiting').textContent = FF.fmt(r.data.waiting_total);
      FF.$('#s-today').textContent = FF.fmt(r.data.chats_today);
    });
  }
  setInterval(function () { if (!document.hidden) refresh(); }, 8000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
})();
