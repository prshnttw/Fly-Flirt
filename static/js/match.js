(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$;
  var socket = FF.socket();
  FF.prefs.save({ mode: boot.mode, seeking: boot.seeking, nick: boot.nick });
  var joinedAt = Date.now(), timeout = boot.timeout || 120, matched = false, inviteData = null, timedOut = false, retrying = false;

  if (window.FlyBrain) {
    var brain = new FlyBrain($('#bg'), { interactive: false, autoRotate: true, rotateSpeed: 0.0012, distance: 2.6, rest: 0.28 });
    
  }

  function setConn(state) {
    var dot = $('#cdot'), text = $('#cstate');
    dot.className = 'dot ' + (state === 'ok' ? 'live' : 'off');
    text.textContent = state === 'ok' ? 'connected, scanning live' : (state === 'wait' ? 'reconnecting…' : 'connecting…');
  }

  function render() {
    var elapsed = Math.floor((Date.now() - joinedAt) / 1000);
    if (elapsed >= timeout) { elapsed = timeout; if (!timedOut) giveUp(); }
    $('#elapsed').textContent = FF.clock(elapsed);
    $('#prog').style.width = Math.min(100, (elapsed / timeout) * 100) + '%';
  }
  setInterval(function () { if (!matched && !timedOut) render(); }, 500);

  function giveUp() {
    timedOut = true;
    $('#radar').classList.remove('found');
    $('#radar').classList.add('idle');
    $('#title').textContent = 'No match this time';
    $('#sub').textContent = 'Nobody was online within ' + FF.clock(timeout) + '. Try again, or invite a friend with a code.';
    $('#retry').hidden = false;
    if (!inviteData) $('#invite-btn').hidden = false;
    socket.emit('queue_cancel', { keep_invite: true });   // stop scanning; an invite code stays valid
  }

  function retry() {
    retrying = true; timedOut = false;
    $('#retry').hidden = true;
    socket.emit('queue_cancel');
  }
  function restartSearch() {
    retrying = false; joinedAt = Date.now();
    $('#radar').classList.remove('idle');
    $('#title').textContent = 'Scanning…';
    $('#sub').textContent = 'Scanning for people online right now.';
    $('#invite').hidden = true; inviteData = null; $('#invite-btn').hidden = false;
    render();
    socket.emit('queue_join', { mode: boot.mode, seeking: boot.seeking, nickname: boot.nick });
  }

  function showInvite(p) {
    inviteData = p;
    var url = location.origin + p.path;
    $('#code').textContent = p.code;
    $('#link').textContent = url;
    $('#ttl').textContent = Math.max(1, Math.round((p.expires_in || 1800) / 60));
    $('#invite').hidden = false;
    $('#invite-btn').hidden = true;
    if (navigator.share) $('#share').hidden = false;
  }

  socket.on('connect', function () {
    setConn('ok');
    if (timedOut) return;
    socket.emit('queue_join', { mode: boot.mode, seeking: boot.seeking, nickname: boot.nick });
    if (inviteData) socket.emit('invite_create', { mode: boot.mode, nickname: boot.nick });
  });
  socket.on('disconnect', function () { setConn('wait'); });
  socket.on('connect_error', function () { setConn('wait'); });

  socket.on('queue_waiting', function (s) {
    if (timedOut) return;
    if (typeof s.elapsed === 'number') joinedAt = Date.now() - s.elapsed * 1000;
    timeout = s.timeout || timeout;
    applyCounts(s.counts);
  });
  socket.on('queue_status', function (s) {
    if (timedOut || retrying) return;
    joinedAt = Date.now() - s.elapsed * 1000;
    timeout = s.timeout || timeout;
    applyCounts(s.counts, s.online);
  });
  function applyCounts(c, online) {
    if (!c) return;
    $('#c-male').textContent = c.male; $('#c-female').textContent = c.female; $('#c-friends').textContent = c.friends;
    if (online != null) $('#c-online').textContent = online;
  }

  socket.on('invite_suggested', function (p) {
    if (retrying) return;
    showInvite(p);
    if (!timedOut) giveUp();
    $('#title').textContent = 'No match this time. Bring a friend?';
    $('#sub').textContent = 'Nobody was online within ' + FF.clock(timeout) + '. Use the code below to invite someone, or try again.';
    FF.toast('No match yet. Here is an invite code for a friend.', 'warn', 4500);
  });
  socket.on('invite_created', function (p) { showInvite(p); });

  socket.on('matched', function (m) {
    matched = true;
    $('#radar').classList.add('found');
    $('#title').textContent = 'Match found! 🎉';
    $('#sub').textContent = 'Connecting you with ' + (m.partner && m.partner.nickname ? m.partner.nickname : 'your partner') + '…';
    $('#prog').style.width = '100%';
    setTimeout(function () { window.location.href = '/chat/' + m.room_id; }, 900);
  });

  socket.on('error_message', function (e) { FF.toast(e.message || 'Something went wrong.', 'bad'); });

  $('#invite-btn').addEventListener('click', function () {
    socket.emit('invite_create', { mode: boot.mode, nickname: boot.nick });
  });
  $('#copy-code').addEventListener('click', function () { if (inviteData) FF.copy(inviteData.code, 'Code copied!'); });
  $('#copy-link').addEventListener('click', function () { if (inviteData) FF.copy(location.origin + inviteData.path, 'Link copied!'); });
  $('#share').addEventListener('click', function () {
    if (inviteData) FF.share({ title: 'Chat with me on Fly//Flirt', text: 'Join my chat, a real fly brain will read it! Code: ' + inviteData.code, url: location.origin + inviteData.path });
  });
  $('#cancel').addEventListener('click', function () {
    socket.emit('queue_cancel');
    setTimeout(function () { window.location.href = '/'; }, 150);
  });
  socket.on('queue_cancelled', function () {
    if (retrying) return restartSearch();
    if (!timedOut) window.location.href = '/';
  });
  $('#retry').addEventListener('click', retry);

  render();
})();
