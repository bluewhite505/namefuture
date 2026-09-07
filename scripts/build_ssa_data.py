#!/usr/bin/env python3
import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

MIN_REQUIRED_LATEST_YEAR = 2025

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ZIP = ROOT / "data" / "source" / "names.zip"
OUT = ROOT / "data"
SHARDS = OUT / "shards"

def normalize_name(s):
    return re.sub(r"[^a-z]", "", s.lower())

def shard_for(name):
    n = normalize_name(name)
    return n[0] if n and "a" <= n[0] <= "z" else "_"

def main():
    if not SOURCE_ZIP.exists():
        raise SystemExit(
            "FATAL: data/source/names.zip was not found. "
            "Upload the official SSA national names ZIP there."
        )

    try:
        z = zipfile.ZipFile(SOURCE_ZIP)
    except zipfile.BadZipFile:
        raise SystemExit("FATAL: data/source/names.zip is not a valid ZIP file.")

    year_files = []
    for member in z.namelist():
        m = re.fullmatch(r"(?:.*/)?yob(\d{4})\.txt", member)
        if m:
            year_files.append((int(m.group(1)), member))

    if not year_files:
        raise SystemExit("FATAL: names.zip contains no yobYYYY.txt files.")

    year_files.sort()
    first_year = year_files[0][0]
    latest_year = year_files[-1][0]

    print(f"SSA archive coverage: {first_year}-{latest_year}")

    if latest_year < MIN_REQUIRED_LATEST_YEAR:
        raise SystemExit(
            f"FATAL: SSA archive only reaches {latest_year}. "
            f"Need at least {MIN_REQUIRED_LATEST_YEAR}. Refusing to deploy stale data."
        )

    if not any(year == MIN_REQUIRED_LATEST_YEAR for year, _ in year_files):
        raise SystemExit(
            f"FATAL: yob{MIN_REQUIRED_LATEST_YEAR}.txt is missing from names.zip."
        )

    rows_by_name = defaultdict(lambda: {"F": [], "M": []})
    totals = defaultdict(lambda: {"F": 0, "M": 0})
    max_ranks = {}

    for year, member in year_files:
        by_sex = {"F": [], "M": []}

        with z.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            for row in csv.reader(text):
                if len(row) < 3:
                    continue

                name = row[0].strip()
                sex = row[1].strip().upper()

                if sex not in ("F", "M"):
                    continue

                try:
                    count = int(row[2])
                except ValueError:
                    continue

                by_sex[sex].append((name, count))
                totals[year][sex] += count

        ranks_this_year = {}

        for sex in ("F", "M"):
            # SSA files are normally already count-descending, but rank explicitly
            # so the result is deterministic even if source order changes.
            ordered = sorted(by_sex[sex], key=lambda x: (-x[1], x[0].lower()))
            ranks_this_year[sex] = len(ordered)

            for rank, (name, count) in enumerate(ordered, start=1):
                rows_by_name[name][sex].append((year, count, rank))

        max_ranks[year] = ranks_this_year

        if year == latest_year:
            print(
                f"{year}: {len(by_sex['F']):,} female names, "
                f"{len(by_sex['M']):,} male names"
            )

    OUT.mkdir(parents=True, exist_ok=True)
    SHARDS.mkdir(parents=True, exist_ok=True)

    # Remove old generated shards so stale files cannot survive a rebuild.
    for old in SHARDS.glob("*.json"):
        old.unlink()

    shards = defaultdict(dict)
    record_count = 0
    combo_count = 0

    for name in sorted(rows_by_name, key=str.lower):
        packed = {}

        for sex in ("F", "M"):
            series = rows_by_name[name][sex]
            if not series:
                continue

            flat = []
            for year, count, rank in series:
                flat.extend([year, count, rank])
                record_count += 1

            packed[sex] = flat
            combo_count += 1

        shards[shard_for(name)][name] = packed

    for shard, mapping in shards.items():
        path = SHARDS / f"{shard}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(mapping, f, separators=(",", ":"), ensure_ascii=False)

    meta = {
        "latestYear": latest_year,
        "firstYear": first_year,
        "nameCount": len(rows_by_name),
        "nameSexCombinations": combo_count,
        "records": record_count,
        "source": "U.S. Social Security Administration — National Baby Names",
        "sourceUrl": "https://www.ssa.gov/oact/babynames/limits.html",
        "privacyThreshold": 5,
        "totals": {str(y): totals[y] for y in sorted(totals)},
        "maxRanks": {str(y): max_ranks[y] for y in sorted(max_ranks)},
        "generatedBy": "scripts/build_ssa_data.py",
    }

    with (OUT / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, separators=(",", ":"), ensure_ascii=False)

    total_bytes = (OUT / "meta.json").stat().st_size
    total_bytes += sum(p.stat().st_size for p in SHARDS.glob("*.json"))

    print(
        f"Built {len(rows_by_name):,} unique names, "
        f"{record_count:,} yearly records through {latest_year}."
    )
    print(
        f"Generated {len(shards)} shards; "
        f"total browser data size {total_bytes / 1024 / 1024:.1f} MiB."
    )
    print("SUCCESS: current SSA dataset built from data/source/names.zip")

if __name__ == "__main__":
    main()
