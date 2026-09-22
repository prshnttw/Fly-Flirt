(function () {
  'use strict';
  var FF = window.FF, boot = FF.boot(), $ = FF.$, el = FF.el;
  var TIER = { resonance: 'Strong resonance', warming: 'Warming up', quiet: 'Quiet circuit', friction: 'Friction' };
  var ROLES = ['accumulator', 'output', 'brake', 'input', 'context'];

  FF.api('/api/card/' + encodeURIComponent(boot.token)).then(function (r) {
    if (!r.ok) { $('#loading').hidden = true; $('#failure').hidden = false; return; }
    render(r.data);
  });

  function render(c) {
    $('#loading').hidden = true; $('#content').hidden = false;
    var t = $('#tier'); t.textContent = TIER[c.tier] || c.tier; t.className = 'tier ' + c.tier;
    $('#headline').textContent = c.headline;
    $('#b-cells').textContent = FF.fmt(c.totals.cells_active);
    $('#b-cells-of').textContent = 'of ' + FF.fmt(c.totals.cells_total) + ' in the circuit';
    $('#b-edges').textContent = FF.fmt(c.totals.edges_active);
    $('#b-edges-of').textContent = 'of ' + FF.fmt(c.totals.edges_total) + ' real synapse pairs';
    $('#b-meter').textContent = FF.pct(c.meter);
    $('#b-peak').textContent = 'peak ' + FF.pct(c.peak_meter);
    $('#b-msgs').textContent = String(c.messages);
    $('#disclaimer').textContent = c.disclaimer;

    var box = $('#rolebars');
    ROLES.forEach(function (role) {
      var r = c.by_role[role]; if (!r) return;
      box.appendChild(el('div', { class: 'rolebar' },
        el('div', { class: 't' }, el('span', {}, el('i', { style: { background: FlyBrain.ROLE_HEX[role] } }), FlyBrain.ROLE_LABEL[role]),
          el('span', { class: 'mono', text: FF.fmt(r.active) + ' / ' + FF.fmt(r.total) })),
        el('div', { class: 'b' }, el('i', { style: { width: Math.round((r.total ? r.active / r.total : 0) * 100) + '%', background: FlyBrain.ROLE_HEX[role] } }))));
    });
    var pb = $('#sigpaths');
    (c.signature_pathways || []).forEach(function (p) {
      pb.appendChild(el('div', { class: 'path' }, el('span', { text: p.from }), el('span', { class: 'arrow', text: '→' }), el('span', { text: p.to }),
        el('span', { class: 'n', text: FF.fmt(p.connections) + ' connections' })));
    });

    var legend = $('#legend');
    ['brake', 'input', 'accumulator', 'output', 'context'].forEach(function (r) {
      legend.appendChild(el('span', {}, el('i', { style: { background: FlyBrain.ROLE_HEX[r] } }), FlyBrain.ROLE_LABEL[r].split(' (')[0]));
    });
    var brain = new FlyBrain($('#brain'), { autoRotate: true, distance: 2.3, rest: 0.14, scale: c.scale });
    brain.ready.then(function (ok) {
      if (!ok) return;
      var byRole = {};
      c.active_cells.forEach(function (i) { var role = brain.data.roles[brain.data.role[i]]; (byRole[role] = byRole[role] || []).push(i); });
      brain.paint(Object.keys(byRole).map(function (role) { return { cells: byRole[role], color: FlyBrain.ROLE_HEX[role], level: 1 }; }), { rest: 0.14 });
    });
  }
})();
