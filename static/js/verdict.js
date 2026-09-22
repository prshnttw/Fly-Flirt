(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$, el = FF.el;
  var TIER = { resonance: 'Strong resonance', warming: 'Warming up', quiet: 'Quiet circuit', friction: 'Friction' };
  var PARAM_ORDER = ['warmth', 'humor', 'reciprocity', 'curiosity', 'disclosure', 'energy', 'tension'];
  var V = null, brain = null, selected = -1, owner = {}, cards = [];

  function fail(msg) {
    $('#loading').hidden = true; $('#content').hidden = true;
    $('#fail-msg').textContent = msg; $('#failure').hidden = false;
  }

  function load(attempt) {
    FF.api('/api/rooms/' + boot.room_id + '/verdict').then(function (r) {
      if (r.ok) return render(r.data);
      if (r.status === 409 && r.data.error === 'analysing' && attempt < 25) {
        $('#load-sub').textContent = 'Still reading the latest messages…';
        return setTimeout(function () { load(attempt + 1); }, 1200);
      }
      if (r.status === 409) return fail(r.data.message || 'Send a few more messages first.');
      fail(r.data.message || 'Could not load the verdict.');
    }).catch(function () { fail('Could not reach the server. Please try again.'); });
  }

  function render(v) {
    V = v;
    $('#loading').hidden = true; $('#content').hidden = false;
    var t = $('#tier'); t.textContent = TIER[v.tier] || v.tier; t.className = 'tier ' + v.tier;
    $('#headline').textContent = v.headline;
    $('#b-cells').textContent = FF.fmt(v.totals.cells_active);
    $('#b-cells-of').textContent = 'of ' + FF.fmt(v.totals.cells_total) + ' in the circuit';
    $('#b-edges').textContent = FF.fmt(v.totals.edges_active);
    $('#b-edges-of').textContent = 'of ' + FF.fmt(v.totals.edges_total) + ' real synapse pairs';
    $('#b-meter').textContent = FF.pct(v.meter);
    $('#b-peak').textContent = 'peak ' + FF.pct(v.peak_meter);
    $('#b-msgs').textContent = String(v.messages_analysed);
    $('#b-dur').textContent = v.duration_s >= 60 ? Math.round(v.duration_s / 60) + ' min chat' : v.duration_s + ' s chat';

    rolebars(v); toptypes(v); paths($('#sigpaths'), v.signature_pathways, true); messageCards(v); share(v); method(v);
    v.messages.forEach(function (m, i) { m.footprint.cells.forEach(function (c) { owner[c] = i; }); });
    setupBrain(v);
  }

  function rolebars(v) {
    var box = $('#rolebars');
    ['accumulator', 'output', 'brake', 'input', 'context'].forEach(function (role) {
      var r = v.by_role[role]; if (!r) return;
      var frac = r.total ? r.active / r.total : 0;
      box.appendChild(el('div', { class: 'rolebar' },
        el('div', { class: 't' }, el('span', {}, el('i', { style: { background: FlyBrain.ROLE_HEX[role] } }), FlyBrain.ROLE_LABEL[role]),
          el('span', { class: 'mono', text: FF.fmt(r.active) + ' / ' + FF.fmt(r.total) })),
        el('div', { class: 'b' }, el('i', { style: { width: Math.round(frac * 100) + '%', background: FlyBrain.ROLE_HEX[role] } }))));
    });
  }

  function toptypes(v) {
    var box = $('#toptypes');
    v.top_types.slice(0, 8).forEach(function (t) {
      box.appendChild(el('div', { class: 'path' },
        el('i', { style: { width: '9px', height: '9px', borderRadius: '50%', background: FlyBrain.ROLE_HEX[t.role], display: 'inline-block' } }),
        el('span', { text: t.type }), el('span', { class: 'n', text: t.active + ' / ' + t.total + ' cells' })));
    });
    if (!v.top_types.length) box.appendChild(el('div', { class: 'muted', text: 'Nothing crossed the activation threshold yet.' }));
  }

  function paths(box, list, withFlux) {
    box.textContent = '';
    (list || []).forEach(function (p) {
      box.appendChild(el('div', { class: 'path' },
        el('span', { text: p.from }), el('span', { class: 'arrow', text: '→' }), el('span', { text: p.to }),
        el('span', { class: 'n', text: FF.fmt(p.connections) + ' connections' })));
    });
    if (!list || !list.length) box.appendChild(el('div', { class: 'muted', text: 'No connection crossed the activation threshold.' }));
  }

  function messageCards(v) {
    var box = $('#cards');
    v.messages.forEach(function (m, i) {
      var color = FF.msgHex(i);
      var mini = el('div', { class: 'mini' });
      PARAM_ORDER.forEach(function (name) {
        var val = (m.params && m.params[name]) || 0;
        mini.appendChild(el('div', { title: name + ' ' + val.toFixed(2) },
          el('div', { class: 'b' + (name === 'tension' ? ' tension' : '') }, el('i', { style: { height: Math.round(val * 100) + '%' } })),
          el('small', { text: name.slice(0, 4) })));
      });
      var facts = el('div', { class: 'facts' },
        el('span', { class: 'chip', text: FF.fmt(m.responsible_cells) + ' neurons' }),
        el('span', { class: 'chip', text: FF.fmt(m.responsible_edges) + ' connections' }),
        el('span', { class: 'chip', text: Math.round(m.share * 100) + '% of the activity' }));
      if (m.recruited_cells) facts.appendChild(el('span', { class: 'chip', text: '+' + FF.fmt(m.recruited_cells) + ' first recruited' }));
      m.channels.forEach(function (c) {
        facts.appendChild(el('span', { class: 'chip', text: (c.strength < 0 ? '⏸ ' : '⚡ ') + c.population }));
      });
      var plist = el('div', { class: 'path-list' });
      m.pathways.slice(0, 3).forEach(function (p) {
        plist.appendChild(el('div', { text: p.from + ' → ' + p.to + '  (' + p.connections + ')' }));
      });
      var card = el('button', { class: 'mcard', type: 'button', style: { '--c': color }, 'aria-pressed': 'false', onclick: function () { select(i); } },
        el('div', { class: 'stripe' }),
        el('div', { class: 'body' },
          el('div', { class: 'top' }, el('b', { text: '#' + (i + 1) }), el('span', { text: m.nickname + ' (' + m.label + ')' }),
            el('span', { text: 'hub → ' + FF.pct(m.meter) })),
          el('div', { class: 'text', text: m.text }), facts, mini, plist));
      cards.push(card);
      box.appendChild(card);
    });
  }

  function share(v) {
    $('#share-url').value = v.share_url;
    $('#open-card').href = v.share_url;
    $('#copy').addEventListener('click', function () { FF.copy(v.share_url, 'Link copied!'); });
    if (navigator.share) {
      $('#share').hidden = false;
      $('#share').addEventListener('click', function () {
        FF.share({ title: 'My Fly//Flirt result', text: v.headline + ' ' + FF.fmt(v.totals.cells_active) + ' neurons and ' + FF.fmt(v.totals.edges_active) + ' connections lit up.', url: v.share_url });
      });
    }
  }

  function method(v) {
    $('#method').textContent = v.method;
    $('#disclaimer').textContent = v.disclaimer;
    var rows = $('#legend-rows');
    v.channel_legend.forEach(function (r) {
      rows.appendChild(el('tr', {}, el('td', { text: r.drivers }), el('td', { text: r.population }), el('td', { text: r.cells })));
    });
  }

  /* ---------------- 3D replay ---------------- */
  function setupBrain(v) {
    var tip = $('#tip-hover');
    brain = new FlyBrain($('#brain'), {
      autoRotate: true, distance: 2.3, rest: 0.16, scale: boot.scale,
      onHover: function (i, pos) {
        if (i == null) { tip.hidden = true; return; }
        var d = brain.describe(i), who = owner[i];
        tip.textContent = d.type + ' · ' + d.role + (who != null ? ' · message #' + (who + 1) : '');
        tip.style.left = pos.x + 'px'; tip.style.top = pos.y + 'px'; tip.hidden = false;
      }
    });
    brain.ready.then(function (ok) { if (ok) paintAll(); });
    $('#show-all').addEventListener('click', function () { select(-1); });
  }

  function groupsAll() {
    return V.messages.map(function (m, i) { return { cells: m.footprint.cells, color: FF.msgHex(i), level: 1 }; });
  }
  function paintAll() {
    if (!brain.target) return;
    brain.paint(groupsAll(), { rest: 0.16 });
    brain.focusEdges([], '#ffffff');
    $('#hud-sel').textContent = 'every colour = one message';
    $('#show-all').hidden = true;
  }

  function select(i) {
    if (!brain || !brain.target) return;
    if (i === selected || i < 0) {
      selected = -1;
      cards.forEach(function (c) { c.classList.remove('on'); c.setAttribute('aria-pressed', 'false'); });
      paintAll();
      return;
    }
    selected = i;
    var m = V.messages[i], color = FF.msgHex(i);
    cards.forEach(function (c, k) { c.classList.toggle('on', k === i); c.setAttribute('aria-pressed', k === i ? 'true' : 'false'); });
    var others = [];
    V.messages.forEach(function (mm, k) { if (k !== i) others = others.concat(mm.footprint.cells); });
    brain.paint([{ cells: others, color: '#6f6a92', level: 0.22 }, { cells: m.footprint.cells, color: color, level: 1 }], { rest: 0.1 });
    brain.focusEdges(m.footprint.edges, color);
    $('#hud-sel').textContent = 'message #' + (i + 1) + ': ' + FF.fmt(m.responsible_cells) + ' neurons, ' + FF.fmt(m.responsible_edges) + ' connections';
    $('#show-all').hidden = false;
    var box = $('#brain').parentElement; if (box && box.scrollIntoView && window.innerWidth < 1000) box.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  $('#report').addEventListener('click', function () { FF.reportDialog(boot.room_id); });

  load(0);
})();
