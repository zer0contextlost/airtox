#!/bin/sh
# Block-level noncancer hazard for 2021 and 2020, one region at a time; skips regions already written.
cd "$(dirname "$0")/.." || exit 1
for y in 2021 2020; do
  out=/srv/airtox/derived/hazard_$y
  for r in 1 2 3 4 5 6 7 8 9 10; do
    [ -f "$out/${y}_R${r}_hi_summary.json" ] && continue
    venv/bin/python -I scripts/hi_csv.py "$y" "$r" "$out" || { echo "HI $y REGION $r FAILED"; exit 1; }
  done
done
echo "ALL HI DONE"
