import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "video-analysis"
sys.path[:0] = [
    str(PLUGIN / "shared-video"),
    str(PLUGIN / "skills" / "analyze-video" / "scripts"),
    str(Path(__file__).resolve().parent),
]
