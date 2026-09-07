#!/usr/bin/env python3
import csv, io, json, math, os, re, sys, urllib.request, zipfile
from collections import defaultdict
from pathlib import Path

SSA_URL = 'https://www.ssa.gov/oact/babynames/names.zip'
MIRROR_URL = 'https://raw.githubusercontent.com/hackerb9/ssa-baby-names/main/alldata.txt'
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data'
SHARDS = OUT / 'shards'


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent':'NameFuture/1.0 (+GitHub Pages data build)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def normalize_name(s):
    return re.sub(r'[^a-z]', '', s.lower())


def shard_for(name):
    n = normalize_name(name)
    return n[0] if n and 'a' <= n[0] <= 'z' else '_'


def add_year(rows_by_name, year, sex_rows, totals, max_ranks):
    # SSA annual files are sorted by sex, then descending count. Rank is row order within sex.
    ranks = {'F':0, 'M':0}
    for name, sex, count in sex_rows:
        ranks[sex] += 1
        rank = ranks[sex]
        totals[year][sex] += count
        rows_by_name[name][sex].append((year, count, rank))
    max_ranks[year] = ranks


def parse_official_zip(blob):
    rows_by_name = defaultdict(lambda: {'F':[], 'M':[]})
    totals = defaultdict(lambda: {'F':0,'M':0})
    max_ranks = {}
    latest = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        files = sorted([n for n in z.namelist() if re.fullmatch(r'yob\d{4}\.txt', Path(n).name)])
        if not files:
            raise RuntimeError('SSA ZIP contained no yobYYYY.txt files')
        for fn in files:
            year = int(re.search(r'(\d{4})', Path(fn).name).group(1))
            latest = max(latest, year)
            sex_rows=[]
            with z.open(fn) as f:
                text=io.TextIOWrapper(f, encoding='utf-8-sig', newline='')
                for row in csv.reader(text):
                    if len(row) < 3: continue
                    name, sex, count = row[0].strip(), row[1].strip(), int(row[2])
                    if sex in ('F','M'):
                        sex_rows.append((name,sex,count))
            add_year(rows_by_name, year, sex_rows, totals, max_ranks)
    return rows_by_name, totals, max_ranks, latest, 'SSA names.zip'


def parse_mirror(blob):
    # mirror format: name,sex,occurrences,year
    temp=defaultdict(list)
    for raw in blob.decode('utf-8-sig').splitlines():
        if not raw.strip(): continue
        parts=raw.split(',')
        if len(parts) < 4: continue
        name, sex, count, year = parts[0].strip(), parts[1].strip(), int(parts[2]), int(parts[3])
        if sex in ('F','M'):
            temp[year].append((name,sex,count))
    rows_by_name = defaultdict(lambda: {'F':[], 'M':[]})
    totals = defaultdict(lambda: {'F':0,'M':0})
    max_ranks = {}
    latest=max(temp)
    for year in sorted(temp):
        # rank independently by sex, descending count then alpha to match SSA semantics
        rows=sorted(temp[year], key=lambda x:(x[1],-x[2],x[0]))
        add_year(rows_by_name, year, rows, totals, max_ranks)
    return rows_by_name, totals, max_ranks, latest, 'GitHub mirror of SSA data'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    SHARDS.mkdir(parents=True, exist_ok=True)
    for p in SHARDS.glob('*.json'): p.unlink()

    try:
        print('Downloading official SSA national data…', flush=True)
        parsed=parse_official_zip(fetch(SSA_URL))
    except Exception as e:
        print(f'Official SSA download failed ({e}); trying public mirror…', file=sys.stderr, flush=True)
        parsed=parse_mirror(fetch(MIRROR_URL, timeout=120))

    rows_by_name, totals, max_ranks, latest, source = parsed

    shards=defaultdict(dict)
    entries=0
    combos=0
    for name in sorted(rows_by_name, key=str.lower):
        d=rows_by_name[name]
        out={}
        for sex in ('F','M'):
            arr=d[sex]
            if arr:
                # Flat triples save substantial JSON overhead: [year,count,rank,year,count,rank,...]
                flat=[]
                for y,c,r in arr:
                    flat += [y,c,r]
                    entries += 1
                out[sex]=flat
                combos += 1
        shards[shard_for(name)][name]=out

    for key, mapping in shards.items():
        with (SHARDS/f'{key}.json').open('w', encoding='utf-8') as f:
            json.dump(mapping, f, separators=(',',':'), ensure_ascii=False)

    meta={
      'latestYear': latest,
      'firstYear': min(totals),
      'nameCount': len(rows_by_name),
      'nameSexCombinations': combos,
      'records': entries,
      'source': source,
      'sourceUrl': SSA_URL,
      'privacyThreshold': 5,
      'totals': {str(y): totals[y] for y in sorted(totals)},
      'maxRanks': {str(y): max_ranks[y] for y in sorted(max_ranks)},
      'generatedBy': 'scripts/build_ssa_data.py'
    }
    with (OUT/'meta.json').open('w',encoding='utf-8') as f:
        json.dump(meta,f,separators=(',',':'))

    size=sum(p.stat().st_size for p in SHARDS.glob('*.json')) + (OUT/'meta.json').stat().st_size
    print(f'Built {len(rows_by_name):,} unique names, {entries:,} yearly records through {latest}.')
    print(f'Data size: {size/1024/1024:.1f} MiB across {len(shards)} shards.')

if __name__=='__main__':
    main()
