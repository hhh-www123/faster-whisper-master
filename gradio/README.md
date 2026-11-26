# Gradio 语音识别原型

首次从 GitHub 获取项目，请先按 [根目录安装指南](../README.md) 创建环境并准备 Whisper-small 权重。本文主要记录 Web 实现和开发验证；安装依赖不会自动下载 ASR 模型。

## 启动

所有命令均在项目根目录执行：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv-web\Scripts\python.exe -u gradio/app.py
```

打开终端 `Running on local URL` 后的实际地址，Ctrl+C 停止。也可执行 `./gradio/start.ps1`。浏览器不会自动弹出。
默认从 7860 开始自动寻找可用端口；7860 被占用时会尝试 7861、7862 等端口（Gradio 默认尝试 100 个端口）。可使用 `--port 7861` 指定固定端口；固定端口被占用时会明确提示，不自动换端口。
若之前的服务仍在运行，可以直接打开其地址，不需要重复启动。默认只监听本机，不开启公网分享。设置 `GRADIO_SERVER_PORT` 环境变量可改变自动搜索的起始端口。

首次安装：

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\python.exe -m pip install -r gradio/requirements.txt
.\.venv-web\Scripts\python.exe gradio/prepare_translation.py
.\.venv-web\Scripts\python.exe -u gradio/app.py
```

首次安装还需按根目录指南准备 `model/small/`；如果只使用转写，可以跳过翻译包下载。已有完整环境和模型时无需重复准备。不要使用 `--system-site-packages`，避免原 Anaconda 的 OpenMP 运行库冲突。`requirements.lock.txt` 保存了已验证的 Windows/Python 3.13 环境依赖快照。

## 文件

| 路径 | 用途 |
|---|---|
| `app.py` | 页面、交互、任务进度、错误反馈及下载 |
| `core.py` | 模型管理、识别、字幕时间单元、离线翻译、结果导出 |
| `assets/` | 样式、持续识别/播放控制 `transport.js` 与时间同步字幕 `sync.js` |
| `subtitles/` | 句级断句、字幕换行和中文字幕的时间关联 |
| `prepare_translation.py` | 仅下载缺失的离线翻译包 |
| `start.ps1` | 从任意目录启动本机 Web 系统 |
| `requirements.txt` / `requirements.lock.txt` | Web 依赖及环境快照 |
| `tests/` | 真实推理、错误处理、API、长音频、字幕渲染测试 |
| `test_output/` | 测试生成的结果，不属于程序运行依赖 |
| `TEST_REPORT.md` | 实际验证记录和待手工验收项 |

不要把此目录命名为 Python 包（不添加 `__init__.py`），以免与第三方 `gradio` 包混淆。使用脚本命令启动；入口会定位项目根目录，复用本地核心库、数据和模型，不依赖当前工作目录。

## 使用与实现

1. 选择 CPU/GPU 和转写/中文翻译，点击左侧圆形“开始识别”键开启自动识别。三角形变为双竖线，表示本次页面会话保持开启；再次点击可关闭。
2. 上传 WAV/MP3/M4A，或通过浏览器麦克风录音后停止；也可选择四语言 FLEURS 示例。点击旁边的圆形播放键，系统开始识别；已确认字幕覆盖至少约 3 秒后从头播放，后续识别和中文翻译继续进行，不必等整段完成。短音频或结果不足 3 秒时等最终结果再播放。
3. 更换音频后仍只需点击播放，识别开关不会自动关闭。同一音频的暂停、继续和重播复用已有结果，不重复推理；新上传或新录音会清空上一段音频的结果缓存。首次等待和缓冲时播放键显示处理中；播放追上已确认字幕会暂停媒体，补足约 3 秒后从原位置续播。手动暂停不会因新结果到来而擅自续播。处理期间锁定上传、示例、设备和模式，但保留播放控制；关闭识别会停止等待中的自动播放，已开始的推理仍会完成。
4. 识别完成后可直接切换任务模式：首次切到中文翻译时仅翻译已有原文，随后缓存译文；切回转写或再次切到翻译均无需重新识别。结果标签可复制完整文本、下载 TXT/SRT/JSON。

右侧圆形播放键用三角形表示暂停、双竖线表示播放中；原生音频条保留进度拖动、音量等功能。自动识别关闭时仍可播放试听；刷新页面后识别开关恢复关闭。

桌面版采用单屏工作台（宽度至少 900px）：左侧输入和设置，右侧在同步字幕、原文、译文、时间戳及下载标签间切换。长结果只在面板内部滚动；窄屏保留正常页面滚动以避免裁掉控件。

新音频首次播放时先暂停媒体，随后通过同一次真实推理逐段返回已确认原文和译文，准备好前段后便缓冲播放。结果使用同一 ID 追加，不重置播放位置；下载在全部完成后生成。播放器明确禁用 Gradio 波形播放模式，字幕监听实际播放的原生 audio 元素，始终按 `currentTime` 更新。自动播放若被浏览器阻止，会提示再次点击播放。

这是“上传/录音结束后的分段处理与缓冲播放”，不是直播麦克风的实时识别，也不保证零等待。保留 Whisper 原有窗口、上下文和解码设置；首次字幕仍需完成一个模型窗口的识别，CPU 慢时可能等待数十秒，译文还需文本翻译时间。130.8 秒英文重复长样本在本机 CPU 实测约 40.94 秒获得首个可播放缓冲，全部转写和中译约 135.85 秒；不能把播放缓冲阈值 3 秒误解为推理延迟 3 秒。技术基础为 [faster-whisper 的惰性分段生成器](https://github.com/SYSTRAN/faster-whisper#usage)。

固定读取 `../model/small`，不重新下载 ASR 模型。CPU 优先 int8，GPU 优先 float16；不支持相应精度时使用 float32，显示实际精度。只保留一个 Whisper 模型，同设备复用；切换设备先卸载旧模型。全局锁覆盖加载和推理，Gradio 模型并发为 1；任务期间禁用输入，防止结果与音频错配。

默认自动检测语言，始终使用 `task="transcribe"` 保留原语言。CUDA 不可用时明确提示，用户可以切换 CPU。GPU 配置参考 [上游文档](../docs/FASTER_WHISPER.md)。

字幕读取音频元素的 `currentTime`，暂停、跳转、重播均按媒体时间更新。词时间戳来自模型估计；中日文多字词内字符采用线性插值，没有可靠词时间戳时使用片段内插值。JSON 中的 `timing` 明确区分 `model-word` 与 `interpolated`，字符级时间只用于显示估算。

中文翻译复用 CTranslate2，加载 `../model/translation/` 中的 [Argos 官方翻译包](https://github.com/argosopentech/argospm-index)：英文直译中文，日文和西班牙文经英文中转。分词采用 SentencePiece 或 Moses/BPE，参考 [Argos 分词实现](https://github.com/argosopentech/argos-translate/blob/master/argostranslate/tokenizer.py)。不需要 PyTorch/Stanza。翻译仅缓存一个 CPU int8 模型，安装完成后不联网，音频和文本均在本机处理。各模型包保留原 README 与 OPUS CC-BY 4.0 许可说明。

## 测试

字幕优先采用完整句子。标点和停顿规则、翻译时间关联以及新旧字幕对比见 [测试报告](TEST_REPORT.md#2026-11-26句级字幕断句)。长句优先在块内自然换行，字幕面板跟随实际显示的内容滚动。

翻译模式按原文字幕块显示译文。完整包含的译文片段可以复用。字幕边界切到识别片段内部时，只对对应原文补译。中文译文按自己的标点组织，并根据对应语音区间逐字显示。暂停、跳转和重播均由音频进度决定。

转写/翻译模式仍可直接切换，译文复用缓存，不重复识别音频。播放进度同步、播放前隐藏、暂停和跳转等交互保持不变。

```powershell
.\.venv-web\Scripts\python.exe gradio/tests/web_smoke.py
.\.venv-web\Scripts\python.exe gradio/tests/web_errors.py
.\.venv-web\Scripts\python.exe gradio/tests/web_modes.py
.\.venv-web\Scripts\python.exe gradio/tests/web_transport.py
.\.venv-web\Scripts\python.exe gradio/tests/web_sentences.py
node gradio/tests/web_sync.cjs
node gradio/tests/web_transport.cjs
# 先启动 Web 服务；long.wav 由 web_long.py 准备，可用 --audio 指定其他长音频
.\.venv-web\Scripts\python.exe gradio/tests/web_progressive.py --url http://127.0.0.1:7861
# 可选：约两分钟音频的长输入检查
.\.venv-web\Scripts\python.exe gradio/tests/web_long.py
# 先启动 Web 服务，再在另一个终端运行
.\.venv-web\Scripts\python.exe gradio/tests/web_api.py
```

句级字幕测试使用本地 FLEURS 样本，可通过 `--long-chinese 路径` 加入本地中文长音频，结果保存到 `test_output/sentence_comparison.json`。前端脚本测试可独立运行。详见 [实际测试报告](TEST_REPORT.md)。

## 限制

- 录音需要浏览器麦克风授权，使用 localhost 或 HTTPS。自动化扩展的文件权限不影响手动上传。
- 本机没有 CUDA GPU，GPU 实际推理与设备切换显存释放未实机验证。
- 麦克风实录仍需手工验收；浏览器已验证示例音频的自动识别、字幕联动、暂停、重播和多音频切换。渲染逻辑自动测试不能替代真实麦克风测试。
- 翻译质量有限，日西语中转及缺少跨段上下文会放大误差。中文源保留原文；其他语种可识别，但暂不支持中译。失败时保留原文并明确提示，可能只留下部分译文。
- 单段最长 30 分钟，建议课程演示使用几分钟以内音频。音频会解码到内存，不提供实时流式 ASR。
- 本地存在 `test_output/long.wav` 时，示例区会提供这个英文重复拼接长样本。它仅用于稳定性演示，不代表自然连续讲话或识别质量测试。
- 下载文件使用系统临时目录，长期使用需清理，重启后不保证旧链接仍有效。
