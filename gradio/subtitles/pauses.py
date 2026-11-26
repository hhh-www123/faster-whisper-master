"""Find pauses in the original waveform and align them to Whisper word edges."""
from bisect import bisect_left, bisect_right

from faster_whisper.vad import VadOptions, get_speech_timestamps


def detect_pauses(audio, sample_rate=16000):
    speech = get_speech_timestamps(
        audio, VadOptions(min_silence_duration_ms=128, speech_pad_ms=0),
        sampling_rate=sample_rate,
    )
    return [(left["end"] / sample_rate, right["start"] / sample_rate)
            for left, right in zip(speech, speech[1:])]


def align_pauses(segments, intervals):
    words = []
    offset = 0
    for segment in segments:
        entries = segment.get("words") or []
        if "".join(word["word"] for word in entries) != segment["text"]:
            entries = [dict(word=segment["text"], start=segment["start"], end=segment["end"])]
        for word in entries:
            offset += len(word["word"])
            if word["word"].strip():
                words.append((offset, word["start"], word["end"]))

    if len(words) < 2:
        return []

    edges = sorted(zip(words, words[1:]), key=lambda pair: pair[0][2])
    ends = [left[2] for left, _ in edges]
    matches = {}
    for start, end in intervals:
        if end > words[-1][2]:
            break
        first = bisect_left(ends, start - .42)
        last = bisect_right(ends, end + .20)
        if first == last:
            continue

        def distance(edge):
            _, before_start, before_end = edge[0]
            _, after_start, after_end = edge[1]
            # Allow VAD's frame delay and a word stretched across the gap.
            stretch = max(0, min(.30, max(before_end - before_start, after_end - after_start) - .20))
            return .55 * abs(before_end - start + .10) + .45 * abs(after_start - end + .08) - .36 * stretch

        best = min(edges[first:last], key=distance)
        offset = best[0][0]
        if distance(best) <= .28 and offset not in matches:
            matches[offset] = round(end - start, 3)
    return sorted(matches.items())
