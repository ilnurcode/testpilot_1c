from typing import Literal
from pydantic import Field
from testpilot.contracts import Model,Text,Identifier

from pydantic import ConfigDict

from decimal import Decimal, InvalidOperation
from datetime import date as _date, datetime as _datetime
from typing import Any, Literal
from pydantic import model_validator

class AssertionSelector(Model):
    entity: Identifier | None = None
    key: Text | None = None
    field: Text | None = None
    section: Text | None = None
    path: list[Text] = Field(default_factory=list, max_length=8)

class ExpectedValue(Model):
    type: Literal['string','integer','number','boolean','date','null']
    value: Any = None

    @model_validator(mode='after')
    def typed(self):
        kind, value = self.type, self.value
        if kind == 'null':
            if value is not None:
                raise ValueError('null expected требует value=null')
            return self
        if kind == 'boolean':
            if not isinstance(value, bool):
                raise ValueError('boolean expected требует bool, не 1/0')
            return self
        if kind == 'integer':
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError('integer expected требует int, не bool')
            return self
        if kind == 'number':
            if isinstance(value, bool) or value is None:
                raise ValueError('number expected требует число')
            try:
                decimal = Decimal(str(value))
            except (InvalidOperation, ValueError) as error:
                raise ValueError('number expected требует конечное число') from error
            if not decimal.is_finite():
                raise ValueError('number expected требует конечное число')
            self.value = decimal
            return self
        if kind == 'string':
            if not isinstance(value, str):
                raise ValueError('string expected требует str')
            return self
        if kind == 'date':
            if not isinstance(value, str):
                raise ValueError('date expected требует ISO-8601 str')
            try:
                parsed = _datetime.fromisoformat(value.replace('Z', '+00:00'))
                self.value = parsed.isoformat()
            except ValueError:
                try:
                    self.value = _date.fromisoformat(value).isoformat()
                except ValueError as error:
                    raise ValueError('date expected требует ISO-8601') from error
            return self
        raise ValueError('неизвестный тип expected')

class Assertion(Model):
    id: Identifier
    scope: Literal['business','connectivity','metadata','manual']
    check: Literal['odata_field_equals','odata_record_count','odata_posted_state','metadata_entity','web_section_visible','manual']
    engine: Literal['odata','playwright','manual']
    operator: Literal['equals','contains','count_equals','exists','posted','manual']
    selector: AssertionSelector = Field(default_factory=AssertionSelector)
    expected: ExpectedValue | None = None
    source_ref: Text | None = None
    requirement: Text | None = None
    required: bool = True

    @model_validator(mode='after')
    def combination(self):
        sel, exp = self.selector, self.expected
        if self.check == 'manual':
            if self.scope != 'manual' or self.engine != 'manual' or self.operator != 'manual':
                raise ValueError('manual check требует scope/engine/operator=manual')
            if not self.requirement and not self.source_ref:
                raise ValueError('manual check требует requirement или source_ref')
            return self
        if self.check == 'metadata_entity':
            if self.scope != 'metadata' or self.engine != 'odata' or self.operator != 'exists':
                raise ValueError('metadata_entity требует metadata/odata/exists')
            if not sel.entity:
                raise ValueError('metadata_entity требует selector.entity')
            return self
        if self.check == 'web_section_visible':
            if self.scope != 'connectivity' or self.engine != 'playwright':
                raise ValueError('web_section_visible требует connectivity/playwright')
            if self.operator not in ('exists','contains'):
                raise ValueError('web_section_visible требует operator exists/contains')
            if not (sel.section or sel.path):
                raise ValueError('web_section_visible требует selector.section или path')
            if exp is not None and exp.type != 'boolean':
                raise ValueError('web_section_visible expected должен быть boolean')
            return self
        if self.scope != 'business' or self.engine != 'odata':
            raise ValueError('бизнес-check требует scope=business, engine=odata')
        if not self.source_ref:
            raise ValueError('бизнес-check требует source_ref (не HTTP200)')
        if self.check == 'odata_field_equals':
            if self.operator != 'equals' or not (sel.entity and sel.key and sel.field):
                raise ValueError('odata_field_equals требует operator=equals, entity/key/field')
            if exp is None or exp.type == 'null':
                raise ValueError('odata_field_equals требует typed expected')
        elif self.check == 'odata_record_count':
            if self.operator != 'count_equals' or not sel.entity:
                raise ValueError('odata_record_count требует operator=count_equals, selector.entity')
            if exp is None or exp.type != 'integer':
                raise ValueError('odata_record_count требует expected.type=integer')
        elif self.check == 'odata_posted_state':
            if self.operator != 'posted' or not (sel.entity and sel.key):
                raise ValueError('odata_posted_state требует operator=posted, entity/key')
            if exp is None or exp.type != 'boolean':
                raise ValueError('odata_posted_state требует expected.type=boolean')
        else:
            raise ValueError('неизвестный check')
        return self

class AssertionResult(Model):
    assertion_id: Identifier
    scope: Literal['business','connectivity','metadata','manual']
    status: Literal['passed','failed','error','skipped']
    expected: dict | None = None
    actual: dict | None = None
    message: str = ''

class CaseResult(Model):
    status: Literal['passed','failed','error','skipped']
    business_success: bool | None = None
    assertions: list[AssertionResult] = Field(default_factory=list)
    message: str = ''


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
    assertions: list[Assertion] = Field(default_factory=list, max_length=12)

class Proposal(Model):
    title:Text
    rationale:Text
    engine:str
    object_ids:list[Identifier]=Field(min_length=1,max_length=30)
    steps:list[Text]=Field(min_length=1,max_length=12)
    expected_result:Text
    cases:list[Case]=Field(default_factory=list,max_length=3)

class Suggestion(Model):
    summary:Text
    selected_test_ids:list[Identifier]=Field(default_factory=list,max_length=100)
    proposals:list[Proposal]=Field(min_length=1,max_length=6)
    warnings:list[Text]=Field(default_factory=list,max_length=20)

class CodexStatus(Model):
    available:bool
    authenticated:bool
    state:Literal['unavailable','signed_out','pending','authenticated','error']
    model:str|None=None
    verification_uri:str|None=None
    user_code:str|None=None
    message:str

class GenerateRequest(Model):
    package_id:Identifier
    project_id:str=Field(max_length=36)
    provider:Literal['llm','codex','demo','local']='local'
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
    # persist=true требует TP_ALLOW_ENV_WRITE=1: по умолчанию настройки живут только в памяти процесса.
    persist: bool = False
