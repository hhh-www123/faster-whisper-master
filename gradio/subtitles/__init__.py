"""Sentence cues on the original audio clock, independent of ASR and translation."""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

from opencc import OpenCC

from .pauses import align_pauses


TERMINAL = re.compile(r"[。！？.!?…]+[”’」』】）》\"')\]]*")
CLAUSE = re.compile(r"[，、；,;]")
PROTECTED = re.compile(
    r"(?:https?://|www\.)[^\s<>\"“”。，！？、；]+"
    r"|[\w.%+-]+@(?:[\w-]+\.)+[\w-]+"
    r"|\b(?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|io|es|jp|cn)(?:/[^\s<>\"“”。，！？、；]+)?"
    r"|\d+(?:[.,/:]\d+)+"
    r"|\b(?:dr|dra|mr|mrs|ms|prof|sr|sra|srta|jr|st|vs|ud|uds|núm|aprox|etc|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\."
    r"|\b(?:p\.\s*ej\.|(?:[a-z]\.){2,}|[A-Z]\.(?=\s+[A-Z]))",
    re.IGNORECASE,
)
CONTINUATION = re.compile(r"(?:\b(?:the|of|and|or|to|a|de|del|y|o|que|la|el)|[的将將把和与與在从從のをが])$", re.I)
TRANSITION = re.compile(
    r"^(?:接下[来來]|另一方面|首先|其次|最[后後]|[总總]之|"
    r"[这這](?:是|[一二三四五六七八九十几幾两兩][个個件点點次期])|如果|先看|再看|一方|次に|そして|"
    r"(?:however|next|then|finally|therefore|además|luego|finalmente|sin embargo)\b)", re.I
)
PUNCTUATION = "，。！？；、：,.!?;:…"
QUOTES = "”’」』】）》\"')]"
ZH_SHORT_CLAUSE = re.compile(r"(?:的[时時]候|[之以][前后後])$")
ZH_SENTENCE_END = re.compile(r"(?:在哪(?:[里裡裏儿兒])?|何[处處]|[吗嗎])$")
CJK = re.compile(r"[\u2e80-\u2fff\u3000-\u30ff\u3400-\u9fff\uff01-\uff60]")
HAN = re.compile(r"[\u3400-\u9fff\U00020000-\U000323af]")
LINE_WIDTH = 68


@dataclass(frozen=True)
class Pauses:
    weak: float = 0.30
    strong: float = 0.60
    long: float = 1.00


@lru_cache(maxsize=1)
def _simplifier():
    return OpenCC("tw2s")


def _protected(text):
    positions = set()
    for match in PROTECTED.finditer(text):
        value = match[0]
        if value.startswith(("https://", "http://", "www.")) or "/" in value:
            value = value.rstrip(".,;:!?)]}")
        positions.update(range(match.start(), match.start() + len(value)))
    return positions


def _width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _split_boundaries(text, gaps, pauses, punctuate, acoustic):
    protected = _protected(text)
    cuts = set()
    for match in TERMINAL.finditer(text):
        # A regex run can include both a protected dot and actual punctuation.
        if not any(ch in ".!?。！？" and i not in protected
                   for i, ch in enumerate(match[0], match.start())):
            continue
        if match[0].startswith(("...", "…")) and set(match[0]) <= set(".…"):
            continue
        cuts.add(match.end())

    terminals = sorted(cuts)
    marks = {}
    soft_pauses = []
    punctuation = [i + 1 for i, ch in enumerate(text) if ch in PUNCTUATION and i not in protected]
    start = sentence = last_mark = 0
    strong = pauses.strong - .12 if acoustic else pauses.strong
    for offset, gap in gaps:
        while sentence < len(terminals) and terminals[sentence] <= offset:
            start = max(start, terminals[sentence])
            sentence += 1
        if offset <= start:
            continue
        if not acoustic and gap < pauses.weak:
            continue
        # Only nearby context is needed; the rest of the cue may be very long.
        before = text[max(start, offset - 32):offset]
        left = before.rstrip()
        after = text[offset:offset + 32]
        right = after.lstrip()
        if not left.strip() or not right:
            continue
        previous = offset - (len(before) - len(left)) - 1
        next_start = offset + len(after) - len(right)
        # 的 can finish a Mandarin clause (e.g. “一起出现的”). Other function
        # words still bar a split when the pause merely interrupts the phrase.
        if previous in protected or (CONTINUATION.search(left)
                                     and not (acoustic and gap >= .40 and left.endswith("的"))):
            continue
        if right[0] in PUNCTUATION + QUOTES or (
                next_start in protected and previous + 1 in protected):
            continue
        if punctuate and left[-1].isdecimal() and (
                right[0].isdecimal() or right[0] in "年月日时時分秒号號个個"):
            continue

        if acoustic and gap < .20 and not ZH_SHORT_CLAUSE.search(left):
            continue
        transition = bool(TRANSITION.match(right))
        terminal = (left.endswith((";", "；")) or gap >= pauses.long
                    or (gap >= strong and transition)
                    or (acoustic and gap >= .40 and ZH_SENTENCE_END.search(left)))
        if terminal:
            cuts.add(offset)
            start = offset
        else:
            bonus = .2 if left.endswith(("，", "、", ",", "…")) or transition else 0
            soft_pauses.append((offset, gap + bonus))

        if punctuate and left[-1].isalnum() and right[0].isalnum():
            prior = bisect_right(punctuation, offset)
            previous_mark = max(last_mark, punctuation[prior - 1] if prior else 0)
            if terminal:
                mark = "？" if left.endswith(("吗", "嗎")) else "。"
            elif offset - previous_mark >= (4 if acoustic else 6):
                mark = "，"
            else:
                continue
            marks[offset] = mark
            last_mark = offset

    # Unpunctuated speech can run for minutes. Prefer a measured pause near
    # two lines' worth of text while leaving ordinary complete sentences alone.
    widths = [0]
    for ch in text:
        widths.append(widths[-1] + _width(ch))
    section = cursor = 0
    for end in sorted(cuts | {len(text)}):
        limit = LINE_WIDTH * (3 if end in terminals else 2)
        while widths[end] - widths[section] > limit:
            candidates = []
            while cursor < len(soft_pauses):
                point, weight = soft_pauses[cursor]
                width = widths[point] - widths[section]
                if point >= end or (candidates and width > 2.35 * LINE_WIDTH):
                    break
                if width >= LINE_WIDTH * .75 and widths[end] - widths[point] >= LINE_WIDTH * .4:
                    candidates.append((point, weight - abs(width / LINE_WIDTH - 1.55) * .20, cursor))
                cursor += 1
            if not candidates:
                break
            section, _, index = max(candidates, key=lambda entry: entry[1])
            cuts.add(section)
            cursor = index + 1
        section = end
    return sorted(point for point in cuts if point < len(text)), marks


def _interpolated(text, start, end, language):
    matches = re.finditer(r"\s+|[^\s]" if language in ("zh", "ja") else r"\s*\S+|\s+$", text)
    scale = (end - start) / max(len(text), 1)
    return [dict(text=m[0], start=start + scale * m.start(), end=start + scale * m.end()) for m in matches]


def _piece_slice(piece, start, end):
    if start == 0 and end == len(piece["text"]):
        return piece
    scale = (piece["end"] - piece["start"]) / len(piece["text"])
    return dict(text=piece["text"][start:end], start=piece["start"] + scale * start,
                end=piece["start"] + scale * end)


def _display(pieces, closed, language):
    raw = "".join(p["text"] for p in pieces)
    text = []
    offsets = [0] * (len(raw) + 1)
    for m in re.finditer(r"\s+|\S", raw):
        left, right = raw[m.start()-1:m.start()] if m.start() else "", raw[m.end():m.end()+1]
        space = m[0].isspace()
        if not space or left and right and not (CJK.fullmatch(left) and CJK.fullmatch(right)):
            text.append(" " if space else m[0])
        for i in range(m.start() + 1, m.end() + 1):
            offsets[i] = len(text)
    display = "".join(text)
    if language == "zh" and HAN.search(display):
        # The pinned tw2s tables preserve character counts, so source offsets still apply.
        display = _simplifier().convert(display)
    width = _width(display)
    line_width = LINE_WIDTH
    if closed and width > line_width:
        protected = _protected(display)
        breaks = [
            m.end() for m in CLAUSE.finditer(display) if m.start() not in protected
            and max(_width(display[:m.end()].strip()), _width(display[m.end():].strip())) <= line_width
        ]
        if breaks:
            cut = min(breaks, key=lambda pos: abs(_width(display[:pos]) - width / 2))
            # Prefer a clause wrap only when both resulting lines are reasonable.
            if _width(display[:cut]) >= width * .30 and _width(display[cut:]) >= width * .30:
                if display[cut:cut+1] == " ":
                    display = display[:cut] + "\n" + display[cut+1:]
                else:
                    display = display[:cut] + "\n" + display[cut:]
                    offsets = [n + (n > cut) for n in offsets]
    steps = []
    utf16 = [0]
    for ch in display:
        utf16.append(utf16[-1] + (2 if ord(ch) > 0xFFFF else 1))
    offset = through = 0
    for p in pieces:
        offset += len(p["text"])
        if offsets[offset] > through:
            through = offsets[offset]
            # JS slices UTF-16 strings, so an astral character takes two positions.
            steps.append([p["start"], utf16[through]])
    return display, steps


def _cues(pieces, pauses, complete, language, *, punctuate=False, measured_gaps=None):
    pieces = [p for p in pieces if p["text"]]
    text = "".join(p["text"] for p in pieces)
    ends = []
    for piece in pieces:
        ends.append((ends[-1] if ends else 0) + len(piece["text"]))
    punctuate = punctuate and bool(HAN.search(text))
    if measured_gaps is None:
        spoken_pieces = [(offset, p) for offset, p in zip(ends, pieces) if p["text"].strip()]
        gaps = [(offset, round(after["start"] - before["end"], 3))
                for (offset, before), (_, after) in zip(spoken_pieces, spoken_pieces[1:])]
    else:
        gaps = measured_gaps
    cuts, marks = _split_boundaries(text, gaps, pauses, punctuate, measured_gaps is not None)
    start = 0
    for stop in [*cuts, len(text)]:
        first = bisect_right(ends, start)
        group = []
        for i in range(first, len(pieces)):
            base = ends[i-1] if i else 0
            if base >= stop:
                break
            p = pieces[i]
            group.append(_piece_slice(p, max(0, start-base), min(stop-base, len(p["text"]))))
            offset = min(ends[i], stop)
            if offset in marks:
                at = group[-1]["end"]
                if i + 1 < len(pieces):
                    at = max(group[-1]["start"], min(at, pieces[i + 1]["start"]))
                group.append(dict(text=marks[offset], start=at, end=at))
        spoken = [p for p in group if p["text"].strip()]
        if spoken:
            end = max(p["end"] for p in spoken)
            closed = complete or stop < len(text)
            if punctuate and stop == len(text) and complete:
                tail = text.rstrip().rstrip(QUOTES).rstrip()
                if tail and tail[-1].isalnum():
                    mark = "？" if tail.endswith(("吗", "嗎")) else "。"
                    group.append(dict(text=mark, start=end, end=end))
            display, steps = _display(group, closed, language)
            yield start, stop, dict(start=spoken[0]["start"], end=end,
                                    text=text[start:stop], display=display, steps=steps)
        start = stop


def _translation_cues(row, segments, translate, pauses, complete):
    """Reuse whole-segment translations, or translate this exact source cue."""
    if all(s.get("translation", "").strip() for s in segments) and (
            "".join(s["text"] for s in segments).strip() == row["text"].strip()):
        pieces = [
            piece for s in segments
            for piece in _interpolated(s["translation"].strip(), max(row["start"], s["start"]),
                                       min(row["end"], s["end"]), "zh")
        ]
    else:
        text = translate(row["text"].strip())
        pieces = _interpolated(text.strip(), row["start"], row["end"], "zh") if text else []

    for _, _, target in _cues(pieces, pauses, complete, "zh"):
        # Translate timing estimates from this cue's start through its end.
        steps = target["steps"]
        if len(steps) > 1:
            interval = (target["end"] - target["start"]) / (len(steps) - 1)
            for i, step in enumerate(steps[:-1]):
                step[0] = target["start"] + interval * i
            steps[-1][0] = target["end"]
        yield target


def sentence_captions(segments, language, *, translate=None, complete=True, pauses=Pauses(), audio_pauses=None):
    """Build source cues and optional Chinese cues on their shared audio clock."""
    pieces = []
    segment_ends = []
    offset = 0
    for segment in segments:
        units = segment.get("units") or []
        text = segment["text"]
        if "".join(u["text"] for u in units) != text:
            units = _interpolated(text, segment["start"], segment["end"], language)
        pieces.extend(units)
        offset += len(text)
        segment_ends.append(offset)

    punctuate = language == "zh"
    gaps = align_pauses(segments, audio_pauses) if punctuate and audio_pauses is not None else None
    rows = []
    for start, stop, row in _cues(pieces, pauses, complete, language, punctuate=punctuate, measured_gaps=gaps):
        row["translations"] = []
        if translate:
            first = bisect_right(segment_ends, start)
            last = bisect_left(segment_ends, stop)
            row["translations"] = list(
                _translation_cues(row, segments[first:last+1], translate, pauses, complete)
            )
        rows.append(row)
    return rows
