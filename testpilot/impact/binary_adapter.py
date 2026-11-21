"""Адаптер прямого разбора бинарных расширений (.cfe), обработок (.epf, .erf) и конфигураций (.cf)."""
import re
from dataclasses import dataclass
from testpilot.contracts import Configuration, Object, Edge
from testpilot.impact.v8_container import V8Container, V8LimitsExceeded

# Признак исходного модуля: проверяется по декодированному тексту потока.
BSL_SIGNATURE = re.compile(r'(?:Процедура|Функция|КонецПроцедуры|КонецФункции|Procedure|Function|EndProcedure|EndFunction)\b')
STREAM_ENCODINGS = ('utf-8-sig', 'utf-16le', 'cp1251')
PRINTABLE_RATIO = 0.9


def decode_bsl(data: bytes) -> str | None:
    """Текст модуля BSL из потока или None, если поток исходником не является."""
    for encoding in STREAM_ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if '\x00' in text or not BSL_SIGNATURE.search(text):
            continue
        sample = text[:4096]
        printable = sum(1 for ch in sample if ch.isprintable() or ch in '\r\n\t')
        if sample and printable >= PRINTABLE_RATIO * len(sample):
            return text
    return None


def bsl_sources(files: dict[str, bytes]) -> dict[str, str]:
    """Исходники BSL из потоков контейнера: {имя потока: текст}. Пустой словарь — исходников нет."""
    sources = {}
    for name, data in files.items():
        text = decode_bsl(data)
        if text is not None:
            sources[name] = text
    return sources


@dataclass(frozen=True)
class BinaryParseReport:
    """Что фактически извлечено из контейнера: без этого статус разбора не отличить от полного."""

    container_valid: bool
    streams: int
    bsl_modules: tuple[str, ...]
    objects: int
    edges: int

    @property
    def has_bsl(self) -> bool:
        return bool(self.bsl_modules)


class Binary1CAdapter:
    """Извлечение структуры метаданных и зависимостей из бинарных контейнеров 1С."""

    def parse(self, raw: bytes, filename: str, configuration_id: str = 'c1_binary', version: str = '1.0.0') -> Configuration:
        model, _ = self.parse_with_report(raw, filename, configuration_id=configuration_id, version=version)
        return model

    def parse_with_report(self, raw: bytes, filename: str, configuration_id: str = 'c1_binary', version: str = '1.0.0') -> tuple[Configuration, BinaryParseReport]:
        container = V8Container(raw)
        if not container.is_valid():
            raise ValueError("Не является валидным составным контейнером 1С v8")
        try:
            files = container.extract_files()
        except V8LimitsExceeded as error:
            # Предел разбора — это не «разобрано»: нужен внешний адаптер/агент 1С.
            raise ValueError(
                f"Контейнер 1С превышает безопасные пределы разбора ({error.code}); "
                "автоматический разбор остановлен, нужен внешний адаптер или агент 1С."
            ) from error
        if not files:
            raise ValueError("Контейнер 1С v8 не содержит распознаваемых внутренних файлов")

        ext = filename.lower().rsplit('.', 1)[-1]
        base_name = filename.rsplit('.', 1)[0]

        objects = []
        edges = []
        seen_ids = set()

        def add_obj(obj_id, obj_type, full_name, attrs=None):
            clean_id = re.sub(r'[^\w.:-]', '_', obj_id)[:120].strip('_').lower()
            if clean_id not in seen_ids:
                seen_ids.add(clean_id)
                objects.append(Object(
                    object_id=clean_id,
                    type=obj_type,
                    full_name=full_name,
                    parent_id=None,
                    attributes=attrs or {}
                ))
            return clean_id

        # 1. Корневой объект разработки
        if ext == 'epf':
            root_id = add_obj(f"processing.{base_name}", 'document', f"ВнешняяОбработка.{base_name}", {'kind': 'epf'})
        elif ext == 'erf':
            root_id = add_obj(f"report.{base_name}", 'document', f"ВнешнийОтчет.{base_name}", {'kind': 'erf'})
        elif ext == 'cfe':
            root_id = add_obj(f"extension.{base_name}", 'common_module', f"Расширение.{base_name}", {'kind': 'cfe'})
        else:
            root_id = add_obj(f"config.{base_name}", 'common_module', f"Конфигурация.{base_name}", {'kind': 'cf'})

        # 2. Разбор только текстовых потоков BSL: произвольные байты контейнера кодом не считаются.
        sources = bsl_sources(files)
        for stream_name, text in sources.items():
            # Поиск директив расширений: &Вместо, &Перед, &После, &ИзменениеИКонтроль
            extension_directives = re.findall(r'&(Вместо|Перед|После|ИзменениеИКонтроль)\s*\(\s*["\']([^"\']+)["\']\s*\)', text, re.IGNORECASE)
            for directive, target_method in extension_directives:
                method_id = add_obj(f"method.{target_method}", 'procedure', f"Метод.{target_method}", {'directive': directive})
                edges.append(Edge(source=root_id, target=method_id, type='calls', origin=f"&{directive}", confidence=1.0))

            # Поиск обращений к объектам метаданных в коде: Справочники.X, Документы.Y, РегистрыСведений.Z
            references = re.findall(r'(Справочники|Документы|РегистрыСведений|РегистрыНакопления)\.([a-zA-Zа-яА-Я0-9_]+)', text, re.IGNORECASE)
            type_map = {
                'справочники': ('catalog', 'Справочник'),
                'документы': ('document', 'Документ'),
                'регистрысведений': ('information_register', 'РегистрСведений'),
                'регистрынакопления': ('accumulation_register', 'РегистрНакопления'),
            }
            for ref_group, ref_name in references:
                group_lower = ref_group.lower()
                if group_lower in type_map:
                    obj_type, prefix = type_map[group_lower]
                    target_id = add_obj(f"{obj_type}.{ref_name}", obj_type, f"{prefix}.{ref_name}")
                    if target_id != root_id:
                        edges.append(Edge(source=root_id, target=target_id, type='uses', origin='BSL_Call', confidence=0.85))

        # Очистка и дедупликация рёбер
        valid_ids = {o.object_id for o in objects}
        seen_edges = set()
        clean_edges = []
        for e in edges:
            if e.source in valid_ids and e.target in valid_ids and e.source != e.target:
                k = (e.source, e.target, e.type)
                if k not in seen_edges:
                    seen_edges.add(k)
                    clean_edges.append(e)

        model = Configuration(
            schema_version='1.0',
            configuration_id=configuration_id,
            version=version,
            source=f"1C:{ext.upper()}",
            synthetic=False,
            objects=objects,
            edges=clean_edges
        )
        report = BinaryParseReport(
            container_valid=True,
            streams=len(files),
            bsl_modules=tuple(sources),
            objects=len(objects),
            edges=len(clean_edges),
        )
        return model, report
