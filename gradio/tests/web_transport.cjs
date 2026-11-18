// Exercise the shipped transport state machine; actual ASR is tested in Python.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assert = require('node:assert/strict');
function button() {
  return {dataset:{}, attrs:{}, classes:new Set(), classList:{toggle(name,on){on?this.owner.classes.add(name):this.owner.classes.delete(name);}},
    setAttribute(k,v){this.attrs[k]=v;}};
}
const start=button(), play=button(), message={textContent:''};
start.classList.owner=start; play.classList.owner=play;
const session={dataset:{enabled:'false'}}, signal={dataset:{phase:'idle'}};
let audio=null, root=null, frame;
const window={};
const nodes={'#recognize-button':start,'#play-button':play,'#playback-notice .player-notice':message,
  '#recognition-session [data-enabled]':session,'#playback-signal [data-phase]':signal};
const context={window, document:{querySelectorAll:()=>[],querySelector:s=>s==='#source-audio audio'?audio:s.includes('caption-data')?root:nodes[s]},
  requestAnimationFrame:fn=>(frame=fn,1),cancelAnimationFrame:()=>{}};
vm.runInNewContext('('+fs.readFileSync(path.join(__dirname,'../assets/transport.js'),'utf8')+')()',context);
function tick(){frame();}
function media(name){
  const listeners={};
  return {paused:true,ended:false,currentTime:0,plays:0,
    getAttribute:()=>`http://localhost/gradio_api/file=${encodeURIComponent('C:/audio/'+name+'.wav')}`,
    addEventListener:(e,fn)=>{listeners[e]=fn;},removeEventListener:e=>{delete listeners[e];},
    pause(){this.paused=true;},play(){this.paused=false;this.ended=false;this.plays++;listeners.play?.();return Promise.resolve();}};
}
tick();assert(play.disabled);assert.equal(start.dataset.icon,'▶');
session.dataset.enabled='true';tick();assert.equal(start.dataset.icon,'❚❚');
audio=media('one');tick();window.speechTransport.togglePlayback();tick();
assert(audio.paused);assert(window.speechTransport.isWaiting());assert(play.disabled);
signal.dataset={phase:'working',path:'C:\\audio\\one.wav',job:'a'};tick();assert(audio.paused);
// Audio DOM can be transiently absent during an interactive prop update.
const original=audio;audio=null;tick();assert(window.speechTransport.isWaiting());audio=original;tick();
signal.dataset={phase:'ready',path:'C:\\audio\\one.wav',job:'a',result:'one',hasText:'true'};
tick();assert(audio.paused,'played before subtitle renderer accepted result');
root={dataset:{audioPath:'C:\\audio\\one.wav'}};window.speechCaptionResultId='one';tick();
assert(!audio.paused);assert.equal(audio.currentTime,0);assert.equal(audio.plays,2);assert(!window.speechTransport.isWaiting());
window.speechTransport.togglePlayback();tick();assert(audio.paused);assert.equal(play.dataset.icon,'▶');
window.speechTransport.togglePlayback();tick();assert(!audio.paused);assert(!window.speechTransport.isWaiting(),'cached replay triggered ASR wait');
// A long recording starts after confirmed text is buffered, before final ASR.
audio=media('long');root=null;signal.dataset={phase:'idle'};tick();window.speechTransport.togglePlayback();tick();
root={dataset:{audioPath:'C:/audio/long.wav'}};window.speechCaptionResultId='long';
signal.dataset={phase:'partial',path:'C:/audio/long.wav',job:'stream',result:'long',hasText:'true',readyUntil:'2'};
tick();assert(audio.paused,'insufficient startup buffer');
signal.dataset.readyUntil='8';tick();assert(!audio.paused,'partial result did not start playback');
audio.currentTime=7.9;tick();assert(audio.paused);assert(window.speechTransport.isWaiting(),'buffer underrun did not pause media');
signal.dataset.readyUntil='12';tick();assert(!audio.paused);assert.equal(audio.currentTime,7.9,'buffer resume reset playback');
window.speechTransport.togglePlayback();tick();assert(audio.paused);
signal.dataset.readyUntil='16';tick();assert(audio.paused,'incoming result overrode manual pause');
audio.currentTime=50;window.speechTransport.togglePlayback();tick();assert(audio.paused,'seek beyond known text was allowed');
signal.dataset.readyUntil='54';tick();assert(!audio.paused);assert.equal(audio.currentTime,50);
audio=media('two');root=null;tick();window.speechTransport.togglePlayback();tick();assert(window.speechTransport.isWaiting());
// An old ready signal cannot satisfy a newly uploaded file.
assert(audio.paused);session.dataset.enabled='false';tick();assert(!window.speechTransport.isWaiting());
signal.dataset={phase:'ready',path:'C:/audio/two.wav',job:'b',result:'two',hasText:'false'};tick();assert(audio.paused,'disabled session autoplayed');
window.speechTransport.togglePlayback();tick();assert(!audio.paused,'disabled preview blocked');
audio.pause();session.dataset.enabled='true';signal.dataset={phase:'idle'};tick();window.speechTransport.togglePlayback();tick();
signal.dataset={phase:'error',path:'C:/audio/two.wav',job:'c'};tick();assert(!window.speechTransport.isWaiting());
window.speechTransport.togglePlayback();tick();assert(window.speechTransport.isWaiting(),'stale error cancelled retry');
signal.dataset={phase:'ready',path:'C:/audio/two.wav',job:'d',result:'silent',hasText:'false'};tick();assert(!audio.paused,'silent result did not resume playback');
window.__speechTransportCleanup();assert(!window.speechTransport);
console.log('PASS: persistent toggle, deferred autoplay, cached replay, replaced audio, disabled preview, error retry and silence');
