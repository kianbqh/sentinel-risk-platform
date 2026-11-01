"""Collect a successful container run as the host user, preserving non-root services."""
import argparse
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]


def collect(source, destination):
    source, destination = Path(source), Path(destination)
    summary = json.loads((source / 'integration/summary.json').read_text())
    if summary.get('all_assertions_passed') is not True:
        raise RuntimeError('Only a validated integration run can be collected')
    for required in ('summary.json', 'terminal-progress.json', 'scores.jsonl',
                     'reference-predictions.jsonl', 'alerts-wire.jsonl', 'ato-publish.json'):
        if not (source / 'integration' / required).is_file():
            raise RuntimeError('Incomplete integration outputs: ' + required)
    copied = []
    for module in ('integration', 'streaming', 'ato'):
        directory = source / module
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            if module == 'streaming' and path.name == 'progress.jsonl' and not data.endswith(b'\n'):
                data = data.rsplit(b'\n', 1)[0] + b'\n'
            target = destination / module / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.collect-' + uuid.uuid4().hex)
            try:
                temporary.write_bytes(data)
                temporary.chmod(0o644)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            copied.append(str(target.relative_to(destination)))
    return copied


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / '.runtime/results')
    parser.add_argument('--destination', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    print(json.dumps({'collected': collect(args.source, args.destination)}, indent=2))
