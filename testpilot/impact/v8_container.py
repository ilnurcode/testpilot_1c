"""Парсер бинарных составных файлов 1С:Предприятия (.cf, .cfe, .epf, .erf)."""
import io
import struct
import zlib
from typing import Iterator

BLOCK_HEADER_SIZE = 31

class V8BlockReader:
    """Чтение фрагментированного блока 1C v8."""

    def __init__(self, stream: io.BytesIO):
        self.stream = stream

    def read_block(self, offset: int) -> bytes:
        chunks = []
        current_offset = offset
        while current_offset != 0x7FFFFFFF and current_offset < self.stream.getbuffer().nbytes:
            self.stream.seek(current_offset)
            header = self.stream.read(BLOCK_HEADER_SIZE)
            if len(header) < BLOCK_HEADER_SIZE:
                break
            try:
                # Формат: \r\n{data_len:8x} {block_len:8x} {next_offset:8x} \r\n
                parts = header.strip().split()
                if len(parts) < 3:
                    break
                data_len = int(parts[0], 16)
                next_offset = int(parts[2], 16)
            except (ValueError, IndexError):
                break

            data = self.stream.read(data_len)
            chunks.append(data)
            current_offset = next_offset

        return b''.join(chunks)

def unpack_v8_stream(data: bytes) -> bytes:
    """Распаковка потока (сжатого Deflate без заголовка zlib)."""
    if not data:
        return b''
    try:
        return zlib.decompress(data, -15)
    except zlib.error:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return data

class V8Container:
    """Парсер контейнера 1С (.cf, .cfe, .epf, .erf)."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.stream = io.BytesIO(raw)
        self.reader = V8BlockReader(self.stream)

    def is_valid(self) -> bool:
        if len(self.raw) < BLOCK_HEADER_SIZE:
            return False
        # Первый блок всегда начинается с \r\n
        return self.raw.startswith(b'\r\n') or self.raw.startswith(b'\n')

    def extract_files(self) -> dict[str, bytes]:
        """Извлечение всех файлов из контейнера."""
        if not self.is_valid():
            return {}

        # Корневой блок каталога файлов находится со смещения 0
        catalog_bytes = self.reader.read_block(0)
        if not catalog_bytes or len(catalog_bytes) % 12 != 0:
            return {}

        entries = []
        num_entries = len(catalog_bytes) // 12
        for i in range(num_entries):
            header_offset, data_offset, max_len = struct.unpack('<III', catalog_bytes[i*12:(i+1)*12])
            entries.append((header_offset, data_offset))

        files = {}
        for header_offset, data_offset in entries:
            header_bytes = self.reader.read_block(header_offset)
            # В заголовке содержится имя файла в UTF-16LE
            # Структура заголовка: временные метки (16 байт) + имя файла (UTF-16LE)
            filename = ""
            if len(header_bytes) > 20:
                name_bytes = header_bytes[20:]
                try:
                    # Имя оканчивается нулевым символом UTF-16
                    null_idx = name_bytes.find(b'\x00\x00')
                    if null_idx != -1 and null_idx % 2 == 0:
                        name_bytes = name_bytes[:null_idx]
                    filename = name_bytes.decode('utf-16le', errors='ignore').strip('\x00').strip()
                except Exception:
                    pass

            if not filename:
                filename = f"stream_{header_offset:x}"

            raw_file_data = self.reader.read_block(data_offset)
            file_data = unpack_v8_stream(raw_file_data)
            files[filename] = file_data

        return files
