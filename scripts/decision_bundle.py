"""Project an offline probability bundle into the small browser decision input.

The decision engine considers only records whose ``reference_only`` value is
exactly false. Research records remain in the unchanged source snapshot and in
the separately published probability atlas; transmitting them to a Worker
cannot improve a decision. Verified records retain every field and their order.

The runtime input keeps model/build identity, calibration flags and the known
summary provenance fields. Research entries become one target-presence entry
per target: this preserves the engine's unmatched-reference explanation without
shipping ammunition/view catalogs or shard lists a second time. Unknown extra
top-level research payloads are deliberately not copied into the website.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


IDENTITY_FIELDS = (
    'schema_version', 'model_version', 'decision_model_version', 'build_key',
    'status', 'calibrated_game_probability', 'reference_only_default',
    'external_verified_records_contract',
)
PROVENANCE_FIELDS = (
    'index_sha256', 'config_sha256', 'engine_sha256', 'decoder_sha256',
    'summarizer_sha256', 'source_version', 'datamine_commit', 'aim_context_key',
    'integration_source', 'verification_policy', 'game_probability_validated',
    'partition',
)


def project_runtime_bundle(bundle: dict, source_bundle_sha256: str) -> dict:
    """Return a new runtime projection without modifying its offline source."""
    if (not isinstance(source_bundle_sha256, str) or len(source_bundle_sha256) != 64
            or any(c not in '0123456789abcdef' for c in source_bundle_sha256)):
        raise ValueError('A source bundle SHA-256 is required for the runtime projection.')
    if not isinstance(bundle, dict) or not isinstance(bundle.get('records'), list):
        raise ValueError('Decision probability bundle requires a records array.')
    records = bundle['records']
    if any(not isinstance(record, dict) for record in records):
        raise ValueError('Decision probability records must be objects.')
    entries = bundle.get('entries', [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError('Decision probability entries must be an array of objects.')
    provenance = bundle.get('provenance', {})
    if not isinstance(provenance, dict):
        raise ValueError('Decision probability provenance must be an object.')

    published = [dict(record) for record in records if record.get('reference_only') is False]
    targets = []
    seen = set()
    for entry in entries:
        target = entry.get('target_id')
        if target is None:
            continue
        if not isinstance(target, str):
            raise ValueError('Decision probability entry target_id must be a string.')
        if target not in seen:
            targets.append({'target_id': target})
            seen.add(target)
    result = {key: bundle[key] for key in IDENTITY_FIELDS if key in bundle}
    result.update(
        records=published,
        entries=targets,
        provenance={key: provenance[key] for key in PROVENANCE_FIELDS if key in provenance},
        runtime_projection={
            'schema_version': 1,
            'policy': 'explicit_verified_records_only',
            'source_bundle_sha256': source_bundle_sha256,
            'original_record_count': len(records),
            'published_record_count': len(published),
            'omitted_record_count': len(records) - len(published),
            'original_entry_count': len(entries),
            'published_target_count': len(targets),
            'entries_policy': 'distinct_target_presence_for_unmatched_reference_notes',
        },
    )
    return result


def load_runtime_bundle(path: Path, expected_sha256: str | None = None) -> dict:
    """Load a snapshot and keep its original content identity in the projection.

    The builder's recommendation identity is checked again after parsing so a
    changed source cannot silently be published under the old recommendation
    hash. Only this offline process parses the full research snapshot.
    """
    with path.open('r', encoding='utf-8') as stream:
        bundle = json.load(stream)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('Decision probability snapshot changed after recommendation validation.')
    return project_runtime_bundle(bundle, digest)
