"""Pristine source filtering and three-word node binding, client 2.59.0.86.

Shared name IDs are opaque input identities. Part positions are dense after
the original resource/prefix filter; neither is a captured live-process ID.
"""
from copy import deepcopy
from .native_material import resolve_damageparts_tree


def filtered_damage_parts(tree, classes, collision_names, name_ids, prefix=''):
    leaves = resolve_damageparts_tree(tree, classes, name_ids)
    accepted = []
    names = None if collision_names is None else set(collision_names)
    for leaf in leaves:
        name = leaf['name']
        if not name.startswith(prefix):
            continue
        if names is not None and (not name[len(prefix):] or name[len(prefix):] not in names):
            continue
        item = deepcopy(leaf)
        item['part_index'] = len(accepted)
        for callback in item['callbacks']:
            callback['part_index'] = item['part_index']
        accepted.append(item)
    return accepted


def bind_pristine_nodes(parts, node_names, shared_name_ids, template_name_ids):
    """Ordinary no-voxel source initializer, including ex_/anim_ex_ fallback.

Each binding is (dense part position, global template ID, shared name ID).
The second word is NOT the collision node index. Unknown source nodes remain
unbound unless the original detachable/external fallback creates a part.
"""
    output = deepcopy(parts)
    states = [{'part_index': i, 'template_id': template_name_ids.get(part['name'], -1),
               'shared_name_id': shared_name_ids[part['name']], 'flags': 0x0a}
              for i, part in enumerate(parts)]
    bindings, generated, cls_nodes, detachable_nodes = [], [], [], []
    last_node_for_part = [-1]*len(output)
    for node, name in enumerate(node_names):
        index = next((i for i, part in enumerate(output) if part['name'] == name), -1)
        if index >= 0:
            last_node_for_part[index] = node
        if name.startswith('cls_'):
            cls_nodes.append(node)
        detachable = index >= 0 and bool(output[index]['flags'] & 0x40)
        if detachable or name.startswith('ex_') or name.startswith('anim_ex_'):
            if index < 0:
                index = len(output)
                output.append({'name': name, 'flags': 0, 'generated_default': True})
                # The inline fresh constructor does not load global defaults.
                states.append({'part_index': index, 'template_id': -1,
                               'shared_name_id': shared_name_ids[name], 'flags': 0x0a})
                last_node_for_part.append(node)
                generated.append(index)
            states[index]['flags'] |= 4
            detachable_nodes.append(node)
        bindings.append([-1, -1, -1] if index < 0 else
                        [index, states[index]['template_id'], states[index]['shared_name_id']])
    return {'bindings': bindings, 'part_names': [part['name'] for part in output],
            'class_flags': [part['flags'] for part in output], 'states': states,
            'last_node_for_part': last_node_for_part, 'generated_part_indices': generated,
            'cls_node_indices': cls_nodes, 'detachable_node_indices': detachable_nodes}
