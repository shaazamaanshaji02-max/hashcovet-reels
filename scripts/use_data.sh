#!/usr/bin/env bash
# Puts the PRIVATE data (scripts, history, learnings, Arjun's voice sample, settings) in place.
# Data lives in the private repo hashcovet-reels-data (secret DATA_TOKEN); this public repo has only code.
set -e
if [ -z "$DATA_TOKEN" ]; then echo "::error::DATA_TOKEN secret missing - stopping (private data must never be written to this public repo)"; exit 1; fi
OWNER="${GITHUB_REPOSITORY%%/*}"
git clone -q --depth 50 "https://x-access-token:${DATA_TOKEN}@github.com/${OWNER}/hashcovet-reels-data.git" _data
git -C _data config user.name "hashcovet-bot"; git -C _data config user.email "bot@users.noreply.github.com"
for p in jobs memory data assets/voice config.json test_lines; do
  [ -e "_data/$p" ] || continue
  rm -rf "$p"; mkdir -p "$(dirname "$p")"; ln -s "$PWD/_data/$p" "$p"
done
[ -f config.json ] || { echo "::error::private data not found"; exit 1; }
echo "private data linked"
