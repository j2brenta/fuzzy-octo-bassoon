// Run with: node --test tests/test_brain_animation.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('packet arrival sparks its target; pause and reduced motion stop the clock', () => {
  const frames = new Map();
  const paused = new Set();
  const sparks = new Set();
  const motion = { matches: false, addEventListener() {} };
  const attributes = {};
  const packet = {
    dataset: { route: 'route', target: 'target', duration: '1000', offset: '950' },
    setAttribute(name, value) { attributes[name] = value; },
  };
  const route = { getTotalLength: () => 100, getPointAtLength: x => ({ x, y: 0 }) };
  let id = 0;
  const context = vm.createContext({
    document: {
      querySelectorAll: selector => selector === '.brain-packet' ? [packet] : [],
      getElementById: name => name === 'route' ? route : { classList: sparks },
    },
    graphPanel: { classList: { contains: name => paused.has(name) } },
    matchMedia: () => motion,
    requestAnimationFrame(callback) { frames.set(++id, callback); return id; },
    cancelAnimationFrame(frame) { frames.delete(frame); },
  });
  const source = fs.readFileSync('refresher/static/app.js', 'utf8');
  vm.runInContext(source.slice(source.indexOf('// Packet positions and node arrival')), context);
  function tick(time) {
    assert.equal(frames.size, 1, 'only one animation loop should run');
    const [key, callback] = frames.entries().next().value;
    frames.delete(key);
    callback(time);
  }
  tick(0);
  assert.equal(sparks.size, 0);
  tick(100);
  assert.ok(sparks.has('is-sparking'), 'spark must coincide with arrival');
  const beforePause = attributes.transform;
  paused.add('motion-paused');
  vm.runInContext('syncBrainAnimation()', context);
  assert.equal(frames.size, 0);
  paused.clear();
  vm.runInContext('syncBrainAnimation()', context);
  tick(100000);
  assert.equal(attributes.transform, beforePause, 'resume must not jump over paused time');
  motion.matches = true;
  vm.runInContext('syncBrainAnimation()', context);
  assert.equal(frames.size, 0);
});
