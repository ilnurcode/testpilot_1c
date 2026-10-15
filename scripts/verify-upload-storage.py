"""Снимок и проверка сохранности файлов вокруг ручного перезапуска приложений."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID
import httpx
from testpilot.contracts import checksum

p=argparse.ArgumentParser();p.add_argument('phase',choices=['capture','verify']);args=p.parse_args()
record=Path('.runtime/uploads-preservation.json')
if args.phase=='capture':
    report=json.loads(Path('.runtime/projects-smoke-result.json').read_text())
    pid=str(UUID(report['project_url'].split('#projects/')[1]))
else:pid=json.loads(record.read_text())['project_id']
base=os.environ.get('IMPACT_URL','http://127.0.0.1:8001')+'/api/v1/projects/'+pid
with httpx.Client(auth=('expert',os.environ['TP_EXPERT_PASSWORD']),timeout=30) as c:
    response=c.get(base);response.raise_for_status();project=response.json()
    current={'project_id':pid,'metadata_checksum':checksum(project),'files':[]}
    for u in project['uploads']:
        response=c.get(base+'/uploads/'+u['id']+'/download');response.raise_for_status()
        digest=hashlib.sha256(response.content).hexdigest()
        assert digest==u['checksum']
        current['files'].append({'id':u['id'],'checksum':digest})
if args.phase=='capture':
    record.write_text(json.dumps(current,indent=2));print('Снимок сохранён:',len(current['files']),'исходных файлов и метаданные проекта')
else:
    assert current==json.loads(record.read_text())
    print('Сохранность подтверждена: все исходные файлы побайтово и метаданные совпали после перезапуска')
