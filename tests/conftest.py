import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["VYRO_DATA_DIR"] = tempfile.mkdtemp(prefix="vyro-web-tests-")

# The offline development container does not include yt-dlp. This stub only
# permits API plumbing tests; it does not simulate extractor behavior.
mock = types.ModuleType("yt_dlp")
mock.version = types.SimpleNamespace(__version__="TEST-STUB")

class YoutubeDL:
    def __init__(self, opts=None):
        self.opts = opts or {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def extract_info(self, *args, **kwargs):
        return {"id": "fixture", "title": "Fixture", "formats": []}
    def download(self, *args, **kwargs):
        return 0

mock.YoutubeDL = YoutubeDL
mock.__path__ = []
networking_pkg = types.ModuleType("yt_dlp.networking")
networking_pkg.__path__ = []
impersonate_module = types.ModuleType("yt_dlp.networking.impersonate")
class ImpersonateTarget:
    @classmethod
    def from_str(cls, value):
        return ("impersonate-target", value)
impersonate_module.ImpersonateTarget = ImpersonateTarget
sys.modules["yt_dlp.networking"] = networking_pkg
sys.modules["yt_dlp.networking.impersonate"] = impersonate_module
postprocessor_pkg = types.ModuleType("yt_dlp.postprocessor")
postprocessor_pkg.__path__ = []
metadata_module = types.ModuleType("yt_dlp.postprocessor.metadataparser")
class MetadataParserPP:
    class Actions:
        INTERPRET = object()
        REPLACE = object()
metadata_module.MetadataParserPP = MetadataParserPP
utils_module = types.ModuleType("yt_dlp.utils")
def download_range_func(chapters, ranges):
    return lambda info, ydl: [
        {"start_time": start, "end_time": end} for start, end in ranges
    ]
utils_module.download_range_func = download_range_func
sys.modules["yt_dlp"] = mock
sys.modules["yt_dlp.postprocessor"] = postprocessor_pkg
sys.modules["yt_dlp.postprocessor.metadataparser"] = metadata_module
sys.modules["yt_dlp.utils"] = utils_module
