"""Two-minute inference check from 20 repetitions of a real FLEURS sample."""
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ROOT, APP_DIR, load_audio, recognize


if __name__ == "__main__":
    out = APP_DIR / "test_output"
    out.mkdir(exist_ok=True)
    audio = load_audio(ROOT / "datasets/data/en_us/audio/dev/10010138729160973689.wav")
    path = out / "long.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes((np.tile(audio, 20)*32767).astype("<i2").tobytes())
    result = recognize(str(path))
    assert result["duration"] > 120 and result["language"] == "en" and len(result["text"]) > 1000
    (out / "long.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"duration": result["duration"], "elapsed": result["elapsed"], "segments": len(result["segments"])})
