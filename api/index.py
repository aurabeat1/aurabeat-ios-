import sys
import os

# Add addon-server to path so python can import main.py
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'addon-server'))

from main import app
