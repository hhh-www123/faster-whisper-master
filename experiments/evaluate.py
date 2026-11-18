# -*- coding: utf-8 -*-
"""faster-whisper 四语言评测脚本（dev / test）

在终端运行时可用的参数（全部可选，均有默认值；用 ``python evaluate.py -h`` 可随时查看）::

    --split {dev,test}        选择评测集：dev=验证集, test=测试集        (默认 dev)
    --samples N               每种语言随机抽取的语音条数                  (默认 100)
    --seed N                  手动指定随机种子以复现某次抽样；
                              不指定则每次运行自动生成新种子（每次随机抽样）(默认 自动)
    --zh-simplify             对中文做繁体->简体归一后再算 CER，避免模型
                              输出繁体导致 CER 虚高（需 opencc-python-reimplemented）
    --model PATH              本地 CTranslate2 模型目录或模型名           (默认 model/small)
    --device {cpu,cuda,auto}  运行设备                                  (默认 cpu)
    --compute-type T          cpu 用 int8/float32/int16；cuda 可用 float16 (默认 int8)
    --cpu-threads N           CPU 线程数，0 表示由 CTranslate2 自动决定    (默认 0)
    --beam-size N             解码 beam 宽度，越大越准但越慢               (默认 5)
    --vad-filter              开启 Silero VAD 切掉静音（短句评测一般不需要）
    --data-root PATH          数据集根目录                              (默认 ../datasets/data)
    --output-dir PATH         结果输出根目录                            (默认 ./output)
    --run-index N             手动指定本次结果目录编号（如 dev3）；
                              不指定则自动递增：dev1、dev2 ……            (默认 自动)

运行示例::

    python evaluate.py --split dev
    python evaluate.py --split test --samples 100
    python evaluate.py --split dev --zh-simplify
    python evaluate.py --split dev --seed 2024 --run-index 5

输出：每次运行新建一个编号目录，历史结果不会被覆盖::

    output/dev1/{utterances.csv, summary.json, summary.txt}
    output/dev2/...
    output/test1/...

指标说明：

* 识别错误率：zh/ja 只用 CER（字符级），en/es 只用 WER（词级）；
  每种指标同时给出 micro（总编辑数/总参考长度，语料级）与 mean（逐句算术平均，句级）
* LID Accuracy：语言识别准确率（language=None 自动判语种）
* RTF：实时率 = 推理耗时 / 音频时长，越小越快；同样给出 micro 与 mean

终端中文若出现乱码，脚本启动时已自动把控制台与 stdout 统一为 UTF-8；
如仍有异常，可在运行前设置环境变量 PYTHONUTF8=1。

参考耗时：CPU + small + int8 + beam_size=5 约 4~5 秒/条，
每种语言 100 条（共 400 条）单次运行约需 30 分钟。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import sys
import tarfile
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from tqdm import tqdm


def setup_utf8_console() -> None:
    """把 Windows 控制台与 stdout/stderr 统一为 UTF-8，避免中文乱码。

    VS Code 集成终端按 UTF-8 解码显示，而中文 Windows 的控制台代码页默认 936，
    Python 输出 GBK 字节就会显示成乱码。这里在进程内统一成 UTF-8（只影响本进程）。
    """
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


setup_utf8_console()

try:  # 可选依赖：仅在中日/繁简归一时需要
    from opencc import OpenCC  # type: ignore

    _OPENCC_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖缺失时降级
    OpenCC = None  # type: ignore
    _OPENCC_AVAILABLE = False

_ZH_CONVERTER = None  # 懒加载的繁体->简体转换器
_ZH_SIMPLIFY_ENABLED = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_DATA_ROOT = PROJECT_ROOT / "datasets" / "data"
DEFAULT_MODEL = PROJECT_ROOT / "model" / "small"
DEFAULT_OUTPUT = SCRIPT_DIR / "output"

# (数据集目录名, faster-whisper 语言代码, 唯一指标)
LANGUAGES: List[Tuple[str, str, str]] = [
    ("cmn_hans_cn", "zh", "CER"),
    ("en_us", "en", "WER"),
    ("es_419", "es", "WER"),
    ("ja_jp", "ja", "CER"),
]

CHAR_METRIC_LANGS = ("zh", "ja")

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


class Item:
    """一条待评测样本。"""

    __slots__ = ("dataset", "lang", "primary_metric", "audio_name", "reference", "gender")

    def __init__(self, dataset: str, lang: str, primary_metric: str, audio_name: str,
                 reference: str, gender: str = "") -> None:
        self.dataset = dataset
        self.lang = lang
        self.primary_metric = primary_metric
        self.audio_name = audio_name
        self.reference = reference
        self.gender = gender


def load_manifest(tsv_path: Path) -> List[Tuple[str, str, str]]:
    """读取 Common Voice 风格 TSV（无表头），返回 (文件名, 句子, 性别)。"""
    rows: List[Tuple[str, str, str]] = []
    with tsv_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[1].lower().endswith(".wav"):
                continue
            gender = parts[6] if len(parts) > 6 else ""
            rows.append((parts[1], parts[2], gender))
    return rows


def extract_audio(tar_path: Path, wanted: Sequence[str], dest: Path) -> set:
    """单次顺序扫描 tar.gz，只解出需要的音频，避免整包载入内存。"""
    wanted_set = set(wanted)
    found: set = set()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = os.path.basename(member.name)
            if name not in wanted_set:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            with handle, open(dest / name, "wb") as out_file:
                shutil.copyfileobj(handle, out_file)
            found.add(name)
    return found


def enable_zh_simplify() -> bool:
    """开启中文繁->简归一；opencc 不可用时返回 False。"""
    global _ZH_CONVERTER, _ZH_SIMPLIFY_ENABLED
    if not _OPENCC_AVAILABLE:
        return False
    if _ZH_CONVERTER is None:
        _ZH_CONVERTER = OpenCC("t2s")
    _ZH_SIMPLIFY_ENABLED = True
    return True


def normalize_text(text: str, lang: str) -> str:
    """统一大小写/标点/空白，使参考文本与识别结果可比。"""
    out = unicodedata.normalize("NFKC", text).lower()
    out = _PUNCT_RE.sub(" ", out)
    if lang == "zh" and _ZH_CONVERTER is not None:
        out = _ZH_CONVERTER.convert(out)
    if lang in CHAR_METRIC_LANGS:
        return re.sub(r"\s+", "", out)
    return re.sub(r"\s+", " ", out).strip()


def levenshtein(ref: Sequence, hyp: Sequence) -> int:
    """编辑距离（序列元素级），用于算 CER/WER 的分子。"""
    if not ref:
        return len(hyp)
    if not hyp:
        return len(ref)
    previous = list(range(len(hyp) + 1))
    for i, ref_item in enumerate(ref, 1):
        current = [i]
        for j, hyp_item in enumerate(hyp, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ref_item != hyp_item),
            ))
        previous = current
    return previous[-1]


def score_pair(reference: str, hypothesis: str, lang: str) -> Dict[str, object]:
    """按语言只算一个指标：zh/ja -> CER（字符级），en/es -> WER（词级）。"""
    ref_norm = normalize_text(reference, lang)
    hyp_norm = normalize_text(hypothesis, lang)
    if lang in CHAR_METRIC_LANGS:
        units_ref, units_hyp, metric = list(ref_norm), list(hyp_norm), "CER"
    else:
        units_ref, units_hyp, metric = ref_norm.split(), hyp_norm.split(), "WER"
    edits = levenshtein(units_ref, units_hyp)
    ref_len = len(units_ref)
    return {
        "metric": metric,
        "ref_len": ref_len,
        "edits": edits,
        "error_rate": edits / ref_len if ref_len else float(edits > 0),
    }


def transcribe_one(model, audio_path: Path, args) -> Tuple[str, str, float, float]:
    """返回 (识别文本, 检测语言, 语言置信度, 音频时长)。含解码/VAD/推理全流程耗时。"""
    segments, info = model.transcribe(
        str(audio_path),
        language=None,                      # 必须为 None，否则无法评估 LID
        beam_size=args.beam_size,
        vad_filter=args.vad_filter,
        condition_on_previous_text=False,   # 各条语音彼此独立
        word_timestamps=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, info.language, float(info.language_probability), float(info.duration)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate faster-whisper on dev/test splits of the bundled 4-language dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--split", choices=["dev", "test"], default="dev",
                        help="dev=验证集, test=测试集")
    parser.add_argument("--samples", type=int, default=100,
                        help="每种语言随机抽取的条数")
    parser.add_argument("--seed", type=int, default=None,
                        help="默认不指定：每次运行自动生成随机种子（会打印并写入 summary）；"
                             "仅在需要复现某次抽样时手动指定")
    parser.add_argument("--model", default=str(DEFAULT_MODEL),
                        help="本地 CTranslate2 模型目录或模型名")
    parser.add_argument("--device", default="cpu", help="cpu / cuda / auto")
    parser.add_argument("--compute-type", default="int8",
                        help="int8 / float32 / int16 / float16(cuda)")
    parser.add_argument("--cpu-threads", type=int, default=0,
                        help="CPU 线程数，0 表示由 CTranslate2 决定")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--vad-filter", action="store_true",
                        help="开启 silero VAD（短句通常关闭即可，且会额外增加耗时）")
    parser.add_argument("--zh-simplify", action="store_true",
                        help="中文繁->简归一再算 CER（需 opencc-python-reimplemented；缺失时自动跳过）")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--run-index", type=int, default=None,
                        help="手动指定本次结果目录编号（如 3 -> dev3）；默认自动递增 dev1/dev2/...")
    return parser.parse_args(argv)


def next_run_dir(output_dir: Path, split: str, run_index: Optional[int] = None) -> Path:
    """返回本次结果目录：output/<split><n>，n 取该 split 已有最大编号+1。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    if run_index is not None:
        return output_dir / f"{split}{run_index}"
    pattern = re.compile(rf"^{re.escape(split)}(\d+)$")
    used = []
    for entry in output_dir.iterdir():
        match = pattern.match(entry.name)
        if match and entry.is_dir():
            used.append(int(match.group(1)))
    return output_dir / f"{split}{(max(used) + 1) if used else 1}"


def build_items(data_root: Path, split: str, samples: int, rng: random.Random) -> List[Item]:
    items: List[Item] = []
    for dataset, lang, primary_metric in LANGUAGES:
        tsv_path = data_root / dataset / f"{split}.tsv"
        if not tsv_path.is_file():
            print(f"[warn] 缺少标注文件，跳过：{tsv_path}")
            continue
        rows = load_manifest(tsv_path)
        if not rows:
            print(f"[warn] 标注为空，跳过：{tsv_path}")
            continue
        take = min(samples, len(rows))
        picked = rng.sample(rows, take)
        items.extend(
            Item(dataset, lang, primary_metric, name, sentence, gender)
            for name, sentence, gender in picked
        )
        print(f"[data] {dataset:<12} {split}: 候选 {len(rows):>4} 条 -> 随机抽取 {take} 条")
    return items


def evaluate(model, items: List[Item], data_root: Path, split: str, args,
             seed: Optional[int] = None) -> Tuple[List[dict], dict]:
    records: List[dict] = []
    temp_root = Path(tempfile.mkdtemp(prefix=f"fwhisper_{split}_"))

    try:
        by_dataset: Dict[str, List[Item]] = {}
        for item in items:
            by_dataset.setdefault(item.dataset, []).append(item)

        audio_dirs: Dict[str, Path] = {}
        for dataset, dataset_items in by_dataset.items():
            tar_path = data_root / dataset / "audio" / f"{split}.tar.gz"
            audio_dir = temp_root / dataset
            names = [item.audio_name for item in dataset_items]
            print(f"[audio] 从 {tar_path.name} 解出 {len(names)} 个文件 ...")
            found = extract_audio(tar_path, names, audio_dir)
            missing = [name for name in names if name not in found]
            if missing:
                print(f"[warn] {dataset}: {len(missing)} 个音频未在压缩包中找到")
            for name in missing:
                records.append({
                    "lang": dataset_items[0].lang,
                    "dataset": dataset,
                    "audio": name,
                    "reference": "",
                    "hypothesis": "",
                    "detected_language": "",
                    "lid_correct": "",
                    "lid_probability": "",
                    "audio_seconds": "",
                    "elapsed_seconds": "",
                    "rtf": "",
                    "metric": dataset_items[0].primary_metric,
                    "ref_len": 0,
                    "edits": 0,
                    "error_rate": "",
                    "status": "missing_audio",
                })
            audio_dirs[dataset] = audio_dir

        pending = [item for item in items if (audio_dirs[item.dataset] / item.audio_name).is_file()]
        progress = tqdm(pending, desc=f"{split}", unit="utt", dynamic_ncols=True)

        for item in progress:
            audio_path = audio_dirs[item.dataset] / item.audio_name
            started = time.perf_counter()
            try:
                text, detected, probability, duration = transcribe_one(model, audio_path, args)
                elapsed = time.perf_counter() - started
                scored = score_pair(item.reference, text, item.lang)
                lid_correct = detected == item.lang
                records.append({
                    "lang": item.lang,
                    "dataset": item.dataset,
                    "audio": item.audio_name,
                    "reference": item.reference,
                    "hypothesis": text,
                    "detected_language": detected,
                    "lid_correct": int(lid_correct),
                    "lid_probability": round(probability, 6),
                    "audio_seconds": round(duration, 3),
                    "elapsed_seconds": round(elapsed, 3),
                    "rtf": round(elapsed / duration, 4) if duration > 0 else "",
                    "metric": scored["metric"],
                    "ref_len": scored["ref_len"],
                    "edits": scored["edits"],
                    "error_rate": round(scored["error_rate"], 6),
                    "status": "ok",
                })
                progress.set_postfix_str(f"{item.lang} {scored['metric']}={scored['error_rate']:.1%}")
            except Exception as exc:  # 单条失败不影响整体
                elapsed = time.perf_counter() - started
                records.append({
                    "lang": item.lang,
                    "dataset": item.dataset,
                    "audio": item.audio_name,
                    "reference": item.reference,
                    "hypothesis": "",
                    "detected_language": "",
                    "lid_correct": "",
                    "lid_probability": "",
                    "audio_seconds": "",
                    "elapsed_seconds": round(elapsed, 3),
                    "rtf": "",
                    "metric": item.primary_metric,
                    "ref_len": 0,
                    "edits": 0,
                    "error_rate": "",
                    "status": f"error: {type(exc).__name__}: {exc}",
                })
        progress.close()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return records, summarize(records, args, seed)


def _basic_stats(rows: List[dict]) -> dict:
    """通用统计：样本量、音频/推理总时长、RTF(micro)、RTF 平均值、LID 准确率。"""
    ok = [row for row in rows if row["status"] == "ok"]
    if not ok:
        return {
            "num_utterances": len(rows), "num_evaluated": 0,
            "total_audio_seconds": 0.0, "total_inference_seconds": 0.0,
            "rtf": None, "rtf_mean": None, "lid_accuracy": None,
        }
    total_audio = sum(row["audio_seconds"] for row in ok)
    total_elapsed = sum(row["elapsed_seconds"] for row in ok)
    per_utterance_rtf = [row["rtf"] for row in ok if row["rtf"] != ""]
    return {
        "num_utterances": len(rows),
        "num_evaluated": len(ok),
        "total_audio_seconds": round(total_audio, 3),
        "total_inference_seconds": round(total_elapsed, 3),
        "rtf": round(total_elapsed / total_audio, 4) if total_audio > 0 else None,
        "rtf_mean": round(sum(per_utterance_rtf) / len(per_utterance_rtf), 4)
        if per_utterance_rtf else None,
        "lid_accuracy": round(sum(row["lid_correct"] for row in ok) / len(ok), 4),
    }


def _metric_stats(rows: List[dict]) -> Optional[dict]:
    """同一指标下的错误率：micro（总编辑数/总参考长度）与句级 mean。"""
    ok = [row for row in rows if row["status"] == "ok"]
    if not ok:
        return None
    ref_len = sum(row["ref_len"] for row in ok)
    edits = sum(row["edits"] for row in ok)
    return {
        "metric": ok[0]["metric"],
        "num_utterances": len(ok),
        "micro": round(edits / ref_len, 4) if ref_len else None,
        "mean": round(sum(row["error_rate"] for row in ok) / len(ok), 4),
    }


def summarize(records: List[dict], args, seed: Optional[int] = None) -> dict:
    per_language: Dict[str, dict] = {}
    for dataset, lang, metric in LANGUAGES:
        rows = [row for row in records if row["dataset"] == dataset]
        if not rows:
            continue
        stats = _basic_stats(rows)
        stats.update(_metric_stats(rows) or {"metric": metric, "micro": None, "mean": None})
        stats["dataset"] = dataset
        per_language[lang] = stats

    ok_rows = [row for row in records if row["status"] == "ok"]
    overall = _basic_stats(records)
    overall["metrics"] = {
        metric: _metric_stats([row for row in ok_rows if row["metric"] == metric])
        for metric in ("CER", "WER")
        if any(row["metric"] == metric for row in ok_rows)
    }

    return {
        "split": args.split,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "beam_size": args.beam_size,
        "vad_filter": args.vad_filter,
        "samples_per_language": args.samples,
        "seed": seed if seed is not None else args.seed,
        "zh_simplify": _ZH_SIMPLIFY_ENABLED,
        "overall": overall,
        "per_language": per_language,
    }


def write_outputs(run_dir: Path, records: List[dict], summary: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)

    fields = [
        "lang", "dataset", "audio", "reference", "hypothesis",
        "detected_language", "lid_correct", "lid_probability",
        "audio_seconds", "elapsed_seconds", "rtf",
        "metric", "ref_len", "edits", "error_rate", "status",
    ]
    csv_path = run_dir / "utterances.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "summary.txt").write_text(render_summary(summary), encoding="utf-8")

    print(f"[out] {run_dir}")


def render_summary(summary: dict) -> str:
    overall = summary["overall"]

    def pct(value):
        return "n/a" if value is None else f"{value * 100:.2f}%"

    def num(value, digits=4):
        return "n/a" if value is None else f"{value:.{digits}f}"

    lines = [
        "=" * 78,
        f"faster-whisper 评测汇总  split={summary['split']}",
        f"时间: {summary['created_at']}",
        f"结果目录: {summary.get('output_dir', '')}",
        f"模型: {summary['model']}  device={summary['device']}  compute_type={summary['compute_type']}",
        f"beam_size={summary['beam_size']}  vad_filter={summary['vad_filter']}  "
        f"每语言抽样={summary['samples_per_language']}  seed={summary['seed']}  "
        f"zh_mg2s={summary.get('zh_simplify', False)}",
        "=" * 78,
        "",
        "[总体]",
        f"  样本数(已评测/总数) : {overall['num_evaluated']}/{overall['num_utterances']}",
        f"  音频总时长(s)       : {overall['total_audio_seconds']:.1f}",
        f"  推理总耗时(s)       : {overall['total_inference_seconds']:.1f}",
        f"  RTF  (micro / mean) : {num(overall['rtf'])} / {num(overall['rtf_mean'])}",
        f"  LID Accuracy        : {pct(overall['lid_accuracy'])}",
    ]
    for metric, stats in overall["metrics"].items():
        lines.append(
            f"  {metric:<3}  (micro / mean) : {pct(stats['micro'])} / {pct(stats['mean'])}"
            f"   [{stats['num_utterances']} 条]"
        )
    lines.extend([
        "",
        "[分语言]",
        f"  {'lang':<5}{'dataset':<13}{'metric':<7}{'micro':>9}{'mean':>9}"
        f"{'LID':>9}{'RTF':>8}{'RTF_avg':>9}{'utt':>6}",
    ])
    for lang, stats in summary["per_language"].items():
        lines.append(
            f"  {lang:<5}{stats['dataset']:<13}{stats['metric']:<7}"
            f"{pct(stats['micro']):>9}{pct(stats['mean']):>9}"
            f"{pct(stats['lid_accuracy']):>9}{num(stats['rtf']):>8}"
            f"{num(stats['rtf_mean']):>9}{stats['num_evaluated']:>6}"
        )
    lines.append("")
    lines.append("注:")
    lines.append("  1. zh/ja 只用 CER（字符级），en/es 只用 WER（词级）。")
    lines.append("  2. micro = 总编辑数/总参考长度（语料级）；mean = 逐句错误率的算术平均（句级）。")
    lines.append("  3. RTF = 推理耗时/音频时长（越小越快）；RTF(micro) 用总时长比，RTF_avg 为逐句平均。")
    if summary.get("zh_simplify"):
        lines.append("  4. 已启用中文繁->简归一（opencc t2s），中文 CER 不再受繁体输出影响。")
    else:
        lines.append("  4. 未启用繁简归一（加 --zh-simplify 可开启）；zh 的繁体输出会抬高 CER。")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    data_root = Path(args.data_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not data_root.is_dir():
        print(f"[error] 数据集目录不存在：{data_root}", file=sys.stderr)
        return 2

    # 每次运行都生成新的随机种子，保证抽样随机；种子会打印并写入 summary 便于复现
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2 ** 31)
    rng = random.Random(seed)

    if args.zh_simplify:
        if enable_zh_simplify():
            print("[info] 已启用中文繁->简归一（opencc t2s）")
        else:
            print("[warn] 未安装 opencc-python-reimplemented，--zh-simplify 已忽略")

    run_dir = next_run_dir(output_dir, args.split, args.run_index)
    print(f"[info] 本次结果目录：{run_dir}")

    items = build_items(data_root, args.split, args.samples, rng)
    if not items:
        print("[error] 没有可评测样本", file=sys.stderr)
        return 2
    print(f"[info] split={args.split}  待评测 {len(items)} 条  本次随机种子 seed={seed}")

    from faster_whisper import WhisperModel

    load_started = time.perf_counter()
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        cpu_threads=args.cpu_threads,
    )
    load_seconds = time.perf_counter() - load_started
    print(f"[info] 模型加载耗时 {load_seconds:.2f}s")

    records, summary = evaluate(model, items, data_root, args.split, args, seed)
    summary["model_load_seconds"] = round(load_seconds, 3)
    summary["output_dir"] = str(run_dir)

    write_outputs(run_dir, records, summary)
    print()
    print(render_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
