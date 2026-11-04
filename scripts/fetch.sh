#!/bin/bash
# usage: fetch.sh <url> <outfile>  -- resumable, retrying download
url="$1"; out="$2"
for i in $(seq 1 30); do
  curl -sSL --retry 5 --retry-delay 10 -C - -o "$out.part" "$url" && { mv "$out.part" "$out"; echo "done $out"; exit 0; }
  echo "retry $i $out"; sleep 15
done
exit 1
