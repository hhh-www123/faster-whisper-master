() => {
  // All rendering is derived from media.currentTime. No wall-clock typing timer.
  if (window.__speechSyncCleanup) window.__speechSyncCleanup();
  let frame, disposed = false, audio = null, root = null, rows = [], lineNodes = [], lastKey = '';
  let activated = false;
  let resultId = null;
  let payloadKey = '', lineContainer = null;
  let lastRenderTime = null, restoreScroll = null;
  function revealOffset(row, t) {
    if (t < row.start) return 0;
    if (t >= row.end) return row.display.length;
    let through = 0;
    for (const [start, offset] of row.steps) {
      if (t >= start) through = offset;
    }
    return through;
  }
  function timedText(tag, className, cue) {
    const node = document.createElement(tag);
    node.className = className;
    const shown = document.createElement('span');
    const upcoming = document.createElement('span');
    upcoming.className = 'caption-upcoming';
    upcoming.setAttribute('aria-hidden', 'true');
    upcoming.textContent = cue.display;
    node.append(shown, upcoming);
    return {node, shown, upcoming, cue, through: 0};
  }
  function updateText(text, time, visible) {
    const {cue, shown, upcoming} = text;
    const through = visible ? revealOffset(cue, time) : 0;
    if (through !== text.through) {
      shown.textContent = cue.display.slice(0, through);
      upcoming.textContent = cue.display.slice(through);
      text.through = through;
    }
    return through > 0;
  }
  function onPlay() { if (root && !window.speechTransport?.isWaiting()) { activated = true; lastKey = ''; } }
  function render() {
    if (disposed) return;
    const nextRoot = document.querySelector('#sync-captions .caption-data');
    const nextAudio = document.querySelector('#source-audio audio');
    const nextContainer = nextRoot?.querySelector('.caption-lines');
    const nextPayload = nextRoot ? nextRoot.dataset.segments + nextRoot.dataset.mode + nextRoot.dataset.language : '';
    if (nextAudio !== audio) {
      if (audio) audio.removeEventListener('play', onPlay);
      audio = nextAudio;
      if (audio) audio.addEventListener('play', onPlay);
    }
    if (nextRoot !== root || nextPayload !== payloadKey || nextContainer !== lineContainer ||
        (nextContainer && nextContainer.children.length !== rows.length)) {
      if (lineContainer && restoreScroll === null) restoreScroll = lineContainer.scrollTop;
      root = nextRoot;
      payloadKey = nextPayload;
      lineContainer = nextContainer;
      const nextId = root ? root.dataset.resultId : null;
      // Gradio can briefly remove the HTML root during a mode update.
      // Only a new nonempty result ID resets playback; a missing root is transient.
      const isNewResult = !!root && nextId !== resultId;
      if (isNewResult) restoreScroll = 0;
      else if (nextContainer && restoreScroll === null) restoreScroll = nextContainer.scrollTop;
      if (root) resultId = nextId;
      rows = root ? JSON.parse(root.dataset.segments) : [];
      lineNodes = [];
      lastKey = '';
      if (isNewResult) {
        activated = false;
        lastRenderTime = null;
        if (root && audio) { audio.pause(); audio.currentTime = 0; }
      }
      if (root) window.speechCaptionResultId = resultId;
      document.querySelector('#workspace')?.classList.toggle('has-played', !!root && activated);
      if (root) {
        const container = root.querySelector('.caption-lines');
        container.replaceChildren();
        rows.forEach((row) => {
          const line = document.createElement('div'); line.className = 'caption-line';
          line.hidden = true;
          const stamp = document.createElement('small');
          stamp.textContent = `${row.start.toFixed(2)}–${row.end.toFixed(2)}s`;
          const source = timedText('span', 'caption-source', row);
          const translations = (root.dataset.mode === '中文翻译' ? row.translations : [])
            .map(cue => timedText('div', 'caption-translation', cue));
          line.append(stamp, source.node, ...translations.map(text => text.node));
          container.append(line);
          lineNodes.push({line, source, translations});
        });
      }
    }
    if (root && audio) {
      const t = audio.currentTime || 0;
      const key = `${t}|${audio.ended}|${activated}`;
      if (key !== lastKey) {
        lastKey = key;
        const moved = lastRenderTime !== null && t !== lastRenderTime;
        lastRenderTime = t;
        document.querySelector('#workspace')?.classList.toggle('has-played', activated);
        root.querySelector('.caption-wait').hidden = activated;
        root.querySelector('.caption-clock').textContent = `${Math.floor(t/60).toString().padStart(2,'0')}:${Math.floor(t%60).toString().padStart(2,'0')}`;
        let follow = null, current = null, currentTranslation = null;
        rows.forEach((row, i) => {
          const visible = activated && t >= row.start;
          const node = lineNodes[i];
          const sourceVisible = updateText(node.source, t, visible);
          let translated = false, targetActive = false;
          for (const target of node.translations) {
            if (updateText(target, t, visible)) {
              translated = true;
              if (t < target.cue.end) targetActive = true;
            }
          }
          node.line.hidden = !visible || (!sourceVisible && !translated);
          if (!node.line.hidden) {
            follow = node;
            if (visible && t < row.end) current = node;
            if (targetActive) currentTranslation = node;
          }
        });
        const highlight = current || currentTranslation;
        for (const node of lineNodes) node.line.classList.toggle('active', node === highlight);
        const container = lineContainer;
        if (restoreScroll !== null) {
          container.scrollTop = restoreScroll;
          restoreScroll = null;
        }
        if (!follow && moved) container.scrollTop = 0;
        // Follow the growing text too, not just the start of a new sentence.
        // Pausing leaves the viewer free to scroll back and read older captions.
        if (follow && (!audio.paused || audio.ended || moved)) {
          const {line, source, translations} = follow;
          let bottom = line.offsetTop + line.offsetHeight;
          if (line.offsetHeight > container.clientHeight) {
            // Hidden words reserve their layout, but should not scroll the
            // visible prefix out of view in a very long unpunctuated cue.
            const bounds = line.getBoundingClientRect();
            const visibleText = translations.filter(text => text.through).map(text => text.shown);
            const visibleBottom = Math.max(source.shown.getBoundingClientRect().bottom,
              ...visibleText.map(node => node.getBoundingClientRect().bottom));
            bottom = line.offsetTop + visibleBottom - bounds.top;
          }
          if (bottom > container.scrollTop + container.clientHeight - 20 ||
              line.offsetTop < container.scrollTop) {
            container.scrollTop = Math.max(0, bottom - container.clientHeight + 20);
          }
        }
      }
    }
    frame = requestAnimationFrame(render);
  }
  frame = requestAnimationFrame(render);
  window.__speechSyncCleanup = () => { disposed = true; cancelAnimationFrame(frame); if (audio) audio.removeEventListener('play', onPlay); };
}
