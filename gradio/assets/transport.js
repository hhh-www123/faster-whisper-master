() => {
  if (window.__speechTransportCleanup) window.__speechTransportCleanup();
  let audio = null, pending = null, lastEnabled = false, followingJob = '', frame, disposed = false;
  const normalize = path => (path || '').replace(/\\/g, '/').toLowerCase();
  const lockedInputs = new Map();
  function mediaKey() {
    const url = audio?.getAttribute('src') || audio?.currentSrc || '';
    try { return normalize(decodeURIComponent(url).split('/gradio_api/file=')[1]?.split('?')[0]); }
    catch { return ''; }
  }
  function signal() { return document.querySelector('#playback-signal [data-phase]')?.dataset || {}; }
  function enabled() { return document.querySelector('#recognition-session [data-enabled]')?.dataset.enabled === 'true'; }
  function notice(message) {
    const node = document.querySelector('#playback-notice .player-notice');
    if (node) node.textContent = message;
  }
  function isReady(key) {
    const root = document.querySelector('#sync-captions .caption-data');
    const state = signal();
    return !!key && (normalize(root?.dataset.audioPath) === key ||
      (state.phase === 'ready' && normalize(state.path) === key));
  }
  function playAudio() {
    const promise = audio.play();
    promise?.catch(() => notice('识别已完成，浏览器阻止了自动播放，请再点一次播放键。'));
  }
  function available(state, key) {
    return normalize(state.path) === key && (state.phase === 'partial' || state.phase === 'ready');
  }
  function waitForCaptions(key, resumeAt, previousJob) {
    pending = {key, resumeAt, previousJob};
    audio.pause();
    notice(resumeAt ? '正在补充后续字幕，稍后继续播放…' : '正在准备首段字幕，稍后自动播放…');
  }
  function onPlay() {
    const key = mediaKey(), state = signal();
    if (enabled() && key && !isReady(key)) {
      // Gradio receives this play event and starts one real inference generator.
      waitForCaptions(key, 0, state.phase === 'working' && normalize(state.path) === key ? '' : state.job || '');
    } else if (enabled() && available(state, key) && state.phase === 'partial' &&
               audio.currentTime >= Number(state.readyUntil) - .15) {
      waitForCaptions(key, audio.currentTime, '');
    }
  }
  function render() {
    if (disposed) return;
    const next = document.querySelector('#source-audio audio');
    if (next !== audio) {
      audio?.removeEventListener('play', onPlay);
      audio = next;
      audio?.addEventListener('play', onPlay);
    }
    const on = enabled(), key = mediaKey(), state = signal();
    // Keep the native audio element mounted while inference runs. Changing
    // Gradio Audio.interactive would replace it and reset a playing recording.
    const processing = normalize(state.path) === key && (state.phase === 'working' || state.phase === 'partial');
    document.querySelector('#source-audio')?.classList.toggle('is-processing', processing);
    if (processing) {
      document.querySelectorAll('#source-audio button, #source-audio input[type="file"], #audio-examples button').forEach(node => {
        if (!lockedInputs.has(node)) lockedInputs.set(node, node.disabled);
        node.disabled = true;
      });
    } else {
      lockedInputs.forEach((disabled, node) => { node.disabled = disabled; });
      lockedInputs.clear();
    }
    if (lastEnabled && !on) { pending = null; followingJob = ''; audio?.pause(); notice(''); }
    lastEnabled = on;
    // Updating Audio.interactive can briefly replace the native media node.
    if (pending && key && pending.key !== key) { pending = null; followingJob = ''; notice(''); }
    // Never play beyond confirmed timestamps. If inference is slower than audio,
    // hold the actual media clock and resume after a small buffer is available.
    if (!pending && on && audio && !audio.paused && available(state, key) &&
        state.phase === 'partial' && audio.currentTime >= Number(state.readyUntil) - .15) {
      waitForCaptions(key, audio.currentTime, '');
    }
    if (on && state.phase === 'error' && state.job === followingJob) {
      audio?.pause(); followingJob = '';
      notice('识别中断，请检查提示后再次点击播放重试。');
    }
    if (pending && state.job && state.job !== pending.previousJob && normalize(state.path) === key) {
      if (state.phase === 'error') { pending = null; notice('识别失败，可检查提示后再次点击播放重试。'); }
      else if ((state.phase === 'ready' || (state.phase === 'partial' && Number(state.readyUntil) >= pending.resumeAt + 3)) &&
        (state.hasText !== 'true' || window.speechCaptionResultId === state.result)) {
        const resumeAt = pending.resumeAt;
        pending = null;
        notice('');
        if (on && audio) { followingJob = state.job; audio.currentTime = resumeAt; playAudio(); }
      }
    }
    const start = document.querySelector('#recognize-button');
    if (start) {
      start.dataset.icon = on ? '❚❚' : '▶';
      start.classList.toggle('is-on', on);
      start.setAttribute('aria-label', on ? '暂停自动识别' : '开启自动识别');
      start.setAttribute('aria-pressed', String(on));
      start.title = on ? '识别持续开启，点击暂停' : '开启后，上传音频点播放即可识别';
    }
    const play = document.querySelector('#play-button');
    if (play) {
      const playing = !!audio && !audio.paused && !audio.ended;
      play.dataset.icon = playing || pending ? '❚❚' : '▶';
      play.disabled = !key || !!pending;
      play.classList.toggle('is-on', playing);
      play.classList.toggle('is-busy', !!pending);
      play.setAttribute('aria-label', pending ? '识别处理中' : playing ? '暂停音频' : '播放音频');
      play.setAttribute('aria-busy', String(!!pending));
    }
    frame = requestAnimationFrame(render);
  }
  window.speechTransport = {
    isWaiting: () => !!pending,
    togglePlayback: () => {
      if (!audio || !mediaKey() || pending) return;
      notice('');
      if (audio.paused || audio.ended) playAudio(); else audio.pause();
    }
  };
  frame = requestAnimationFrame(render);
  window.__speechTransportCleanup = () => {
    disposed = true; cancelAnimationFrame(frame);
    audio?.removeEventListener('play', onPlay);
    lockedInputs.forEach((disabled, node) => { node.disabled = disabled; });
    delete window.speechTransport;
  };
}
