import json
import os
import sys


def _config_path() -> str:
    """config.json lives next to the .exe (PyInstaller) or this script,
    regardless of the directory the agent is launched from."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'config.json')


CONFIG_FILE = _config_path()

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: {CONFIG_FILE} is missing.")
        print("Please ensure config.json is in the same folder as this agent.")
        input("Press Enter to exit...")
        sys.exit(1)
        
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"ERROR: Could not read {CONFIG_FILE}. Invalid JSON format.")
        print(f"Details: {e}")
        input("Press Enter to exit...")
        sys.exit(1)
        
    # Accept either 'company_name' (current config.json) or the older
    # 'business_name', normalising to 'company_name' for the agent.
    if not str(config.get('company_name', '')).strip():
        config['company_name'] = str(config.get('business_name', '')).strip()

    required_keys = [
        'business_id', 'agent_token', 'tally_host',
        'tally_port', 'backend_url', 'company_name'
    ]

    for key in required_keys:
        if key not in config or not str(config[key]).strip():
            print(f"ERROR: '{key}' is missing or empty in {CONFIG_FILE}.")
            print("Please fill in all required fields.")
            input("Press Enter to exit...")
            sys.exit(1)

    if 'your-railway-url' in str(config.get('backend_url', '')):
        print("ERROR: 'backend_url' in config.json is still the placeholder.")
        print("Set it to your real Railway URL, e.g. https://myapp.up.railway.app")
        input("Press Enter to exit...")
        sys.exit(1)

    return config
