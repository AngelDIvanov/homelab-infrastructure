"""Make scripts/ importable so tests can `import webhook` when run from repo root."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))
