"""One conversation turn's read-only settings, using the shared request read cache.

Only settings are reused. Prepared views, records, authorization and command
inputs always reach their normal live readers. The phase is never persisted or
passed to a command, so writes and subsequent turns cannot reuse its values.
"""
from .read_session import ReadSession


class AssistantReadSession:
    def __init__(self, repository):
        self._repository = repository
        self._settings = ReadSession(repository)

    def athlete_settings(self, alias):
        reader = getattr(self._repository, "athlete_settings", None)
        return self._settings.athlete_settings(alias) if callable(reader) else None

    def for_query(self, query):
        # Editable inputs carry fresh revisions and can lead to a write. Their
        # complete canonical read, including timezone, stays outside this phase.
        return self._repository if query.get("kind") == "inputs" else self

    def __getattr__(self, name):
        return getattr(self._repository, name)
