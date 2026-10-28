#!/usr/bin/env python3
"""Build the local Chinese WOFF2 subset from pinned official Google Fonts bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def json_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from json_strings(item)


def display_characters(root, config):
    """Include UI copy and displayed names, not full source attribute dumps."""
    chunks, inputs = [], {}
    paths = {p for pattern in config['text_sources']['globs'] for p in root.glob(pattern) if p.is_file()}
    for path in sorted(paths):
        raw = path.read_bytes()
        chunks.append(raw.decode('utf8'))
        inputs[str(path.relative_to(root))] = sha256(raw)
    source = config['text_sources']
    catalog_path = root / source['catalog']
    raw = catalog_path.read_bytes()
    inputs[source['catalog']] = sha256(raw)
    catalog = json.loads(raw)
    chunks.extend(json_strings(catalog.get('nations', [])))
    for vehicle in catalog['vehicles']:
        for field in source['vehicle_fields']:
            chunks.extend(json_strings(vehicle.get(field)))
        for collection, fields in source['nested_display_fields'].items():
            for item in vehicle.get(collection, []):
                for field in fields:
                    chunks.extend(json_strings(item.get(field)))
        chunks.extend(json_strings(vehicle.get('features', [])))
        chunks.extend(json_strings(vehicle.get('multi_vehicle_system', {})))
    scenario_path = root / source['scenario']
    if scenario_path.exists():
        raw = scenario_path.read_bytes()
        chunks.extend(json_strings(json.loads(raw)))
        inputs[source['scenario']] = sha256(raw)
    characters = {ord(char) for char in ''.join(chunks) if char.isprintable()}
    # Basic ASCII is needed for user-entered search text and formatted numbers.
    characters.update(range(ord(' '), ord('~') + 1))
    return characters, inputs


def verified_source(spec, cache):
    path = cache / spec['filename']
    if not path.exists():
        request = urllib.request.Request(spec['url'], headers={'User-Agent': 'WT-Ground-Atlas-font-builder'})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    raw = path.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if sha256(raw) != spec['sha256'] or blob != spec['git_blob_sha1']:
        raise ValueError(f'Pinned upstream hash mismatch: {path.name}')
    return path, raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--config', default='config/font.json')
    parser.add_argument('--cache-dir', type=Path, default=Path(tempfile.gettempdir()) / 'wt-atlas-font')
    parser.add_argument('--check', action='store_true', help='Check the existing subset coverage and hash without downloading or writing')
    args = parser.parse_args()
    root = args.root.resolve()
    config = json.loads((root / args.config).read_text())
    from fontTools import __version__ as fonttools_version, subset
    from fontTools.ttLib import TTFont, woff2
    if not woff2.haveBrotli:
        raise RuntimeError('WOFF2 requires Brotli; install the versions listed in config/font.json')
    import brotli
    requested, inputs = display_characters(root, config)
    output = root / config['output']
    provenance_path = root / config['provenance']
    if args.check:
        provenance = json.loads(provenance_path.read_text())
        font = TTFont(output, recalcTimestamp=False)
        available = set(font.getBestCmap())
        unsupported = set(provenance['source_unsupported_codepoints'])
        missing = requested - available - unsupported
        if missing or sha256(output.read_bytes()) != provenance['output']['sha256']:
            raise ValueError(f'Font needs rebuilding; newly missing codepoints: {sorted(missing)}')
        print(json.dumps({'status': 'ok', 'bytes': output.stat().st_size,
            'requested_characters': len(requested), 'covered_characters': len(requested & available)}, ensure_ascii=False))
        return
    font_path, _ = verified_source(config['upstream']['font'], args.cache_dir)
    _, license_raw = verified_source(config['upstream']['license'], args.cache_dir)
    font = TTFont(font_path, recalcTimestamp=False)
    available = set(font.getBestCmap())
    unsupported = sorted(requested - available)
    cjk_missing = [codepoint for codepoint in unsupported if '\u3400' <= chr(codepoint) <= '\u9fff']
    if cjk_missing:
        raise ValueError(f'Official font lacks required CJK characters: {cjk_missing}')
    options = subset.Options()
    options.flavor = 'woff2'
    options.recalc_timestamp = False
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14, 16, 17, 25]
    options.name_legacy = True
    options.name_languages = ['*']
    options.layout_features = ['*']
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=sorted(requested & available))
    subsetter.subset(font)
    # This modified subset has its own family name; preserve upstream license names.
    renamed = {1: config['family'], 3: config['postscript_family'] + ';subset',
        4: config['family'], 6: config['postscript_family'] + '-Regular',
        16: config['family'], 25: config['postscript_family']}
    for record in font['name'].names:
        if record.nameID in renamed:
            record.string = renamed[record.nameID].encode(record.getEncoding())
    font.flavor = 'woff2'
    output.parent.mkdir(parents=True, exist_ok=True)
    font.save(output, reorderTables=True)
    reopened = TTFont(output, recalcTimestamp=False)
    covered = set(reopened.getBestCmap())
    if requested & available - covered:
        raise ValueError('Generated font lost required source-supported characters')
    for destination in config['license_outputs']:
        target = root / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(license_raw)
    raw = output.read_bytes()
    provenance = {'family': config['family'], 'upstream': config['upstream'],
        'license': 'SIL Open Font License 1.1', 'modified': 'Display-text subset and WOFF2 conversion; renamed family',
        'build_versions': {'fonttools': fonttools_version, 'Brotli': brotli.__version__},
        'input_sha256': inputs, 'requested_characters': len(requested),
        'covered_characters': len(requested & covered),
        'source_unsupported_codepoints': unsupported,
        'source_unsupported_characters': ''.join(chr(codepoint) for codepoint in unsupported),
        'all_requested_cjk_present': True,
        'variation_axes': [{'tag': a.axisTag, 'minimum': a.minValue, 'default': a.defaultValue,
            'maximum': a.maxValue} for a in reopened['fvar'].axes],
        'output': {'path': config['output'], 'bytes': len(raw), 'sha256': sha256(raw)}}
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: provenance[key] for key in ['requested_characters', 'covered_characters',
        'source_unsupported_characters', 'all_requested_cjk_present', 'variation_axes', 'output']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
