from pathlib import Path
import json
from testpilot.contracts import Configuration,ImpactPackage,TestCase,RunInput,JobReport,ExecutionJob
from testpilot.demo import configurations,catalog,histories
root=Path(__file__).resolve().parents[1]/'contracts'
for name,model in [('configuration',Configuration),('impact-package',ImpactPackage),('test-case',TestCase),('test-run',RunInput),('execution-report',JobReport),('execution-job',ExecutionJob)]:
    (root/(name+'-1.0.schema.json')).write_text(json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
for i,c in enumerate(configurations(),1):(root/'examples'/f'configuration-v{i}.json').write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n')
(root/'examples'/'catalog.json').write_text(json.dumps([{'test':t,'coverage':cov,'state':state} for t,cov,state in catalog()],ensure_ascii=False,indent=2)+'\n')
(root/'examples'/'synthetic-history.json').write_text(json.dumps(histories(),ensure_ascii=False,indent=2)+'\n')
