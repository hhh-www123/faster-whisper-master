"""Compare sentence captions with raw segments from real local audio."""
import argparse
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
    rows = fixture([("歡迎大家參加會議", 0, 2), ("今天我們討論字幕同步", 2.45, 4),
                    ("你準備好了嗎", 5.35, 6.5)], "zh")
    cues = sentence_captions(rows, "zh")
    assert [r["display"] for r in cues] == ["欢迎大家参加会议，今天我们讨论字幕同步。", "你准备好了吗？"]
    assert cues[0]["text"] == "歡迎大家參加會議今天我們討論字幕同步"
    assert [2, len("欢迎大家参加会议，")] in cues[0]["steps"]
    assert cues[0]["steps"][-1] == [4, len(cues[0]["display"])]
    assert cues[1]["steps"][-1] == [6.5, len(cues[1]["display"])]
    assert sentence_captions(rows, "zh", complete=False)[-1]["display"] == "你准备好了吗"
    cue, = sentence_captions(fixture([("這本書是我的", 0, 2)], "zh"), "zh")
    assert cue["display"] == "这本书是我的。"
    cue, = sentence_captions(fixture([("乾隆拿著古書閱讀著作", 0, 2)], "zh"), "zh")
    assert cue["display"] == "乾隆拿着古书阅读著作。"
    rows = fixture([("這裏就是第一段話", 10, 12.64), ("這是新的句子", 13.24, 15)], "zh")
    assert [r["display"] for r in sentence_captions(rows, "zh")] == ["这里就是第一段话。", "这是新的句子。"]

    # Acoustic pause intervals can fall inside contiguous Whisper word times.
    words = [("接受採訪的時候", 0, 1), ("我們", 1, 1.52), ("說明原因", 1.52, 3),
             ("接下來", 3, 3.74), ("還有問題嗎", 3.74, 5)]
    text = "".join(word for word, _, _ in words)
    row = dict(text=text, start=0, end=5,
               words=[dict(word=word, start=start, end=end) for word, start, end in words])
    row["units"] = timed_units(row, "zh")
    cues = sentence_captions([row], "zh", audio_pauses=[(1.1, 1.292), (3.1, 3.612)])
    assert [r["display"] for r in cues] == ["接受采访的时候，我们说明原因。", "接下来还有问题吗？"]
    assert "".join(r["text"] for r in cues) == text
    assert [1, len("接受采访的时候，")] in cues[0]["steps"]
    assert cues[0]["steps"][-1] == [3, len(cues[0]["display"])]
    assert sentence_captions([row], "zh", audio_pauses=[])[0]["display"] == "接受采访的时候我们说明原因接下来还有问题吗？"

    cue, = sentence_captions(fixture([("原值是3.14，现在是3.15", 0, 3)], "zh"), "zh")
    assert cue["display"] == "原值是3.14，现在是3.15。"
    cue, = sentence_captions(fixture([("这是录音中的一句话！", 0, 3)], "zh"), "zh")
    assert cue["display"] == "这是录音中的一句话！"
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
    rows[0]["translation"], rows[1]["translation"] = "當你到達", "請告訴我。"
    cue, = sentence_captions(rows, "en", translate=split_translations.__getitem__)
    assert cue["translations"][0]["display"] == "当你到达请告诉我。"
    _assert_translation_clocks([cue])
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
        segments = result["segments"]
        cues = result["sentence_lines"]
        assert "".join(r["text"] for r in cues).split() == "".join(s["text"] for s in segments).split()
        assert all(0 <= r["start"] <= r["end"] <= result["duration"] + .01 for r in cues)
        assert all(a["start"] <= b["start"] for a, b in zip(cues, cues[1:]))
        assert result["translation"] and result["sentence_translation_ready"]
        _assert_translation_clocks(cues)
        assert all(row["steps"] == sorted(row["steps"]) for row in cues)
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
