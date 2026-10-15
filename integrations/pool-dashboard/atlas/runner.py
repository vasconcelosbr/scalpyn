"""Operator CLI, no public run button. Does not bypass daily invocation locks."""
import argparse
from .pipeline import cycle
from .provider import Managed,ProviderError,budget_cents
from . import store

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--preflight',action='store_true')
    p.add_argument('--resume-blocked',action='store_true')
    args=p.parse_args();store.init()
    if args.preflight:
        try: budget_cents();Managed().preflight();status='OK'
        except ProviderError as exc: status=str(exc)
        store.state('preflight',{'status':status,'checked_at':store.now()})
        print(store.encode({'preflight':status}))
        return
    run_id=cycle(resume_blocked=args.resume_blocked)
    with store.db() as c: row=c.execute('SELECT id,status,error_code FROM cycles WHERE id=?',(run_id,)).fetchone()
    print(store.encode(dict(row)))

if __name__=='__main__':main()
