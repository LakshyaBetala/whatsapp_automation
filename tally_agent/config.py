import json
import os
import sys

CONFIG_FILE = 'config.json'

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
        
    required_keys = [
        'business_id', 'agent_token', 'tally_host', 
        'tally_port', 'backend_url', 'business_name'
    ]
    
    for key in required_keys:
        if key not in config or not str(config[key]).strip():
            print(f"ERROR: '{key}' is missing or empty in {CONFIG_FILE}.")
            print("Please fill in all required fields.")
            input("Press Enter to exit...")
            sys.exit(1)
            
    return config
