"""Data-only Agent catalog; agents do not import the scoring implementation."""
import os
from pathlib import Path
import yaml
from shared.config import ROOT_DIR


def load_catalog():
    path = Path(os.environ.get('RULES_PATH') or ROOT_DIR / 'risk_engine' / 'rules.yaml')
    rules = yaml.safe_load(path.read_text(encoding='utf-8'))
    return {
        'classification': rules['phase1_classification'],
        'allowed': rules['phase1_allowed_signals_by_type'],
        'signals': {st: sorted(set(spec.get('flags', {})) | set(spec.get('tiers', {}))
                               | set(spec.get('values', {})) | set(spec.get('less_than', {}))
                               | {'resolved', 'lifted', 'reason'} | ({spec['zero_if']} if spec.get('zero_if') else set()))
                    for group in ('red','orange','yellow') for st, spec in rules[group].items()},
        'quiet_types': rules.get('phase1_quiet_types', ['OWNERSHIP']),
    }
