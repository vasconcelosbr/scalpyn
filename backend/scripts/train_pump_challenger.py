"""Explicit isolated offline Pump trainer; JSONL v2 export and frozen manifest required."""
import argparse
import json
from app.services.pump_ml_research import train_challenger


def load_rows(path,spec):
    rows=[]
    with open(path,encoding="utf-8") as source:
        for line in source:
            item=json.loads(line);p=item["observation"]
            label=next((l for l in item["labels"] if l["horizon_minutes"]==5 and l["label_spec_hash"]==p["manifest"]["label_spec_hash"]),None)
            rows.append({**p,"target":label.get("targets",{}).get("0.8",{}).get("hit") if label else None,
                         "label_coverage_complete":label.get("coverage_complete") is True if label else False})
            if len(rows)>spec["max_rows"]:raise ValueError("Pump dataset exceeds declared row budget")
    return rows


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("dataset","manifest","output-root"):p.add_argument(f"--{key}",required=True)
    args=p.parse_args();spec=json.load(open(args.manifest,encoding="utf-8"))
    result=train_challenger(load_rows(args.dataset,spec),spec=spec,output_root=args.output_root)
    print(json.dumps(result,sort_keys=True))
