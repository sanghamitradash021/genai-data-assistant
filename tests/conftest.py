import os
import sys
from pathlib import Path

os.environ["AUTO_INGEST"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
