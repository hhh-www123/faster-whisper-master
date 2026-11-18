"""Real automatic ASR across uploads; a session stays armed until toggled off."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import audio_changed, auto_recognize, toggle_recognition
from core import ROOT, MANAGER


def main():
    enabled = toggle_recognition(False)[0]
    assert enabled
    samples = [('en_us', 'en', 'satellite'), ('cmn_hans_cn', 'zh', '伊拉克')]
    cached = None
    instance = None
    for directory, language, expected in samples:
        path = str(next((ROOT / 'datasets/data' / directory / 'audio/dev').glob('*.wav')))
        changed = audio_changed(path, enabled, cached)
        assert '点击播放即可自动识别' in changed[0]
        assert changed[-2] is None
        packets = list(auto_recognize(path, 'cpu', '语音转写', enabled, None))
        assert all(len(packet) == 14 for packet in packets)
        assert 'data-phase="working"' in packets[0][-1]
        partials = [p[12] for p in packets if isinstance(p[12], dict) and p[12].get('complete') is False]
        assert partials, 'no intermediate model output'
        assert all(p['_id'] == packets[-1][12]['_id'] for p in partials)
        assert [p['ready_until'] for p in partials] == sorted(p['ready_until'] for p in partials)
        assert partials[0]['segments'] is not packets[-1][12]['segments'], 'published state mutated'
        final = packets[-1]
        cached = final[12]
        assert cached and cached['language'] == language, final[0]
        assert expected in cached['text'], cached['text']
        assert 'data-phase="ready"' in final[-1]
        if instance is not None:
            assert MANAGER.model is instance, 'model reloaded for next file'
        instance = MANAGER.model
        with patch('app.recognize_iter', side_effect=AssertionError('duplicate ASR')):
            assert all(p.get('__type__') == 'update' for p in list(auto_recognize(path, 'cpu', '语音转写', enabled, cached))[-1])
            unchanged = audio_changed(path, enabled, cached)
            assert '已有识别结果' in unchanged[0]
            disabled = list(auto_recognize(path, 'cpu', '语音转写', False, None))[-1]
            assert '仅试听' in disabled[0]
    assert toggle_recognition(enabled)[0] is False
    failed = list(auto_recognize('', 'cpu', '语音转写', True, None))[-1]
    assert 'data-phase="error"' in failed[-1]
    assert failed[12] is None
    print('PASS: arm before upload, English/Chinese actual ASR, model reuse, cached replay, disabled preview and retry signal')


if __name__ == '__main__':
    main()
