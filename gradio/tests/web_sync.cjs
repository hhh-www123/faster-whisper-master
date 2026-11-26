// Test the shipped renderer against its sentence cue contract.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() {
    this.children = [];
    this.textContent = '';
    this.scrollTop = 0;
    this.clientHeight = 200;
    this.offsetTop = 0;
    this.offsetHeight = 80;
    this.visibleHeight = 80;
    this.classes = new Set();
    this.classList = {toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name)};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  setAttribute() {}
  getBoundingClientRect() { return {top: this.offsetTop, bottom: this.offsetTop + this.visibleHeight}; }
}

const rows = [
  {start: 1, end: 3, display: 'Hello, world.', steps: [[1, 6], [2, 13]], translations: [
    {start: 1, end: 2, display: '你好，世界。', steps: [[1, 1], [1.2, 2], [1.4, 3], [1.6, 4], [1.8, 5], [2, 6]]},
    {start: 2, end: 3, display: '欢迎回来。', steps: [[2, 1], [2.25, 2], [2.5, 3], [2.75, 4], [3, 5]]}
  ]},
  {start: 4, end: 6, display: "I'm ready!", steps: [[4, 3], [5, 10]], translations: [
    {start: 4, end: 6, display: '准备好了。', steps: [[4, 1], [4.5, 2], [5, 3], [5.5, 4], [6, 5]]}
  ]}
];
const clock = new Element(), lines = new Element(), wait = new Element(), workspace = new Element();
const root = new Element();
root.dataset = {segments: JSON.stringify(rows), resultId: 'one', mode: '语音转写', language: 'en'};
root.querySelector = s => s === '.caption-clock' ? clock : s === '.caption-wait' ? wait : lines;
let attached = root;
let frame;
const listeners = {};
const audio = {
  currentTime: 100, ended: false, paused: true,
  pause() { this.paused = true; },
  addEventListener(name, fn) { listeners[name] = fn; },
  removeEventListener(name) { delete listeners[name]; }
};
const context = {
  window: {}, document: {
    querySelector: s => s.includes('caption-data') ? attached : s === '#workspace' ? workspace : audio,
    createElement: () => new Element()
  },
  requestAnimationFrame: fn => (frame = fn, 1), cancelAnimationFrame: () => {}
};
vm.runInNewContext('(' + fs.readFileSync(path.join(__dirname, '../assets/sync.js'), 'utf8') + ')()', context);
function tick(time) { audio.currentTime = time; frame(); }
const source = i => lines.children[i].children[1].children;
const target = (i, j = 0) => lines.children[i].children[2 + j].children;

frame();
assert.equal(audio.currentTime, 0);
tick(6);
assert(lines.children.every(line => line.hidden));
assert(!workspace.classes.has('has-played') && !wait.hidden);
context.window.speechTransport = {isWaiting: () => true};
listeners.play();
tick(6);
assert(lines.children.every(line => line.hidden));
delete context.window.speechTransport;
assert.equal(context.window.speechCaptionResultId, 'one');

audio.paused = false;
listeners.play();
tick(1.25);
assert.equal(source(0)[0].textContent, 'Hello,');
assert.equal(source(0)[1].textContent, ' world.', 'future text must reserve space');
assert(lines.children[0].classes.has('active'));
assert(lines.children[1].hidden);
lines.children[0].offsetHeight = 1000;
tick(2);
assert.equal(lines.scrollTop, 0, 'reserved text pushed the visible subtitle off screen');
source(0)[0].visibleHeight = 500;
tick(2.1);
assert.equal(lines.scrollTop, 320, 'scroll did not follow tall visible text');
assert.equal(source(0)[0].textContent, 'Hello, world.');
assert.equal(source(0)[1].textContent, '');
audio.paused = true;
frame();
assert.equal(source(0)[0].textContent, 'Hello, world.');
tick(1.25);
assert.equal(source(0)[0].textContent, 'Hello,', 'backward seek must restore partial text');

// The same result ID survives mode switches and incremental HTML replacement.
attached = null;
frame();
attached = root;
root.dataset.mode = '中文翻译';
lines.replaceChildren();
frame();
assert.equal(audio.currentTime, 1.25);
assert.equal(source(0)[0].textContent, 'Hello,');
assert.equal(target(0)[0].textContent, '你好');
assert.equal(target(0)[1].textContent, '，世界。');
assert.equal(target(0, 1)[0].textContent, '');
tick(2);
assert.equal(target(0)[0].textContent, '你好，世界。');
assert.equal(target(0, 1)[0].textContent, '欢');
tick(2.8);
assert.equal(target(0, 1)[0].textContent, '欢迎回来');
frame();
assert.equal(target(0, 1)[0].textContent, '欢迎回来', 'paused translation must not advance');
tick(3);
assert.equal(target(0, 1)[0].textContent, '欢迎回来。');
tick(5);
assert.equal(source(1)[0].textContent, "I'm ready!");
assert.equal(target(1)[0].textContent, '准备好');
tick(1);
assert.equal(target(0)[0].textContent, '你', 'a backward seek must restore the translated prefix');
assert.equal(target(1)[0].textContent, '');
tick(0);
assert(lines.children.every(line => line.hidden));
assert.equal(lines.scrollTop, 0);
context.window.__speechSyncCleanup();
assert(!listeners.play);
console.log('PASS: stable sentence layout, timed source and translation, pause, seek, replay, scroll and mode switching');
