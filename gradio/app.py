"""Run from project root: .venv-web/Scripts/python.exe gradio/app.py."""
import argparse
import html
import json
import tempfile
import os
import sys
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
import gradio as gr

from core import LANGUAGES, MANAGER, ROOT, export_result, load_audio, recognize, recognize_iter, translate_result
from subtitles import sentence_captions

ASSETS = Path(__file__).parent / "assets"


def subtitle_html(result, mode="语音转写"):
    if not result or not result["segments"]:
        return '<div class="caption-empty">完成识别后，在上方播放器播放音频，字幕将跟随播放进度出现。</div>'
    groups = result.get("sentence_lines") if mode == "中文翻译" else None
    if groups is None:
        groups = sentence_captions(result["segments"], result["language"],
                                   complete=result.get("complete", True), audio_pauses=result.get("audio_pauses"))
    data = html.escape(json.dumps(groups, ensure_ascii=False), quote=True)
    key = html.escape(result["_id"], quote=True)
    language = html.escape(result["language"], quote=True)
    path = html.escape(result.get("_audio_path", ""), quote=True)
    return f'<div class="caption-data" data-result-id="{key}" data-audio-path="{path}" data-language="{language}" data-mode="{mode}" data-segments="{data}"><div class="caption-clock">00:00</div><div class="caption-wait">识别已就绪 · 点击播放，文字将随声音出现</div><div class="caption-lines"></div></div>'


def playback_signal(phase="idle", path="", job="", result=None):
    attrs = {"phase": phase, "path": path, "job": job,
             "result": result["_id"] if result else "",
             "has-text": "true" if result and result["segments"] else "false",
             "ready-until": result.get("ready_until", result["duration"]) if result else 0}
    return '<span ' + ' '.join(f'data-{k}="{html.escape(str(v), quote=True)}"' for k, v in attrs.items()) + '></span>'


def audio_key(path):
    return str(Path(path).resolve()) if path else ""


def toggle_recognition(enabled):
    enabled = not enabled
    marker = f'<span data-enabled="{str(enabled).lower()}"></span>'
    return (enabled, "❚❚" if enabled else "▶", marker,
            "识别已开启" if enabled else "开始识别",
            "识别已开启：上传或录音后，点击旁边的播放键即可。" if enabled else "自动识别已暂停；再次点击圆形识别键可开启。")


def result_outputs(result, mode):
    downloads = export_result(result, tempfile.mkdtemp(prefix="whisper-result-"))
    rows = [[f"{s['start']:.2f}–{s['end']:.2f}", s["text"], s.get("translation", "")] for s in result["segments"]]
    return result["translation"], rows, subtitle_html(result, mode), downloads


def switch_mode(mode, result, progress=gr.Progress()):
    if not result:
        yield ("模式已切换；请先识别一段音频。", *[gr.skip() for _ in range(5)], gr.update(interactive=True), gr.update(interactive=True))
        return
    yield ("⏳ 正在切换；复用本次识别原文…", *[gr.skip() for _ in range(5)], gr.update(interactive=False), gr.update(interactive=False))
    try:
        if mode == "中文翻译":
            result = translate_result(result, progress)
        status = "✅ 已切换为" + mode + "；未重新识别音频。"
        if result["warnings"]:
            status += "\n\n" + "\n\n".join(result["warnings"])
        yield (status, *result_outputs(result, mode), result, gr.update(interactive=True), gr.update(interactive=True))
    except Exception as exc:
        yield (f"⚠️ {exc}", *[gr.skip() for _ in range(5)], gr.update(interactive=True), gr.update(interactive=True))


def run(audio, device, mode, progress=gr.Progress()):
    yield ("⏳ 正在识别，请等待…", MANAGER.status(), "处理中…", "", "", [], subtitle_html(None), [],
           *[gr.update(interactive=False) for _ in range(4)], None)
    try:
        result = recognize(audio, device, mode == "中文翻译", progress)
        result["_id"] = uuid.uuid4().hex
        result["_audio_path"] = audio_key(audio)
        folder = tempfile.mkdtemp(prefix="whisper-result-")
        downloads = export_result(result, folder)
        lang = LANGUAGES.get(result["language"], result["language"])
        summary = (f"**{lang}** · 语言置信度 **{result['language_probability']:.1%}** · "
                   f"音频 **{result['duration']:.2f}s** · ASR（含首次加载） **{result['asr_seconds']:.2f}s** · "
                   f"总耗时 **{result['elapsed']:.2f}s**")
        rows = [[f"{s['start']:.2f}–{s['end']:.2f}", s["text"], s.get("translation", "")] for s in result["segments"]]
        status = "✅ 识别完成。请播放音频查看同步字幕。"
        if result["warnings"]:
            status += "\n\n" + "\n\n".join(result["warnings"])
        yield (status, MANAGER.status(), summary, result["text"], result["translation"],
                rows, subtitle_html(result, mode), downloads,
                *[gr.update(interactive=True) for _ in range(4)], result)
    except Exception as exc:
        yield (f"⚠️ {exc}", MANAGER.status(), "尚无结果", "", "", [], subtitle_html(None), [],
               *[gr.update(interactive=True) for _ in range(4)], None)


def auto_recognize(audio, device, mode, enabled, cached, progress=gr.Progress()):
    """A media play event recognizes a new file only while the session is armed."""
    if not enabled:
        yield ("识别尚未开启，当前仅试听音频。", *[gr.skip() for _ in range(13)])
        return
    path = audio_key(audio)
    if cached and cached.get("_audio_path") == path:
        yield tuple(gr.skip() for _ in range(14))
        return
    job = uuid.uuid4().hex
    yield ("⏳ 正在准备首段字幕，稍后缓冲播放…", MANAGER.status(), "处理中…", "", "", [], subtitle_html(None), [],
           gr.skip(), gr.update(interactive=False), gr.update(interactive=False), gr.skip(), None, playback_signal("working", path, job))
    try:
        # One model call yields real confirmed segments; close also on cancellation.
        with closing(recognize_iter(audio, device, mode == "中文翻译", progress)) as stream:
            for result in stream:
                result.update(_id=job, _audio_path=path)
                complete = result["complete"]
                status = ("✅ 全部识别完成；字幕随播放自动跟随。" if complete else
                          f"⏳ 边识别边播放 · 已就绪 {result['ready_until']:.1f}/{result['duration']:.1f}s；后续仍在处理。")
                if result["warnings"]:
                    status += "\n\n" + "\n\n".join(result["warnings"])
                lang = LANGUAGES.get(result["language"], result["language"])
                summary = (f"**{lang}** · 语言置信度 **{result['language_probability']:.1%}** · "
                           f"音频 **{result['duration']:.2f}s** · ASR **{result['asr_seconds']:.2f}s** · "
                           f"{'总耗时' if complete else '已处理'} **{result['elapsed']:.2f}s**")
                rows = [[f"{s['start']:.2f}–{s['end']:.2f}", s["text"], s.get("translation", "")] for s in result["segments"]]
                downloads = export_result(result, tempfile.mkdtemp(prefix="whisper-result-")) if complete else gr.skip()
                yield (status, MANAGER.status(), summary, result["text"], result["translation"], rows,
                       subtitle_html(result, mode), downloads,
                       gr.skip(), *[gr.update(interactive=True) if complete else gr.skip() for _ in range(2)], gr.skip(), result,
                       playback_signal("ready" if complete else "partial", path, job, result))
    except Exception as exc:
        yield (f"⚠️ {exc}", MANAGER.status(), "识别中断，可重试", "", "", [], subtitle_html(None), [],
               gr.skip(), gr.update(interactive=True), gr.update(interactive=True), gr.skip(), None, playback_signal("error", path, job))


def audio_changed(path, enabled=False, cached=None):
    if path and cached and cached.get("_audio_path") == audio_key(path):
        return ("音频已有识别结果，点击播放即可。", *[gr.skip() for _ in range(8)])
    try:
        duration = len(load_audio(path)) / 16000 if path else 0
        action = "点击播放即可自动识别。" if enabled else "请先开启识别，再点击播放。"
        status = f"音频已就绪 · {duration:.2f} 秒。{action}" if path else "请上传音频或录音。"
    except ValueError as exc:
        status = str(exc)
    return status, "尚无结果", "", "", [], subtitle_html(None), [], None, playback_signal()


def device_changed(device):
    import ctranslate2
    if device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
        return "⚠️ CUDA 不可用，请选择 CPU。", MANAGER.status()
    return f"已选择 {device.upper()}；下一次识别时切换并释放旧模型。", MANAGER.status()


def build_app():
    with gr.Blocks(title="多语言连续语音识别系统") as demo:
        cached_result = gr.State(None)
        recognition_enabled = gr.State(False)
        session_marker = gr.HTML('<span data-enabled="false"></span>', elem_id="recognition-session", elem_classes="session-meta")
        player_signal = gr.HTML(playback_signal(), elem_id="playback-signal", elem_classes="session-meta")
        with gr.Column(elem_id="workspace"):
            gr.HTML('<header class="hero"><div><div class="eyebrow">SPEECH LAB / WHISPER SMALL</div><h1>多语言连续语音识别系统</h1></div><span class="badge">本地推理 · 声音同步</span></header>')
            model_status = gr.Markdown(MANAGER.status(), elem_id="model-status")
            with gr.Row(elem_id="dashboard"):
                with gr.Column(scale=3, min_width=300, elem_id="input-panel"):
                    gr.Markdown("### 音频工作台")
                    audio = gr.Audio(sources=["upload", "microphone"], type="filepath", label="上传 / 录音 / 播放", elem_id="source-audio",
                                     waveform_options=gr.WaveformOptions(show_recording_waveform=False))
                    with gr.Row(elem_id="transport-controls"):
                        with gr.Column(min_width=0, elem_classes="transport-item"):
                            start = gr.Button("▶", elem_id="recognize-button", elem_classes="round-control", min_width=0)
                            recognition_label = gr.Markdown("开始识别", elem_id="recognition-label")
                        with gr.Column(min_width=0, elem_classes="transport-item"):
                            play = gr.Button("▶", elem_id="play-button", elem_classes="round-control", min_width=0)
                            gr.Markdown("播放 / 暂停")
                    gr.HTML('<span class="player-notice"></span>', elem_id="playback-notice")
                    device = gr.Radio([("CPU · INT8", "cpu"), ("GPU · CUDA", "cuda")], value="cpu", label="计算设备")
                    mode = gr.Radio(["语音转写", "中文翻译"], value="语音转写", label="任务模式")
                    target = gr.Dropdown(["简体中文"], value="简体中文", label="目标语言", visible=False, interactive=False)
                    privacy = gr.Markdown("离线中译 · 日文/西班牙文经英文中转", visible=False)
                    status = gr.Markdown("先开启识别 → 上传或录音 → 点击播放", elem_id="job-status")
                    with gr.Accordion("四语言示例", open=False, elem_id="audio-examples"):
                        samples = []
                        for directory in ("cmn_hans_cn", "en_us", "ja_jp", "es_419"):
                            sample = next((ROOT / "datasets" / "data" / directory / "audio" / "dev").glob("*.wav"), None)
                            if sample:
                                samples.append([str(sample)])
                        long_sample = ASSETS.parent / "test_output" / "long.wav"
                        if long_sample.is_file():
                            samples.append([str(long_sample)])
                        if samples:
                            gr.Examples(samples, inputs=audio, label="中文 / English / 日本語 / Español；long.wav 为英文重复拼接长样本")
                with gr.Column(scale=7, min_width=400, elem_id="result-panel"):
                    summary = gr.Markdown("自动检测语言 · 等待音频输入", elem_id="result-summary")
                    gr.HTML('<div class="play-hint">点击播放后先准备首段字幕，随后边识别边播放；字幕不足时自动缓冲。</div>')
                    with gr.Tabs(elem_id="result-tabs"):
                        with gr.Tab("同步字幕"):
                            captions = gr.HTML(subtitle_html(None), elem_id="sync-captions")
                        with gr.Tab("完整原文"):
                            source = gr.Textbox(label="原语言转写 · 可复制", lines=12, buttons=["copy"], interactive=False, elem_id="full-source")
                        with gr.Tab("中文译文"):
                            translated = gr.Textbox(label="中文翻译 · 可复制", lines=12, buttons=["copy"], interactive=False, elem_id="full-translation")
                        with gr.Tab("时间戳"):
                            table = gr.Dataframe(headers=["起止 / 秒", "原文", "中文"], datatype=["str"]*3, interactive=False, wrap=True, elem_id="segment-table")
                        with gr.Tab("下载与说明"):
                            files = gr.File(label="TXT / SRT / 时间戳 JSON", file_count="multiple", interactive=False)
                            gr.Markdown("支持 WAV / MP3 / M4A。录音需浏览器授权，请使用 localhost 或 HTTPS。\n\n每个原文字幕块下方显示对应译文。译文按中文标点断句，并在对应语音区间内逐字出现。每个译文字的时间为显示估算。\n\n中译完全离线，日文/西班牙文经英文中转，可能有误差。全文和下载保留完整识别结果。")
        outputs = [status, model_status, summary, source, translated, table, captions, files, audio, device, mode, start, cached_result]
        start.click(toggle_recognition, recognition_enabled, [recognition_enabled, start, session_marker, recognition_label, status], queue=False, api_name="toggle_recognition")
        play.click(None, None, None, js="() => { window.speechTransport?.togglePlayback(); }", queue=False)
        audio.play(auto_recognize, [audio, device, mode, recognition_enabled, cached_result], [*outputs, player_signal],
                   concurrency_limit=1, concurrency_id="asr", trigger_mode="once", api_name="play_audio")
        # Keep the existing direct inference API for scripts and regression tests.
        gr.on(triggers=[], fn=run, inputs=[audio, device, mode], outputs=outputs,
              concurrency_limit=1, concurrency_id="asr", api_name="recognize")
        audio.change(audio_changed, [audio, recognition_enabled, cached_result], [status, summary, source, translated, table, captions, files, cached_result, player_signal], concurrency_id="asr", concurrency_limit=1)
        mode.input(switch_mode, [mode, cached_result], [status, translated, table, captions, files, cached_result, mode, start],
                   concurrency_id="asr", concurrency_limit=1, trigger_mode="always_last", api_name="switch_mode")
        mode.change(lambda m: (gr.update(visible=m=="中文翻译"), gr.update(visible=m=="中文翻译")), mode, [target, privacy], queue=False)
        device.change(device_changed, device, [status, model_status], queue=False)
    return demo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=None, type=int,
                        help="指定固定端口；默认从 7860 开始自动寻找可用端口")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port 必须是 1 到 65535 之间的整数")
    try:
        # None enables Gradio's built-in search instead of forcing one port.
        build_app().queue(max_size=8).launch(server_name=args.host, server_port=args.port,
                                        css=(ASSETS / "style.css").read_text(encoding="utf-8"),
                                        js="() => { (" + (ASSETS / "transport.js").read_text(encoding="utf-8") + ")(); (" + (ASSETS / "sync.js").read_text(encoding="utf-8") + ")(); }",
                                        theme=gr.themes.Soft(primary_hue="cyan", neutral_hue="slate"),
                                        head='<style>@media(min-width:900px){html,body,gradio-app{height:100%;overflow:hidden!important}}</style>',
                                        inbrowser=False, share=False, footer_links=[])
    except OSError as exc:
        if "Cannot find empty port" not in str(exc):
            raise
        parser.exit(1, f"无法绑定{'指定端口 ' + str(args.port) if args.port else '默认端口范围'}。"
                    "可能已有程序占用或端口被系统保留。\n"
                    "请打开已运行服务的地址，或不加 --port 自动选择端口，"
                    "也可使用 --port 其他端口号。\n")
