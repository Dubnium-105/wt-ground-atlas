"""Declared mission mixtures within a sortie, independent of vehicle classes."""
import math

from .capabilities import TASKS
from .data import number


def phase_tasks(spawn):
    """Legacy phases contain one task; mixed phases contain a normalized prior.

    A task mixture is expected conditional utility, not simultaneous independent
    engagements or a probability of earning spawn points.
    """
    phases = spawn['phases']
    mixtures = spawn.get('phase_task_weights')
    if mixtures is None:
        if any(task not in TASKS for task in phases):
            raise ValueError('Unknown sortie task')
        return tuple({task: 1.} for task in phases)
    if not isinstance(mixtures, list) or len(mixtures) != len(phases):
        raise ValueError('Task mixture must match sortie phases')
    if spawn.get('task_weight_status') != 'assumed_not_observed':
        raise ValueError('Task mixture requires an explicit assumption status')
    for mix in mixtures:
        if (not isinstance(mix, dict) or not mix or any(task not in TASKS for task in mix)
                or any(not number(w) or w < 0 for w in mix.values())
                or not math.isclose(sum(mix.values()), 1.)):
            raise ValueError('Invalid normalized sortie task mixture')
    return tuple({task: weight for task, weight in mix.items() if weight > 0} for mix in mixtures)


def modeled_tasks(spawn):
    return {task for mix, weight in zip(phase_tasks(spawn), spawn['phase_weights'])
            if weight > 0 for task in mix}


def task_supported(variant, task):
    """Only domain impossibility is known here; roles never imply task skill."""
    if task not in TASKS:
        raise ValueError('Unknown mission task')
    if task in ('ground', 'ground_control'):
        return variant['domain'] == 'ground'
    if task in ('cap', 'cas'):
        return variant['domain'] == 'air'
    return True
