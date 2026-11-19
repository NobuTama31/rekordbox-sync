"""Read an already-running monitor without opening another MIDI connection."""
import json
from urllib.request import urlopen

if __name__ == "__main__":
    with urlopen("http://127.0.0.1:8765/state", timeout=5) as response:
        print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
