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
    """Парсер контейнера 1С (.cf, .cfe, .epf, .erf).

    Поддерживаются оба варианта: служебная фикстура без файлового заголовка и
    настоящий файл платформы (16-байтовый заголовок 0xFFFFFF7F перед каталогом).
    """

    REAL_HEADER = b'\xff\xff\xff\x7f'
    MAX_DEPTH = 3

    def __init__(self, raw: bytes, depth: int = 0):
        self.raw = raw
        self.stream = io.BytesIO(raw)
        self.reader = V8BlockReader(self.stream)
        self.depth = depth

    @property
    def toc_offset(self) -> int:
        return 16 if self.raw.startswith(self.REAL_HEADER) else 0

    def is_valid(self) -> bool:
        if len(self.raw) < BLOCK_HEADER_SIZE:
            return False
        # Настоящий файл платформы: 16-байтовый заголовок, затем блок каталога.
        if self.raw.startswith(self.REAL_HEADER):
            return self.raw[16:18] == b'\r\n'
        return self.raw.startswith(b'\r\n') or self.raw.startswith(b'\n')

    def _catalog_entries(self) -> list[tuple[int, int]]:
        catalog = self.reader.read_block(self.toc_offset)
        if not catalog or len(catalog) % 12:
            return []
        return [(struct.unpack('<III', catalog[i * 12:(i + 1) * 12])[0],
                 struct.unpack('<III', catalog[i * 12:(i + 1) * 12])[1])
                for i in range(len(catalog) // 12)]

    @staticmethod
    def _entry_name(header: bytes) -> str:
        """Имя записи: после 20-байтового служебного префикса идёт UTF-16LE."""
        for start in (20, 0):
            chunk = header[start:start + 512]
            text = chunk.decode('utf-16le', errors='ignore')
            text = text.split('\x00', 1)[0].strip()
            if text and all(ord(c) >= 32 for c in text):
                return text[:200]
        return ''

    def extract_files(self) -> dict[str, bytes]:
        """Извлечение всех файлов, включая вложенные контейнеры платформы."""
        if not self.is_valid():
            return {}
        files: dict[str, bytes] = {}
        for index, (header_offset, data_offset) in enumerate(self._catalog_entries()):
            header = self.reader.read_block(header_offset)
            data = unpack_v8_stream(self.reader.read_block(data_offset))
            name = self._entry_name(header) or f'stream{index}'
            nested = V8Container(data, self.depth + 1)
            if self.depth < self.MAX_DEPTH and nested.is_valid():
                inner = nested.extract_files()
                if inner:
                    files.update({f'{name}/{k}' if name != f'stream{index}' else k: v
                                  for k, v in inner.items()})
                    continue
            files[name] = data
        return files
