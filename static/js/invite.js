(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$;
  FF.prefs.save(null);   // an invite chat has no 'next person'
  var socket = FF.socket(), invite = null, wantCode = false;

  if (window.FlyBrain) new FlyBrain($('#bg'), { interactive: false, autoRotate: true, rotateSpeed: 0.0012, distance: 2.6, rest: 0.28 });

  function url() { return location.origin + invite.path; }
  function err(t) { $('#err').textContent = t || ''; }

  function showCode(p) {
    invite = p; wantCode = true;
    $('#code').textContent = p.code; $('#link').textContent = url();
    $('#ttl').textContent = Math.max(1, Math.round((p.expires_in || 1800) / 60));
    $('#choose').hidden = true; $('#code-form').hidden = true; $('#mine').hidden = false;
    $('#title').textContent = 'Your code is ready'; $('#sub').textContent = 'Send it to your buddy.';
    if (navigator.share) $('#share').hidden = false;
  }

  $('#make').addEventListener('click', function () { err(); socket.emit('invite_create', { mode: 'friends', nickname: boot.nick }); });
  $('#have').addEventListener('click', function () {
    $('#choose').hidden = true; $('#code-form').hidden = false; $('#code-in').focus();
    $('#sub').textContent = 'Type the 6-character code your buddy sent you.';
  });
  $('#code-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var raw = $('#code-in').value.replace(/[\s\-_]/g, '').toUpperCase();
    if (raw.length !== 6) return err('Invite codes have 6 characters.');
    window.location.href = '/join/' + encodeURIComponent(raw) + '?go=1' + (boot.nick ? '&nick=' + encodeURIComponent(boot.nick) : '');
  });
  $('#copy-code').addEventListener('click', function () { if (invite) FF.copy(invite.code, 'Code copied!'); });
  $('#copy-link').addEventListener('click', function () { if (invite) FF.copy(url(), 'Link copied!'); });
  $('#share').addEventListener('click', function () {
    if (invite) FF.share({ title: 'Chat with me on Fly//Flirt', text: 'Join my chat! Code: ' + invite.code, url: url() });
  });
  $('#cancel').addEventListener('click', function () { wantCode = false; socket.emit('queue_cancel'); });   // discards the code

  socket.on('connect', function () {
    $('#cdot').className = 'dot live'; $('#cstate').textContent = 'connected';
    if (wantCode) socket.emit('invite_create', { mode: 'friends', nickname: boot.nick });   // same code after a reconnect
  });
  socket.on('disconnect', function () { $('#cdot').className = 'dot off'; $('#cstate').textContent = 'reconnecting…'; });
  socket.on('invite_created', showCode);
  socket.on('queue_cancelled', function () { window.location.href = '/'; });
  socket.on('matched', function (m) {
    $('#radar').classList.add('found'); $('#title').textContent = 'Your buddy joined! 🎉';
    setTimeout(function () { window.location.href = '/chat/' + m.room_id; }, 700);
  });
  socket.on('error_message', function (e) { err(e.message || 'Something went wrong.'); });
})();
