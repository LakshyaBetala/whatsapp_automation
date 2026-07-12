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
        print("Set it to your backend server, e.g. http://192.168.1.50:8000")
        print("(the IP of the laptop running the WhatsApp Tally backend)")
        input("Press Enter to exit...")
        sys.exit(1)

    return config


def save_config(config: dict) -> None:
    """Write config.json back (used by --add-company). Internal keys the
    agent adds at runtime (tally host/port overrides) are preserved as-is."""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def company_entries(config: dict) -> list:
    """Every Tally company this agent serves, each with its own backend
    identity. The primary company is the top-level config; extra companies
    live in config['companies'] = [{company_name, business_id, agent_token}].
    Returns a list of full per-company config dicts (shared connection
    settings + that company's identity)."""
    entries = [{
        "company_name": config["company_name"],
        "business_id": config["business_id"],
        "agent_token": config["agent_token"],
    }]
    for extra in (config.get("companies") or []):
        if not isinstance(extra, dict):
            continue
        name = str(extra.get("company_name") or "").strip()
        if not name or name == config["company_name"]:
            continue
        if extra.get("business_id") and extra.get("agent_token"):
            entries.append({
                "company_name": name,
                "business_id": extra["business_id"],
                "agent_token": extra["agent_token"],
            })
    out = []
    for e in entries:
        merged = dict(config)
        merged.update(e)
        merged.pop("companies", None)
        out.append(merged)
    return out
