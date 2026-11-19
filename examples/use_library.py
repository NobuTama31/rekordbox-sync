"""Run after installing the package. Ctrl+C stops and releases the sources."""
import time
from rekordbox_sync import MonitorConfig, RekordboxMonitor


def main():
    try:
        with RekordboxMonitor(MonitorConfig()) as monitor:
            while True:
                for deck in monitor.snapshot().decks:
                    print(deck.deck_id, deck.title, deck.bpm_current, deck.position_seconds)
                time.sleep(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
