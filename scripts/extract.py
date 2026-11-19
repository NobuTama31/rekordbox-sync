"""Repository convenience entry point; install the package first."""
import sys
from rekordbox_sync.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main(['extract'] + sys.argv[1:]))
