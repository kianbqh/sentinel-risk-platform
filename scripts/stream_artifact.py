"""Record/check a current native assembly; a fresh clone uses the source Docker build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
STREAM=ROOT/'streaming'
MANIFEST=ROOT/'.runtime/streaming-artifact.json'


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(2**20):value.update(block)
    return value.hexdigest()


def inputs():
    paths=[STREAM/'build.sbt']
    for directory in ('src','project','model'):
        paths.extend(path for path in (STREAM/directory).rglob('*') if path.is_file() and 'target' not in path.relative_to(STREAM).parts)
    return {str(path.relative_to(ROOT)).replace('\\','/'):digest(path) for path in sorted(paths)}


def record():
    jars=list((STREAM/'target/scala-2.13').glob('*assembly*.jar'))
    if len(jars)!=1:raise RuntimeError('Expected exactly one freshly built assembly')
    jar=jars[0];classes=STREAM/'target/scala-2.13/classes'
    with zipfile.ZipFile(jar) as archive:
        required={'fraudstream/Main.class','fraudstream/AlertContract.class'}
        if not required<=set(archive.namelist()):raise RuntimeError('Missing platform entrypoint/producer class')
        checked=0
        for path in classes.rglob('*.class'):
            name=path.relative_to(classes).as_posix()
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest(path):raise RuntimeError('Assembly differs from compiled class '+name)
            checked+=1
    result={'jar':jar.relative_to(ROOT).as_posix(),'jar_sha256':digest(jar),'jar_bytes':jar.stat().st_size,
            'inputs':inputs(),'compiled_class_matches':checked,'scope':'All Scala sources/resources, project files excluding generated targets, build.sbt and model artifacts.'}
    MANIFEST.parent.mkdir(parents=True,exist_ok=True);MANIFEST.write_text(json.dumps(result,indent=2)+'\n')
    out=ROOT/'results/build';out.mkdir(parents=True,exist_ok=True)
    (out/'streaming-artifact.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Recorded checked native assembly:',result['jar_sha256'])


def check(inputs_only=False):
    if not MANIFEST.is_file():return False
    recorded=json.loads(MANIFEST.read_text())
    if recorded['inputs'] != inputs() or not (ROOT/recorded['jar']).is_file():
        return False
    return inputs_only or recorded['jar_sha256'] == digest(ROOT/recorded['jar'])


def package(destination):
    if not check(inputs_only=True):raise RuntimeError('No matching recorded native assembly; use the source build')
    recorded=json.loads(MANIFEST.read_text());destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    copied = hashlib.sha256()
    with (ROOT/recorded['jar']).open('rb') as source, (destination/'app.jar').open('wb') as target:
        while block := source.read(2**20):
            target.write(block)
            copied.update(block)
    if copied.hexdigest() != recorded['jar_sha256']:
        raise RuntimeError('Native assembly changed since recording')
    shutil.copytree(STREAM/'model',destination/'model',dirs_exist_ok=True)
    shutil.copyfile(STREAM/'entrypoint.sh',destination/'entrypoint.sh')
    shutil.copyfile(STREAM/'Dockerfile.runtime',destination/'Dockerfile')
    if digest(destination/'app.jar')!=recorded['jar_sha256']:raise RuntimeError('Copied assembly checksum mismatch')
    print('Native assembly runtime context ready.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('record','check','package'));parser.add_argument('--destination',default='.runtime/stream-image')
    parser.add_argument('--inputs-only', action='store_true', help='Check source inputs now; package validates the jar while copying it.')
    args=parser.parse_args()
    if args.action=='record':record()
    elif args.action=='check':sys.exit(0 if check(args.inputs_only) else 1)
    else:package(args.destination)
