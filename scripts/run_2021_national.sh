#!/bin/sh
# 2021 national cancer risk, one region at a time (skips regions already written). Run under systemd-run with a MemoryMax.
cd "$(dirname "$0")/.." || exit 1
out=/srv/airtox/derived/recomputed_2021
for r in 1 2 3 4 5 6 7 8 9 10; do
  [ -f "$out/2021_R${r}_summary.json" ] && [ "$out/2021_R${r}_summary.json" -nt scripts/risk_csv.py ] && continue
  venv/bin/python -I scripts/risk_csv.py 2021 "$r" "$out" || { echo "REGION $r FAILED"; exit 1; }
done
echo "ALL REGIONS DONE"
