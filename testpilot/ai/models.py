from typing import Literal
from pydantic import Field,model_validator
from testpilot.contracts import Model,Text,Identifier

from pydantic import ConfigDict

class Case(Model):
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    # Поля учебного стенда продаж (для обратной совместимости)
    quantity: int | None = Field(default=None, ge=1, le=1000)
    price: float | None = Field(default=None, ge=0, le=1000000)
    discount: float | None = Field(default=None, ge=0, le=100)
    expected_total: float | None = Field(default=None, ge=0, le=1000000000)
    # Универсальные поля для произвольных метаданных 1С
    entity: str | None = None
    fields: dict = Field(default_factory=dict)
    expected: str | float | int | dict | None = None
    steps: list[dict] = Field(default_factory=list)

class Proposal(Model):
    title:Text
    rationale:Text
    engine:Literal['odata','playwright','manual']
    object_ids:list[Identifier]=Field(min_length=1,max_length=30)
    steps:list[Text]=Field(min_length=1,max_length=12)
    expected_result:Text
    cases:list[Case]=Field(default_factory=list,max_length=3)
    @model_validator(mode='after')
    def executable(self):
        if self.engine!='manual' and not self.cases:raise ValueError('Для автоматической проверки нужны данные и ожидаемые значения')
        return self

class Suggestion(Model):
    summary:Text
    selected_test_ids:list[Identifier]=Field(default_factory=list,max_length=100)
    proposals:list[Proposal]=Field(min_length=1,max_length=6)
    warnings:list[Text]=Field(default_factory=list,max_length=20)

class GenerateRequest(Model):
    package_id:Identifier
    project_id:str=Field(max_length=36)
    provider:Literal['llm','demo']='demo'
    consent:bool=False
    model_context:list[dict]=Field(default_factory=list,max_length=100)
    object_names:dict[str,str]=Field(default_factory=dict,max_length=10000)

class Decision(Model):
    reason:Text
    selected_proposals:list[int]=Field(min_length=1,max_length=6)

class LLMSettingsInput(Model):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
