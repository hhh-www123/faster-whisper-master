"""Real ASR once, then verify switching can never call ASR again."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import run, switch_mode
from core import ROOT, ChineseTranslator, caption_groups, WhisperModel


def main():
    path = str(ROOT / "datasets/data/en_us/audio/dev/10010138729160973689.wav")
    initial = list(run(path, "cpu", "语音转写"))[-1]
    cached = initial[-1]
    assert cached and "satellite" in cached["text"], initial[0]
    assert not cached["translation"]
    with patch.object(WhisperModel, "transcribe", side_effect=AssertionError("ASR was rerun")):
        translated = list(switch_mode("中文翻译", cached))[-1]
        assert "卫星" in translated[1], translated[0]
        assert translated[5]["_id"] == cached["_id"]
        assert translated[5]["asr_seconds"] == cached["asr_seconds"]
        assert not cached["translation"], "source session result mutated"
        with patch.object(ChineseTranslator, "translate", side_effect=AssertionError("translation was rerun")):
            original = list(switch_mode("语音转写", translated[5]))[-1]
            again = list(switch_mode("中文翻译", original[5]))[-1]
            assert again[1] == translated[1]
            assert again[5]["text"] == cached["text"]
            assert 'data-mode="语音转写"' in original[3]
            assert 'data-mode="中文翻译"' in again[3]
    # No extra translations or subtitle splits are introduced by punctuation,
    # sentence length, or gaps inside an original model segment.
    for text in [" Hello, world. I'm ready!", ' '.join(['word']*40)]:
        segment = dict(start=0,end=40,text=text,units=[dict(start=0,end=40,text=text)],translation='完整译文')
        grouped = caption_groups([segment])
        assert len(grouped) == 1
        assert grouped[0]['text'] == text
        assert grouped[0]['translations'] == [dict(start=0,text='完整译文')]
    groups = translated[5]['caption_lines']
    assert len(groups) == len(cached['segments'])
    assert [g['text'] for g in groups] == [s['text'] for s in cached['segments']]
    assert [g['translations'][0]['text'] for g in groups] == [s['translation'] for s in translated[5]['segments']]
    print('PASS: whole-segment translation, no clause splitting, cached modes and original text preservation')


if __name__ == '__main__':
    main()
