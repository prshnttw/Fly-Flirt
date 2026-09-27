(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$, el = FF.el;
  var tip = $('#tip-hover'), socket = FF.socket();

  var legend = $('#l-legend');
  ['brake', 'input', 'accumulator', 'output'].forEach(function (r) {
    legend.appendChild(el('span', { style: { marginRight: '.7rem' } }, el('i', { style: { display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', marginRight: '.3rem', background: FlyBrain.ROLE_HEX[r] } }), FlyBrain.ROLE_LABEL[r].split(' (')[0]));
  });

  var scale = new URLSearchParams(location.search).get('scale') !== 'standard' && boot.full_available ? 'full' : 'standard';
  var brain = new FlyBrain($('#brain'), {
    autoRotate: true, distance: 2.3, interactive: true, scale: scale,
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

  var toggle = $('#lab-scale-toggle');
  toggle.checked = scale === 'full';
  toggle.disabled = !boot.full_available;
  toggle.addEventListener('change', function () {
    var url = new URL(location.href);
    url.searchParams.set('scale', toggle.checked ? 'full' : 'standard');
    location.href = url.toString();
  });

  socket.on('connect', function () { socket.emit('lab_subscribe', { scale: scale }); });
  socket.on('disconnect', function () {
    $('#lab-empty').hidden = false;
    $('#lab-status-title').textContent = 'Reconnecting to the live lab';
    $('#lab-status-copy').textContent = 'Live activity will resume when the connection returns.';
  });
  socket.on('lab_stats', apply);
  socket.on('lab_pulse', function (p) {
    $('#lab-empty').hidden = true;
    Promise.all([brain.ready, FlyBrain.mapActivity(p, scale)]).then(function (result) {
      // Overlay every conversation's transient firing; one chat must not replace another's state.
      if (result[0]) brain.playWave(result[1].frames);
    }).catch(function () { FF.toast('Could not load live activity. Refresh to reconnect.', 'warn'); });
  });

  function apply(s) {
    $('#l-online').textContent = FF.fmt(s.online);
    $('#l-chats').textContent = FF.fmt(s.chats_active);
    if (!socket.connected) return;
    $('#lab-empty').hidden = s.chats_active > 0;
    $('#lab-status-title').textContent = 'Waiting for a conversation';
    $('#lab-status-copy').textContent = 'Waiting for messages across all chats. Activity appears here as conversations happen.';
  }
  setInterval(function () {
    if (document.hidden) return;
    FF.api('/api/stats').then(function (r) { if (r.ok) apply(r.data); });
  }, 6000);
})();
