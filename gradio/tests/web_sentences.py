"""Compare sentence captions with raw segments from real local audio."""
import argparse
import copy
import json
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ROOT, load_audio, recognize, timed_units
from subtitles import Pauses, sentence_captions


def fixture(parts, language):
    rows = [dict(text=text, start=start, end=end, words=[]) for text, start, end in parts]
    for row in rows:
        row["units"] = timed_units(row, language)
    return rows


def rules():
    text = "Dr. Smith met Mr. Jones in the U.S. on Nov. 26. It was 3.14 at example.com. Then they left!"
    lines = sentence_captions(fixture([(text, 0, 10)], "en"), "en")
    assert [r["display"] for r in lines] == [
        "Dr. Smith met Mr. Jones in the U.S. on Nov. 26.",
        "It was 3.14 at example.com.", "Then they left!",
    ]
    text = "Visite https://example.com/a.b?x=3.14. ¿Cómo estás? ¡Muy bien!"
    cues = sentence_captions(fixture([(text, 0, 10)], "es"), "es")
    assert [r["display"] for r in cues] == [
        "Visite https://example.com/a.b?x=3.14.", "¿Cómo estás?", "¡Muy bien!",
    ]
    rows = fixture([("今天我们介绍 Whisper", 0, 2), ("模型，它支持多语言", 2.1, 4),
                    ("语音识别。接下来讲解原理。", 4.1, 7)], "zh")
    cues = sentence_captions(rows, "zh")
    assert [r["display"] for r in cues] == ["今天我们介绍 Whisper模型，它支持多语言语音识别。", "接下来讲解原理。"]
    assert cues[0]["start"] == 0 and 4.1 < cues[1]["start"] < 7
    rows = fixture([("今日の説明を始めます", 0, 2), ("次に、実例を見ます", 3.2, 5)], "ja")
    assert len(sentence_captions(rows, "ja")) == 2
    assert len(sentence_captions(rows, "ja", pauses=Pauses(long=2, strong=1.5))) == 1
    assert all("。" not in r["display"] for r in sentence_captions(rows, "ja"))

    rows = fixture([("One, two. Three!", 0, 6), (" Four.", 6.1, 8)], "en")
    rows[0]["translation"] = "原始整段只有一条译文。"
    rows[1]["translation"] = "四。结束了。"
    split_translations = {"One, two.": "一、二。", "Three!": "接下来是三！"}
    cues = sentence_captions(rows, "en", translate=split_translations.__getitem__)
    assert [[t["display"] for t in row["translations"]] for row in cues] == [
        ["一、二。"], ["接下来是三！"], ["四。", "结束了。"]
    ]
    _assert_translation_clocks(cues)

    rows = fixture([("When you arrive", 0, 2), (", please let me know.", 2.2, 4)], "en")
    rows[0]["translation"], rows[1]["translation"] = "当你到达", "请告诉我。"
    cue, = sentence_captions(rows, "en", translate=split_translations.__getitem__)
    assert cue["translations"][0]["display"] == "当你到达请告诉我。"
    long = "我们今天讨论如何根据实际停顿和语言标点组织一条较长的自然字幕，并检查字幕播放时能否在保持句子完整的情况下维持版面稳定。"
    cue, = sentence_captions(fixture([(long, 0, 8)], "zh"), "zh")
    assert cue["display"].count("\n") == 1 and cue["text"] == long
    assert cue["steps"][-1][1] == len(cue["display"])
    cue, = sentence_captions(fixture([("🙂 Hello.", 0, 5)], "en"), "en")
    assert cue["steps"][0][1] == 2


def _assert_translation_clocks(cues):
    for row in cues:
        assert row["translations"]
        for target in row["translations"]:
            times = [step[0] for step in target["steps"]]
            assert times[0] == target["start"] and times == sorted(times)
            assert row["start"] <= times[0] <= times[-1] <= row["end"]
            if len(times) > 1:
                assert times[-1] == target["end"]


def pack(destination, files, gaps):
    samples = []
    for i, path in enumerate(files):
        if i:
            samples.append(np.zeros(round(gaps[i-1] * 16000), dtype=np.float32))
        samples.append(load_audio(path))
    with wave.open(str(destination), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(np.concatenate(samples), -1, 1) * 32767).astype("<i2").tobytes())


def real_audio(long_chinese=None):
    base = ROOT / "datasets/data"
    report = {}
    for language, dataset, gaps in [("zh", "cmn_hans_cn", [.25]), ("en", "en_us", [.65, .85]),
                                    ("ja", "ja_jp", [3]), ("es", "es_419", [.75])]:
        folder = base / dataset / "audio/dev"
        files = list(sorted(folder.glob("*.wav")))[:2]
        if language == "en":
            files = [folder / f"{n}.wav" for n in ("7387066245031034472", "9810650684898829002", "10010138729160973689")]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.wav"
            if language == "zh" and long_chinese:
                path = long_chinese
            else:
                assert len(files) >= 2, f"missing samples in {folder}"
                pack(path, files, gaps)
            result = recognize(str(path), translate=True)
        assert result["language"] == language and result["segments"], result["language"]
        segments = copy.deepcopy(result["segments"])
        cues = result["sentence_lines"]
        assert result["segments"] == segments
        assert "".join(r["text"] for r in cues).split() == "".join(s["text"] for s in segments).split()
        assert all(0 <= r["start"] <= r["end"] <= result["duration"] + .01 for r in cues)
        assert all(a["start"] <= b["start"] for a, b in zip(cues, cues[1:]))
        assert result["translation"] and result["sentence_translation_ready"]
        _assert_translation_clocks(cues)
        report[language] = dict(duration=result["duration"],
                                before=[{k: s[k] for k in ("start", "end", "text")} for s in segments],
                                after=cues)
        print(f"{language}: {result['duration']:.2f}s, {len(segments)} raw segments → {len(cues)} timed bilingual cues", flush=True)
    out = ROOT / "gradio/test_output/sentence_comparison.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-chinese", type=Path, help="optional local continuous Chinese recording")
    args = parser.parse_args()
    rules()
    real_audio(args.long_chinese)
