"""Пределы разбора контейнера 1С: недоверенный файл не должен вешать и съедать память.

Все контейнеры здесь синтетические: проверяются только пути ошибок и пределы.
Реальное извлечение модулей BSL из файлов платформы эти тесты не подтверждают.
"""
import struct
import time
import zlib

import pytest

from testpilot.impact import v8_container as v8
from testpilot.impact.binary_adapter import Binary1CAdapter
from testpilot.impact.v8_container import (
    END_OF_CHAIN,
    V8Container,
    V8ContainerError,
    V8LimitsExceeded,
    unpack_v8_stream,
)


def block(payload: bytes, next_offset: int) -> bytes:
    """Один блок контейнера: заголовок ровно 31 байт плюс данные."""
    return f"\r\n{len(payload):08x} {len(payload):08x} {next_offset:08x} \r\n".encode() + payload


def catalog(payload: bytes) -> bytes:
    return block(payload, END_OF_CHAIN)


def container_with_streams(streams: list[bytes]) -> bytes:
    """Каталог плюс потоки, без сжатия: такие данные возвращаются как есть."""
    base = 31 + 12 * len(streams)  # длина блока каталога не зависит от содержимого
    header = b"\x00" * 20 + "stream".encode("utf-16le") + b"\x00\x00"
    body = bytearray()
    entries: list[tuple[int, int]] = []
    for stream in streams:
        header_offset = base + len(body)
        body += block(header, END_OF_CHAIN)
        data_offset = base + len(body)
        body += block(stream, END_OF_CHAIN)
        entries.append((header_offset, data_offset))
    entries_data = b"".join(struct.pack("<III", h, d, END_OF_CHAIN) for h, d in entries)
    return catalog(entries_data) + bytes(body)


def test_block_cycle_is_reported_instead_of_hanging():
    raw = block(b"\x00" * 12, 0)  # каталог ссылается сам на себя
    assert V8Container(raw).is_valid()
    started = time.monotonic()
    with pytest.raises(V8ContainerError) as error:
        V8Container(raw).extract_files()
    assert error.value.code == "block_cycle"
    assert time.monotonic() - started < 2.0


def test_block_chain_length_is_limited(monkeypatch):
    monkeypatch.setattr(v8, "MAX_BLOCKS_PER_CHAIN", 8)
    size = 31 + 12
    count = 12
    raw = b"".join(
        block(b"\x00" * 12, size * (index + 1) if index + 1 < count else END_OF_CHAIN)
        for index in range(count)
    )
    with pytest.raises(V8LimitsExceeded) as error:
        V8Container(raw).extract_files()
    assert error.value.code == "blocks"


def test_oversized_and_truncated_blocks_are_rejected(monkeypatch):
    truncated = f"\r\n{100:08x} {100:08x} {END_OF_CHAIN:08x} \r\n".encode() + b"\x00" * 10
    with pytest.raises(V8ContainerError) as short:
        V8Container(truncated).extract_files()
    assert short.value.code == "truncated_block"

    monkeypatch.setattr(v8, "MAX_BLOCK_BYTES", 64)
    with pytest.raises(V8LimitsExceeded) as oversized:
        V8Container(block(b"\x00" * 100, END_OF_CHAIN)).extract_files()
    assert oversized.value.code == "block_bytes"


def test_chain_byte_budget_is_limited(monkeypatch):
    monkeypatch.setattr(v8, "MAX_CHAIN_BYTES", 32)
    first = block(b"\x00" * 24, len(block(b"\x00" * 24, END_OF_CHAIN)))
    raw = first + block(b"\x00" * 24, END_OF_CHAIN)
    with pytest.raises(V8LimitsExceeded) as error:
        V8Container(raw).extract_files()
    assert error.value.code == "chain_bytes"


def test_deflate_bomb_is_stopped_before_memory_grows():
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = comp.compress(b"\x00" * (8 * 1024 * 1024)) + comp.flush()
    assert len(packed) < 64 * 1024
    started = time.monotonic()
    with pytest.raises(V8LimitsExceeded) as error:
        unpack_v8_stream(packed, max_bytes=1024 * 1024)
    assert error.value.code == "stream_bytes"
    assert time.monotonic() - started < 5.0


def test_incomplete_deflate_stream_is_an_explicit_error():
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = comp.compress("Процедура Тест()\nКонецПроцедуры".encode("utf-8")) + comp.flush()
    with pytest.raises(V8ContainerError) as error:
        unpack_v8_stream(packed[:-4])
    assert error.value.code == "incomplete_stream"


def test_catalog_entry_count_is_limited(monkeypatch):
    monkeypatch.setattr(v8, "MAX_ENTRIES", 3)
    entries = b"".join(struct.pack("<III", 40, 80, END_OF_CHAIN) for _ in range(4))
    with pytest.raises(V8LimitsExceeded) as error:
        V8Container(catalog(entries)).extract_files()
    assert error.value.code == "entries"


def test_total_unpacked_bytes_budget_is_limited(monkeypatch):
    budget = v8.V8Budget(total_bytes=10, entries=5)
    budget.spend_bytes(9)
    with pytest.raises(V8LimitsExceeded) as overflow:
        budget.spend_bytes(2)
    assert overflow.value.code == "total_bytes"

    monkeypatch.setattr(v8, "MAX_TOTAL_BYTES", 100)
    raw = container_with_streams([b"a" * 60, b"b" * 60])
    with pytest.raises(V8LimitsExceeded) as error:
        V8Container(raw).extract_files()
    # Либо остаток бюджета ограничивает распаковку потока, либо общий счётчик.
    assert error.value.code in {"total_bytes", "stream_bytes"}


def test_adapter_reports_cycle_as_value_error():
    with pytest.raises(ValueError):
        Binary1CAdapter().parse_with_report(block(b"\x00" * 12, 0), "bomb.cfe")


def test_adapter_reports_limits_as_value_error_for_requires_adapter_path(monkeypatch):
    monkeypatch.setattr(v8, "MAX_TOTAL_BYTES", 1024)
    raw = container_with_streams([b"a" * 2048, b"b" * 2048])
    with pytest.raises(ValueError) as limit:
        Binary1CAdapter().parse_with_report(raw, "bomb2.cfe")
    assert "предел" in str(limit.value).lower()


def test_valid_synthetic_container_still_parses():
    """Ограничения не ломают корректный контейнер: положительный контроль."""
    from test_binary_adapter import build_mock_v8_container

    bsl = "Процедура Тест()\n\tСообщить(\"ok\");\nКонецПроцедуры".encode("utf-8")
    raw = build_mock_v8_container({"Module.bsl": bsl})
    model, report = Binary1CAdapter().parse_with_report(raw, "Ext.cfe")
    assert report.container_valid is True
    assert report.bsl_modules == ("Module.bsl",)
    assert report.objects >= 1


def test_uncompressed_fallback_respects_the_byte_limit():
    """Несжатый поток не обходит бюджет: размер проверяется до возврата."""
    with pytest.raises(V8LimitsExceeded) as error:
        unpack_v8_stream(b"plain-bytes" * 200, max_bytes=1024)
    assert error.value.code == "stream_bytes"


def test_corrupted_confirmed_nested_container_is_an_error_not_raw_bytes(tmp_path):
    """Подтверждённый вложенный контейнер с битой ссылкой не выдаётся за сырой поток."""
    header_payload = b"\x00" * 20 + "Модуль".encode("utf-16le") + b"\x00\x00"
    inner = catalog(struct.pack("<III", 43, 999999, END_OF_CHAIN)) + block(header_payload, END_OF_CHAIN)
    assert V8Container(inner)._try_nested is not None
    outer = container_with_streams([inner])

    with pytest.raises(V8ContainerError) as error:
        V8Container(outer).extract_files()
    assert error.value.code == "block_offset_out_of_range"


def test_repeated_offsets_are_read_once_and_stay_within_budget(monkeypatch):
    """Много записей на один блок не должны многократно копировать те же байты."""
    count = 40
    payload = b"module-bytes-" * 40
    header_payload = b"\x00" * 20 + "Модуль".encode("utf-16le") + b"\x00\x00"
    base = 31 + 12 * count
    header_block = block(header_payload, END_OF_CHAIN)
    entries = struct.pack("<III", base, base + len(header_block), END_OF_CHAIN) * count
    raw = catalog(entries) + header_block + block(payload, END_OF_CHAIN)
    # Бюджета хватает ровно на одно чтение каждого блока с запасом на поток.
    monkeypatch.setattr(v8, "MAX_TOTAL_BYTES", 12 * count + len(header_payload) + 2 * len(payload) + 2048)

    started = time.monotonic()
    files = V8Container(raw).extract_files()
    # Одно имя на все записи схлопывается в один ключ; важно, что прочитано один раз.
    assert list(files.values()) == [payload]
    assert time.monotonic() - started < 2.0
