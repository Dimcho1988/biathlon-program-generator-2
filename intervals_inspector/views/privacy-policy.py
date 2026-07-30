"""Public bilingual privacy policy for the onFlows pilot."""

from __future__ import annotations

from pathlib import Path
import sys


if __package__ in (None, ""):
    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)

from intervals_inspector.public_pages import render_privacy_policy


render_privacy_policy()
