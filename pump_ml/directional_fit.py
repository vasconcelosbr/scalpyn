"""One bounded subprocess fit; parent enforces shared wall-clock budget."""
import argparse,json
from pathlib import Path
from app.services.pump_directional_research import train_directional

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    data=json.loads(Path(args.input).read_text(encoding='utf-8'))
    try:result=train_directional(data['rows'],spec=data['spec'],output_root=args.output)
    except ValueError as exc:result={'status':'blocked','reason':str(exc),'support_diagnostics':getattr(exc,'details',None)}
    print(json.dumps(result,allow_nan=False))

if __name__=='__main__':main()
