from typing import Literal
from pydantic import Field,model_validator
from testpilot.contracts import Model,Text,Identifier

class Case(Model):
    quantity:int=Field(ge=1,le=1000)
    price:float=Field(ge=0,le=1000000)
    discount:float=Field(ge=0,le=100)
    expected_total:float=Field(ge=0,le=1000000000)

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
