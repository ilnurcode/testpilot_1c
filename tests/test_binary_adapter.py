"""Тесты разбора бинарных расширений (.cfe) и обработок (.epf)."""
import io
import struct
import zlib
import pytest
from testpilot.impact.v8_container import V8Container
from testpilot.impact.binary_adapter import Binary1CAdapter

def build_mock_v8_container(files: dict[str, bytes]) -> bytes:
    """Создание тестового бинарного контейнера v8."""
    # Собираем данные файлов
    encoded_files = []
    for name, data in files.items():
        compressed = zlib.compress(data)[2:-4]  # raw deflate
        encoded_files.append((name, compressed))

    out = io.BytesIO()
    
    # Резервируем место под заголовок каталога
    # Заголовок блока: \r\n{data_len:8x} {block_len:8x} {next:8x} \r\n (31 байт)
    catalog_entries = []
    
    # Начинаем запись после каталога
    # Размер каталога: 31 байт заголовок + len(files)*12 байт
    catalog_data_len = len(encoded_files) * 12
    catalog_block_len = 512
    
    # Пишем пустой заголовок каталога
    out.seek(0)
    out.write(b'\r\n00000000 00000200 7fffffff \r\n' + b'\x00' * 512)

    current_offset = out.tell()

    for name, compressed_data in encoded_files:
        # Пишем заголовок файла (содержит имя в utf-16le)
        header_offset = current_offset
        name_utf16 = name.encode('utf-16le') + b'\x00\x00'
        h_data = b'\x00' * 20 + name_utf16
        
        h_block_hdr = f"\r\n{len(h_data):08x} {len(h_data):08x} 7fffffff \r\n".encode('ascii')
        out.write(h_block_hdr)
        out.write(h_data)
        current_offset = out.tell()

        # Пишем данные файла
        data_offset = current_offset
        d_block_hdr = f"\r\n{len(compressed_data):08x} {len(compressed_data):08x} 7fffffff \r\n".encode('ascii')
        out.write(d_block_hdr)
        out.write(compressed_data)
        current_offset = out.tell()

        catalog_entries.append((header_offset, data_offset, 0x7FFFFFFF))

    # Теперь перезаписываем каталог
    out.seek(0)
    cat_data = b''.join(struct.pack('<III', h, d, m) for h, d, m in catalog_entries)
    cat_header = f"\r\n{len(cat_data):08x} 00000200 7fffffff \r\n".encode('ascii')
    out.write(cat_header)
    out.write(cat_data)

    return out.getvalue()

def test_v8_container_unpacking():
    payload = {
        'Module.bsl': 'Процедура Тест()\nКонецПроцедуры'.encode('utf-8'),
        'root': b'metadata-root-bytes'
    }
    raw = build_mock_v8_container(payload)
    container = V8Container(raw)
    assert container.is_valid()
    files = container.extract_files()
    assert 'Module.bsl' in files
    assert 'Процедура' in files['Module.bsl'].decode('utf-8')

def test_binary_adapter_parses_cfe_extension():
    bsl_code = """
&Вместо("РассчитатьСкидку")
Процедура Расш_РассчитатьСкидку()
    Товар = Справочники.Номенклатура.ПустаяСсылка();
    Заказ = Документы.ЗаказКлиента.ПустаяСсылка();
КонецПроцедуры
""".encode('utf-8')

    raw = build_mock_v8_container({'ExtensionModule': bsl_code})
    adapter = Binary1CAdapter()
    cfg = adapter.parse(raw, 'MyDiscountExtension.cfe', configuration_id='trade_ext', version='1.0.0')

    assert cfg.configuration_id == 'trade_ext'
    assert cfg.version == '1.0.0'
    assert cfg.source == '1C:CFE'
    
    full_names = {o.full_name for o in cfg.objects}
    assert 'Расширение.MyDiscountExtension' in full_names
    assert 'Метод.РассчитатьСкидку' in full_names
    assert 'Справочник.Номенклатура' in full_names
    assert 'Документ.ЗаказКлиента' in full_names

    # Проверка связей: вызовы методов (&Вместо) и использование объектов
    assert len(cfg.edges) >= 3
    origins = {e.origin for e in cfg.edges}
    assert '&Вместо' in origins
    assert 'BSL_Call' in origins
