import os
from uuid import uuid4
from sqlalchemy import (create_engine, String, Float, Boolean, Integer, ForeignKey, ForeignKeyConstraint, UniqueConstraint, JSON, Text, LargeBinary, CheckConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from testpilot.contracts import now

def uid(): return str(uuid4())
class Base(DeclarativeBase): pass
class ConfigurationRow(Base):
    __tablename__='impact_configurations'
    id: Mapped[str]=mapped_column(String(120),primary_key=True)
    synthetic: Mapped[bool]=mapped_column(Boolean)
class VersionRow(Base):
    __tablename__='impact_versions'
    __table_args__=(UniqueConstraint('configuration_id','version'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    configuration_id: Mapped[str]=mapped_column(ForeignKey('impact_configurations.id'),index=True)
    version: Mapped[str]=mapped_column(String(300))
    source: Mapped[str]=mapped_column(String(300))
    imported_at: Mapped[str]=mapped_column(String(50),default=now)
    checksum: Mapped[str]=mapped_column(String(64))
    raw_checksum: Mapped[str]=mapped_column(String(64))
    payload: Mapped[dict]=mapped_column(JSON)
class ObjectRow(Base):
    __tablename__='impact_objects'
    __table_args__=(UniqueConstraint('version_id','object_id'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    version_id: Mapped[str]=mapped_column(ForeignKey('impact_versions.id'),index=True)
    object_id: Mapped[str]=mapped_column(String(120),index=True)
    type: Mapped[str]=mapped_column(String(40))
    full_name: Mapped[str]=mapped_column(String(300))
    parent_id: Mapped[str|None]=mapped_column(String(120),nullable=True)
    attributes: Mapped[dict]=mapped_column(JSON)
class EdgeRow(Base):
    __tablename__='impact_edges'
    __table_args__=(UniqueConstraint('version_id','source','target','type'),ForeignKeyConstraint(['version_id','source'],['impact_objects.version_id','impact_objects.object_id']),ForeignKeyConstraint(['version_id','target'],['impact_objects.version_id','impact_objects.object_id']))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    version_id: Mapped[str]=mapped_column(ForeignKey('impact_versions.id'),index=True)
    source: Mapped[str]=mapped_column(String(120),index=True)
    target: Mapped[str]=mapped_column(String(120),index=True)
    type: Mapped[str]=mapped_column(String(40))
    origin: Mapped[str]=mapped_column(String(300))
    confidence: Mapped[float]=mapped_column(Float)
class AnalysisRow(Base):
    __tablename__='impact_analyses'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    base_id: Mapped[str|None]=mapped_column(ForeignKey('impact_versions.id'),nullable=True)
    target_id: Mapped[str]=mapped_column(ForeignKey('impact_versions.id'))
    parameters: Mapped[dict]=mapped_column(JSON)
    comparison: Mapped[list]=mapped_column(JSON)
    package: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[str]=mapped_column(String(50),default=now)
class AffectedRow(Base):
    __tablename__='impact_affected'
    analysis_id: Mapped[str]=mapped_column(ForeignKey('impact_analyses.id'),primary_key=True)
    object_id: Mapped[str]=mapped_column(String(120),primary_key=True)
    paths: Mapped[list]=mapped_column(JSON)
class AuditRow(Base):
    __tablename__='audit'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    service: Mapped[str]=mapped_column(String(30),index=True)
    actor: Mapped[str]=mapped_column(String(100))
    action: Mapped[str]=mapped_column(String(100))
    entity_id: Mapped[str]=mapped_column(String(120))
    details: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[str]=mapped_column(String(50),default=now)
class PackageRow(Base):
    __tablename__='ti_packages'
    id: Mapped[str]=mapped_column(String(120),primary_key=True)
    checksum: Mapped[str]=mapped_column(String(64))
    configuration_id: Mapped[str]=mapped_column(String(120),index=True)
    payload: Mapped[dict]=mapped_column(JSON)
class TestRow(Base):
    __tablename__='ti_tests'
    id: Mapped[str]=mapped_column(String(120),primary_key=True)
    configuration_id: Mapped[str]=mapped_column(String(120),index=True)
    active: Mapped[bool]=mapped_column(Boolean)
    mandatory: Mapped[bool]=mapped_column(Boolean)
    criticality: Mapped[str]=mapped_column(String(20))
    duration_seconds: Mapped[float]=mapped_column(Float)
    payload: Mapped[dict]=mapped_column(JSON)
class CoverageRow(Base):
    __tablename__='ti_coverage'
    test_id: Mapped[str]=mapped_column(ForeignKey('ti_tests.id'),primary_key=True)
    object_id: Mapped[str]=mapped_column(String(120),primary_key=True)
    state: Mapped[str]=mapped_column(String(20))
    source: Mapped[str]=mapped_column(String(300))
    actor: Mapped[str]=mapped_column(String(100))
    reason: Mapped[str]=mapped_column(String(300))
    updated_at: Mapped[str]=mapped_column(String(50),default=now)
class RunRow(Base):
    __tablename__='ti_runs'
    id: Mapped[str]=mapped_column(String(120),primary_key=True)
    configuration_id: Mapped[str]=mapped_column(String(120),index=True)
    version: Mapped[str]=mapped_column(String(300))
    synthetic: Mapped[bool]=mapped_column(Boolean)
    checksum: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[str]=mapped_column(String(50),default=now)
class ResultRow(Base):
    __tablename__='ti_results'
    __table_args__=(UniqueConstraint('run_id','test_id'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    run_id: Mapped[str]=mapped_column(ForeignKey('ti_runs.id'),index=True)
    test_id: Mapped[str]=mapped_column(ForeignKey('ti_tests.id'),index=True)
    status: Mapped[str]=mapped_column(String(20))
    duration_seconds: Mapped[float]=mapped_column(Float)
    started_at: Mapped[str]=mapped_column(String(50),index=True)
    finished_at: Mapped[str]=mapped_column(String(50))
    payload: Mapped[dict]=mapped_column(JSON)
class DefectRow(Base):
    __tablename__='ti_defects'
    id: Mapped[str]=mapped_column(String(120),primary_key=True)
    run_id: Mapped[str]=mapped_column(ForeignKey('ti_runs.id'),index=True)
    test_id: Mapped[str]=mapped_column(ForeignKey('ti_tests.id'),index=True)
    severity: Mapped[str]=mapped_column(String(20))
    state: Mapped[str]=mapped_column(String(20))
    description: Mapped[str]=mapped_column(Text)
class SettingsRow(Base):
    __tablename__='ti_settings'
    id: Mapped[str]=mapped_column(String(30),primary_key=True)
    payload: Mapped[dict]=mapped_column(JSON)
class PlanRow(Base):
    __tablename__='ti_plans'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    package_id: Mapped[str]=mapped_column(ForeignKey('ti_packages.id'),index=True)
    payload: Mapped[dict]=mapped_column(JSON)
    approved: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[str]=mapped_column(String(50),default=now)
class PriorityRow(Base):
    __tablename__='ti_priorities'
    plan_id: Mapped[str]=mapped_column(ForeignKey('ti_plans.id'),primary_key=True)
    test_id: Mapped[str]=mapped_column(ForeignKey('ti_tests.id'),primary_key=True)
    score: Mapped[float]=mapped_column(Float)
    factors: Mapped[dict]=mapped_column(JSON)
class MLRow(Base):
    __tablename__='ti_ml_models'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    configuration_id: Mapped[str]=mapped_column(String(120),index=True)
    payload: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[str]=mapped_column(String(50),default=now)
class JobRow(Base):
    __tablename__='ti_execution_jobs'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    plan_id: Mapped[str]=mapped_column(ForeignKey('ti_plans.id'),index=True)
    payload: Mapped[dict]=mapped_column(JSON)
    report_checksum: Mapped[str|None]=mapped_column(String(64),nullable=True)

class ProjectRow(Base):
    __tablename__='impact_projects'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    name: Mapped[str]=mapped_column(String(120))
    description: Mapped[str]=mapped_column(Text,default='')
    configuration_id: Mapped[str|None]=mapped_column(String(120),nullable=True,unique=True)
    created_by: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(50),default=now)
    next_number: Mapped[int]=mapped_column(Integer,default=0)

class UploadRow(Base):
    __tablename__='impact_uploads'
    __table_args__=(UniqueConstraint('project_id','number'),
                   UniqueConstraint('project_id','kind','component','checksum'),
                   UniqueConstraint('project_id','kind','component','label'),
                   CheckConstraint('size_bytes > 0 AND size_bytes <= 67108864',name='upload_size_limit'))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    project_id: Mapped[str]=mapped_column(ForeignKey('impact_projects.id'),index=True)
    number: Mapped[int]=mapped_column(Integer)
    filename: Mapped[str]=mapped_column(String(240))
    kind: Mapped[str]=mapped_column(String(30))
    component: Mapped[str]=mapped_column(String(120))
    label: Mapped[str]=mapped_column(String(120))
    size_bytes: Mapped[int]=mapped_column(Integer)
    checksum: Mapped[str]=mapped_column(String(64))
    content: Mapped[bytes]=mapped_column(LargeBinary)
    status: Mapped[str]=mapped_column(String(30))
    message: Mapped[str]=mapped_column(Text)
    version_id: Mapped[str|None]=mapped_column(ForeignKey('impact_versions.id'),nullable=True,index=True)
    baseline_id: Mapped[str|None]=mapped_column(ForeignKey('impact_uploads.id'),nullable=True)
    created_by: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(50),default=now)
    summary: Mapped[dict]=mapped_column(JSON)

class UploadAnalysisRow(Base):
    __tablename__='impact_upload_analyses'
    analysis_id: Mapped[str]=mapped_column(ForeignKey('impact_analyses.id'),primary_key=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('impact_projects.id'),index=True)
    upload_id: Mapped[str]=mapped_column(ForeignKey('impact_uploads.id'),index=True)
    baseline_id: Mapped[str|None]=mapped_column(ForeignKey('impact_uploads.id'),nullable=True)
    mode: Mapped[str]=mapped_column(String(20))
    created_at: Mapped[str]=mapped_column(String(50),default=now)

class AISuiteRow(Base):
    __tablename__='ti_ai_suites'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    package_id: Mapped[str]=mapped_column(ForeignKey('ti_packages.id'),index=True)
    project_id: Mapped[str]=mapped_column(String(36),index=True)
    status: Mapped[str]=mapped_column(String(30))
    payload: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[str]=mapped_column(String(50),default=now)

class EvidenceRow(Base):
    __tablename__='ti_ai_evidence'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    suite_id: Mapped[str]=mapped_column(ForeignKey('ti_ai_suites.id'),index=True)
    content: Mapped[bytes]=mapped_column(LargeBinary)

class DemoOrderRow(Base):
    __tablename__='demo_orders'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    payload: Mapped[dict]=mapped_column(JSON)

_engine=None
_factory=None
def engine():
    global _engine,_factory
    if _engine is None:
        url=os.environ.get('DATABASE_URL')
        if not url: raise RuntimeError('Задайте DATABASE_URL PostgreSQL')
        _engine=create_engine(url,pool_pre_ping=True)
        _factory=sessionmaker(_engine,expire_on_commit=False)
    return _engine

def session():
    engine()
    with _factory() as s:
        with s.begin(): yield s

def audit(s,service,actor,action,entity_id,details=None):
    s.add(AuditRow(service=service,actor=actor,action=action,entity_id=entity_id,details=details or {}))
