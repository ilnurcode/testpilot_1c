"""Точки расширения: неподтверждённые форматы намеренно не парсятся."""
from typing import Protocol
from testpilot.contracts import Configuration, Edge
class ConfigurationAdapter(Protocol):
    def parse(self, content:bytes) -> Configuration: ...
class ProgramDependenciesAdapter(Protocol):
    def extract(self, modules:dict[str,str]) -> list[Edge]: ...
class NormalizedJSONAdapter:
    def parse(self, content:bytes) -> Configuration:
        return Configuration.model_validate_json(content)
class UnsupportedXMLAdapter:
    def parse(self, content:bytes) -> Configuration:
        raise NotImplementedError('Выгрузки XML/EDT не поддерживаются: нужны спецификация и проверенные образцы')
class FutureBSLAdapter:
    def extract(self, modules:dict[str,str]) -> list[Edge]:
        raise NotImplementedError('BSL Language Server не подключён; требуется адаптер подтверждённого формата')
