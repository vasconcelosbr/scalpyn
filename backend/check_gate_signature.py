import gate_api
import sys

def test_gate(api_key, api_secret):
    configuration = gate_api.Configuration(
        key=api_key,
        secret=api_secret,
        host="https://api.gateio.ws/api/v4"
    )
    
    # We must instantiate the API client
    api_client = gate_api.ApiClient(configuration)
    spot_api = gate_api.SpotApi(api_client)

    try:
        print("Buscando saldos...")
        # The library should automatically attach Date, API Key, and Signature Headers
        accounts = spot_api.list_spot_accounts()
        print("Sucesso! Contas:")
        for acc in accounts:
            if float(acc.available) > 0:
                print(f"{acc.currency}: {acc.available}")
    except gate_api.exceptions.GateApiException as e:
        print("GateApiException:", e.message)
    except Exception as e:
        print("Erro:", str(e))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python check_gate_signature.py <KEY> <SECRET>")
    else:
        test_gate(sys.argv[1], sys.argv[2])
