// Run with: node --test tests/test_frontend.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const THREE = require('../static/vendor/three.min.js');
const root = path.resolve(__dirname, '..');
const window = { THREE };
const context = vm.createContext({window, fetch: async url => ({ok:true, json:async () => JSON.parse(fs.readFileSync(path.join(root, url)))})});
vm.runInContext(fs.readFileSync(path.join(root, 'static/js/brain.js'), 'utf8'), context);
for (const scale of ['standard', 'full']) {
  test(`${scale}: entire rotating graph fits desktop and portrait canvases`, async () => {
    const data = await window.FlyBrain.load(scale);
    for (const [width, height] of [[1280,720],[390,406],[320,460]]) {
      let size;
      const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 50);
      camera.position.set(0,0.05,1.55);
      const view = {
        data, camera, opts:{fov:50,distance:1.55}, el:{clientWidth:width,clientHeight:height},
        renderer:{setSize:(...args)=>{size=args;},getPixelRatio:()=>2},uniforms:{uScale:{value:0}}
      };
      window.FlyBrain.prototype.resize.call(view);
      assert.deepEqual(size,[width,height], 'setSize must also update CSS dimensions at Retina resolution');
      camera.updateMatrixWorld();
      for (let angle = 0; angle < Math.PI * 2; angle += Math.PI / 8) {
        const rotation = new THREE.Euler(0.15,angle,0);
        for (let i = 0; i < data.pos.length; i += 3) {
          const p = new THREE.Vector3(...data.pos.slice(i,i+3)).applyEuler(rotation).project(camera);
          assert.ok(Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1 && Math.abs(p.z) <= 1, 'neuron is clipped');
        }
      }
      camera.position.z *= 1.2;
      const zoomed = camera.position.z;
      window.FlyBrain.prototype.resize.call(view);
      assert.equal(camera.position.z,zoomed,'a resize notification must preserve user zoom');
    }
  });
}

for (const scale of ['standard', 'full']) {
  test(`${scale}: 3D layout preserves neuron positions and real connections`, async () => {
    const data = await window.FlyBrain.load(scale);
    const raw = JSON.parse(fs.readFileSync(path.join(root, 'static', scale === 'full' ? 'connectome_full.json' : 'connectome.json')));
    assert.deepEqual(Array.from(data.ids), raw.cells.id);
    assert.deepEqual(Array.from(data.src), raw.edges.src);
    assert.deepEqual(Array.from(data.dst), raw.edges.dst);
    const positions = new Set();
    for (let i = 0; i < data.n; i++) {
      const p = Array.from(data.pos.slice(i * 3, i * 3 + 3));
      assert.ok(p.every(Number.isFinite));
      positions.add(p.join(','));
    }
    for (let i = 3; i < data.pos.length; i++) {
      assert.ok(Math.abs((data.pos[i] - data.pos[i % 3]) - (raw.cells.pos[i] - raw.cells.pos[i % 3])) < 1e-6, 'relative anatomical positions must be preserved');
    }
    const view = {data, group:new THREE.Group()};
    const proto = window.FlyBrain.prototype;
    proto._buildActiveLayer.call(view);
    proto._buildFocusEdges.call(view);
    proto._buildPulses.call(view);
    proto._addActiveEdge.call(view, 0);
    proto.focusEdges.call(view, [0], '#00b8a6');
    assert.ok(view.actLines.isLineSegments && view.focusLines.isLineSegments && view.pulses.isPoints);
    assert.equal(view.focusLines.geometry.drawRange.count, 2);
    const source = data.src[0], target = data.dst[0];
    for (let j = 0; j < 3; j++) {
      const expected = data.pos[source * 3 + j];
      assert.ok(Math.abs(view.actPos[j] - expected) < 1e-6, 'connection must start at its source neuron');
      assert.ok(Math.abs(view.focusPos[j] - expected) < 1e-6);
      assert.equal(view.actPos[3 + j], data.pos[target * 3 + j]);
      assert.equal(view.focusPos[3 + j], data.pos[target * 3 + j]);
    }
    assert.ok(view.pPos.every(x => x === 9999), 'inactive pulses must not pile up at the origin');
  });
}

test('cross-pathway activity maps real neuron identities and directed synapses', async () => {
  for (const [source, destination] of [['standard','full'], ['full','standard']]) {
    const from = await window.FlyBrain.load(source), to = await window.FlyBrain.load(destination);
    const wave = {scale:source, frames:[{cells:Array.from({length:from.n},(_,i)=>i), acts:Array(from.n).fill(.7), edges:Array.from({length:from.src.length},(_,i)=>i)}]};
    const mapped = await window.FlyBrain.mapActivity(wave,destination);
    const frame = mapped.frames[0];
    const sourceIds = new Set(from.ids);
    assert.equal(frame.cells.length, to.ids.filter(id => sourceIds.has(id)).length);
    assert.equal(frame.cells.length, frame.acts.length);
    const realEdges = new Set(Array.from(from.src,(s,e)=>from.ids[s]+':'+from.ids[from.dst[e]]));
    for (const e of frame.edges) {
      assert.ok(e >= 0 && e < to.src.length);
      assert.ok(realEdges.has(to.ids[to.src[e]]+':'+to.ids[to.dst[e]]));
    }
    assert.ok(frame.edges.length > 0);
  }
});
