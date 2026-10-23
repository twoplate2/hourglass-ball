"""Current release decision: withdrawals override historical green evidence."""
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path,PurePosixPath

ROOT=Path(__file__).resolve().parents[2]
DECISION=ROOT/'design/checks/current-review.json'


def runtime_manifest(root=ROOT):
    root=Path(root)
    names=['main.py','buildozer.spec']+[p.relative_to(root).as_posix() for p in sorted((root/'src/app').glob('*.py'))]
    return {name:hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in names}


def source_digest(root=ROOT):
    return hashlib.sha256(json.dumps(runtime_manifest(root),sort_keys=True).encode()).hexdigest()


def current_version(root=ROOT):
    tree=ast.parse((Path(root)/'src/app/app_version.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='APP_VERSION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing APP_VERSION')


def require_decision(decision,version,digest):
    if decision.get('schema')!=1 or decision.get('status')!='approved':
        raise ValueError('Current review is not approved: '+str(decision.get('status')))
    if decision.get('version')!=version or decision.get('source_sha256')!=digest:
        raise ValueError('Current review belongs to different runtime sources or version')
    blockers=decision.get('blockers')
    if not isinstance(blockers,list) or any(x.get('status')!='closed' for x in blockers):
        raise ValueError('Unresolved review blockers')
    record=decision.get('record','')
    path=PurePosixPath(record)
    if not record or path.is_absolute() or '..' in path.parts or ':' in record or '\\' in record:
        raise ValueError('Invalid current release record')
    return record


def check_current(root=ROOT):
    root=Path(root)
    value=json.loads((root/'design/checks/current-review.json').read_text(encoding='utf-8'))
    record=require_decision(value,current_version(root),source_digest(root))
    if not (root/record).is_file():raise ValueError('Current release record is missing')
    return root/record


def require_committed_bytes(files,committed):
    for name,value in files.items():
        try:saved=committed(name)
        except (KeyError,subprocess.CalledProcessError) as exc:
            raise ValueError('Review evidence is not committed: '+name) from exc
        if saved!=value:raise ValueError('Review evidence differs from committed HEAD: '+name)


def check_committed(root=ROOT):
    root=Path(root);record=check_current(root)
    names=[root/'design/checks/current-review.json']+sorted(p for p in record.parent.rglob('*') if p.is_file())
    # Apply Git's own path-specific clean rules: HTML may use CRLF in Windows,
    # whereas JSON -text, archives and images must keep their exact bytes.
    files={p.relative_to(root).as_posix():subprocess.check_output(
        ['git','hash-object','--path='+p.relative_to(root).as_posix(),'--stdin'],
        input=p.read_bytes(),cwd=root,stderr=subprocess.DEVNULL).strip() for p in names}
    require_committed_bytes(files,lambda name:subprocess.check_output(
        ['git','rev-parse','--verify','HEAD:'+name],cwd=root,stderr=subprocess.DEVNULL).strip())
    return record


if __name__=='__main__':
    print('CURRENT_REVIEW_OK',check_committed() if '--committed' in sys.argv else check_current())
