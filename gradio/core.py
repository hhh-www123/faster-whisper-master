"""Web-only services. Existing ASR and evaluation entry points are untouched."""
from __future__ import annotations

import gc
import copy
import json
import re
import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ctranslate2
import numpy as np
import sentencepiece as spm

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from subtitles import sentence_captions
from subtitles.pauses import detect_pauses

APP_DIR = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "model" / "small"
LANGUAGES = {"zh": "中文", "en": "English", "ja": "日本語", "es": "Español"}


class ModelManager:
    """One resident model, serialized access including lazy segment iteration."""
    def __init__(self, path=MODEL_PATH):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.model = None
        self.device = None
        self.precision = None

    def status(self):
        if self.model is None:
            return "Whisper-small · 未加载（首次识别时加载本地权重）"
        return f"Whisper-small · {self.device.upper()} / {self.precision} · 已加载"

    def get(self, device):
        # Caller holds lock for the entire inference, not just model construction.
        if device not in ("cpu", "cuda"):
            raise ValueError("请选择 CPU 或 GPU。")
        if device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
            raise ValueError("CUDA 不可用：未检测到可用 NVIDIA GPU。请选择 CPU；GPU 需要匹配的驱动、CUDA 和 cuDNN。")
        if self.model is not None and self.device == device:
            return self.model
        for name in ("model.bin", "config.json", "tokenizer.json"):
            if not (self.path / name).is_file():
                raise ValueError(f"本地 small 模型缺少 {name}：{self.path}")
        if self.model is not None:
            self.model.model.unload_model()
            self.model = None
            self.device = self.precision = None
            gc.collect()
        supported = ctranslate2.get_supported_compute_types(device)
        preferred = "float16" if device == "cuda" else "int8"
        precision = preferred if preferred in supported else "float32"
        try:
            self.model = WhisperModel(str(self.path), device=device,
                                      compute_type=precision, local_files_only=True,
                                      cpu_threads=4, num_workers=1)
        except Exception as exc:
            self.model = None
            raise ValueError(f"{device.upper()} 模型加载失败，请检查 CTranslate2/CUDA/cuDNN：{exc}") from exc
        self.device, self.precision = device, precision
        return self.model


def load_audio(path):
    if not path:
        raise ValueError("请先上传音频或完成麦克风录音。")
    try:
        audio = decode_audio(str(path), sampling_rate=16000)
    except Exception as exc:
        raise ValueError("音频无法解码，请检查文件是否完整，或改用 WAV/MP3/M4A。") from exc
    if not audio.size or not np.isfinite(audio).all():
        raise ValueError("音频为空或包含无效采样。")
    if len(audio) > 16000 * 1800:
        raise ValueError("演示系统限制单段音频不超过 30 分钟。")
    return audio


def timed_units(segment, language):
    """Preserve exact display text; explicitly distinguish estimated timing."""
    words = segment.get("words") or []
    exact = "".join(w["word"] for w in words) == segment["text"]
    entries = words if words and exact else [dict(word=segment["text"], start=segment["start"], end=segment["end"])]
    result = []
    for word in entries:
        text = word["word"]
        parts = list(text) if language in ("zh", "ja") else re.findall(r"\s*\S+|\s+$", text)
        start = max(segment["start"], min(segment["end"], float(word["start"])))
        end = max(start, min(segment["end"], float(word["end"])))
        for i, part in enumerate(parts):
            estimated = not (words and exact) or len(parts) > 1
            result.append(dict(text=part, start=start + (end-start)*i/max(1,len(parts)),
                               end=start + (end-start)*(i+1)/max(1,len(parts)),
                               timing="interpolated" if estimated else "model-word"))
    return result


class ChineseTranslator:
    """Argos-compatible offline CTranslate2 packages; a single resident translator.

    ja/es pivot via English. SentencePiece is shared between source and target.
    No torch/stanza required: short ASR segments are already sentence-like units.
    """
    _translator = None
    _tokenizer = None
    _pair = None
    _packages = {"en_zh": "translate-en_zh-1_9", "ja_en": "translate-ja_en-1_1", "es_en": "translate-es_en-1_9"}

    def _step(self, text, pair):
        cls = type(self)
        if cls._pair != pair:
            path = ROOT / "model" / "translation" / self._packages[pair]
            if not (path / "model" / "model.bin").is_file():
                raise ValueError("离线翻译包未安装，请运行 python gradio/prepare_translation.py。")
            if cls._translator is not None:
                cls._translator.unload_model()
                cls._translator = cls._tokenizer = None
                cls._pair = None
                gc.collect()
            # SentencePiece's Windows file loader cannot reliably open Unicode paths.
            if (path / "sentencepiece.model").exists():
                cls._tokenizer = spm.SentencePieceProcessor(model_proto=(path / "sentencepiece.model").read_bytes())
            else:
                cls._tokenizer = BPETokenizer(path / "bpe.model", pair.split("_")[0])
            cls._translator = ctranslate2.Translator(str(path / "model"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)
            cls._pair = pair
        tokens = cls._tokenizer.encode(text.strip(), out_type=str)
        outputs = []
        for offset in range(0, len(tokens), 200):
            batch = cls._translator.translate_batch([tokens[offset:offset+200]], beam_size=4, max_decoding_length=512)
            outputs.append(cls._tokenizer.decode(batch[0].hypotheses[0]).replace("▁", " "))
        value = " ".join(outputs).strip()
        if not value:
            raise ValueError("离线模型未返回译文。")
        return value

    def translate(self, text, language):
        if language == "zh":
            return text
        if language not in ("en", "ja", "es"):
            raise ValueError(f"离线中文翻译暂不支持 {language}，原文已保留。")
        if language != "en":
            text = self._step(text, language + "_en")
        return self._step(text, "en_zh")


class BPETokenizer:
    def __init__(self, path, language):
        from sacremoses import MosesTokenizer, MosesDetokenizer, MosesPunctNormalizer
        from subword_nmt.apply_bpe import BPE
        self.tokenizer = MosesTokenizer(lang=language)
        self.detokenizer = MosesDetokenizer(lang="en")
        self.normalizer = MosesPunctNormalizer(lang=language)
        with path.open(encoding="utf-8") as handle:
            self.bpe = BPE(handle)

    def encode(self, text, out_type=str):
        return self.bpe.segment_tokens(self.tokenizer.tokenize(self.normalizer.normalize(text)))

    def decode(self, tokens):
        return self.detokenizer.detokenize(" ".join(tokens).replace("@@ ", "").split())


MANAGER = ModelManager()


def _prepare_sentence_lines(result, translator, cache):
    """Caller holds MANAGER.lock. Cache extra translations across partial updates."""
    def translate_missing(text):
        if text not in cache:
            try:
                cache[text] = translator.translate(text, result["language"])
            except Exception as exc:
                warning = f"字幕块翻译失败：{exc}"
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
                cache[text] = None
        return cache[text]

    result["sentence_lines"] = sentence_captions(
        result["segments"], result["language"], translate=translate_missing,
        complete=result.get("complete", True), audio_pauses=result.get("audio_pauses"),
    )
    result["sentence_translation_ready"] = (
        all(s.get("translation") for s in result["segments"])
        and all(row["translations"] for row in result["sentence_lines"])
    )


def recognize(path, device="cpu", translate=False, progress=None):
    """Complete-result API retained for existing experiments and regression tests."""
    for result in recognize_iter(path, device, translate, progress):
        pass
    return result


def recognize_iter(path, device="cpu", translate=False, progress=None):
    """Publish confirmed model segments while continuing the same inference.

    Keep Whisper's original window/context settings and whole-segment translation.
    Consumers must close this generator on cancellation to release the model lock.
    """
    started = time.perf_counter()
    # Reject duplicate jobs rather than consuming memory or waiting behind inference.
    if not MANAGER.lock.acquire(blocking=False):
        raise ValueError("模型正在处理另一项任务，请等待完成后再试。")
    try:
        audio = load_audio(path)
        duration = len(audio) / 16000
        if progress:
            progress(0, desc="加载本地 Whisper-small…")
        model = MANAGER.get(device)
        stream, info = model.transcribe(audio, language=None, task="transcribe",
                                        beam_size=5, vad_filter=True, word_timestamps=True)
        audio_pauses = detect_pauses(audio) if info.language == "zh" else None
        result = dict(language=info.language, language_probability=info.language_probability,
                      duration=duration, asr_seconds=time.perf_counter()-started, elapsed=0,
                      device=MANAGER.device, precision=MANAGER.precision, segments=[],
                      text="", translation="", translation_requested=translate, warnings=[],
                      complete=False, ready_until=0, audio_pauses=audio_pauses)
        translator = ChineseTranslator() if translate else None
        sentence_cache = {}
        iterator = iter(stream)
        while True:
            step = time.perf_counter()
            try:
                seg = next(iterator)
            except StopIteration:
                result["asr_seconds"] += time.perf_counter()-step
                break
            result["asr_seconds"] += time.perf_counter()-step
            row = dict(start=seg.start, end=seg.end, text=seg.text,
                       words=[dict(start=w.start, end=w.end, word=w.word, probability=w.probability)
                              for w in (seg.words or [])])
            row["units"] = timed_units(row, info.language)
            result["segments"].append(row)
            if translator:
                try:
                    row["translation"] = translator.translate(row["text"], info.language)
                except Exception as exc:
                    result["warnings"].append(f"第 {len(result['segments'])} 段翻译失败：{exc}")
            result["text"] = "".join(s["text"] for s in result["segments"]).strip()
            result["translation"] = "\n".join(s["translation"] for s in result["segments"] if s.get("translation"))
            if translator:
                _prepare_sentence_lines(result, translator, sentence_cache)
            result["ready_until"] = max(result["ready_until"], min(duration, seg.end))
            result["elapsed"] = time.perf_counter()-started
            if progress:
                progress(min(.98, seg.end/duration), desc=f"已就绪 {len(result['segments'])} 段 · {seg.end:.1f}/{duration:.1f}s")
            # Gradio stores session state, so never mutate a previously sent snapshot.
            yield copy.deepcopy(result)
        if not result["segments"]:
            result["warnings"].append("未检测到可识别语音，请检查音量或录音内容。")
        result.update(complete=True, ready_until=duration, elapsed=time.perf_counter()-started)
        if translate:
            _prepare_sentence_lines(result, translator, sentence_cache)
            result["elapsed"] = time.perf_counter() - started
        yield result
    finally:
        MANAGER.lock.release()


def timestamp(seconds):
    ms = max(0, round(seconds * 1000))
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def translate_result(result, progress=None):
    """Translate cached ASR text; never decode audio or invoke Whisper again."""
    if result.get("sentence_translation_ready"):
        return result
    if not MANAGER.lock.acquire(blocking=False):
        raise ValueError("模型正在处理另一项任务，请稍后切换。")
    try:
        return _translate_result_in_place(copy.deepcopy(result), progress)
    finally:
        MANAGER.lock.release()


def _translate_result_in_place(result, progress=None):
    """Caller holds MANAGER.lock; raw exports and display pairs have real translations."""
    started = time.perf_counter()
    result["warnings"] = [w for w in result["warnings"] if "段翻译失败" not in w and not w.startswith("字幕块翻译失败")]
    translator = ChineseTranslator()
    for i, segment in enumerate(result["segments"]):
        if segment.get("translation"):
            continue
        if progress:
            progress(.85 + .07*i/len(result["segments"]), desc=f"仅翻译已有文本 {i+1}/{len(result['segments'])}")
        try:
            segment["translation"] = translator.translate(segment["text"], result["language"])
        except Exception as exc:
            result["warnings"].append(f"第 {i+1} 段翻译失败：{exc}")
            break
    result["translation"] = "\n".join(s["translation"] for s in result["segments"] if s.get("translation"))
    result["translation_requested"] = True
    _prepare_sentence_lines(result, translator, {})
    result["elapsed"] += time.perf_counter() - started
    return result


def export_result(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "transcript.txt").write_text(result["text"], encoding="utf-8")
    if result["translation"]:
        (directory / "translation.zh.txt").write_text(result["translation"], encoding="utf-8")
    srt = "\n\n".join(f"{i+1}\n{timestamp(s['start'])} --> {timestamp(s['end'])}\n{s['text'].strip()}"
                         + ("\n"+s["translation"] if s.get("translation") else "")
                         for i,s in enumerate(result["segments"]))
    (directory / "subtitles.srt").write_text(srt, encoding="utf-8")
    return [str(p) for p in sorted(directory.iterdir()) if p.is_file()]
