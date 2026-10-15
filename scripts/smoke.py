import argparse
import asyncio
import os
import time
import json
import httpx
from testpilot.demo import run

def main():
    p=argparse.ArgumentParser();p.add_argument('--health-only',action='store_true');args=p.parse_args()
    urls=[os.environ.get('IMPACT_URL','http://127.0.0.1:8001'),os.environ.get('INTELLIGENCE_URL','http://127.0.0.1:8002'),os.environ.get('WEB_URL','http://127.0.0.1:8000')]
    for url in urls:
        deadline=time.monotonic()+45
        while True:
            try:
                r=httpx.get(url+'/health',timeout=2);r.raise_for_status();assert r.json()['status']=='ok';break
            except (httpx.HTTPError,AssertionError):
                if time.monotonic()>deadline:raise RuntimeError('Не готов '+url)
                time.sleep(.5)
    print('Health всех трёх сервисов: ok')
    if not args.health_only:
        print(json.dumps(asyncio.run(run(urls[0],urls[1],auth=('expert',os.environ['TP_EXPERT_PASSWORD']))),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
