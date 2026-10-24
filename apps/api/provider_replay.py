"""Refresh-scoped disk replay: HRmax re-analysis never downloads streams twice."""
from hashlib import sha256
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory


class ProviderReplay:
    def __init__(self, client):
        self.client = client
        self.directory = TemporaryDirectory(prefix="onflows-refresh-replay-")

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in {"get_activities_result", "get_activity_result", "get_streams_result"}:
            return method
        def read(*args, **kwargs):
            key = sha256(pickle.dumps((name, args, sorted(kwargs.items())))).hexdigest()
            path = Path(self.directory.name)/key
            if path.exists():
                # These bytes were written by this same call from its own
                # response objects, never from an uploaded pickle or a URL.
                with path.open("rb") as handle:
                    return pickle.load(handle)
            value = method(*args, **kwargs)
            with path.open("wb") as handle:
                pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
            return value
        return read

    def close(self):
        self.directory.cleanup()
