#!/usr/bin/env bash
# Snapshot config.json (and weight-index total size) for every model in models/repos.tsv.
cd "$(cd "$(dirname "$0")/.." && pwd)/models"; mkdir -p configs
tail -n +2 repos.tsv | while IFS=$'\t' read -r slug repo; do
  code=$(curl -s -L --max-time 30 -o configs/$slug.json -w '%{http_code}' "https://huggingface.co/$repo/resolve/main/config.json")
  curl -s -L --max-time 30 -o configs/$slug.index.json -w '' "https://huggingface.co/$repo/resolve/main/model.safetensors.index.json"
  python3 - "$slug" "$code" <<'PY'
import json,sys,os
s,c=sys.argv[1:]; p=f"configs/{s}.index.json"
try:
    t=json.load(open(p))["metadata"]["total_size"]; json.dump({"total_size_bytes":t},open(p,"w"))
except Exception: os.remove(p)
print(s,c,"index" if os.path.exists(p) else "no-index")
PY
done

# Second snapshots (separate draft models, params.json): models/extras.tsv -> configs/<slug>.<kind>.json
tail -n +2 extras.tsv | while IFS=$'\t' read -r slug kind rpath; do
  repo=$(awk -F'\t' -v s="$slug" '$1==s {print $2}' repos.tsv)
  code=$(curl -s -L --max-time 30 -o "configs/$slug.$kind.json" -w '%{http_code}' "https://huggingface.co/$repo/resolve/main/$rpath")
  echo "$slug.$kind $code"
done
