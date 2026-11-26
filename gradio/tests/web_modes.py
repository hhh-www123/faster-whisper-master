"""Real ASR once, then verify switching can never call ASR again."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import run, switch_mode
from core import ROOT, ChineseTranslator, WhisperModel


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
    groups = translated[5]['sentence_lines']
    assert ''.join(g['text'] for g in groups).strip() == cached['text']
    assert all(g['translations'] for g in groups)
    print('PASS: cached modes, bilingual sentence cues and original text preservation')


if __name__ == '__main__':
    main()
