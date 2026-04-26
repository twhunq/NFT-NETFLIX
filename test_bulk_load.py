"""Load test cho /api/check-bulk: mô phỏng frontend (chia batch 20) và đo
kết quả thực tế. Dùng để verify server có xử lý hết toàn bộ cookies hay
không, không phụ thuộc vào browser SSE handling.

Usage:
  python test_bulk_load.py [--batch-size 20] [--cookies-dir <path>]
"""
import os
import sys
import time
import json
import argparse
import requests

DEFAULT_COOKIES_DIR = r"C:\Users\trant\OneDrive\Documents\NFT-NETFLIX\Cookies"
SERVER_URL = "http://127.0.0.1:5000/api/check-bulk"
BATCH_TIMEOUT = 120  # seconds; nếu 1 batch không xong trong 120s coi là stuck


def load_cookies(directory):
    files = sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f))
    )
    return files


def process_batch(batch_files, batch_idx):
    """Gửi 1 batch lên server, parse SSE stream. Trả về dict thống kê."""
    fields = []
    for path in batch_files:
        with open(path, 'rb') as f:
            data = f.read()
        fields.append(('files', (os.path.basename(path), data, 'text/plain')))

    t0 = time.time()
    last_event = t0
    stats = {'total': len(batch_files), 'live': 0, 'dead': 0, 'error': 0,
             'received_results': 0, 'completed': False, 'timeout': False,
             'elapsed': 0}

    try:
        with requests.post(SERVER_URL, files=fields, stream=True,
                           timeout=(10, 30)) as r:
            r.raise_for_status()
            buf = b''
            for chunk in r.iter_content(chunk_size=4096):
                if chunk:
                    buf += chunk
                    last_event = time.time()
                    while b'\n\n' in buf:
                        line, buf = buf.split(b'\n\n', 1)
                        if line.startswith(b'data: '):
                            try:
                                d = json.loads(line[6:].decode('utf-8'))
                                if d.get('type') == 'result':
                                    stats['received_results'] += 1
                                    s = d.get('result', {}).get('status', 'ERROR')
                                    if s == 'LIVE':
                                        stats['live'] += 1
                                    elif s == 'DEAD':
                                        stats['dead'] += 1
                                    else:
                                        stats['error'] += 1
                                elif d.get('type') == 'complete':
                                    stats['completed'] = True
                            except Exception:
                                pass
                if time.time() - last_event > BATCH_TIMEOUT:
                    stats['timeout'] = True
                    break
                if time.time() - t0 > BATCH_TIMEOUT * 2:
                    stats['timeout'] = True
                    break
    except Exception as e:
        stats['error_msg'] = str(e)

    stats['elapsed'] = round(time.time() - t0, 2)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--cookies-dir', default=DEFAULT_COOKIES_DIR)
    parser.add_argument('--max-files', type=int, default=0,
                        help='Limit total files (0 = all)')
    args = parser.parse_args()

    files = load_cookies(args.cookies_dir)
    if args.max_files > 0:
        files = files[:args.max_files]

    print(f"[INFO] Total cookies: {len(files)}")
    print(f"[INFO] Batch size: {args.batch_size}")
    print(f"[INFO] Server: {SERVER_URL}")
    print()

    total_stats = {'live': 0, 'dead': 0, 'error': 0, 'received': 0,
                   'batches_ok': 0, 'batches_stuck': 0}
    t_start = time.time()
    n_batches = (len(files) + args.batch_size - 1) // args.batch_size

    for i in range(n_batches):
        offset = i * args.batch_size
        batch = files[offset:offset + args.batch_size]
        print(f"[BATCH {i+1}/{n_batches}] Submitting {len(batch)} files...",
              flush=True)
        stats = process_batch(batch, i)

        total_stats['live'] += stats['live']
        total_stats['dead'] += stats['dead']
        total_stats['error'] += stats['error']
        total_stats['received'] += stats['received_results']

        status = "OK" if stats['received_results'] == stats['total'] and not stats['timeout'] else \
                 ("STUCK" if stats['timeout'] else "PARTIAL")
        if status == "OK":
            total_stats['batches_ok'] += 1
        else:
            total_stats['batches_stuck'] += 1

        print(f"[BATCH {i+1}] {status} | recv {stats['received_results']}/{stats['total']} "
              f"| live {stats['live']} dead {stats['dead']} err {stats['error']} "
              f"| {stats['elapsed']}s", flush=True)

        if status == "STUCK":
            print(f"[!] Batch stuck — aborting full test")
            break

    elapsed = time.time() - t_start
    print()
    print("=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"Total elapsed: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Total received: {total_stats['received']}/{len(files)}")
    print(f"Live: {total_stats['live']}")
    print(f"Dead: {total_stats['dead']}")
    print(f"Error: {total_stats['error']}")
    print(f"Batches OK: {total_stats['batches_ok']}")
    print(f"Batches stuck: {total_stats['batches_stuck']}")
    if elapsed > 0:
        print(f"Throughput: {total_stats['received']/elapsed:.2f} cookies/sec "
              f"({total_stats['received']*60/elapsed:.1f} cookies/min)")


if __name__ == '__main__':
    main()
