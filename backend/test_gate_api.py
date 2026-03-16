import asyncio
import os
import sys

# Add the app directory to the path so we can import from it
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:jM%23%3CzRc%24X%5B3%7DLtVb@34.45.112.237:5432/scalpyn"

from app.database import AsyncSessionLocal as async_session
from sqlalchemy import text
from app.utils.encryption import decrypt
import gate_api
from gate_api.exceptions import ApiException, GateApiException

async def fetch_gate_balances():
    try:
        async with async_session() as session:
            # Pega a ultima conexao Gate.io
            result = await session.execute(text("SELECT exchange_name, api_key_encrypted, api_secret_encrypted FROM exchange_connections WHERE exchange_name = 'Gate.io' LIMIT 1"))
            connection = result.first()
            
            if not connection:
                print("ERRO: Nenhuma conexao encontrada para a Gate.io no banco de dados.")
                return
            
            exchange_name, api_key_blob, api_secret_blob = connection
            api_key = decrypt(api_key_blob)
            api_secret = decrypt(api_secret_blob)
            
            print(f"[OK] Credenciais descriptografadas do Banco de Dados: {exchange_name}")
            print(f"[OK] API Key: {api_key[:5]}...{api_key[-5:]}")
            
            # Setup Gate.io API configuration
            configuration = gate_api.Configuration(
                key=api_key,
                secret=api_secret
            )
            
            # Spot API Client
            api_client = gate_api.ApiClient(configuration)
            spot_api = gate_api.SpotApi(api_client)
            
            print("\n[INFO] Consultando contas SPOT na corretora Gate.io...")
            
            accounts = spot_api.list_spot_accounts()
            
            has_balance = False
            for account in accounts:
                if float(account.available) > 0 or float(account.locked) > 0:
                    print(f"[VALOR] Moeda: {account.currency} | Disponivel: {account.available} | Em Ordem: {account.locked}")
                    has_balance = True
                    
            if not has_balance:
                print("[AVISO] Conexao testada com sucesso, mas a conta Spot esta sem saldo.")
                
    except GateApiException as ex:
        print(f"[ERRO] Erro da API Gate.io: {ex.label} - {ex.message}")
    except ApiException as e:
        print(f"[ERRO] Falha de Autenticacao/Conexao: {e}")
    except Exception as e:
        import traceback
        print(f"[ERRO] Erro inesperado:\n{traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(fetch_gate_balances())
