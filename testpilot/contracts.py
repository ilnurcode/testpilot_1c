"""Контракты учебного JSON 1.0. Не формат выгрузки 1С/EDT."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300, pattern=r'^[^\x00-\x1f]+$')]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r'^[\w.:-]+$')]
ObjectType = Literal['document','catalog','information_register','accumulation_register','common_module','form','procedure','function','event_subscription']
Relation = Literal['calls','uses','belongs_to','subscribes_to','dynamic','external','unknown']

def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    @model_validator(mode='after')
    def finite_json(self):
        import math
        pending=[self.model_dump(mode='python')]
        while pending:
            value=pending.pop()
            if isinstance(value,dict): pending.extend(value.values())
            elif isinstance(value,(list,tuple)): pending.extend(value)
            elif isinstance(value,float) and not math.isfinite(value):
                raise ValueError('NaN и Infinity запрещены в JSON')
        return self

class Object(Model):
    object_id: Identifier
    type: ObjectType
    full_name: Text
    parent_id: Identifier | None = None
    attributes: dict = Field(default_factory=dict)

class Edge(Model):
    source: Identifier
    target: Identifier
    type: Relation
    origin: Text
    confidence: float = Field(default=1, ge=0, le=1)

class Configuration(Model):
    schema_version: Literal['1.0']
    configuration_id: Identifier
    version: Text
    source: Text
    synthetic: bool = False
    imported_at: datetime | None = None
    objects: list[Object] = Field(max_length=10000)
    edges: list[Edge] = Field(default_factory=list, max_length=50000)

    @model_validator(mode='after')
    def references(self):
        ids = [o.object_id for o in self.objects]
        if len(set(ids)) != len(ids):
            raise ValueError('Повторяющиеся object_id')
        for o in self.objects:
            if o.parent_id is not None and (o.parent_id not in ids or o.parent_id == o.object_id):
                raise ValueError('Некорректный parent_id')
        parents = {o.object_id:o.parent_id for o in self.objects}
        for oid in ids:
            seen=set(); current=oid
            while current is not None:
                if current in seen: raise ValueError('Цикл принадлежности родителей')
                seen.add(current); current=parents[current]
        seen=set()
        for e in self.edges:
            if e.source not in parents or e.target not in parents:
                raise ValueError('Связь ссылается на отсутствующий объект')
            key=(e.source,e.target,e.type)
            if key in seen: raise ValueError('Дубликат связи')
            seen.add(key)
        canonical(self.model_dump(mode='json'))
        return self

    def content(self):
        d=self.model_dump(mode='json', exclude={'imported_at'})
        d['objects']=sorted(d['objects'],key=lambda o:o['object_id'])
        d['edges']=sorted(d['edges'],key=lambda e:(e['source'],e['target'],e['type']))
        return d

class CompareRequest(Model):
    configuration_id: Identifier
    base_version: Text
    target_version: Text

class AnalysisRequest(CompareRequest):
    max_depth: int = Field(default=10, ge=0, le=100)
    edge_types: list[Relation] = Field(default_factory=lambda:['calls','uses','belongs_to','subscribes_to'])
    direction: Literal['impact','dependencies'] = 'impact'

class Change(Model):
    object_id: Identifier
    status: Literal['added','removed','modified','unchanged']
    properties: list[str]

class PathEdge(Edge):
    version: Text

class DependencyPath(Model):
    changed_object: Identifier
    affected_object: Identifier
    nodes: list[Identifier] = Field(min_length=1, max_length=101)
    edges: list[PathEdge] = Field(max_length=100)
    depth: int = Field(ge=0, le=100)
    confirmed: bool
    @model_validator(mode='after')
    def path(self):
        if self.depth != len(self.edges) or len(self.nodes)!=self.depth+1:
            raise ValueError('Некорректная длина пути')
        if self.nodes[0]!=self.changed_object or self.nodes[-1]!=self.affected_object:
            raise ValueError('Некорректные концы пути')
        for i,e in enumerate(self.edges):
            if {e.source,e.target}!={self.nodes[i],self.nodes[i+1]}:
                raise ValueError('Ребро не соответствует пути')
        return self

class ImpactPackage(Model):
    schema_version: Literal['1.0']
    package_id: Identifier
    configuration_id: Identifier
    base_version: Text
    target_version: Text
    analysis_id: Identifier
    created_at: datetime
    synthetic: bool
    changed_objects: list[Change] = Field(max_length=10000)
    affected_objects: list[Identifier] = Field(max_length=10000)
    dependency_paths: list[DependencyPath] = Field(max_length=20000)
    warnings: list[str]
    checksum: str = Field(pattern=r'^[a-f0-9]{64}$')
    @model_validator(mode='after')
    def valid(self):
        ids=[c.object_id for c in self.changed_objects]
        if len(ids)!=len(set(ids)) or len(self.affected_objects)!=len(set(self.affected_objects)):
            raise ValueError('Повторяющиеся объекты пакета')
        if any(c.status=='unchanged' for c in self.changed_objects):
            raise ValueError('Неизменённый объект в списке изменений')
        if not set(ids)<=set(self.affected_objects): raise ValueError('Изменения вне зоны влияния')
        for p in self.dependency_paths:
            if p.changed_object not in ids or p.affected_object not in self.affected_objects or not set(p.nodes)<=set(self.affected_objects):
                raise ValueError('Неизвестный объект в пути')
        if checksum(self.model_dump(mode='json',exclude={'checksum'}))!=self.checksum:
            raise ValueError('Контрольная сумма не совпадает')
        return self

class TestCase(Model):
    test_id: Identifier
    title: Text
    description: str = Field(default='', max_length=5000)
    active: bool = True
    criticality: Literal['low','medium','high','critical'] = 'medium'
    mandatory: bool = False
    duration_seconds: float = Field(gt=0, le=86400)
    configuration_id: Identifier
    compatible_versions: list[Text] = Field(default_factory=list, max_length=100)
    executor_parameters: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    synthetic: bool = False

class CoverageInput(Model):
    object_id: Identifier
    state: Literal['confirmed','unconfirmed','rejected'] = 'unconfirmed'
    source: Text = 'expert'
    reason: Text

class DefectInput(Model):
    defect_id: Identifier
    test_id: Identifier
    severity: Literal['low','medium','high','critical']
    state: Literal['open','closed'] = 'open'
    description: str = Field(default='',max_length=5000)

class ResultInput(Model):
    test_id: Identifier
    status: Literal['passed','failed','error','skipped']
    duration_seconds: float = Field(ge=0, le=86400)
    started_at: datetime
    finished_at: datetime
    error: str = Field(default='',max_length=5000)
    artifacts: list[Annotated[str,StringConstraints(max_length=1000,pattern=r'^https://')]] = Field(default_factory=list,max_length=20)
    @model_validator(mode='after')
    def dates(self):
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None or self.finished_at<self.started_at:
            raise ValueError('Нужны упорядоченные даты с часовым поясом')
        self.started_at=self.started_at.astimezone(timezone.utc)
        self.finished_at=self.finished_at.astimezone(timezone.utc)
        return self

class RunInput(Model):
    run_id: Identifier
    configuration_id: Identifier
    version: Text
    synthetic: bool = False
    results: list[ResultInput] = Field(min_length=1,max_length=1000)
    defects: list[DefectInput] = Field(default_factory=list,max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        ids=[r.test_id for r in self.results]
        if len(ids)!=len(set(ids)): raise ValueError('Повтор test_id в прогоне')
        ds=[d.defect_id for d in self.defects]
        if len(ds)!=len(set(ds)): raise ValueError('Повтор defect_id')
        if any(d.test_id not in ids for d in self.defects): raise ValueError('Дефект вне прогона')
        return self

class Weights(Model):
    coverage: float = Field(default=.35,ge=0)
    risk: float = Field(default=.2,ge=0)
    defects: float = Field(default=.15,ge=0)
    criticality: float = Field(default=.2,ge=0)
    cost: float = Field(default=.1,ge=0)
    @model_validator(mode='after')
    def normalize(self):
        total=sum(self.model_dump().values())
        if not total: raise ValueError('Нулевая сумма весов')
        for k in type(self).model_fields: setattr(self,k,getattr(self,k)/total)
        return self

class PlanRequest(Model):
    package_id: Identifier
    budget_seconds: float = Field(ge=0,le=100000000)
    min_coverage: float = Field(default=.9,ge=0,le=1)
    use_ml: bool = False

class Approval(Model):
    selected_ids: list[Identifier] | None = None
    allow_deviation: bool = False
    reason: Text

class JobReport(Model):
    execution_job_id: Identifier
    run: RunInput

class ExecutionTest(Model):
    test_id: Identifier
    parameters: dict

class ExecutionJob(Model):
    schema_version: Literal['1.0']
    execution_job_id: Identifier
    test_plan_id: Identifier
    configuration_id: Identifier
    version: Text
    synthetic: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status: Literal['awaiting_external_runner','reported']
    executor_connected: Literal[False]
    tests: list[ExecutionTest]
    run_id: Identifier | None = None
