"""Explicit default cohorts and task-profile scope for static publications."""
import copy

from .data import validate_config
from .tasks import phase_tasks

SKILLS = ('novice', 'normal', 'expert')
PREFERENCES = ('ground', 'balanced', 'air_support')
TASK_FIELDS = ('phases', 'phase_weights', 'phase_task_weights', 'task_weight_status')


def default_cohort(config, skill=None, preference=None):
    declared = config.get('default_cohort', {})
    if not isinstance(declared, dict) or set(declared) - {'skill', 'preference'}:
        raise ValueError('Invalid default cohort')
    chosen = (skill or declared.get('skill', 'normal'), preference or declared.get('preference', 'ground'))
    if chosen[0] not in SKILLS or chosen[1] not in PREFERENCES:
        raise ValueError('Unknown default skill or task preference')
    return chosen


def resolve(config, skill=None, preference=None):
    """Preserve legacy hand-tuned base settings unless a cohort was requested."""
    selected = default_cohort(config, skill, preference)
    result = copy.deepcopy(config)
    if skill is not None or 'default_cohort' in config:
        result['population'] = copy.deepcopy(config['skill_profiles'][selected[0]])
    if preference is not None or 'default_cohort' in config:
        profile = config['task_profiles'][selected[1]]
        if set(profile) - {*TASK_FIELDS, 'publication_scope'}:
            raise ValueError('Unknown task profile field')
        for field in TASK_FIELDS:
            result['spawn'].pop(field, None)
        result['spawn'].update({k: copy.deepcopy(profile[k]) for k in TASK_FIELDS if k in profile})
        if 'publication_scope' in profile:
            if profile['publication_scope'] not in ('full_ground_rb', 'native_distance_ground_engagement', 'ground_engagement'):
                raise ValueError('Unknown task profile publication scope')
            result['publication']['scope'] = profile['publication_scope']
    validate_config(result)
    phase_tasks(result['spawn'])
    return result, selected


def cohorts(config, skill=None, preference=None):
    first = default_cohort(config, skill, preference)
    return [first] + [(s, p) for s in SKILLS for p in PREFERENCES if (s, p) != first]
