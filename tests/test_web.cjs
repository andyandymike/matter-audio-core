// Browser-independent controller tests. No audio device or real browser is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/matter_audio_core/web/app.js'), 'utf8');

function setup() {
  const nodes = new Map(), storage = new Map(), started = [], gains = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {id, value: 0, checked: false, textContent: '',
      setAttribute() {}, append() {}, replaceChildren() {}, classList: {toggle() {}}, dataset: {},
      getBoundingClientRect() { return {width: 400, height: 100}; },
      getContext() { return {clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}}; }});
    return nodes.get(id);
  }
  class AudioContext {
    currentTime = 10;
    async resume() {}
    async decodeAudioData() { return {}; }
    createGain() { const gain = {gain: {value: 0}, connect() {}, disconnect() { this.disconnected = true; }}; gains.push(gain); return gain; }
    createBufferSource() { return {connect() {}, disconnect() {}, stop() {}, start(...args) { started.push({args, source: this}); }}; }
  }
  const scope = vm.createContext({document: {getElementById: node, querySelectorAll() { return []; }, createElement: node},
    location: {hash: ''}, window: {addEventListener() {}}, devicePixelRatio: 1, AudioContext, crypto: {randomUUID: () => 'id'},
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    fetch: async () => ({ok: true, arrayBuffer: async () => new ArrayBuffer(0)}), console});
  vm.runInContext(source, scope);
  const item = (id, level) => ({asset_id: id, label: id, media: {frame_count: 8000, sample_rate_hz: 8000, duration_seconds: 1, channels: 1},
    levels: {rms_dbfs: level}, waveform: [], protected_regions: []});
  scope.fixture = {candidates: [item('a', -10), item('b', -20)], audition: {request: {reference_asset_id: 'a'}},
    session: {current: {selected_asset: {asset_id: 'b'}}}};
  vm.runInContext("state = fixture; previewId = 'b'; variantId = 'b';", scope);
  node('start').value = 2000; node('end').value = 6000;
  return {scope, node, started, gains, storage, run: script => vm.runInContext(script, scope)};
}

(async () => {
  let test = setup();
  test.node('match').checked = true;
  await test.run("play()");
  assert.equal(test.gains.at(-1).gain.value, 1);
  test.run("previewId = 'a'");
  await test.run("play()");
  assert.ok(Math.abs(test.gains.at(-1).gain.value - 10 ** (-10 / 20)) < 1e-12);
  test.started[0].source.onended();
  assert.equal(test.gains[0].disconnected, true, 'An older playback group must release its own gain');
  assert.equal(test.run('playing.length'), 1, 'Older ended events must not stop the new group');

  test = setup();
  await test.run("play('repeat')");
  assert.equal(test.started.length, 4);
  assert.deepEqual(test.started.map(x => x.args), [[10, .25, .5], [10.7, .25, .5], [11.4, .25, .5], [12.1, .25, .5]]);
  test = setup();
  await test.run("play('loop')");
  assert.equal(test.started[0].source.loopStart, .25);
  assert.equal(test.started[0].source.loopEnd, .75);

  test = setup();
  let release, entered;
  const fetching = new Promise(resolve => { entered = resolve; });
  test.scope.fetch = () => { entered(); return new Promise(resolve => { release = resolve; }); };
  const pending = test.run("play()");
  await fetching;
  test.run("stop()");
  release({ok: true, arrayBuffer: async () => new ArrayBuffer(0)});
  await pending;
  assert.equal(test.started.length, 0, 'Stopping during load must prevent delayed playback');

  test = setup();
  let attempts = 0;
  const committed = new Set();
  test.scope.fetch = async (url, options) => {
    committed.add(JSON.parse(options.body).request_id);
    if (++attempts === 1) throw new Error('Lost response after commit');
    return {ok: true, json: async () => ({revision: 2})};
  };
  await assert.rejects(test.run("mutation('/api/select', {request_id: 'stable-id'})"));
  assert.equal(test.storage.size, 1);
  await assert.rejects(test.run("mutation('/api/feedback', {request_id: 'duplicate-risk'})"));
  await test.run("recoverPending()");
  assert.deepEqual([...committed], ['stable-id']);
  assert.equal(test.storage.size, 0);
  console.log('PASS: A/B gain, repeated ranges, loop boundaries, stop during load, lost-response recovery');
})().catch(error => { console.error(error); process.exitCode = 1; });
