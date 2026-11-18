"""Real long-file upload through Gradio; measure confirmed partial delivery."""
import argparse
import json
import re
import time
from pathlib import Path

from gradio_client import Client, handle_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:7861')
    parser.add_argument('--audio', default=str(Path(__file__).resolve().parents[1] / 'test_output/long.wav'))
    args = parser.parse_args()
    client = Client(args.url)
    client.predict(api_name='/toggle_recognition')
    started = time.perf_counter()
    partials = []
    first_buffer = None
    for packet in client.submit(handle_file(args.audio), 'cpu', '中文翻译', api_name='/play_audio'):
        signal = packet[-1]
        phase = re.search(r'data-phase="([^"]+)"', signal).group(1)
        if phase in ('partial', 'ready'):
            ready = float(re.search(r'data-ready-until="([^"]+)"', signal).group(1))
            seconds = time.perf_counter()-started
            if phase == 'partial':
                partials.append(dict(seconds=seconds, ready_until=ready, source_length=len(packet[3])))
                assert packet[4], 'translation missing from confirmed buffer'
                if ready >= 3 and first_buffer is None:
                    first_buffer = seconds
                    print(f'FIRST BUFFER: {seconds:.2f}s, confirmed audio through {ready:.2f}s', flush=True)
        elif phase == 'error':
            raise AssertionError(packet[0])
    total = time.perf_counter()-started
    assert len(partials) > 1 and first_buffer < total
    assert [p['ready_until'] for p in partials] == sorted(p['ready_until'] for p in partials)
    assert 'satellite' in packet[3] and '卫星' in packet[4]
    assert packet[7], 'final exports missing'
    report = dict(first_buffer_seconds=first_buffer, total_seconds=total, partial_updates=len(partials),
                  ready_until=ready, text_length=len(packet[3]), translation=packet[4], updates=partials)
    output = Path(__file__).resolve().parents[1] / 'test_output/progressive.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('translation','updates')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
