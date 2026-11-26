"""Sentence cues on the original audio clock, independent of ASR and translation."""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import re
import unicodedata


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
CONTINUATION = re.compile(r"(?:\b(?:the|of|and|or|to|a|de|del|y|o|que|la|el)|[的将把和与在从のをが])$", re.I)
TRANSITION = re.compile(
    r"^(?:接下来|另一方面|首先|其次|最后|总之|一方|次に|そして|"
    r"(?:however|next|then|finally|therefore|además|luego|finalmente|sin embargo)\b)", re.I
)
CJK = re.compile(r"[\u2e80-\u2fff\u3000-\u30ff\u3400-\u9fff\uff01-\uff60]")
LINE_WIDTH = 68


@dataclass(frozen=True)
class Pauses:
    weak: float = 0.30
    strong: float = 0.60
    long: float = 1.00


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


def _split_boundaries(text, pieces, ends, pauses):
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
    soft_pauses = []
    start = sentence = 0
    for i, offset in enumerate(ends[:-1]):
        while sentence < len(terminals) and terminals[sentence] <= offset:
            start = max(start, terminals[sentence])
            sentence += 1
        if offset <= start:
            continue
        gap = pieces[i + 1]["start"] - pieces[i]["end"]
        if gap < pauses.weak:
            continue
        left = text[start:offset].strip()
        tail = re.match(r"\s*", text[offset:offset+100])
        next_start = offset + tail.end()
        right = text[next_start:next_start+100]
        if not left or not right or not any(ch.isalnum() for ch in left):
            continue
        last = offset - (len(text[start:offset]) - len(text[start:offset].rstrip())) - 1
        if last in protected or CONTINUATION.search(left):
            continue
        if right[0] in "。！？.!?,，、；;…」』”’" or (
                next_start in protected and last + 1 in protected):
            continue
        if (left.endswith((";", "；")) or gap >= pauses.long
                or gap >= pauses.strong and TRANSITION.match(right)):
            cuts.add(offset)
            start = offset
        else:
            bonus = .2 if left.endswith(("，", "、", ",", "…")) or TRANSITION.match(right) else 0
            soft_pauses.append((offset, gap + bonus))

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
    return sorted(point for point in cuts if point < len(text))


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


def _display(pieces, closed):
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


def _cues(pieces, pauses, complete=True):
    pieces = [p for p in pieces if p["text"]]
    text = "".join(p["text"] for p in pieces)
    ends = []
    for piece in pieces:
        ends.append((ends[-1] if ends else 0) + len(piece["text"]))
    cuts = _split_boundaries(text, pieces, ends, pauses)
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
        spoken = [p for p in group if p["text"].strip()]
        if spoken:
            display, steps = _display(group, complete or stop < len(text))
            yield start, stop, dict(start=spoken[0]["start"], end=max(p["end"] for p in spoken),
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

    for _, _, target in _cues(pieces, pauses, complete):
        # Translate timing estimates from this cue's start through its end.
        steps = target["steps"]
        if len(steps) > 1:
            interval = (target["end"] - target["start"]) / (len(steps) - 1)
            for i, step in enumerate(steps[:-1]):
                step[0] = target["start"] + interval * i
            steps[-1][0] = target["end"]
        yield target


def sentence_captions(segments, language, *, translate=None, complete=True, pauses=Pauses()):
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

    rows = []
    for start, stop, row in _cues(pieces, pauses, complete):
        row["translations"] = []
        if translate:
            first = bisect_right(segment_ends, start)
            last = bisect_left(segment_ends, stop)
            row["translations"] = list(
                _translation_cues(row, segments[first:last+1], translate, pauses, complete)
            )
        rows.append(row)
    return rows
