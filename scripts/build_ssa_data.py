#!/usr/bin/env python3
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

# NameFuture requires the latest released SSA national baby-name year.
# As of 2026-09, SSA has released 2025 data.
MIN_REQUIRED_LATEST_YEAR = 2025

SSA_URLS = [
    'https://www.ssa.gov/oact/babynames/names.zip',
    'https://www.ssa.gov/OACT/babynames/names.zip',
]

# Only use this mirror if it has caught up to the required year.
MIRROR_URL = 'https://raw.githubusercontent.com/hackerb9/ssa-baby-names/main/alldata.txt'

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data'
SHARDS = OUT / 'shards'

BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/152.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/zip,application/octet-stream;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.ssa.gov/oact/babynames/limits.html',
}


def validate_zip(blob: bytes) -> int:
    """Return latest yob year if this is a valid SSA names ZIP."""
    if len(blob) < 100_000:
        raise RuntimeError(f'download too small ({len(blob)} bytes)')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            years = []
            for n in z.namelist():
                m = re.fullmatch(r'yob(\d{4})\.txt', Path(n).name)
                if m:
                    years.append(int(m.group(1)))
            if not years:
                raise RuntimeError('ZIP contains no yobYYYY.txt files')
            return max(years)
    except zipfile.BadZipFile as e:
        raise RuntimeError('response was not a valid ZIP') from e


def fetch_urllib(url: str, timeout=90) -> bytes:
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_command(url: str, tool: str) -> bytes:
    """Fallback to curl/wget because SSA/CDN may treat Python urllib differently."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tf:
        path = tf.name
    try:
        if tool == 'curl':
            cmd = [
                'curl', '-fL', '--retry', '4', '--retry-delay', '2',
                '--connect-timeout', '25', '--max-time', '120',
                '-A', BROWSER_HEADERS['User-Agent'],
                '-e', BROWSER_HEADERS['Referer'],
                '-o', path, url,
            ]
        else:
            cmd = [
                'wget', '-q', '--tries=4', '--timeout=30',
                '--user-agent=' + BROWSER_HEADERS['User-Agent'],
                '--referer=' + BROWSER_HEADERS['Referer'],
                '-O', path, url,
            ]
        subprocess.run(cmd, check=True)
        return Path(path).read_bytes()
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def download_official() -> tuple[bytes, str, int]:
    errors = []
    for url in SSA_URLS:
        for method in ('urllib', 'curl', 'wget'):
            try:
                print(f'Trying official SSA via {method}: {url}', flush=True)
                if method == 'urllib':
                    blob = fetch_urllib(url)
                else:
                    blob = fetch_command(url, method)
                latest = validate_zip(blob)
                if latest < MIN_REQUIRED_LATEST_YEAR:
                    raise RuntimeError(
                        f'SSA archive only reaches {latest}; need >= {MIN_REQUIRED_LATEST_YEAR}'
                    )
                print(f'Official SSA archive validated through {latest}.', flush=True)
                return blob, url, latest
            except Exception as e:
                msg = f'{method} {url}: {e}'
                print('  failed: ' + msg, file=sys.stderr, flush=True)
                errors.append(msg)
                time.sleep(1)
    raise RuntimeError('All official SSA download methods failed:\n' + '\n'.join(errors))


def normalize_name(s):
    return re.sub(r'[^a-z]', '', s.lower())


def shard_for(name):
    n = normalize_name(name)
    return n[0] if n and 'a' <= n[0] <= 'z' else '_'


def add_year(rows_by_name, year, sex_rows, totals, max_ranks):
    ranks = {'F': 0, 'M': 0}
    # Do not assume source order. Explicitly rank each sex by count desc, alpha tie-break.
    ordered = sorted(sex_rows, key=lambda x: (x[1], -x[2], x[0]))
    for name, sex, count in ordered:
        ranks[sex] += 1
        rank = ranks[sex]
        totals[year][sex] += count
        rows_by_name[name][sex].append((year, count, rank))
    max_ranks[year] = ranks


def parse_official_zip(blob):
    rows_by_name = defaultdict(lambda: {'F': [], 'M': []})
    totals = defaultdict(lambda: {'F': 0, 'M': 0})
    max_ranks = {}
    latest = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        files = []
        for n in z.namelist():
            m = re.fullmatch(r'yob(\d{4})\.txt', Path(n).name)
            if m:
                files.append((int(m.group(1)), n))
        files.sort()
        if not files:
            raise RuntimeError('SSA ZIP contained no yobYYYY.txt files')
        for year, fn in files:
            latest = max(latest, year)
            sex_rows = []
            with z.open(fn) as f:
                text = io.TextIOWrapper(f, encoding='utf-8-sig', newline='')
                for row in csv.reader(text):
                    if len(row) < 3:
                        continue
                    name = row[0].strip()
                    sex = row[1].strip()
                    try:
                        count = int(row[2])
                    except ValueError:
                        continue
                    if sex in ('F', 'M'):
                        sex_rows.append((name, sex, count))
            add_year(rows_by_name, year, sex_rows, totals, max_ranks)
    return rows_by_name, totals, max_ranks, latest


def parse_mirror_if_current(blob):
    temp = defaultdict(list)
    for raw in blob.decode('utf-8-sig').splitlines():
        if not raw.strip():
            continue
        parts = raw.split(',')
        if len(parts) < 4:
            continue
        name, sex = parts[0].strip(), parts[1].strip()
        try:
            count, year = int(parts[2]), int(parts[3])
        except ValueError:
            continue
        if sex in ('F', 'M'):
            temp[year].append((name, sex, count))
    if not temp:
        raise RuntimeError('mirror contained no usable rows')
    latest = max(temp)
    if latest < MIN_REQUIRED_LATEST_YEAR:
        raise RuntimeError(
            f'mirror is stale (latest={latest}); refusing to deploy stale data'
        )
    rows_by_name = defaultdict(lambda: {'F': [], 'M': []})
    totals = defaultdict(lambda: {'F': 0, 'M': 0})
    max_ranks = {}
    for year in sorted(temp):
        add_year(rows_by_name, year, temp[year], totals, max_ranks)
    return rows_by_name, totals, max_ranks, latest


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    SHARDS.mkdir(parents=True, exist_ok=True)
    for p in SHARDS.glob('*.json'):
        p.unlink()

    source = None
    source_url = None

    try:
        blob, source_url, latest_seen = download_official()
        rows_by_name, totals, max_ranks, latest = parse_official_zip(blob)
        source = 'U.S. Social Security Administration national names.zip'
    except Exception as official_error:
        print('\nOfficial SSA download failed. Trying mirror ONLY if it is current…', file=sys.stderr)
        print(str(official_error), file=sys.stderr)
        try:
            mirror_blob = fetch_urllib(MIRROR_URL, timeout=120)
            rows_by_name, totals, max_ranks, latest = parse_mirror_if_current(mirror_blob)
            source = 'Current GitHub mirror of SSA national data'
            source_url = MIRROR_URL
        except Exception as mirror_error:
            raise SystemExit(
                '\nFATAL: Could not obtain CURRENT SSA data. '\
                'NameFuture refuses to publish stale results.\n'\
                f'Official error: {official_error}\n'\
                f'Mirror error: {mirror_error}\n'
            )

    if latest < MIN_REQUIRED_LATEST_YEAR:
        raise SystemExit(
            f'FATAL: dataset latest year is {latest}; expected at least {MIN_REQUIRED_LATEST_YEAR}. '
            'Refusing to deploy stale data.'
        )

    shards = defaultdict(dict)
    entries = 0
    combos = 0

    for name in sorted(rows_by_name, key=str.lower):
        d = rows_by_name[name]
        out = {}
        for sex in ('F', 'M'):
            arr = d[sex]
            if arr:
                flat = []
                for y, c, r in arr:
                    flat += [y, c, r]
                    entries += 1
                out[sex] = flat
                combos += 1
        shards[shard_for(name)][name] = out

    for key, mapping in shards.items():
        with (SHARDS / f'{key}.json').open('w', encoding='utf-8') as f:
            json.dump(mapping, f, separators=(',', ':'), ensure_ascii=False)

    meta = {
        'latestYear': latest,
        'firstYear': min(totals),
        'nameCount': len(rows_by_name),
        'nameSexCombinations': combos,
        'records': entries,
        'source': source,
        'sourceUrl': source_url,
        'privacyThreshold': 5,
        'totals': {str(y): totals[y] for y in sorted(totals)},
        'maxRanks': {str(y): max_ranks[y] for y in sorted(max_ranks)},
        'generatedBy': 'scripts/build_ssa_data.py',
    }

    with (OUT / 'meta.json').open('w', encoding='utf-8') as f:
        json.dump(meta, f, separators=(',', ':'))

    size = sum(p.stat().st_size for p in SHARDS.glob('*.json')) + (OUT / 'meta.json').stat().st_size
    print(f'Built {len(rows_by_name):,} unique names, {entries:,} yearly records through {latest}.')
    print(f'Data size: {size / 1024 / 1024:.1f} MiB across {len(shards)} shards.')
    print(f'Source: {source_url}')


if __name__ == '__main__':
    main()
