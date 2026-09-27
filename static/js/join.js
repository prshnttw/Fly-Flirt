(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$;
  FF.prefs.save(null);   // an invite chat has no 'next person'
  var socket = FF.socket(), busy = false, watchdog = null, auto = /[?&]go=1(&|$)/.test(location.search);
  if (boot.nick) $('#nick').value = boot.nick;
  FF.wireNickname($('#nick'), $('#nick-dice'));   // fills a random one only if still empty

  var REASONS = {
    invalid: 'That code is not valid.',
    not_found: 'This invite has expired or was already used. Ask your friend for a new one.',
    full: 'Someone already joined this chat.',
    expired: 'This invite has expired. Ask your friend for a new one.',
    own: 'This is your own invite code. Send it to a friend instead.',
    rate_limited: 'Too many attempts. Please wait a minute and try again.'
  };

  function join() {
    if (busy) return;
    var nick = FF.requireNickname($('#nick'));
    if (!nick) return;
    busy = true;
    $('#err').textContent = '';
    $('#join').disabled = true;
    $('#join').textContent = 'Joining…';
    socket.emit('invite_join', { code: boot.code, nickname: nick });
    clearTimeout(watchdog);
    watchdog = setTimeout(function () {
      if (!busy) return;
      reset();
      $('#err').textContent = 'No answer from the server. Check your connection and press Join again.';
    }, 8000);
  }
  function reset() { busy = false; $('#join').disabled = false; $('#join').textContent = 'Join the chat'; }

  socket.on('matched', function (m) {
    busy = true; clearTimeout(watchdog);
    $('#join').textContent = 'Connected ✓';
    window.location.href = '/chat/' + m.room_id;
  });
  socket.on('invite_error', function (e) {
    clearTimeout(watchdog); reset();
    $('#err').textContent = REASONS[e.reason] || 'Could not join this chat.';
  });
  socket.on('error_message', function (e) { reset(); $('#err').textContent = e.message || 'Something went wrong.'; });
  socket.on('connect_error', function () { $('#err').textContent = 'Connecting…'; });
  // Arrived from the landing page with a code already typed: join straight away.
  socket.on('connect', function () {
    if (auto) { auto = false; join(); }
  });

  $('#join').addEventListener('click', join);
  $('#nick').addEventListener('keydown', function (e) { if (e.key === 'Enter') join(); });
})();
