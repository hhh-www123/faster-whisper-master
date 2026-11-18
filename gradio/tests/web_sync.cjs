// Exercise the actual shipped renderer with media time, without simulated ASR.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const appDir = path.resolve(__dirname, '..');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children=[]; this.textContent=''; this.offsetTop=0; this.offsetHeight=80; this.clientHeight=200; this.scrollTop=0; this.classes=new Set(); this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)}; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children=[]; }
}
const rows = JSON.parse(fs.readFileSync(path.join(appDir, 'test_output/zh/result.json'),'utf8')).segments;
const clock = new Element(), lines = new Element(), waiting = new Element(), workspace = new Element();
let root = new Element();
root.dataset = {segments:JSON.stringify(rows),resultId:'one',mode:'语音转写',language:'zh'};
root.querySelector = s => s === '.caption-clock' ? clock : s === '.caption-wait' ? waiting : lines;
const listeners = {};
const audio = {currentTime:100,ended:false,paused:true,
  pause(){this.paused=true;}, addEventListener(name,fn){listeners[name]=fn;},removeEventListener(name){delete listeners[name];}};
let nextFrame;
const context = {window:{},document:{querySelector:s=>s.includes('caption-data')?root:s==='#workspace'?workspace:audio,createElement:()=>new Element()},
  requestAnimationFrame:fn=>{nextFrame=fn;return 1;},cancelAnimationFrame:()=>{}};
vm.runInNewContext('('+fs.readFileSync(path.join(appDir, 'assets/sync.js'),'utf8')+')()',context);
function tick(time) { audio.currentTime=time; nextFrame(); }
nextFrame(); assert.equal(audio.currentTime,0,'result did not reset preview position');
tick(rows.at(-1).end); assert(lines.children.every(line=>line.hidden),'text appeared before play');
assert(!workspace.classes.has('has-played'));
assert(!waiting.hidden);
// A play event used to request ASR must not reveal stale subtitles while waiting.
context.window.speechTransport={isWaiting:()=>true};listeners.play();
tick(rows.at(-1).end);assert(lines.children.every(line=>line.hidden));
delete context.window.speechTransport;
assert.equal(context.window.speechCaptionResultId,'one');
audio.paused=false; listeners.play();
tick(0); assert(lines.children.every(line=>line.hidden));
const middle = rows[0].start+(rows[0].end-rows[0].start)/2;
tick(middle); const partial=lines.children[0].children[1].textContent;
assert(partial.length>0 && partial.length<rows[0].text.length);
assert(lines.children[0].classes.has('active'));
lines.children[0].offsetHeight=1000;
tick(middle+.01);assert.equal(lines.scrollTop,820,'long segment did not follow its growing text');
lines.children[0].offsetHeight=1400;
tick(middle+.02);assert.equal(lines.scrollTop,1220,'same segment growth was not followed');
tick(middle);
audio.paused=true; nextFrame(); assert.equal(lines.children[0].children[1].textContent,partial,'paused text changed');
tick(rows.at(-1).end+.1); assert.equal(lines.children.at(-1).children[1].textContent,rows.at(-1).text.replace(/\s/gu,''));
tick(middle); assert.equal(lines.children[0].children[1].textContent,partial,'seek backward did not restore text');
// Replacing caption HTML for a mode change must preserve media time and play state.
const replacement = new Element();
replacement.dataset = {segments:JSON.stringify(rows.map(r=>({...r,translations:[{start:r.start,text:'中文， 译文！'}]}))),resultId:'one',mode:'中文翻译',language:'zh'};
replacement.querySelector = root.querySelector;
root = null; nextFrame();
root = replacement; lines.children=[];
nextFrame(); assert.equal(audio.currentTime,middle,'mode switch reset playback');
assert.equal(lines.children[0].children[2].textContent,'中文，\n译文！');
assert.equal(lines.children[0].children[1].hidden,false,'translation hid the original');
assert.equal(lines.children[0].children[1].textContent,partial);
assert.equal(lines.children[0].children[2].hidden,false);
assert.equal(waiting.hidden,true,'mode switch lost playback activation');
// Gradio may reconcile inner HTML after our first paint, preserving the root.
lines.replaceChildren(); nextFrame();
assert.equal(lines.children[0].children[2].textContent,'中文，\n译文！','Gradio reconciliation lost captions');
tick(0); assert(lines.children.every(line=>line.hidden),'replay did not reset');
assert(workspace.classes.has('has-played'));
// Both lines are retained; Latin word spaces survive while CJK whitespace is removed.
function languageCase(language, source, expected, target='你好， 世界！', expectedTarget='你好，\n世界！') {
  root.dataset = {resultId:'one',mode:'中文翻译',language,segments:JSON.stringify([
    {start:1,end:2,text:source,units:[],translations:[{start:1,text:target}]}
  ])};
  tick(3);
  assert.equal(lines.children[0].children[1].textContent,expected);
  assert.equal(lines.children[0].children[1].hidden,false);
  assert.equal(lines.children[0].children[2].textContent,expectedTarget);
  // The DOM order is timestamp, original, Chinese translation.
  assert.equal(lines.children[0].children[1].className,'caption-source');
  assert.equal(lines.children[0].children[2].className,'caption-translation');
}
languageCase('en'," Hello,   world! You're welcome. ","Hello,\nworld! You're welcome.");
languageCase('es',' ¡Hola,   mundo! ¿Cómo estás? ','¡Hola,\nmundo! ¿Cómo estás?');
languageCase('ja',' こんにちは、 世界！ ','こんにちは、\n世界！');
languageCase('zh',' 你好， 世界！ ','你好，\n世界！');
languageCase('en','There are 1,000 items, priced at 3,14.','There are 1,000 items,\npriced at 3,14.');
languageCase('en','First clause,','First clause,','第一分句','第一分句，');
languageCase('en','The value is 3.14.','The value is 3.14.','值为3.14.','值为3.14。');
// Multi-sentence captions are ordered as foreign / Chinese / foreign / Chinese.
root.dataset={resultId:'one',mode:'中文翻译',language:'en',segments:JSON.stringify([
  {start:1,end:2,text:'Hello, world.',units:[],translations:[{start:1,text:'你好，世界。'}]},
  {start:3,end:4,text:"I'm ready!",units:[],translations:[{start:3,text:'我准备好了！'}]}
])};
tick(5);
assert.deepEqual(lines.children.flatMap(line=>[line.children[1].textContent,line.children[2].textContent]),
  ['Hello,\nworld.','你好，\n世界。',"I'm ready!",'我准备好了！']);
tick(2.5);assert(lines.children[1].hidden,'later bilingual pair appeared early');
root.dataset.mode='语音转写';nextFrame();
assert.equal(lines.children[0].children[2].hidden,true,'transcription mode showed the translation');
context.window.__speechSyncCleanup(); assert(!listeners.play);
console.log('PASS: alternating bilingual pairs, preserved punctuation, decimal safety, four-language whitespace, playback and mode switching');
