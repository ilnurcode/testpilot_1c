"""Адаптер безопасного разбора выгрузок метаданных 1С в формате XML / EDT."""
import io
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile
from testpilot.contracts import Configuration, Object, Edge

MAX_XML_BYTES = 16 * 1024 * 1024

TYPE_MAPPING = {
    'catalog': 'catalog',
    'справочник': 'catalog',
    'document': 'document',
    'документ': 'document',
    'informationregister': 'information_register',
    'регистрсведений': 'information_register',
    'accumulationregister': 'accumulation_register',
    'регистрнакопления': 'accumulation_register',
    'commonmodule': 'common_module',
    'общиймодуль': 'common_module',
    'eventsubscription': 'event_subscription',
    'подписканасобытие': 'event_subscription',
}

def sanitize_id(val: str) -> str:
    cleaned = re.sub(r'[^\w.:-]', '_', val)
    return cleaned[:120].strip('_') or 'obj'

class XMLEdtAdapter:
    """Безопасный разбор XML-выгрузок метаданных 1С (Configuration.xml, выгрузки объектов или архивы выгрузки)."""

    def parse(self, content: bytes, configuration_id: str = 'c1_extracted', version: str = '1.0.0') -> Configuration:
        if len(content) > MAX_XML_BYTES:
            raise ValueError(f"Размер XML превышает максимально допустимый ({MAX_XML_BYTES} байт)")

        if content.startswith(b'PK\x03\x04'):
            return self._parse_zip(content, configuration_id, version)

        return self._parse_xml_bytes(content, configuration_id, version)

    def _extract_from_xml(self, content: bytes) -> tuple[list[Object], list[Edge]]:
        lower = content[:2048].lower()
        if b'<!doctype' in lower or b'<!entity' in lower:
            raise ValueError("Небезопасный XML: запрещены объявления DOCTYPE и ENTITY")

        try:
            root = ET.fromstring(content)
        except Exception as e:
            raise ValueError(f"Ошибка парсинга XML метаданных 1С: {e}")

        objects: list[Object] = []
        edges: list[Edge] = []
        seen_ids: set[str] = set()

        for elem in root.iter():
            tag = elem.tag.split('}')[-1].lower()
            if tag in TYPE_MAPPING:
                obj_type = TYPE_MAPPING[tag]
                name = elem.attrib.get('name') or elem.findtext('{*}name') or elem.findtext('name') or elem.attrib.get('uuid') or elem.tag
                obj_id = sanitize_id(f"{obj_type}.{name.lower()}")
                if obj_id in seen_ids:
                    continue
                seen_ids.add(obj_id)
                full_name = f"{tag.capitalize()}.{name}"

                attrs = {}
                for attr_elem in elem.findall('.//{*}Attribute') + elem.findall('.//Attribute'):
                    attr_name = attr_elem.attrib.get('name') or attr_elem.findtext('{*}name')
                    attr_type = attr_elem.findtext('.//{*}Type') or attr_elem.findtext('.//Type')
                    if attr_name:
                        attrs[attr_name] = attr_type or 'Unknown'
                        if attr_type:
                            target_match = re.search(r'(Catalog|Document|InformationRegister|AccumulationRegister|CommonModule)\.([\w]+)', attr_type, re.IGNORECASE)
                            if target_match:
                                target_type = target_match.group(1).lower()
                                target_name = target_match.group(2).lower()
                                target_id = sanitize_id(f"{TYPE_MAPPING.get(target_type, 'catalog')}.{target_name}")
                                if target_id != obj_id:
                                    edges.append(Edge(source=obj_id, target=target_id, type='uses', origin='AttributeType', confidence=0.9))

                objects.append(Object(
                    object_id=obj_id,
                    type=obj_type,
                    full_name=full_name,
                    parent_id=None,
                    attributes=attrs
                ))

        if not objects:
            raise ValueError("В XML-документе не обнаружено поддерживаемых объектов метаданных 1С")

        return objects, edges

    def _parse_xml_bytes(self, content: bytes, configuration_id: str, version: str) -> Configuration:
        objects, edges = self._extract_from_xml(content)
        valid_ids = {o.object_id for o in objects}
        edges = [e for e in edges if e.source in valid_ids and e.target in valid_ids]

        return Configuration(
            schema_version='1.0',
            configuration_id=configuration_id,
            version=version,
            source='1C:XML_EDT',
            synthetic=False,
            objects=objects,
            edges=edges
        )

    def _parse_zip(self, content: bytes, configuration_id: str, version: str) -> Configuration:
        try:
            with ZipFile(io.BytesIO(content)) as z:
                xml_files = [name for name in z.namelist() if name.lower().endswith('.xml') and not name.startswith('__')]
                if not xml_files:
                    raise ValueError("В архиве не найдено XML-файлов метаданных 1С")

                combined_objects = []
                combined_edges = []
                seen_ids = set()

                for name in xml_files[:50]:
                    data = z.read(name)
                    if b'<!doctype' in data[:2048].lower() or b'<!entity' in data[:2048].lower():
                        continue
                    try:
                        objs, edges = self._extract_from_xml(data)
                        for o in objs:
                            if o.object_id not in seen_ids:
                                seen_ids.add(o.object_id)
                                combined_objects.append(o)
                        for e in edges:
                            combined_edges.append(e)
                    except ValueError:
                        continue

                if not combined_objects:
                    raise ValueError("Не удалось извлечь объекты метаданных 1С из XML-файлов архива")

                valid_ids = {o.object_id for o in combined_objects}
                valid_edges = [e for e in combined_edges if e.source in valid_ids and e.target in valid_ids]

                return Configuration(
                    schema_version='1.0',
                    configuration_id=configuration_id,
                    version=version,
                    source='1C:ZIP_XML',
                    synthetic=False,
                    objects=combined_objects,
                    edges=valid_edges
                )
        except BadZipFile:
            raise ValueError("Поврежденный ZIP архив")
