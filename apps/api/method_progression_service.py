"""The catalog and planner replay the same revisioned execution observations."""
from .response_service import ResponseStore
from .learning_methods import assess_methods
from biathlon import method_progression


def context(repository, alias, source, today):
    observations = assess_methods(entries=ResponseStore(repository).entries(alias), source=source, today=today)['memory']['observations']
    return {'version': method_progression.VERSION, 'observations': observations,
            'basis': 'CONFIRMED_COMPARABLE_EXECUTION'}
