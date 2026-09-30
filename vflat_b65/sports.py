"""Select a correction from provider sport identifiers, never activity names."""
from . import core, running

RUNNING_SPORTS = frozenset({
    "run", "running", "trailrun", "trailrunning", "virtualrun", "trackrun",
    "trackrunning", "roadrun", "roadrunning", "crosscountryrun",
    "crosscountryrunning", "treadmillrun", "treadmillrunning", "jogging",
})


def is_running(sport):
    return isinstance(sport, str) and "".join(c for c in sport.casefold() if c.isalnum()) in RUNNING_SPORTS


def speed_model_versions(sport):
    model = running if is_running(sport) else core
    return model.MODEL_VERSION, model.CONFIG_VERSION
