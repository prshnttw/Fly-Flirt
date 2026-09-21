/**
 * FlyBrainVisualizer - High-Performance 3D Fruit Fly Connectome Engine
 * Visualizes 497 MaleCNS v1.0 neurons and 2,296 real synaptic connections with real-time firing pulses.
 */

const ROLE_PALETTE = {
  sensory: {
    base: new THREE.Color(0x00f5d4),      // Neon Cyan (mAL inhibitory/sensory)
    glow: '#00f5d4',
    rgb: [0.0, 0.96, 0.83],
    name: 'Sensory (mAL)'
  },
  accumulator: {
    base: new THREE.Color(0xffb703),  // Electric Gold/Amber (pC1 courtship accumulator)
    glow: '#ffb703',
    rgb: [1.0, 0.72, 0.01],
    name: 'Accumulator (pC1)'
  },
  output: {
    base: new THREE.Color(0xff007f),       // Hot Pink/Magenta (pIP10 courtship output)
    glow: '#ff007f',
    rgb: [1.0, 0.0, 0.5],
    name: 'Output (pIP10)'
  },
  background: {
    base: new THREE.Color(0x433878),   // Dim Bioluminescent Indigo
    glow: '#433878',
    rgb: [0.26, 0.22, 0.47],
    name: 'Connectome Scaffolding'
  }
};

class FlyBrainVisualizer {
  constructor(containerId, options = {}) {
    this.container = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
    if (!this.container) return;

    this.options = Object.assign({
      autoRotate: true,
      autoRotateSpeed: 0.003,
      enableControls: true,
      interactive: true,
      showEdges: true,
      pulseDensity: 24,
      cameraDistance: 2.3,
      fov: 55,
      isMini: false
    }, options);

    this.nodes = [];
    this.edges = [];
    this.nodesById = {};
    this.meshesByType = {};
    this.pulses = [];
    this.activeFirings = [];
    this.hoveredNode = null;

    this.initScene();
    this.loadData();
    this.setupEvents();
    this.animate();
  }

  initScene() {
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      this.options.fov,
      this.container.clientWidth / (this.container.clientHeight || 1),
      0.05,
      100
    );
    this.camera.position.set(0, 0.1, this.options.cameraDistance);

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "high-performance"
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    this.renderer.outputEncoding = THREE.sRGBEncoding;

    // Clear any existing canvas elements but preserve overlay children
    const existingCanvases = this.container.querySelectorAll('canvas');
    existingCanvases.forEach(c => c.remove());
    this.container.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = 'block';

    // Root group for clean rotational transforms
    this.brainGroup = new THREE.Group();
    this.scene.add(this.brainGroup);

    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.8);
    this.scene.add(ambientLight);

    const pointLight = new THREE.PointLight(0x00f5d4, 1.2, 10);
    pointLight.position.set(2, 3, 2);
    this.scene.add(pointLight);

    const pointLight2 = new THREE.PointLight(0xff007f, 1.2, 10);
    pointLight2.position.set(-2, -3, 2);
    this.scene.add(pointLight2);

    // Subtle background coordinate ring
    this.createAuraRing();

    // Interaction raycaster
    this.raycaster = new THREE.Raycaster();
    this.raycaster.params.Points = { threshold: 0.04 };
    this.mouse = new THREE.Vector2(-999, -999);
  }

  createAuraRing() {
    if (this.options.isMini) return;
    const ringGeo = new THREE.RingGeometry(0.85, 0.86, 64);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0x00f5d4,
      transparent: true,
      opacity: 0.12,
      side: THREE.DoubleSide
    });
    this.auraRing = new THREE.Mesh(ringGeo, ringMat);
    this.auraRing.rotation.x = Math.PI / 2;
    this.auraRing.position.y = -0.4;
    this.brainGroup.add(this.auraRing);
  }

  async loadData() {
    try {
      const res = await fetch('/static/circuit_viz.json');
      const data = await res.json();
      this.nodes = data.nodes || [];
      this.edges = data.edges || [];

      this.buildNodeMeshes();
      if (this.options.showEdges) {
        this.buildEdgeLines();
      }
      this.buildPulseSystem();

      if (this.onLoaded) {
        this.onLoaded(this);
      }
    } catch (err) {
      console.error('Failed to load circuit visualization data:', err);
    }
  }

  buildNodeMeshes() {
    const sphereGeoCore = new THREE.SphereGeometry(this.options.isMini ? 0.016 : 0.02, 12, 12);
    const sphereGeoBg = new THREE.SphereGeometry(this.options.isMini ? 0.007 : 0.009, 8, 8);

    this.clickableMeshes = [];

    const GREY_REST_COLOR = new THREE.Color(0x555566);

    this.nodes.forEach((n) => {
      const role = n.role || 'background';
      const palette = ROLE_PALETTE[role] || ROLE_PALETTE.background;
      const isCore = role !== 'background';

      // Dotted-matrix rest state: every neuron starts a neutral, uninvolved
      // grey. Only nodes with real per-message activation (see
      // updateActivity) tint toward their real role color — so lighting up
      // means "this part is actually involved," not just "core vs background."
      const restOpacity = isCore ? 0.3 : 0.12;

      const mat = new THREE.MeshBasicMaterial({
        color: GREY_REST_COLOR.clone(),
        transparent: true,
        opacity: restOpacity
      });

      const mesh = new THREE.Mesh(isCore ? sphereGeoCore : sphereGeoBg, mat);
      mesh.position.set(n.x, n.y, n.z);
      mesh.userData = {
        id: n.id,
        type: n.type,
        role: role,
        nt: n.nt,
        restColor: GREY_REST_COLOR,
        roleColor: palette.base.clone(),
        baseOpacity: restOpacity,
        baseScale: 1.0,
        currentActivation: 0.0,
        flash: 0.0
      };

      this.brainGroup.add(mesh);
      this.nodesById[n.id] = { node: n, mesh: mesh };

      if (isCore) {
        this.meshesByType[n.type] = this.meshesByType[n.type] || [];
        this.meshesByType[n.type].push(mesh);
        this.clickableMeshes.push(mesh);
      }
    });
  }

  buildEdgeLines() {
    const coreLinePositions = [];
    const coreLineColors = [];
    const bgLinePositions = [];
    const bgLineColors = [];

    this.edgePairs = [];

    this.edges.forEach((e) => {
      const src = this.nodesById[e.from];
      const tgt = this.nodesById[e.to];
      if (!src || !tgt) return;

      const isCoreEdge = e.from_role !== 'background' || e.to_role !== 'background';
      const p1 = src.mesh.position;
      const p2 = tgt.mesh.position;

      this.edgePairs.push({
        srcPos: p1,
        tgtPos: p2,
        weight: e.weight || 1,
        fromRole: e.from_role,
        toRole: e.to_role,
        isCore: isCoreEdge
      });

      const c1 = ROLE_PALETTE[e.from_role] ? ROLE_PALETTE[e.from_role].base : ROLE_PALETTE.background.base;
      const c2 = ROLE_PALETTE[e.to_role] ? ROLE_PALETTE[e.to_role].base : ROLE_PALETTE.background.base;

      if (isCoreEdge) {
        coreLinePositions.push(p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
        coreLineColors.push(c1.r, c1.g, c1.b, c2.r, c2.g, c2.b);
      } else if (!this.options.isMini) {
        bgLinePositions.push(p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
        bgLineColors.push(c1.r * 0.4, c1.g * 0.4, c1.b * 0.4, c2.r * 0.4, c2.g * 0.4, c2.b * 0.4);
      }
    });

    if (coreLinePositions.length > 0) {
      const coreGeo = new THREE.BufferGeometry();
      coreGeo.setAttribute('position', new THREE.Float32BufferAttribute(coreLinePositions, 3));
      coreGeo.setAttribute('color', new THREE.Float32BufferAttribute(coreLineColors, 3));
      const coreMat = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: this.options.isMini ? 0.35 : 0.45,
        blending: THREE.AdditiveBlending
      });
      this.coreLinesMesh = new THREE.LineSegments(coreGeo, coreMat);
      this.brainGroup.add(this.coreLinesMesh);
    }

    if (bgLinePositions.length > 0) {
      const bgGeo = new THREE.BufferGeometry();
      bgGeo.setAttribute('position', new THREE.Float32BufferAttribute(bgLinePositions, 3));
      bgGeo.setAttribute('color', new THREE.Float32BufferAttribute(bgLineColors, 3));
      const bgMat = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.08,
        blending: THREE.AdditiveBlending
      });
      this.bgLinesMesh = new THREE.LineSegments(bgGeo, bgMat);
      this.brainGroup.add(this.bgLinesMesh);
    }
  }

  buildPulseSystem() {
    const MAX_PULSES = 200;
    const pulseGeo = new THREE.BufferGeometry();
    const positions = new Float32Array(MAX_PULSES * 3);
    const colors = new Float32Array(MAX_PULSES * 3);
    const sizes = new Float32Array(MAX_PULSES);

    for (let i = 0; i < MAX_PULSES; i++) {
      positions[i * 3] = 0;
      positions[i * 3 + 1] = 0;
      positions[i * 3 + 2] = 0;
      colors[i * 3] = 1;
      colors[i * 3 + 1] = 1;
      colors[i * 3 + 2] = 1;
      sizes[i] = 0;
    }

    pulseGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    pulseGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    pulseGeo.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const pulseMat = new THREE.PointsMaterial({
      size: this.options.isMini ? 0.035 : 0.05,
      vertexColors: true,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending
    });

    this.pulseSystem = new THREE.Points(pulseGeo, pulseMat);
    this.brainGroup.add(this.pulseSystem);

    this.pulsePool = [];
    for (let i = 0; i < MAX_PULSES; i++) {
      this.pulsePool.push({
        index: i,
        active: false,
        progress: 0,
        speed: 0.02,
        src: new THREE.Vector3(),
        tgt: new THREE.Vector3(),
        color: new THREE.Color(0x00f5d4),
        targetNode: null
      });
    }
  }

  spawnPulse(srcPos, tgtPos, color = 0x00f5d4, speed = 0.025, targetNode = null) {
    const pulse = this.pulsePool.find((p) => !p.active);
    if (!pulse) return;

    pulse.active = true;
    pulse.progress = 0;
    pulse.speed = speed;
    pulse.src.copy(srcPos);
    pulse.tgt.copy(tgtPos);
    pulse.color.set(color);
    pulse.targetNode = targetNode;
  }

  triggerSynapticFiring(intensity = 1.0) {
    // Node lighting itself is driven only by real per-type activation (see
    // updateActivity) so that only the parts of the circuit actually involved
    // in a message light up — everything else stays grey. This just adds
    // traveling pulses along real synapses for a bit of "electric" flavor on
    // every message; it never lights up a node on its own.
    if (!this.edgePairs || this.edgePairs.length === 0) return;

    const coreEdges = this.edgePairs.filter((e) => e.isCore);
    const count = Math.min(Math.floor((this.options.isMini ? 14 : 40) * intensity), coreEdges.length);

    const shuffled = [...coreEdges].sort(() => 0.5 - Math.random());
    const selected = shuffled.slice(0, count);

    selected.forEach((edge, idx) => {
      setTimeout(() => {
        const roleColor = edge.toRole === 'output' ? 0xff007f : (edge.toRole === 'accumulator' ? 0xffb703 : 0x00f5d4);
        const speed = 0.018 + Math.random() * 0.025 * intensity;
        this.spawnPulse(edge.srcPos, edge.tgtPos, roleColor, speed);
      }, idx * (16 + Math.random() * 25));
    });

    if (this.auraRing) {
      this.auraRing.material.opacity = Math.min(0.4, 0.15 + 0.25 * intensity);
    }
  }

  updateActivity(nodeActivity) {
    if (!nodeActivity) return;

    // This is the ONLY thing that lights a node up — real per-type
    // activation from circuit_sim. Types near zero stay grey; only types
    // genuinely involved right now tint toward their real role color, at a
    // level that PERSISTS (restLevel) until the next real update changes it
    // — a node that's genuinely still active between messages should stay
    // lit, not fade back to grey. A separate fast-decaying "flash" pops on
    // top only when a node's activation just rose, to mark "this just fired."
    Object.entries(nodeActivity).forEach(([type, activation]) => {
      const meshes = this.meshesByType[type];
      if (!meshes) return;

      const raw = Math.max(0, Math.min(1, Math.abs(activation)));
      const level = raw < 0.01 ? 0 : Math.min(1.0, Math.pow(raw, 0.6) * 1.6);

      meshes.forEach((mesh) => {
        const prevLevel = mesh.userData.restLevel || 0;
        mesh.userData.currentActivation = raw;
        mesh.userData.restLevel = level;
        if (level > prevLevel) {
          mesh.userData.flash = Math.min(1.0, mesh.userData.flash + (level - prevLevel));
        }
      });
    });
  }

  setupEvents() {
    this.isDragging = false;
    this.previousMousePosition = { x: 0, y: 0 };
    this.targetRotation = { x: 0, y: 0 };

    const dom = this.renderer.domElement;

    // Mouse Controls
    dom.addEventListener('mousedown', (e) => {
      this.isDragging = true;
      this.previousMousePosition = { x: e.clientX, y: e.clientY };
    });

    window.addEventListener('mouseup', () => {
      this.isDragging = false;
    });

    dom.addEventListener('mousemove', (e) => {
      const rect = dom.getBoundingClientRect();
      this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      if (!this.isDragging) return;

      const deltaX = e.clientX - this.previousMousePosition.x;
      const deltaY = e.clientY - this.previousMousePosition.y;

      this.brainGroup.rotation.y += deltaX * 0.008;
      this.brainGroup.rotation.x += deltaY * 0.008;

      this.previousMousePosition = { x: e.clientX, y: e.clientY };
    });

    // Touch Controls
    let initialTouchDistance = null;
    dom.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        this.isDragging = true;
        this.previousMousePosition = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      } else if (e.touches.length === 2) {
        this.isDragging = false;
        initialTouchDistance = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
      }
    }, { passive: true });

    dom.addEventListener('touchmove', (e) => {
      if (e.touches.length === 1 && this.isDragging) {
        const deltaX = e.touches[0].clientX - this.previousMousePosition.x;
        const deltaY = e.touches[0].clientY - this.previousMousePosition.y;
        this.brainGroup.rotation.y += deltaX * 0.01;
        this.brainGroup.rotation.x += deltaY * 0.01;
        this.previousMousePosition = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      } else if (e.touches.length === 2 && initialTouchDistance !== null) {
        const currentDistance = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        const diff = currentDistance - initialTouchDistance;
        this.camera.position.z = THREE.MathUtils.clamp(
          this.camera.position.z - diff * 0.005,
          1.2,
          4.5
        );
        initialTouchDistance = currentDistance;
      }
    }, { passive: true });

    dom.addEventListener('touchend', () => {
      this.isDragging = false;
      initialTouchDistance = null;
    });

    // Wheel Zoom
    dom.addEventListener('wheel', (e) => {
      e.preventDefault();
      this.camera.position.z = THREE.MathUtils.clamp(
        this.camera.position.z + e.deltaY * 0.0015,
        1.2,
        4.5
      );
    }, { passive: false });

    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    if (!this.container || !this.renderer) return;
    const w = this.container.clientWidth;
    const h = this.container.clientHeight || 1;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  setCameraView(viewName) {
    if (viewName === 'courtship') {
      this.brainGroup.rotation.set(0.15, -0.4, 0);
      this.camera.position.set(0, 0, 1.8);
    } else if (viewName === 'dorsal') {
      this.brainGroup.rotation.set(Math.PI / 2.3, 0, 0);
      this.camera.position.set(0, 0, 2.2);
    } else if (viewName === 'lateral') {
      this.brainGroup.rotation.set(0, Math.PI / 2, 0);
      this.camera.position.set(0, 0, 2.2);
    } else {
      this.brainGroup.rotation.set(0.2, 0.4, 0);
      this.camera.position.set(0, 0.1, this.options.cameraDistance);
    }
  }

  animate() {
    requestAnimationFrame(() => this.animate());

    if (this.options.autoRotate && !this.isDragging) {
      this.brainGroup.rotation.y += this.options.autoRotateSpeed;
    }

    if (this.pulseSystem && this.pulsePool) {
      const posAttr = this.pulseSystem.geometry.attributes.position;
      const colAttr = this.pulseSystem.geometry.attributes.color;
      let hasActivePulses = false;

      this.pulsePool.forEach((pulse) => {
        if (!pulse.active) return;
        hasActivePulses = true;
        pulse.progress += pulse.speed;

        if (pulse.progress >= 1.0) {
          pulse.active = false;
          posAttr.setXYZ(pulse.index, 0, 0, 0);
          colAttr.setXYZ(pulse.index, 0, 0, 0);
        } else {
          const curX = THREE.MathUtils.lerp(pulse.src.x, pulse.tgt.x, pulse.progress);
          const curY = THREE.MathUtils.lerp(pulse.src.y, pulse.tgt.y, pulse.progress);
          const curZ = THREE.MathUtils.lerp(pulse.src.z, pulse.tgt.z, pulse.progress);

          posAttr.setXYZ(pulse.index, curX, curY, curZ);
          colAttr.setXYZ(pulse.index, pulse.color.r, pulse.color.g, pulse.color.b);
        }
      });

      if (hasActivePulses) {
        posAttr.needsUpdate = true;
        colAttr.needsUpdate = true;
      }
    }

    if (this.auraRing && this.auraRing.material.opacity > 0.12) {
      this.auraRing.material.opacity = Math.max(0.12, this.auraRing.material.opacity - 0.005);
    }

    if (this.clickableMeshes) {
      this.clickableMeshes.forEach((mesh) => {
        const data = mesh.userData;
        // Fast decay so a fresh jump pops, then settles back down to
        // restLevel (the real, persistent activation) — not all the way to
        // grey if the node is genuinely still active.
        data.flash *= 0.9;
        if (data.flash < 0.01) data.flash = 0;

        const level = Math.min(1.0, (data.restLevel || 0) + data.flash);
        const rest = data.restColor;
        const role = data.roleColor;

        mesh.material.color.setRGB(
          rest.r + (role.r - rest.r) * level,
          rest.g + (role.g - rest.g) * level,
          rest.b + (role.b - rest.b) * level
        );
        mesh.material.opacity = Math.min(1.0, data.baseOpacity + 0.7 * level);
        mesh.scale.setScalar(1 + level * (this.options.isMini ? 1.8 : 2.8));
      });
    }

    if (this.options.interactive && this.clickableMeshes && this.clickableMeshes.length > 0 && !this.options.isMini) {
      this.raycaster.setFromCamera(this.mouse, this.camera);
      const intersects = this.raycaster.intersectObjects(this.clickableMeshes);

      if (intersects.length > 0) {
        const top = intersects[0].object;
        if (this.hoveredNode !== top) {
          this.hoveredNode = top;
          if (this.onNodeHover) {
            this.onNodeHover(top.userData, {
              x: (this.mouse.x * 0.5 + 0.5) * this.container.clientWidth,
              y: (-this.mouse.y * 0.5 + 0.5) * this.container.clientHeight
            });
          }
        }
      } else if (this.hoveredNode) {
        this.hoveredNode = null;
        if (this.onNodeHover) {
          this.onNodeHover(null);
        }
      }
    }

    this.renderer.render(this.scene, this.camera);
  }
}

window.FlyBrainVisualizer = FlyBrainVisualizer;
window.ROLE_PALETTE = ROLE_PALETTE;
