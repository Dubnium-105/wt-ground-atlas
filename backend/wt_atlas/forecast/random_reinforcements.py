"""Source evidence and release limitation for supplemental random vehicles."""
import hashlib
from pathlib import Path

from lxml import html
from .data import number, safe_file
from ..common import file_hash, read_json
from ..native_binding.ordered_blk import parse_ordered_blk

VERSION = '2.59.0.92'
OFFICIAL_URL = 'https://warthunder.com/en/game/changelog/current/1806'
OFFICIAL_SHA256 = 'cd7734b75625cb8311ad8534b6ccfba4a84f7ebb7caab5a377bee022ee9ad97e'
GUI_SHA256 = '1162cd40e5d29a65b7e68e9e2f1c589a4239bf87cf9bde62fa30e14d396b0ccd'
ACES_SHA256 = 'ef7e4861050d4213b2759f3fb817cc78868f777943afc76608642efe25d7b285'
GAMEPLAY_SHA256 = 'd5e8a3eb44427a52210892fca518b85ab4b08b313ff4b608e72ac214bf845b9e'
GAMEPLAY_PATH = 'config/gameplay.blk'
GROUND_EVENT = 'tank_random_battles_historical_base'
SCRIPT_PATH = 'scripts/miscustomrules/rulebase.nut'
SCRIPT_LF_SHA256 = '49938eece6649cc6766b7da1b6b46e90563966022233f9f5b47e3ea865fb4f37'


def official_claims(raw):
    text = ' '.join(html.fromstring(raw).text_content().split()).lower()
    phrases = ('9.3 or higher', 'one random tank and one random spaa',
               'maximum br for the session', 'if there is no vehicle in the research tree')
    if any(phrase not in text for phrase in phrases):
        raise ValueError('Official supplemental vehicle description changed')
    return {'historical_threshold_br': 9.3, 'historical_ground_rb_groups': ['tank', 'spaa'],
            'historical_room_top_excluded': True, 'historical_lower_br_fallback_documented': True}


def client_claims(raw):
    text = raw.decode('utf8').replace('\r\n', '\n')
    anchors = ('get_user_custom_state(userIdInt64.get(), false)', 'this.getMyStateBlk()?.random_units',
               'function getRandomUnitsList(groupName)', 'function isUnitEnabledByRandomGroups(unitName)')
    if any(anchor not in text for anchor in anchors):
        raise ValueError('Client supplemental unit state interface changed')
    return {'selection_from_user_custom_state': True, 'client_exposes_group_members': True,
            'client_exposes_group_enable_state': True, 'selection_probabilities_verified': False}


def configured_ground_candidates(tree):
    """Read the client's configured candidates without asserting server activation."""
    def one(nodes, name):
        found = [node for node in nodes if node['name'] == name]
        if len(found) != 1: raise ValueError('Missing or duplicated random-unit configuration: ' + name)
        return found[0]
    event = one(one(tree['children'], 'RandomSpawnUnits')['children'], GROUND_EVENT)
    result = {}
    for country in event['children']:
        if country['name'] in result: raise ValueError('Duplicated random-unit country')
        groups = {}
        for group in country['children']:
            if group['name'] in groups: raise ValueError('Duplicated random-unit group')
            params = {}
            for param in group['params']:
                if param['name'] in params: raise ValueError('Duplicated random-unit parameter')
                params[param['name']] = {'type': param['type'], 'value': param['value']}
            units = []
            for unit in group['children']:
                if unit['name'] in units: raise ValueError('Duplicated random-unit candidate')
                if unit['params'] or unit['children']: raise ValueError('Unreviewed candidate configuration')
                units.append(unit['name'])
            groups[group['name']] = {'parameters': params, 'candidates': units}
        result[country['name']] = groups
    if not result: raise ValueError('Empty random-unit configuration')
    return result


def requires_unmodeled_gate(catalog, version, publication_scope):
    """Only the reviewed version is gated; never infer an old rule for other versions."""
    if version != VERSION or publication_scope != 'full_ground_rb': return False
    for vehicle in catalog.get('vehicles', []):
        if vehicle.get('kind') != 'ground' or vehicle.get('system_role') == 'component': continue
        block = vehicle.get('rating_configurations', {}).get('ground_rb')
        values = ([o.get('br', {}).get('current') for o in block.get('options', [])]
                  if isinstance(block, dict) else [vehicle.get('br', {}).get('current')])
        if any(number(value) and value >= 9.3 for value in values): return True
    return False


def audit(capture_path, official_path):
    capture = read_json(capture_path); pack = capture['packs']['gui.vromfs.bin']
    if pack['version'] != VERSION or pack['sha256'] != GUI_SHA256 or file_hash(pack['saved']) != GUI_SHA256:
        raise ValueError('Unreviewed GUI source pack or version')
    script = safe_file(Path(pack['extracted']), SCRIPT_PATH); raw_script = script.read_bytes()
    if hashlib.sha256(raw_script.replace(b'\r\n', b'\n')).hexdigest() != SCRIPT_LF_SHA256:
        raise ValueError('Unreviewed client random-unit script')
    aces = capture['packs']['aces.vromfs.bin']
    if aces['version'] != VERSION or aces['sha256'] != ACES_SHA256 or file_hash(aces['saved']) != ACES_SHA256:
        raise ValueError('Unreviewed gameplay source pack or version')
    gameplay = safe_file(Path(aces['extracted']), GAMEPLAY_PATH)
    if file_hash(gameplay) != GAMEPLAY_SHA256: raise ValueError('Unreviewed gameplay configuration')
    candidates = configured_ground_candidates(parse_ordered_blk(gameplay))
    official = Path(official_path).read_bytes()
    if hashlib.sha256(official).hexdigest() != OFFICIAL_SHA256:
        raise ValueError('Unreviewed official changelog capture')
    result = {'schema_version': '1.0.0', 'source_version': VERSION, 'official_url': OFFICIAL_URL,
        'official_sha256': OFFICIAL_SHA256, 'official_release_date': '2025-12-16',
        'historical_release_claims': official_claims(official), 'client_pack_sha256': GUI_SHA256,
        'client_capture_sha256': file_hash(capture_path), 'client_script_path': SCRIPT_PATH,
        'client_script_sha256': hashlib.sha256(raw_script).hexdigest(), 'client_script_lf_sha256': SCRIPT_LF_SHA256,
        'client_claims': client_claims(raw_script),
        'gameplay_pack_sha256': ACES_SHA256, 'gameplay_path': GAMEPLAY_PATH,
        'gameplay_sha256': GAMEPLAY_SHA256, 'configured_event': GROUND_EVENT,
        'configured_candidates': candidates,
        'configured_candidate_counts': {group: sum(len(groups.get(group, {}).get('candidates', []))
                                                    for groups in candidates.values())
                                        for group in ('tank', 'spaa')},
        'scope': 'The older official release documents high-BR supplemental units; this client reads random-unit availability from user session state. This does not prove the current server rule, pool, distribution, loadout or spawn cost.',
        'runtime_session_state_observed': False, 'selection_probabilities_verified': False,
        'supplemental_sortie_model_implemented': False, 'publishable': False, 'formal_rankings': 0}
    if (file_hash(pack['saved']) != GUI_SHA256 or file_hash(script) != result['client_script_sha256']
            or file_hash(aces['saved']) != ACES_SHA256 or file_hash(gameplay) != GAMEPLAY_SHA256):
        raise ValueError('Client source changed during audit')
    return result
