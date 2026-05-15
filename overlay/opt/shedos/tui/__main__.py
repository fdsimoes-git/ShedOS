"""Entry point: `python3 -m tui` (cwd /opt/shedos) or direct `python3 __main__.py`."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tui.app import run

if __name__ == "__main__":
    run()
