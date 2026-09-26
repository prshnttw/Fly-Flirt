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
