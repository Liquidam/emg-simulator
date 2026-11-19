"""Start the EMG simulator window:  python run_emgsim.py   (or double-click on Windows)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import numpy  # noqa: F401
except ImportError:
    print('This program needs NumPy. Install it once with:\n\n    python -m pip install numpy\n')
    input('Press Return to close.')
    sys.exit(1)
from emgsim.__main__ import main

main()
