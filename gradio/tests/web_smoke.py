"""Real local inference integration check; run from repository root."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import MANAGER, ROOT, APP_DIR, export_result, recognize


def main():
    out = APP_DIR / "test_output"
    out.mkdir(exist_ok=True)
    results = []
    for dataset, lang in [("cmn_hans_cn", "zh"), ("en_us", "en"), ("ja_jp", "ja"), ("es_419", "es")]:
        audio = next((ROOT / "datasets" / "data" / dataset / "audio" / "dev").glob("*.wav"))
        result = recognize(str(audio), translate=True)
        export_result(result, out / lang)
        assert result["language"] == lang, result["language"]
        assert result["text"]
        assert result["translation"], result["warnings"]
        assert not result["warnings"] or lang == "zh", result["warnings"]
        for segment in result["segments"]:
            assert "".join(u["text"] for u in segment["units"]) == segment["text"]
            assert all(segment["start"] <= u["start"] <= u["end"] <= segment["end"] + .001 for u in segment["units"])
        results.append(dict(audio=str(audio), expected=lang, detected=result["language"], confidence=result["language_probability"],
                            elapsed=result["elapsed"], duration=result["duration"], text=result["text"], translation=result["translation"]))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        if lang == "zh":
            model_id = id(MANAGER.model)
        else:
            assert id(MANAGER.model) == model_id, "model unexpectedly reloaded"
    try:
        recognize(results[0]["audio"], "cuda")
    except ValueError as exc:
        assert "CUDA" in str(exc)
        print("CUDA unavailable handled:", exc, flush=True)
    results.append({"cache_reused": True, "cuda_devices": 0})
    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
