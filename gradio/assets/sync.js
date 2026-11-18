() => {
  // All rendering is derived from media.currentTime. No wall-clock typing timer.
  if (window.__speechSyncCleanup) window.__speechSyncCleanup();
  let frame, disposed = false, audio = null, root = null, rows = [], lineNodes = [], lastKey = '';
  let activated = false;
  let resultId = null;
  let payloadKey = '', lineContainer = null;
  let lastRenderTime = null;
  // Formatting is display-only: raw transcripts, translations and timestamps stay intact.
  function captionText(text, language) {
    const value = language === 'en' || language === 'es'
      ? text.replace(/\s+/gu, ' ').trim()
      : text.replace(/\s+/gu, '');
    // Display-only wrapping: keep whole-sentence translation and raw exports.
    // A comma between digits (1,000 or 3,14) is not a clause boundary.
    return value.replace(/[，、,]/gu, (mark, index) =>
      /\d/u.test(value[index-1] || '') && /\d/u.test(value[index+1] || '') ? mark : mark + '\n')
      .replace(/\n[ \t]+/gu, '\n').trimEnd();
  }
  function chineseCaption(text, row) {
    const punctuation = {',':'，',';':'；',':':'：','!':'！','?':'？','.':'。','、':'、','，':'，','。':'。','！':'！','？':'？','；':'；','：':'：','…':'…'};
    let value = captionText(text, 'zh').replace(/\.{3,}/gu, '…')
      .replace(/[,;:!?]/gu, mark => punctuation[mark])
      .replace(/\.(?=$|["'”’」』）)])/gu, '。');
    if (value && !/[,，、;；:：.!?。！？…]["'”’」』）)]*$/u.test(value)) {
      // Some offline translations omit terminal punctuation. Restore the source
      // clause boundary for display; this does not change the model's raw output.
      const ending = row.text.trim().match(/([,，、;；:：.!?。！？…])["'”’」』）)]*$/u);
      value += ending ? punctuation[ending[1]] : '。';
    }
    return value;
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
      root = nextRoot;
      payloadKey = nextPayload;
      lineContainer = nextContainer;
      const nextId = root ? root.dataset.resultId : null;
      // Gradio can briefly remove the HTML root during a mode update.
      // Only a new nonempty result ID resets playback; a missing root is transient.
      const isNewResult = !!root && nextId !== resultId;
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
          const text = document.createElement('span'); text.className = 'caption-source';
          const translation = document.createElement('div'); translation.className = 'caption-translation';
          line.append(stamp, text, translation); container.append(line);
          lineNodes.push({line, text, translation});
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
        let follow = null;
        rows.forEach((row, i) => {
          const active = activated && t >= row.start && t < row.end;
          const visible = activated && t >= row.start;
          const node = lineNodes[i];
          node.line.hidden = !visible;
          node.line.classList.toggle('active', active);
          const source = !visible ? '' : t >= row.end ? row.text : (row.units || []).filter(u => t >= u.start).map(u => u.text).join('');
          node.text.textContent = captionText(source, root.dataset.language);
          const translated = (row.translations || []).filter(item => t >= item.start).map(item => item.text).join(' ');
          const translationMode = root.dataset.mode === '中文翻译';
          // Bilingual mode always keeps the original above its Chinese translation.
          node.text.hidden = false;
          node.translation.textContent = visible && translationMode ? chineseCaption(translated, row) : '';
          node.translation.hidden = !node.translation.textContent;
          node.line.hidden = !visible || (!node.text.textContent && !node.translation.textContent);
          if (!node.line.hidden) follow = node.line;
        });
        // Follow the growing text too, not just the start of a new segment.
        // Pausing leaves the viewer free to scroll back and read older captions.
        if (follow && (!audio.paused || audio.ended || moved)) {
          const container = root.querySelector('.caption-lines');
          const bottom = follow.offsetTop + follow.offsetHeight;
          if (bottom > container.scrollTop + container.clientHeight - 20 ||
              follow.offsetTop < container.scrollTop) {
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
