import asyncio
import os
import sys

# Add the app directory to the path so we can import from it
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import engine
from sqlalchemy import text

async def check_gate_connection():
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT id, exchange_id, name, is_active FROM exchange_connections WHERE exchange_id = 'gate'"))
            connections = result.fetchall()
            
            if not connections:
                print("ERRO: Nenhuma conexão encontrada para a Gate.io no banco de dados.")
                return
            
            print(f"Sucesso! Encontrada(s) {len(connections)} conexão(ões) da Gate.io:")
            for conn_row in connections:
                print(f" - ID: {conn_row[0]}")
                print(f" - Nome: {conn_row[2]}")
                print(f" - Ativa: {'Sim' if conn_row[3] else 'Não'}")
                print("-" * 30)
                
    except Exception as e:
        print(f"Erro ao conectar no banco de dados: {e}")

if __name__ == "__main__":
    asyncio.run(check_gate_connection())
