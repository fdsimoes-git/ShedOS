"""Entry point: `python3 -m shedos.tui` or directly `python3 tui/__main__.py`."""
import os
import sys

# Add the parent dir so we can `import config`, `import tools`, etc.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tui.app import run

if __name__ == "__main__":
    run()
