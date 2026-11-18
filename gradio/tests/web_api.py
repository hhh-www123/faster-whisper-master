"""Requires a running app.py; real upload + queue + download test."""
import json
import argparse
from pathlib import Path

from gradio_client import Client, handle_file

APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    args = parser.parse_args()
    client = Client(args.url)
    audio = ROOT / "datasets/data/en_us/audio/dev/10010138729160973689.wav"
    result = client.predict(handle_file(str(audio)), "cpu", "语音转写", api_name="/recognize")
    assert "完成" in result[0], result[0]
    assert "satellite" in result[3], result[3]
    switched = client.predict("中文翻译", api_name="/switch_mode")
    assert "卫星" in switched[1] and "▁" not in switched[1], switched[0]
    assert "未重新识别" in switched[0]
    client.predict("语音转写", api_name="/switch_mode")
    again = client.predict("中文翻译", api_name="/switch_mode")
    assert again[1] == switched[1]
    assert "caption-data" in result[6]
    for path in switched[4]:
        assert Path(path).is_file()
    report = {"status": result[0], "model": result[1], "summary": result[2], "text": result[3],
              "translation": switched[1], "download_count": len(switched[4]), "mode_switch": "ASR once; cached translation; both directions", "generator_controls": "passed"}
    unavailable = client.predict(handle_file(str(audio)), "cuda", "语音转写", api_name="/recognize")
    assert "CUDA 不可用" in unavailable[0]
    assert not unavailable[3] and not unavailable[7]
    report["cuda_unavailable"] = unavailable[0]
    (APP_DIR / "test_output").mkdir(exist_ok=True)
    (APP_DIR / "test_output" / "api.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
