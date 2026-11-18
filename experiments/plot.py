# -*- coding: utf-8 -*-
"""把 evaluate.py 的评测结果画成图（无需额外参数即可运行）。

用法::

    python plot.py                      # 自动挑选最新的 dev、test 各一次
    python plot.py --run dev1           # 只画 output/dev1
    python plot.py --run dev1 test2     # 画多次结果，并额外生成对比图
    python plot.py --input D:/xx/dev1   # 直接指定结果目录，可多个
    python plot.py --list               # 只列出已有结果目录，不画图

可用参数（均有默认值；``python plot.py -h`` 可查看）::

    --run NAME [NAME ...]     output 下的结果目录名（如 dev1、test2），可多个
    --input PATH [PATH ...]   结果目录的完整路径，可多个
    --output-dir PATH         图片输出根目录；
                              默认写到各结果目录下的 figures/ 子目录
    --dpi N                   图片清晰度                                   (默认 150)
    --list                    列出 output 下所有结果目录后退出

每份结果生成（保存在该结果的 figures/ 下）::

    01_metric_by_language.png   分语言主指标：micro / mean（zh/ja=CER，en/es=WER）
    02_error_distribution.png   逐句错误率分布（箱线图 + 散点）
    03_lid.png                  LID 准确率 + 混淆矩阵
    04_rtf.png                  RTF：micro / mean + 逐句分布
    05_duration_vs_error.png    音频时长 vs 错误率（观察长句是否更差）

多份结果（例如 dev1 与 test1 同时画）额外生成::

    output/figures/compare_<名字>.png   多次运行的主指标横向对比
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "output"

LANG_ORDER = ["zh", "en", "es", "ja"]
LANG_COLORS = {"zh": "#4C72B0", "en": "#DD8452", "es": "#55A868", "ja": "#C44E52"}
GRAY = "#888888"


def setup_utf8_console() -> None:
    """把 Windows 控制台与 stdout/stderr 统一为 UTF-8，避免中文乱码。"""
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


def setup_chinese_font() -> Optional[str]:
    from matplotlib import font_manager

    installed = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans SC", "Deng", "SimSun"):
        if name in installed:
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
    print("[warn] 未找到中文字体，图中的中文可能显示为方框")
    return None


def to_float(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def pct(value: Optional[float], digits: int = 2) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def load_run(run_dir: Path) -> dict:
    """读取一个结果目录的 summary.json 与 utterances.csv。"""
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"缺少 summary.json：{summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    rows: List[dict] = []
    csv_path = run_dir / "utterances.csv"
    if csv_path.is_file():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row["error_value"] = to_float(row.get("error_rate"))
                row["rtf_value"] = to_float(row.get("rtf"))
                row["duration_value"] = to_float(row.get("audio_seconds"))
                rows.append(row)
    return {"dir": run_dir, "name": run_dir.name, "summary": summary, "rows": rows}


def ok_rows(run: dict) -> List[dict]:
    return [row for row in run["rows"] if row.get("status") == "ok"]


def run_title(run: dict) -> str:
    summary = run["summary"]
    return (
        f"{run['name']}  (split={summary.get('split')}, seed={summary.get('seed')}, "
        f"每语言 {summary.get('samples_per_language')} 条)"
    )


def save(fig, path: Path, dpi: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_suptitle = getattr(fig, "_suptitle", None) is not None
    fig.tight_layout(rect=(0, 0, 1, 0.94) if has_suptitle else (0, 0, 1, 1))
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def boxplot(ax, data, labels, **kwargs):
    # matplotlib>=3.9 用 tick_labels，旧版用 labels
    try:
        return ax.boxplot(data, tick_labels=labels, **kwargs)
    except TypeError:
        return ax.boxplot(data, labels=labels, **kwargs)


def fig_metric_by_language(run: dict, path: Path, dpi: int) -> Optional[Path]:
    per = run["summary"].get("per_language", {})
    langs = [lang for lang in LANG_ORDER if lang in per]
    if not langs:
        return None

    labels = [f"{lang}\n({per[lang]['metric']})" for lang in langs]
    micro = [per[lang].get("micro") or 0.0 for lang in langs]
    mean = [per[lang].get("mean") or 0.0 for lang in langs]

    x = np.arange(len(langs))
    width = 0.36
    fig, ax = plt.subplots(figsize=(8, 4.8))
    bars_micro = ax.bar(x - width / 2, micro, width, label="micro（总编辑数/总字数）",
                        color="#4C72B0")
    bars_mean = ax.bar(x + width / 2, mean, width, label="mean（逐句平均）",
                       color="#A8C0DC")

    for bars in (bars_micro, bars_mean):
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f"{height * 100:.1f}%", (bar.get_x() + bar.get_width() / 2, height),
                        ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("识别错误率")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_ylim(0, max(micro + mean) * 1.25 if max(micro + mean) > 0 else 1)

    overall = run["summary"].get("overall", {})
    parts = [f"{name} micro={pct(stats.get('micro'))}"
             for name, stats in (overall.get("metrics") or {}).items()]
    ax.set_title(
        f"分语言识别错误率  {run['name']}\n"
        + (f"总体：{'  '.join(parts)}" if parts else "")
        + f"   LID={pct(overall.get('lid_accuracy'))}",
        fontsize=12,
    )
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    return save(fig, path, dpi)


def fig_error_distribution(run: dict, path: Path, dpi: int) -> Optional[Path]:
    rows = ok_rows(run)
    per = run["summary"].get("per_language", {})
    langs = [lang for lang in LANG_ORDER if lang in per]
    data = [[row["error_value"] for row in rows
             if row["lang"] == lang and row["error_value"] is not None] for lang in langs]
    if not any(data):
        return None

    fig, ax = plt.subplots(figsize=(8, 4.8))
    box = boxplot(ax, data, [f"{lang}\n({per[lang]['metric']})" for lang in langs],
                  showmeans=True, patch_artist=True)
    for patch, lang in zip(box["boxes"], langs):
        patch.set_facecolor(LANG_COLORS.get(lang, GRAY))
        patch.set_alpha(0.35)
    for median in box["medians"]:
        median.set_color("black")

    rng = np.random.default_rng(0)
    for index, (lang, values) in enumerate(zip(langs, data)):
        if not values:
            continue
        jitter = rng.uniform(-0.12, 0.12, size=len(values))
        ax.scatter(np.full(len(values), index + 1) + jitter, values, s=18,
                   color=LANG_COLORS.get(lang, GRAY), alpha=0.75, edgecolors="none")

    ax.set_ylabel("逐句错误率")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_title(f"逐句错误率分布  {run['name']}", fontsize=12)
    ax.grid(axis="y", alpha=0.3)
    return save(fig, path, dpi)


def fig_lid(run: dict, path: Path, dpi: int) -> Optional[Path]:
    rows = ok_rows(run)
    per = run["summary"].get("per_language", {})
    langs = [lang for lang in LANG_ORDER if lang in per]
    if not langs:
        return None

    detected_all = sorted({row["detected_language"] for row in rows if row.get("detected_language")})
    columns = [lang for lang in LANG_ORDER if lang in detected_all]
    columns += [lang for lang in detected_all if lang not in columns]

    matrix = np.zeros((len(langs), len(columns)), dtype=int)
    for row in rows:
        detected = row.get("detected_language")
        if row["lang"] in langs and detected in columns:
            matrix[langs.index(row["lang"]), columns.index(detected)] += 1

    fig, (ax_bar, ax_conf) = plt.subplots(1, 2, figsize=(12, 4.8),
                                          gridspec_kw={"width_ratios": [1, 1.15]})

    accuracies = [per[lang].get("lid_accuracy") or 0.0 for lang in langs]
    bars = ax_bar.bar(langs, accuracies, color=[LANG_COLORS.get(lang, GRAY) for lang in langs])
    for bar, value in zip(bars, accuracies):
        ax_bar.annotate(f"{value * 100:.1f}%", (bar.get_x() + bar.get_width() / 2, value),
                        ha="center", va="bottom", fontsize=9)
    overall_lid = run["summary"].get("overall", {}).get("lid_accuracy")
    if overall_lid is not None:
        ax_bar.axhline(overall_lid, color="black", linestyle="--", linewidth=1,
                       label=f"总体 {overall_lid * 100:.1f}%")
        ax_bar.legend(fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 1.0))
    ax_bar.set_ylim(0, 1.35)
    ax_bar.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax_bar.set_title("各语言 LID 准确率")
    ax_bar.grid(axis="y", alpha=0.3)

    image = ax_conf.imshow(matrix, cmap="Blues")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = matrix[i, j]
            if value:
                ax_conf.text(j, i, str(value), ha="center", va="center", fontsize=10,
                             color="white" if value > matrix.max() * 0.6 else "black")
    ax_conf.set_xticks(range(len(columns)))
    ax_conf.set_xticklabels(columns)
    ax_conf.set_yticks(range(len(langs)))
    ax_conf.set_yticklabels(langs)
    ax_conf.set_xlabel("识别出的语言")
    ax_conf.set_ylabel("真实语言")
    ax_conf.set_title("语言混淆矩阵（条数）")
    fig.colorbar(image, ax=ax_conf, fraction=0.046, pad=0.04)

    fig.suptitle(f"LID 语言识别  {run['name']}", fontsize=12)
    return save(fig, path, dpi)


def fig_rtf(run: dict, path: Path, dpi: int) -> Optional[Path]:
    rows = ok_rows(run)
    per = run["summary"].get("per_language", {})
    langs = [lang for lang in LANG_ORDER if lang in per]
    data = [[row["rtf_value"] for row in rows
             if row["lang"] == lang and row["rtf_value"] is not None] for lang in langs]
    if not langs or not any(data):
        return None

    overall = run["summary"].get("overall", {})
    fig, (ax_bar, ax_box) = plt.subplots(1, 2, figsize=(12, 4.8))

    x = np.arange(len(langs))
    width = 0.36
    micro = [per[lang].get("rtf") or 0.0 for lang in langs]
    mean = [per[lang].get("rtf_mean") or 0.0 for lang in langs]
    bars_micro = ax_bar.bar(x - width / 2, micro, width, label="micro（总耗时/总时长）",
                            color="#4C72B0")
    bars_mean = ax_bar.bar(x + width / 2, mean, width, label="mean（逐句平均）",
                           color="#A8C0DC")
    for bars in (bars_micro, bars_mean):
        for bar in bars:
            ax_bar.annotate(f"{bar.get_height():.3f}",
                            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                            ha="center", va="bottom", fontsize=8)
    ax_bar.set_xticks(x)
    ax_bar.set_xticklabels(langs)
    ax_bar.set_ylabel("RTF（越小越快）")
    ax_bar.set_title(f"实时率 RTF  总体 micro={overall.get('rtf')}")
    ax_bar.legend(fontsize=9)
    ax_bar.grid(axis="y", alpha=0.3)

    box = boxplot(ax_box, data, langs, showmeans=True, patch_artist=True)
    for patch, lang in zip(box["boxes"], langs):
        patch.set_facecolor(LANG_COLORS.get(lang, GRAY))
        patch.set_alpha(0.35)
    for index, (lang, values) in enumerate(zip(langs, data)):
        if values:
            ax_box.scatter(np.full(len(values), index + 1), values, s=18,
                           color=LANG_COLORS.get(lang, GRAY), alpha=0.75, edgecolors="none")
    ax_box.set_ylabel("逐句 RTF")
    ax_box.set_title("逐句 RTF 分布")
    ax_box.grid(axis="y", alpha=0.3)

    fig.suptitle(f"RTF  {run['name']}", fontsize=12)
    return save(fig, path, dpi)


def fig_duration_vs_error(run: dict, path: Path, dpi: int) -> Optional[Path]:
    rows = [row for row in ok_rows(run)
            if row["duration_value"] is not None and row["error_value"] is not None]
    if not rows:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.8))
    for lang in LANG_ORDER:
        picked = [row for row in rows if row["lang"] == lang]
        if not picked:
            continue
        ax.scatter([row["duration_value"] for row in picked],
                   [row["error_value"] for row in picked],
                   s=28, alpha=0.75, label=lang, color=LANG_COLORS.get(lang, GRAY),
                   edgecolors="none")
    ax.set_xlabel("音频时长 (s)")
    ax.set_ylabel("逐句错误率")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_title(f"音频时长 vs 错误率  {run['name']}", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    return save(fig, path, dpi)


def plot_single(run: dict, figures_dir: Path, dpi: int) -> List[Path]:
    jobs = [
        ("01_metric_by_language.png", fig_metric_by_language),
        ("02_error_distribution.png", fig_error_distribution),
        ("03_lid.png", fig_lid),
        ("04_rtf.png", fig_rtf),
        ("05_duration_vs_error.png", fig_duration_vs_error),
    ]
    produced: List[Path] = []
    for filename, func in jobs:
        path = func(run, figures_dir / filename, dpi)
        if path is not None:
            produced.append(path)
    return produced


def fig_compare(runs: List[dict], path: Path, dpi: int) -> Optional[Path]:
    runs = [run for run in runs if run["summary"].get("per_language")]
    if len(runs) < 2:
        return None

    langs = [lang for lang in LANG_ORDER
             if any(lang in run["summary"]["per_language"] for run in runs)]
    if not langs:
        return None

    fig, (ax_micro, ax_lid) = plt.subplots(1, 2, figsize=(13, 4.8))
    x = np.arange(len(langs))
    width = 0.8 / len(runs)

    for index, run in enumerate(runs):
        per = run["summary"]["per_language"]
        values = [(per.get(lang, {}).get("micro") or 0.0) for lang in langs]
        positions = x - 0.4 + width * (index + 0.5)
        bars = ax_micro.bar(positions, values, width, label=run["name"],
                            color=plt.cm.tab10(index % 10))
        for bar, lang in zip(bars, langs):
            if lang in per:
                ax_micro.annotate(f"{bar.get_height() * 100:.1f}%",
                                  (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                                  ha="center", va="bottom", fontsize=7, rotation=90)
    ax_micro.set_xticks(x)
    ax_micro.set_xticklabels([f"{lang}\n({'CER' if lang in ('zh', 'ja') else 'WER'})"
                              for lang in langs])
    ax_micro.set_xlim(-0.6, len(langs) - 0.4)
    ax_micro.set_ylabel("micro 错误率")
    ax_micro.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax_micro.set_ylim(0, max(max((run["summary"]["per_language"].get(lang, {}).get("micro") or 0)
                                for lang in langs) for run in runs) * 1.22)
    ax_micro.set_title("主指标对比（micro）")
    ax_micro.legend(fontsize=9)
    ax_micro.grid(axis="y", alpha=0.3)

    for index, run in enumerate(runs):
        per = run["summary"]["per_language"]
        values = [(per.get(lang, {}).get("lid_accuracy") or 0.0) for lang in langs]
        positions = x - 0.4 + width * (index + 0.5)
        bars = ax_lid.bar(positions, values, width, label=run["name"],
                          color=plt.cm.tab10(index % 10))
        for bar in bars:
            # 100% 时刻度已足够说明，只标注非满分的柱子，避免文字拥挤
            if bar.get_height() < 0.999:
                ax_lid.annotate(f"{bar.get_height() * 100:.1f}%",
                                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                                ha="center", va="bottom", fontsize=7)
    ax_lid.set_xticks(x)
    ax_lid.set_xticklabels(langs)
    ax_lid.set_xlim(-0.6, len(langs) - 0.4)
    ax_lid.set_ylim(0, 1.25)
    ax_lid.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax_lid.set_ylabel("LID 准确率")
    ax_lid.set_title("LID 准确率对比")
    ax_lid.legend(fontsize=9)
    ax_lid.grid(axis="y", alpha=0.3)

    fig.suptitle("多次运行对比：" + " vs ".join(run["name"] for run in runs), fontsize=12)
    return save(fig, path, dpi)


def list_runs(output_dir: Path) -> List[Path]:
    if not output_dir.is_dir():
        return []
    runs = []
    for entry in output_dir.iterdir():
        if entry.is_dir() and (entry / "summary.json").is_file():
            runs.append(entry)
    return sorted(runs, key=lambda p: (p.name.rstrip("0123456789"),
                                       int(re.sub(r"\D", "", p.name) or 0)))


def find_latest(output_dir: Path, split: str) -> Optional[Path]:
    pattern = re.compile(rf"^{re.escape(split)}(\d+)$")
    candidates = [(int(match.group(1)), entry) for entry in list_runs(output_dir)
                  if (match := pattern.match(entry.name))]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot the evaluation results produced by evaluate.py.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--run", nargs="+", default=None,
                        help="output 下的结果目录名，如 dev1 test1")
    parser.add_argument("--input", nargs="+", default=None,
                        help="结果目录路径（可多个），优先于 --run")
    parser.add_argument("--output-dir", default=None,
                        help="图片输出根目录；默认写到各结果目录下的 figures/")
    parser.add_argument("--dpi", type=int, default=150, help="图片清晰度")
    parser.add_argument("--list", action="store_true", help="只列出已有结果目录")
    parser.add_argument("--results-root", default=str(DEFAULT_OUTPUT),
                        help="evaluate.py 的结果根目录")
    return parser.parse_args(argv)


def resolve_runs(args) -> List[Path]:
    root = Path(args.results_root).expanduser().resolve()
    if args.input:
        return [Path(item).expanduser().resolve() for item in args.input]
    if args.run:
        return [root / name for name in args.run]

    selected = [path for path in (find_latest(root, "dev"), find_latest(root, "test")) if path]
    if not selected:
        raise SystemExit(
            f"[error] {root} 下没有找到 dev*/test* 结果目录，请先运行 evaluate.py"
        )
    print("[info] 未指定 --run，自动选择最新结果：" + ", ".join(p.name for p in selected))
    return selected


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_utf8_console()
    setup_chinese_font()
    args = parse_args(argv)

    root = Path(args.results_root).expanduser().resolve()
    if args.list:
        runs = list_runs(root)
        if not runs:
            print(f"[info] {root} 下暂无结果目录")
        for run in runs:
            summary_path = run / "summary.json"
            created = ""
            try:
                created = json.loads(summary_path.read_text(encoding="utf-8")).get("created_at", "")
            except Exception:
                pass
            print(f"  {run.name:<10} {created}")
        return 0

    run_dirs = resolve_runs(args)
    runs: List[dict] = []
    for run_dir in run_dirs:
        try:
            runs.append(load_run(run_dir))
        except FileNotFoundError as exc:
            print(f"[warn] 跳过：{exc}")

    if not runs:
        print("[error] 没有可用的结果目录", file=sys.stderr)
        return 2

    base_out = Path(args.output_dir).expanduser().resolve() if args.output_dir else None
    total = 0
    for run in runs:
        figures_dir = (base_out / run["name"]) if base_out else (run["dir"] / "figures")
        produced = plot_single(run, figures_dir, args.dpi)
        print(f"[plot] {run['name']} -> {figures_dir}  ({len(produced)} 张)")
        for path in produced:
            print(f"       {path.name}")
        total += len(produced)

    if len(runs) > 1:
        compare_dir = (base_out if base_out else runs[0]["dir"].parent / "figures")
        compare_path = compare_dir / f"compare_{'_'.join(run['name'] for run in runs)}.png"
        result = fig_compare(runs, compare_path, args.dpi)
        if result is not None:
            print(f"[plot] 对比图 -> {result}")
            total += 1

    print(f"[done] 共生成 {total} 张图（{datetime.now():%Y-%m-%d %H:%M:%S}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
