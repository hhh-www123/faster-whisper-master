"""Targeted error-path checks; no pytest or network needed."""
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import MANAGER, load_audio, recognize, timed_units


def main():
    for value in (None, "not-an-audio-file.wav"):
        try:
            load_audio(value)
            raise AssertionError("invalid input accepted")
        except ValueError:
            pass
    with tempfile.TemporaryDirectory() as directory:
        silence = Path(directory) / "silence.wav"
        with wave.open(str(silence), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(bytes(16000*2*3))
        result = recognize(str(silence))
        assert not result["segments"] and result["warnings"]
        MANAGER.lock.acquire()
        try:
            try:
                recognize(str(silence))
                raise AssertionError("concurrent task accepted")
            except ValueError as exc:
                assert "另一项任务" in str(exc)
        finally:
            MANAGER.lock.release()
    segment = dict(start=2, end=4, text="你好世界", words=[])
    units = timed_units(segment, "zh")
    assert [u["start"] for u in units] == [2, 2.5, 3, 3.5]
    assert all(u["timing"] == "interpolated" for u in units)
    print("PASS: missing/invalid audio, real silence inference, concurrent rejection, timestamp fallback")


if __name__ == "__main__":
    main()
