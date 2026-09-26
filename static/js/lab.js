(function () {
  'use strict';
  var FF = window.FF, $ = FF.$, el = FF.el;
  var tip = $('#tip-hover'), socket = FF.socket();

  var legend = $('#l-legend');
  ['brake', 'input', 'accumulator', 'output'].forEach(function (r) {
    legend.appendChild(el('span', { style: { marginRight: '.7rem' } }, el('i', { style: { display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', marginRight: '.3rem', background: FlyBrain.ROLE_HEX[r] } }), FlyBrain.ROLE_LABEL[r].split(' (')[0]));
  });

  var brain = new FlyBrain($('#brain'), {
    autoRotate: true, rotateSpeed: 0.0014, distance: 1.55, rest: 0.5,
    onHover: function (i, pos) {
      if (i == null) { tip.hidden = true; return; }
      var d = brain.describe(i);
      tip.textContent = d.type + ' · ' + d.role + ' · ' + d.nt;
      tip.style.position = 'fixed';
      var rect = brain.renderer.domElement.getBoundingClientRect();
      tip.style.left = (rect.left + pos.x) + 'px'; tip.style.top = (rect.top + pos.y) + 'px'; tip.hidden = false;
    }
  });
  brain.ready.then(function (ok) {
    if (!ok) return;
  });

  socket.on('connect', function () { socket.emit('lab_subscribe'); });
  socket.on('disconnect', function () {
    $('#lab-empty').hidden = false;
    $('#lab-status-title').textContent = 'Reconnecting to the live lab';
    $('#lab-status-copy').textContent = 'Live activity will resume when the connection returns.';
  });
  socket.on('lab_stats', apply);
  socket.on('lab_pulse', function (p) {
    $('#lab-empty').hidden = true;
    if (!brain.target) return;
    brain.playWave((p.frames || []).map(function (f) { return { cells: f.cells, acts: f.acts, edges: [] }; }), { gap: 220 });
  });

  function apply(s) {
    $('#l-online').textContent = FF.fmt(s.online);
    $('#l-chats').textContent = FF.fmt(s.chats_active);
    if (!socket.connected) return;
    $('#lab-empty').hidden = s.chats_active > 0;
    $('#lab-status-title').textContent = 'Waiting for a conversation';
    $('#lab-status-copy').textContent = 'The brain is at rest. Start a chat or invite a friend to see messages light up real neural pathways.';
  }
  setInterval(function () {
    if (document.hidden) return;
    FF.api('/api/stats').then(function (r) { if (r.ok) apply(r.data); });
  }, 6000);
})();
