/* FlyBrain: real-time 3D view of the MaleCNS connectome subgraph.
 *
 * - each neuron is one crisp dot in a schematic bilateral matrix (1 draw call)
 * - a chat message plays back as a wave: cells brighten in their role colour, and signal
 *   pulses travel along the real synapses that message drove
 * - a persistent glow shows the room's current activation
 * - paint()/focusEdges() colour cells/synapses by the message responsible (verdict page)
 */
(function (global) {
  'use strict';

  var ROLE_NAMES = ['context', 'brake', 'input', 'accumulator', 'output'];
  var ROLE_RGB = {
    context: [0.42, 0.38, 0.7],
    brake: [0.0, 0.96, 0.83],
    input: [0.66, 0.5, 1.0],
    accumulator: [1.0, 0.72, 0.01],
    output: [1.0, 0.0, 0.5]
  };
  var ROLE_HEX = { context: '#6b60b3', brake: '#00f5d4', input: '#a880ff', accumulator: '#ffb703', output: '#ff007f' };
  var ROLE_LABEL = {
    context: 'Recruited partner cells',
    brake: 'mAL brake (GABA)',
    input: 'Input populations',
    accumulator: 'pC1 courtship hub',
    output: 'Descending output'
  };
  var BASE_SIZE = { context: 0.0105, brake: 0.02, input: 0.0165, accumulator: 0.0195, output: 0.03 };
  var LINK_VERTICES = 2;
  var MAX_ACTIVE = 180;
  var MAX_PULSES = 280;

  var dataPromises = {};
  var SCRIPT_FILES = { standard: '/static/connectome.json', full: '/static/connectome_full.json' };
  function loadConnectome(scale) {
    scale = scale === 'full' ? 'full' : 'standard';
    if (!dataPromises[scale]) {
      dataPromises[scale] = fetch(SCRIPT_FILES[scale], { credentials: 'same-origin' })
        .then(function (r) {
          if (!r.ok) throw new Error('connectome HTTP ' + r.status);
          return r.json();
        })
        .then(function (raw) {
          var n = raw.cells.id.length;
          // Center display coordinates only; neuron indices and connectivity stay intact.
          var positions = Float32Array.from(raw.cells.pos);
          var low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
          for (var i = 0; i < positions.length; i++) {
            var axis = i % 3;
            low[axis] = Math.min(low[axis], positions[i]);
            high[axis] = Math.max(high[axis], positions[i]);
          }
          var radius = 0;
          for (var j = 0; j < positions.length; j += 3) {
            for (var k = 0; k < 3; k++) positions[j + k] -= (low[k] + high[k]) / 2;
            radius = Math.max(radius, Math.hypot(positions[j], positions[j + 1], positions[j + 2]));
          }
          return {
            n: n,
            ids: raw.cells.id,
            pos: positions,
            radius: radius,
            role: Uint8Array.from(raw.cells.role),
            type: Uint16Array.from(raw.cells.type),
            nt: Uint8Array.from(raw.cells.nt),
            src: Uint32Array.from(raw.edges.src),
            dst: Uint32Array.from(raw.edges.dst),
            w: Uint16Array.from(raw.edges.w),
            types: raw.types,
            roles: raw.roles,
            nts: raw.nts,
            meta: raw.meta
          };
        });
    }
    return dataPromises[scale];
  }

  // Map by stable neuron identities, never by indices from a different graph.
  var activityMaps = {};
  function mapActivity(wave, destination) {
    var source = wave.scale || 'standard';
    if (source === destination) return Promise.resolve(wave);
    var key = source + ':' + destination;
    if (!activityMaps[key]) activityMaps[key] = Promise.all([loadConnectome(source), loadConnectome(destination)]).then(function (graphs) {
      var from = graphs[0], to = graphs[1], ids = new Map(), edges = new Map();
      to.ids.forEach(function (id, i) { ids.set(id, i); });
      for (var e = 0; e < to.src.length; e++) edges.set(to.src[e] + ':' + to.dst[e], e);
      var cells = from.ids.map(function (id) { return ids.has(id) ? ids.get(id) : -1; });
      var links = Array.from(from.src, function (src, e) {
        var mapped = edges.get(cells[src] + ':' + cells[from.dst[e]]);
        return mapped === undefined ? -1 : mapped;
      });
      return { cells: cells, edges: links };
    });
    return activityMaps[key].then(function (map) {
      return { scale: destination, frames: (wave.frames || []).map(function (frame) {
        var cells = [], acts = [];
        (frame.cells || []).forEach(function (cell, i) {
          if (map.cells[cell] >= 0) { cells.push(map.cells[cell]); acts.push(frame.acts[i]); }
        });
        return { cells: cells, acts: acts, edges: (frame.edges || []).map(function (e) { return map.edges[e]; }).filter(function (e) { return e >= 0; }) };
      }) };
    });
  }

  FlyBrain.mapActivity = mapActivity;

  var VERT = [
    'attribute vec3 aBase; attribute float aLevel; attribute float aFlash; attribute float aSize;',
    'uniform float uScale; uniform float uRest; uniform float uLight; uniform float uDpr;',
    'varying vec3 vColor; varying float vAlpha;',
    'void main() {',
    '  float e = clamp(aLevel + aFlash, 0.0, 1.0);',
    '  vec3 grey = mix(vec3(0.48, 0.55, 0.67), vec3(0.32, 0.39, 0.49), uLight);',
    '  float k = smoothstep(0.0, 0.3, e);',
    '  vColor = mix(grey, aBase * (1.0 - 0.52 * uLight), 0.18 + 0.82 * k);',
    '  vAlpha = uRest + (1.0 - uRest) * min(1.0, e * 1.2);',
    '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
    '  gl_PointSize = clamp(aSize * uScale / -mv.z, 1.2 * uDpr, 3.8 * uDpr) * (1.0 + 0.65 * e);',
    '  gl_Position = projectionMatrix * mv;',
    '}'
  ].join('\n');
  var FRAG = [
    'varying vec3 vColor; varying float vAlpha;',
    'void main() {',
    '  vec2 c = gl_PointCoord - 0.5;',
    '  float d = length(c);',
    '  if (d > 0.5) discard;',
    '  float core = 1.0 - smoothstep(0.32, 0.5, d);',
    '  gl_FragColor = vec4(vColor, vAlpha * core);',
    '}'
  ].join('\n');

  // Continuous synapses join neuron dots; round particles carry transient activity.
  function linkMaterial(size, opacity, lines) {
    return new global.THREE.ShaderMaterial({
      uniforms: { uLight: { value: 0 }, uDpr: { value: 1 }, uSize: { value: size }, uOpacity: { value: opacity } },
      vertexShader: [
        'attribute vec3 color; uniform float uLight; uniform float uDpr; uniform float uSize; uniform float uOpacity;',
        'varying vec3 vColor; varying float vAlpha;',
        'void main() {',
        'float strength = max(color.r, max(color.g, color.b));',
        'vColor = color / max(strength, 0.001) * (1.0 - 0.55 * uLight);',
        'vAlpha = uOpacity * strength;',
        'gl_PointSize = uSize * uDpr;',
        'gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);',
        '}'
      ].join('\n'),
      fragmentShader: lines ? 'varying vec3 vColor; varying float vAlpha; void main() { gl_FragColor = vec4(vColor, vAlpha); }' : FRAG,
      transparent: true, depthWrite: false, blending: global.THREE.NormalBlending
    });
  }

  function FlyBrain(container, opts) {
    this.el = container;
    this.opts = Object.assign({
      autoRotate: true, rotateSpeed: 0.0016, distance: 2.35, fov: 50,
      interactive: true, onHover: null, edges: true, pixelRatioCap: 2, rest: 0.34, scale: 'standard', onSlow: null
    }, opts || {});
    if (global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches) this.opts.autoRotate = false;
    this.timers = [];
    this.disposed = false;
    this.visible = true;
    this.lastInteract = 0;
    this.dirty = true;
    this.ready = this._init();
  }

  FlyBrain.ROLE_HEX = ROLE_HEX;
  FlyBrain.ROLE_LABEL = ROLE_LABEL;
  FlyBrain.load = loadConnectome;
  /* Heuristic for phones / low-power laptops: used to warn before, and to lighten, the full-pathway view. */
  FlyBrain.weakDevice = function () {
    var mem = global.navigator.deviceMemory || 8, cores = global.navigator.hardwareConcurrency || 8;
    var small = Math.min(global.screen ? global.screen.width : 1200, global.screen ? global.screen.height : 800) < 700;
    return small || mem <= 4 || cores <= 4;
  };

  FlyBrain.prototype._init = function () {
    var self = this;
    if (!global.THREE) { this._fallback('3D engine failed to load.'); return Promise.resolve(false); }
    return loadConnectome(this.opts.scale).then(function (data) {
      if (self.disposed) return false;
      self.data = data;
      try { self._build(); } catch (err) {
        console.error('FlyBrain WebGL init failed', err);
        self._fallback('Your browser could not start WebGL, so the 3D brain is unavailable. Everything else still works.');
        return false;
      }
      return true;
    }).catch(function (err) {
      console.error(err);
      self._fallback('Could not load the connectome data.');
      return false;
    });
  };

  FlyBrain.prototype._fallback = function (msg) {
    this.failed = true;
    this.el.innerHTML = '<div class="brain-fallback"><div>' + msg + '</div></div>';
  };

  FlyBrain.prototype._build = function () {
    var T = global.THREE, d = this.data, n = d.n, self = this, o = this.opts;
    this.scene = new T.Scene();
    this.camera = new T.PerspectiveCamera(o.fov, 1, 0.05, 50);
    this.camera.position.set(0, 0.05, o.distance);
    this.renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(global.devicePixelRatio || 1, o.scale === 'full' && FlyBrain.weakDevice() ? 1 : o.pixelRatioCap));
    this.renderer.setClearColor(0x000000, 0);
    this.el.innerHTML = '';
    this.el.appendChild(this.renderer.domElement);
    this.renderer.domElement.setAttribute('role', 'img');
    this.renderer.domElement.setAttribute('aria-label', '3D neuron cloud with real synaptic connectivity. Drag to rotate; scroll to zoom.');
    var layoutNote = document.createElement('span');
    layoutNote.className = 'brain-layout-note';
    layoutNote.textContent = '3D connectome · drag to explore';
    this.el.appendChild(layoutNote);
    this.group = new T.Group();
    this.group.rotation.set(0.35, 0.85, 0);
    this.rotationAnchorY = 0.85;
    this.rotationPhase = 0;
    this.scene.add(this.group);

    this.level = new Float32Array(n);
    this.target = new Float32Array(n);
    this.flash = new Float32Array(n);
    var base = new Float32Array(n * 3), size = new Float32Array(n);
    this.baseColors = new Float32Array(n * 3);
    for (var i = 0; i < n; i++) {
      var rn = ROLE_NAMES[d.role[i]], c = ROLE_RGB[rn];
      base.set(c, i * 3);
      this.baseColors.set(c, i * 3);
      size[i] = BASE_SIZE[rn] * (0.85 + 0.3 * (((d.ids[i] * 2654435761) >>> 0) % 1000) / 1000);
    }
    var geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(d.pos, 3));
    this.aBase = new T.BufferAttribute(base, 3).setUsage(T.DynamicDrawUsage);
    this.aLevel = new T.BufferAttribute(this.level, 1).setUsage(T.DynamicDrawUsage);
    this.aFlash = new T.BufferAttribute(this.flash, 1).setUsage(T.DynamicDrawUsage);
    geo.setAttribute('aBase', this.aBase);
    geo.setAttribute('aLevel', this.aLevel);
    geo.setAttribute('aFlash', this.aFlash);
    geo.setAttribute('aSize', new T.BufferAttribute(size, 1));
    this.uniforms = { uScale: { value: 400 }, uRest: { value: o.rest }, uLight: { value: 0 }, uDpr: { value: this.renderer.getPixelRatio() } };
    var mat = new T.ShaderMaterial({
      uniforms: this.uniforms, vertexShader: VERT, fragmentShader: FRAG,
      transparent: true, depthWrite: false, blending: T.AdditiveBlending
    });
    this.points = new T.Points(geo, mat);
    this.points.frustumCulled = false;
    this.group.add(this.points);

    if (o.edges) this._buildBaseEdges();
    this._buildActiveLayer();
    this._buildFocusEdges();
    this._buildPulses();
    this._buildOutAdjacency();

    this._theme = this.applyTheme.bind(this);
    global.addEventListener("ff-theme-change", this._theme);
    this.applyTheme();
    this._resize = this.resize.bind(this);
    this.resize();
    if (global.ResizeObserver) { this.ro = new ResizeObserver(this._resize); this.ro.observe(this.el); }
    else global.addEventListener('resize', this._resize);
    if (global.IntersectionObserver) {
      this.io = new IntersectionObserver(function (es) { self.visible = es[0].isIntersecting; }, { threshold: 0.01 });
      this.io.observe(this.el);
    }
    if (o.interactive) this._bindPointer();
    this._loop = this._loop.bind(this);
    this.raf = global.requestAnimationFrame(this._loop);
  };

  FlyBrain.prototype.applyTheme = function () {
    var T = global.THREE, light = document.documentElement.dataset.theme === "light";
    var dpr = this.renderer.getPixelRatio();
    this.group.traverse(function (object) {
      if (!object.material) return;
      object.material.blending = T.NormalBlending;
      if (object.material.uniforms) {
        object.material.uniforms.uLight.value = light ? 1 : 0;
        object.material.uniforms.uDpr.value = dpr;
      }
      object.material.needsUpdate = true;
    });
    this.uniforms.uRest.value = light ? 0.62 : 0.52;
    this.dirty = true;
  };

  FlyBrain.prototype._buildBaseEdges = function () {
    var T = global.THREE, d = this.data, m = d.src.length, idx = [];
    for (var e = 0; e < m; e++) idx.push(e);
    idx.sort(function (a, b) {
      var ca = (d.role[d.src[a]] ? 1000 : 0) + (d.role[d.dst[a]] ? 1000 : 0) + d.w[a];
      var cb = (d.role[d.src[b]] ? 1000 : 0) + (d.role[d.dst[b]] ? 1000 : 0) + d.w[b];
      return cb - ca;
    });
    var keep = Math.min(idx.length, this.opts.scale === 'full' ? 160 : 100);
    var pos = new Float32Array(keep * LINK_VERTICES * 3), col = new Float32Array(pos.length);
    for (var k = 0; k < keep; k++) {
      var source = d.src[idx[k]], target = d.dst[idx[k]], color = ROLE_RGB[ROLE_NAMES[d.role[target]]];
      for (var dot = 0; dot < LINK_VERTICES; dot++) {
        var t = dot / (LINK_VERTICES - 1);
        for (var j = 0; j < 3; j++) {
          var offset = (k * LINK_VERTICES + dot) * 3 + j;
          pos[offset] = d.pos[source * 3 + j] * (1 - t) + d.pos[target * 3 + j] * t;
          col[offset] = color[j];
        }
      }
    }
    var g = new T.BufferGeometry();
    g.setAttribute('position', new T.BufferAttribute(pos, 3));
    g.setAttribute('color', new T.BufferAttribute(col, 3));
    this.baseEdges = new T.LineSegments(g, linkMaterial(1, 0.28, true));
    this.baseEdges.frustumCulled = false;
    this.group.add(this.baseEdges);
  };

  FlyBrain.prototype._buildActiveLayer = function () {
    var T = global.THREE;
    this.actPos = new Float32Array(MAX_ACTIVE * LINK_VERTICES * 3);
    this.actCol = new Float32Array(MAX_ACTIVE * LINK_VERTICES * 3);
    this.actLife = new Float32Array(MAX_ACTIVE);
    this.actRGB = new Float32Array(MAX_ACTIVE * 3);
    this.actHead = 0;
    var g = new T.BufferGeometry();
    this.actPosAttr = new T.BufferAttribute(this.actPos, 3).setUsage(T.DynamicDrawUsage);
    this.actColAttr = new T.BufferAttribute(this.actCol, 3).setUsage(T.DynamicDrawUsage);
    g.setAttribute('position', this.actPosAttr);
    g.setAttribute('color', this.actColAttr);
    this.actLines = new T.LineSegments(g, linkMaterial(1, 0.75, true));
    this.actLines.frustumCulled = false;
    this.group.add(this.actLines);
  };

  FlyBrain.prototype._buildFocusEdges = function () {
    var T = global.THREE, g = new T.BufferGeometry();
    this.focusPos = new Float32Array(MAX_ACTIVE * LINK_VERTICES * 3);
    this.focusAttr = new T.BufferAttribute(this.focusPos, 3).setUsage(T.DynamicDrawUsage);
    g.setAttribute('position', this.focusAttr);
    g.setDrawRange(0, 0);
    this.focusColors = new T.BufferAttribute(new Float32Array(this.focusPos.length), 3).setUsage(T.DynamicDrawUsage);
    g.setAttribute('color', this.focusColors);
    this.focusLines = new T.LineSegments(g, linkMaterial(1, 0.85, true));
    this.focusLines.frustumCulled = false;
    this.group.add(this.focusLines);
  };

  FlyBrain.prototype._buildPulses = function () {
    var T = global.THREE, g = new T.BufferGeometry();
    this.pPos = new Float32Array(MAX_PULSES * 3).fill(9999);
    this.pCol = new Float32Array(MAX_PULSES * 3);
    this.pT = new Float32Array(MAX_PULSES).fill(2);
    this.pSpeed = new Float32Array(MAX_PULSES);
    this.pA = new Float32Array(MAX_PULSES * 3);
    this.pB = new Float32Array(MAX_PULSES * 3);
    this.pHead = 0;
    this.pPosAttr = new T.BufferAttribute(this.pPos, 3).setUsage(T.DynamicDrawUsage);
    this.pColAttr = new T.BufferAttribute(this.pCol, 3).setUsage(T.DynamicDrawUsage);
    g.setAttribute('position', this.pPosAttr);
    g.setAttribute('color', this.pColAttr);
    this.pulses = new T.Points(g, linkMaterial(3.2, 0.95));
    this.pulses.frustumCulled = false;
    this.group.add(this.pulses);
  };

  FlyBrain.prototype._buildOutAdjacency = function () {
    var d = this.data, out = new Array(d.n);
    for (var e = 0; e < d.src.length; e++) {
      var s = d.src[e];
      if (d.role[s] === 0 && d.role[d.dst[e]] === 0) continue; // only wiring that touches the courtship circuit
      (out[s] || (out[s] = [])).push(e);
    }
    this.outEdges = out;
  };

  /* ---------- sizing / interaction ---------- */
  FlyBrain.prototype.resize = function () {
    if (!this.renderer) return;
    var w = this.el.clientWidth || 300, h = this.el.clientHeight || 200;
    this.renderer.setSize(w, h);
    // Preserve vertical framing on narrow screens, including portrait phones.
    var halfFov = (this.opts.fov * Math.PI) / 360;
    var limitingAngle = Math.min(halfFov, Math.atan(Math.tan(halfFov) * w / h));
    var fitDistance = Math.max(this.opts.distance, (this.data.radius + 0.05) * 1.08 / Math.sin(limitingAngle));
    this.camera.position.z *= fitDistance / (this._fitDistance || this.opts.distance);
    this._fitDistance = fitDistance;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    var pr = this.renderer.getPixelRatio();
    this.uniforms.uScale.value = (h * pr) / (2 * Math.tan((this.opts.fov * Math.PI) / 360));
    this.dirty = true;
  };

  FlyBrain.prototype._bindPointer = function () {
    var self = this, el = this.renderer.domElement, pts = {}, lastDist = 0;
    el.style.touchAction = 'none';
    el.addEventListener('pointerdown', function (e) {
      el.setPointerCapture && el.setPointerCapture(e.pointerId);
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      self.lastInteract = performance.now();
    });
    el.addEventListener('pointermove', function (e) {
      var p = pts[e.pointerId];
      if (p) {
        var keys = Object.keys(pts);
        if (keys.length === 2) {
          var other = pts[keys[0] == e.pointerId ? keys[1] : keys[0]];
          var dist = Math.hypot(e.clientX - other.x, e.clientY - other.y);
          if (lastDist) self._zoom((lastDist - dist) * 0.006);
          lastDist = dist;
        } else {
          self.group.rotation.y += (e.clientX - p.x) * 0.008;
          self.group.rotation.x = Math.max(-1.3, Math.min(1.3, self.group.rotation.x + (e.clientY - p.y) * 0.008));
        }
        p.x = e.clientX; p.y = e.clientY;
        self.rotationAnchorY = self.group.rotation.y; self.rotationPhase = 0;
        self.lastInteract = performance.now();
        self.dirty = true;
      } else if (self.opts.onHover) {
        self._hoverAt(e);
      }
    });
    function up(e) { delete pts[e.pointerId]; lastDist = 0; }
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('pointerleave', function () { if (self.opts.onHover) self.opts.onHover(null); });
    el.addEventListener('wheel', function (e) { e.preventDefault(); self._zoom(e.deltaY * 0.0012); self.lastInteract = performance.now(); }, { passive: false });
  };

  FlyBrain.prototype._zoom = function (dz) {
    this.camera.position.z = Math.max(1.15, Math.min(Math.max(4.2, this._fitDistance * 1.8), this.camera.position.z + dz));
    this.dirty = true;
  };

  FlyBrain.prototype._hoverAt = function (e) {
    if (this._hoverBusy) return;
    var self = this;
    this._hoverBusy = true;
    requestAnimationFrame(function () {
      self._hoverBusy = false;
      if (self.disposed) return;
      var rect = self.renderer.domElement.getBoundingClientRect();
      var mx = e.clientX - rect.left, my = e.clientY - rect.top, best = -1, bestD = 14 * 14, T = global.THREE;
      var v = new T.Vector3(), d = self.data;
      self.group.updateMatrixWorld();
      for (var i = 0; i < d.n; i++) {
        v.set(d.pos[i * 3], d.pos[i * 3 + 1], d.pos[i * 3 + 2]).applyMatrix4(self.group.matrixWorld).project(self.camera);
        if (v.z > 1) continue;
        var sx = (v.x * 0.5 + 0.5) * rect.width, sy = (-v.y * 0.5 + 0.5) * rect.height;
        var dd = (sx - mx) * (sx - mx) + (sy - my) * (sy - my);
        var bias = d.role[i] ? 0.55 : 1;
        if (dd * bias < bestD) { bestD = dd * bias; best = i; }
      }
      self.opts.onHover(best >= 0 ? best : null, { x: mx, y: my });
    });
  };

  FlyBrain.prototype.describe = function (i) {
    var d = this.data;
    return {
      index: i, cell: d.ids[i], type: d.types[d.type[i]], role: ROLE_NAMES[d.role[i]],
      nt: d.nts[d.nt[i]], level: Math.max(this.level[i], this.flash[i])
    };
  };

  /* ---------- state / waves ---------- */
  FlyBrain.prototype.applyState = function (idx, val, instant) {
    if (!this.target) return;
    this.target.fill(0);
    for (var k = 0; k < idx.length; k++) this.target[idx[k]] = val[k];
    if (instant) this.level.set(this.target);
    this.dirty = true;
  };

  // Shared by chat and Live Lab: identical frames, cadence and final state.
  FlyBrain.prototype.playActivity = function (wave) {
    var self = this, frames = wave.frames || [];
    this.playWave(frames);
    var id = setTimeout(function () {
      if (!self.disposed && wave.state) self.applyState(wave.state.idx, wave.state.val);
    }, 170 * Math.max(0, frames.length - 1) + 260);
    this.timers.push(id);
  };

  FlyBrain.prototype.playWave = function (frames, opts) {
    if (!this.target || !frames) return;
    var self = this, gap = (opts && opts.gap) || 170;
    frames.forEach(function (f, i) {
      var id = setTimeout(function () { if (!self.disposed) self._applyFrame(f, opts); }, i * gap);
      self.timers.push(id);
    });
  };

  FlyBrain.prototype._applyFrame = function (f, opts) {
    var cells = f.cells || [], acts = f.acts || [], edges = f.edges || [], k;
    for (k = 0; k < cells.length; k++) {
      if (acts[k] > this.flash[cells[k]]) this.flash[cells[k]] = acts[k];
    }
    var maxPulses = (opts && opts.pulses) || 42;
    for (k = 0; k < Math.min(edges.length, MAX_ACTIVE); k++) {
      this._addActiveEdge(edges[k]);
      if (k < maxPulses) this._spawnPulse(edges[k]);
    }
    this.dirty = true;
  };

  FlyBrain.prototype._addActiveEdge = function (e) {
    var d = this.data, s = d.src[e], t = d.dst[e], h = this.actHead, j;
    this.actHead = (h + 1) % MAX_ACTIVE;
    var c = ROLE_RGB[ROLE_NAMES[d.role[t]]];
    for (var dot = 0; dot < LINK_VERTICES; dot++) {
      var fraction = dot / (LINK_VERTICES - 1);
      for (j = 0; j < 3; j++) {
        this.actPos[(h * LINK_VERTICES + dot) * 3 + j] = d.pos[s * 3 + j] * (1 - fraction) + d.pos[t * 3 + j] * fraction;
        this.actRGB[h * 3 + j] = c[j];
      }
    }
    this.actLife[h] = 1;
    this.actPosAttr.needsUpdate = true;
  };

  FlyBrain.prototype._spawnPulse = function (e) {
    var d = this.data, s = d.src[e], t = d.dst[e], h = this.pHead, j;
    this.pHead = (h + 1) % MAX_PULSES;
    var c = ROLE_RGB[ROLE_NAMES[d.role[t]]];
    for (j = 0; j < 3; j++) {
      this.pA[h * 3 + j] = d.pos[s * 3 + j];
      this.pB[h * 3 + j] = d.pos[t * 3 + j];
      this.pCol[h * 3 + j] = Math.min(1, c[j] * 0.5 + 0.5);
    }
    this.pT[h] = 0;
    this.pSpeed[h] = 0.02 + (e % 101) / 100 * 0.02;
  };

  /* An ambient cascade over the real wiring (landing page / lab idle animation). */
  FlyBrain.prototype.ambientPulse = function () {
    if (!this.outEdges) return;
    var d = this.data, seeds = [], i;
    for (i = 0; i < d.n; i++) if (d.role[i] === 1 || d.role[i] === 2) seeds.push(i);
    if (!seeds.length) return;
    var frontier = [seeds[(Math.random() * seeds.length) | 0], seeds[(Math.random() * seeds.length) | 0]];
    var frames = [], seen = {};
    for (var depth = 0; depth < 4; depth++) {
      var cells = [], acts = [], edges = [], next = [];
      frontier.forEach(function (c) {
        if (seen[c]) return;
        seen[c] = 1; cells.push(c); acts.push(0.9 - depth * 0.15);
        var out = this.outEdges[c] || [];
        var pick = out.slice().sort(function () { return Math.random() - 0.5; }).slice(0, 5);
        pick.forEach(function (e) { edges.push(e); next.push(d.dst[e]); });
      }, this);
      frames.push({ cells: cells, acts: acts, edges: edges });
      frontier = next;
    }
    this.playWave(frames, { gap: 260, pulses: 30 });
  };

  /* ---------- painting (verdict page) ---------- */
  FlyBrain.prototype.paint = function (groups, opts) {
    if (!this.aBase) return;
    var T = global.THREE, base = this.aBase.array, i, g, k;
    base.set(this.baseColors);
    this.target.fill(0);
    this.level.fill(0);
    for (g = 0; g < groups.length; g++) {
      var col = new T.Color(groups[g].color), lvl = groups[g].level == null ? 1 : groups[g].level;
      for (k = 0; k < groups[g].cells.length; k++) {
        i = groups[g].cells[k];
        base[i * 3] = col.r; base[i * 3 + 1] = col.g; base[i * 3 + 2] = col.b;
        this.target[i] = lvl; this.level[i] = lvl;
      }
    }
    this.uniforms.uRest.value = (opts && opts.rest != null) ? opts.rest : 0.16;
    this.aBase.needsUpdate = true;
    this.dirty = true;
  };

  FlyBrain.prototype.clearPaint = function () {
    if (!this.aBase) return;
    this.aBase.array.set(this.baseColors);
    this.aBase.needsUpdate = true;
    this.target.fill(0);
    this.level.fill(0);
    this.uniforms.uRest.value = this.opts.rest;
    this.focusLines.geometry.setDrawRange(0, 0);
    this.dirty = true;
  };

  FlyBrain.prototype.focusEdges = function (edgeIdx, hex) {
    if (!this.focusLines) return;
    var d = this.data, n = Math.min(edgeIdx.length, MAX_ACTIVE), j, T = global.THREE;
    var color = new T.Color(hex || '#00b8a6');
    for (var k = 0; k < n; k++) {
      var s = d.src[edgeIdx[k]], t = d.dst[edgeIdx[k]];
      for (var dot = 0; dot < LINK_VERTICES; dot++) {
        var fraction = dot / (LINK_VERTICES - 1), offset = (k * LINK_VERTICES + dot) * 3;
        for (j = 0; j < 3; j++) this.focusPos[offset + j] = d.pos[s * 3 + j] * (1 - fraction) + d.pos[t * 3 + j] * fraction;
        this.focusColors.setXYZ(k * LINK_VERTICES + dot, color.r, color.g, color.b);
      }
    }
    this.focusAttr.needsUpdate = true;
    this.focusColors.needsUpdate = true;
    this.focusLines.geometry.setDrawRange(0, n * LINK_VERTICES);
    this.dirty = true;
  };

  FlyBrain.prototype.setView = function (name) {
    var r = { iso: [0.15, 0.5], top: [1.25, 0], side: [0, 1.5708], front: [0, 0] }[name] || [0.15, 0.5];
    this.group.rotation.set(r[0], r[1], 0);
    this.rotationAnchorY = r[1]; this.rotationPhase = 0;
    this.dirty = true;
  };

  FlyBrain.prototype.reset = function () {
    this.timers.forEach(clearTimeout); this.timers = [];
    if (this.flash) {
      this.flash.fill(0); this.target.fill(0); this.level.fill(0); this.actLife.fill(0); this.pT.fill(2);
      this.actCol.fill(0); this.actColAttr.needsUpdate = true;
      this.pPos.fill(9999); this.pPosAttr.needsUpdate = true;
      this.focusLines.geometry.setDrawRange(0, 0);
    }
    this.dirty = true;
  };

  /* ---------- render loop ---------- */
  FlyBrain.prototype._watchFps = function () {
    if (this.fpsDone || !this.opts.onSlow) return;
    var now = global.performance.now();
    if (this.fpsLast) {
      var dt = now - this.fpsLast;
      if (dt < 1000) { this.fpsN = (this.fpsN || 0) + 1; this.fpsSum = (this.fpsSum || 0) + dt; }   // ignore tab-switch gaps
      if (this.fpsN >= 90) {
        this.fpsDone = true;
        if (this.fpsSum / this.fpsN > 45) this.opts.onSlow(Math.round(1000 / (this.fpsSum / this.fpsN)));   // under ~22 fps
      }
    }
    this.fpsLast = now;
  };

  FlyBrain.prototype._loop = function () {
    if (this.disposed) return;
    this.raf = global.requestAnimationFrame(this._loop);
    if (document.hidden || !this.visible) { this.fpsLast = 0; return; }
    this._watchFps();
    var i, anim = false, n = this.data.n, lvl = this.level, tgt = this.target, fl = this.flash;

    if (this.opts.autoRotate && performance.now() - this.lastInteract > 2500) {
      this.rotationPhase += this.opts.rotateSpeed;
      this.group.rotation.y = this.rotationAnchorY + Math.sin(this.rotationPhase) * 0.5;
      anim = true;
    }
    for (i = 0; i < n; i++) {
      var d = tgt[i] - lvl[i];
      if (d > 0.002 || d < -0.002) { lvl[i] += d * 0.09; anim = true; } else if (d !== 0) { lvl[i] = tgt[i]; anim = true; }
      if (fl[i] > 0.004) { fl[i] *= 0.925; anim = true; } else if (fl[i] !== 0) { fl[i] = 0; anim = true; }
    }
    if (anim || this.dirty) { this.aLevel.needsUpdate = true; this.aFlash.needsUpdate = true; }

    var m, activeAny = false;
    for (m = 0; m < MAX_ACTIVE; m++) {
      var life = this.actLife[m];
      if (life > 0.02) {
        life *= 0.945; this.actLife[m] = life; activeAny = true;
        for (var dot = 0; dot < LINK_VERTICES; dot++) {
          for (i = 0; i < 3; i++) this.actCol[(m * LINK_VERTICES + dot) * 3 + i] = this.actRGB[m * 3 + i] * life;
        }
      } else if (life !== 0) {
        this.actLife[m] = 0;
        for (i = 0; i < LINK_VERTICES * 3; i++) this.actCol[m * LINK_VERTICES * 3 + i] = 0;
        activeAny = true;
      }
    }
    if (activeAny) this.actColAttr.needsUpdate = true;

    var pulseAny = false;
    for (m = 0; m < MAX_PULSES; m++) {
      var t = this.pT[m];
      if (t <= 1) {
        t += this.pSpeed[m]; this.pT[m] = t; pulseAny = true;
        if (t > 1) { this.pPos[m * 3] = this.pPos[m * 3 + 1] = this.pPos[m * 3 + 2] = 9999; }
        else {
          for (i = 0; i < 3; i++) this.pPos[m * 3 + i] = this.pA[m * 3 + i] + (this.pB[m * 3 + i] - this.pA[m * 3 + i]) * t;
        }
      }
    }
    if (pulseAny) { this.pPosAttr.needsUpdate = true; this.pColAttr.needsUpdate = true; }

    if (!anim && !this.dirty && !activeAny && !pulseAny) return; // nothing changed: skip the GPU draw
    this.renderer.render(this.scene, this.camera);
    this.dirty = false;
  };

  FlyBrain.prototype.dispose = function () {
    this.disposed = true;
    this.timers.forEach(clearTimeout);
    if (this.raf) cancelAnimationFrame(this.raf);
    global.removeEventListener("ff-theme-change", this._theme);
    global.removeEventListener("resize", this._resize);
    if (this.ro) this.ro.disconnect();
    if (this.io) this.io.disconnect();
    if (this.renderer) {
      this.renderer.dispose();
      var gl = this.renderer.getContext && this.renderer.getContext();
      var ext = gl && gl.getExtension && gl.getExtension('WEBGL_lose_context');
      if (ext) ext.loseContext();
    }
  };

  global.FlyBrain = FlyBrain;
})(window);
