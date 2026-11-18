import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel

# 加载本地模型
model = WhisperModel(
    str(ROOT / "model" / "small"),
    device="cpu",
    compute_type="float32"
)

# 识别音频
segments, info = model.transcribe(
    str(ROOT / "tests" / "data" / "physicsworks.wav"),
    language=None,
    beam_size=5,
    vad_filter=True
)

print("检测语言：", info.language)
print("语言置信度：", info.language_probability)

for segment in segments:
    print(
        f"[{segment.start:.2f}s -> {segment.end:.2f}s]"
        f" {segment.text}"
    )
