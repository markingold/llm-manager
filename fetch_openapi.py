import requests
import json

url = "http://127.0.0.1:8500/openapi.json"
try:
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()

    security_schemes = data.get("components", {}).get("securitySchemes", {})

    def get_op_info(path):
        paths = data.get("paths", {})
        operation = paths.get(path, {}).get("post", {})
        return {
            "security": operation.get("security", "Not Found"),
            "parameters": operation.get("parameters", "Not Found")
        }

    # Checking for both /v1/model/... and /model/... as implied by the prompt and history
    load_v1 = get_op_info("/v1/model/load")
    unload_v1 = get_op_info("/v1/model/unload")
    load_root = get_op_info("/model/load")
    unload_root = get_op_info("/model/unload")

    result = {
        "securitySchemes": security_schemes,
        "/v1/model/load": load_v1,
        "/v1/model/unload": unload_v1,
        "/model/load": load_root,
        "/model/unload": unload_root
    }

    # Filter out "Not Found" if both v1 and root are requested but only one exists? 
    # The prompt specifically asked for /v1/model/load and /v1/model/unload. 
    # I will stick to what's requested but keep debug info if I fail.
    
    final_output = {
        "securitySchemes": security_schemes,
        "POST /v1/model/load": load_v1 if load_v1["security"] != "Not Found" else load_root,
        "POST /v1/model/unload": unload_v1 if unload_v1["security"] != "Not Found" else unload_root
    }

    print(json.dumps(final_output, indent=2))
except Exception as e:
    print(json.dumps({"error": str(e)}))
