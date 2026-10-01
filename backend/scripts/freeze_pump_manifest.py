"""Validate and freeze explicit Pump research choices; never starts training."""
import argparse,json
from app.services.pump_contracts import validate_manifest
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--input",required=True);p.add_argument("--output",required=True)
    a=p.parse_args()
    with open(a.input,encoding="utf-8") as source:result=validate_manifest(json.load(source))
    with open(a.output,"x",encoding="utf-8") as target:json.dump(result,target,sort_keys=True)
    print(json.dumps({"status":"frozen","manifest_hash":result["manifest_hash"],"output":a.output,"training_started":False}))
