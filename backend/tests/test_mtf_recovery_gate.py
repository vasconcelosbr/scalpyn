"""Exercise the actual recovery middleware without starting application jobs."""
import ast
from pathlib import Path
import re
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def test_recovery_allows_only_observational_mtf_settings():
    # Isolate exactly the declarations from main; importing the full app can
    # initialize unrelated clients and schedulers on developer machines.
    source = ast.parse((Path(__file__).parents[1]/'app/main.py').read_text(encoding='utf-8'))
    names = {'_RECOVERY_RESOURCE_ID','_RECOVERY_EDIT_ROUTES','_RecoveryObservationGate'}
    nodes = [n for n in source.body if isinstance(n,ast.ClassDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
    scope = {'_re':re,'_BaseHTTPMiddleware':BaseHTTPMiddleware,'_JSONResponse':JSONResponse}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<recovery-gate>','exec'),scope)
    app = FastAPI()
    app.add_middleware(scope['_RecoveryObservationGate'])
    @app.api_route('/api/{path:path}',methods=['PUT','POST','DELETE'])
    def endpoint(path:str): return {'reached':True}
    with TestClient(app) as client:
        for path in ('config','preferences'):
            assert client.put('/api/mtf/'+path).status_code == 200
            assert client.post('/api/mtf/'+path).status_code == 403
            assert client.delete('/api/mtf/'+path).status_code == 403
        for path in ('/api/orders/','/api/mtf/execute','/api/config/risk','/api/mtf/config/extra'):
            assert client.put(path).status_code == 403
            assert client.post(path).status_code == 403
