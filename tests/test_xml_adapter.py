"""Тесты адаптера XML/EDT выгрузок 1С."""
import io
import pytest
from zipfile import ZipFile
from testpilot.impact.xml_adapter import XMLEdtAdapter

SAMPLE_CATALOG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
    <Catalog name="Номенклатура">
        <name>Номенклатура</name>
        <Attribute name="Артикул">
            <name>Артикул</name>
            <Type>xs:string</Type>
        </Attribute>
        <Attribute name="ОсновнойПоставщик">
            <name>ОсновнойПоставщик</name>
            <Type>Catalog.Контрагенты</Type>
        </Attribute>
    </Catalog>
</MetaDataObject>
""".encode("utf-8")

SAMPLE_MULTI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ConfigDumpInfo xmlns="http://v8.1c.ru/8.3/MDClasses">
    <Catalog name="Контрагенты">
        <name>Контрагенты</name>
    </Catalog>
    <Catalog name="Номенклатура">
        <name>Номенклатура</name>
        <Attribute name="Поставщик">
            <Type>Catalog.Контрагенты</Type>
        </Attribute>
    </Catalog>
    <Document name="ЗаказПокупателя">
        <name>ЗаказПокупателя</name>
        <Attribute name="Клиент">
            <Type>Catalog.Контрагенты</Type>
        </Attribute>
    </Document>
</ConfigDumpInfo>
""".encode("utf-8")

def test_xml_adapter_parses_single_object():
    adapter = XMLEdtAdapter()
    cfg = adapter.parse(SAMPLE_CATALOG_XML, configuration_id='trade', version='2.0.1')
    assert cfg.configuration_id == 'trade'
    assert cfg.version == '2.0.1'
    assert len(cfg.objects) == 1
    assert cfg.objects[0].type == 'catalog'
    assert cfg.objects[0].full_name == 'Catalog.Номенклатура'
    assert 'Артикул' in cfg.objects[0].attributes

def test_xml_adapter_parses_multi_objects_and_edges():
    adapter = XMLEdtAdapter()
    cfg = adapter.parse(SAMPLE_MULTI_XML, configuration_id='trade', version='2.0.1')
    assert len(cfg.objects) == 3
    names = {o.full_name for o in cfg.objects}
    assert 'Catalog.Контрагенты' in names
    assert 'Catalog.Номенклатура' in names
    assert 'Document.ЗаказПокупателя' in names
    # Связи: Номенклатура -> Контрагенты, ЗаказПокупателя -> Контрагенты
    assert len(cfg.edges) >= 1
    targets = {e.target for e in cfg.edges}
    assert 'catalog.контрагенты' in targets

def test_xml_adapter_rejects_xxe_and_doctype():
    adapter = XMLEdtAdapter()
    bad_xml = b'<!DOCTYPE test [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]><Catalog name="Test"/>'
    with pytest.raises(ValueError, match="запрещены объявления DOCTYPE"):
        adapter.parse(bad_xml)

def test_xml_adapter_rejects_oversized():
    adapter = XMLEdtAdapter()
    huge = b'<Catalog name="Test"/>' + b' ' * (17 * 1024 * 1024)
    with pytest.raises(ValueError, match="превышает максимально допустимый"):
        adapter.parse(huge)

def test_xml_adapter_parses_zip():
    buf = io.BytesIO()
    with ZipFile(buf, 'w') as z:
        z.writestr('Catalogs/Catalog.Номенклатура.xml', SAMPLE_CATALOG_XML)
        z.writestr('Catalogs/Catalog.Контрагенты.xml', '<Catalog name="Контрагенты"/>'.encode('utf-8'))
    data = buf.getvalue()

    adapter = XMLEdtAdapter()
    cfg = adapter.parse(data, configuration_id='zip_trade', version='1.0')
    assert cfg.configuration_id == 'zip_trade'
    assert len(cfg.objects) == 2
