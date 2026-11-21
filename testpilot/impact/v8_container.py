"""Парсер бинарных составных файлов 1С:Предприятия (.cf, .cfe, .epf, .erf).

Разбор недоверенного файла ограничен по числу блоков в цепочке, размеру блока,
объёму распакованных данных и числу записей каталога. Повреждённая ссылка или
цикл смещений даёт явную ошибку вместо зависания, молчаливого пустого
результата или неограниченной распаковки.
"""
import io
import struct
import zlib

BLOCK_HEADER_SIZE = 31
END_OF_CHAIN = 0x7FFFFFFF

# Пределы разбора недоверенного контейнера.
MAX_BLOCKS_PER_CHAIN = 4096
MAX_BLOCK_BYTES = 16 * 1024 * 1024
MAX_CHAIN_BYTES = 32 * 1024 * 1024
MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 20000


class V8ContainerError(ValueError):
    """Контейнер повреждён или использует неподдерживаемую структуру."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class V8LimitsExceeded(V8ContainerError):
    """Разбор остановлен предохранителем: нужен внешний адаптер или агент 1С."""


class V8Budget:
    """Общий бюджет разбора: один контейнер с вложенными потоками."""

    def __init__(self, total_bytes: int | None = None, entries: int | None = None) -> None:
        self.total_bytes = MAX_TOTAL_BYTES if total_bytes is None else total_bytes
        self.max_entries = MAX_ENTRIES if entries is None else entries
        self.bytes_used = 0
        self.entries_used = 0

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.total_bytes - self.bytes_used)

    def spend_bytes(self, count: int) -> None:
        if count < 0:
            raise V8ContainerError("negative_size", "отрицательный размер потока")
        self.bytes_used += count
        if self.bytes_used > self.total_bytes:
            raise V8LimitsExceeded(
                "total_bytes", f"распакованных данных больше {self.total_bytes} байт"
            )

    def spend_entry(self) -> None:
        self.entries_used += 1
        if self.entries_used > self.max_entries:
            raise V8LimitsExceeded("entries", f"записей каталога больше {self.max_entries}")


class V8BlockReader:
    """Чтение фрагментированного блока 1C v8 с ограничениями на цепочку."""

    def __init__(self, stream: io.BytesIO, budget: V8Budget | None = None):
        self.stream = stream
        self.budget = budget or V8Budget()
        # Повторные ссылки на одно смещение читаются один раз: иначе каталог из
        # многих записей на один блок многократно копировал бы те же байты.
        self._cache: dict[int, bytes] = {}

    def read_block(self, offset: int) -> bytes:
        cached = self._cache.get(offset)
        if cached is not None:
            return cached
        chunks: list[bytes] = []
        visited: set[int] = set()
        current = offset
        chain_bytes = 0
        size = self.stream.getbuffer().nbytes
        while current != END_OF_CHAIN:
            if current in visited:
                raise V8ContainerError("block_cycle", f"цепочка блоков зациклилась на смещении {current}")
            visited.add(current)
            if len(visited) > MAX_BLOCKS_PER_CHAIN:
                raise V8LimitsExceeded("blocks", f"в цепочке больше {MAX_BLOCKS_PER_CHAIN} блоков")
            if current < 0 or current >= size:
                raise V8ContainerError("block_offset_out_of_range", f"смещение блока {current} вне файла")
            self.stream.seek(current)
            header = self.stream.read(BLOCK_HEADER_SIZE)
            if len(header) < BLOCK_HEADER_SIZE:
                raise V8ContainerError(
                    "truncated_block_header", f"заголовок блока по смещению {current} обрезан"
                )
            # Формат: \r\n{data_len:8x} {block_len:8x} {next_offset:8x} \r\n
            parts = header.strip().split()
            if len(parts) < 3:
                raise V8ContainerError(
                    "invalid_block_header", f"заголовок блока по смещению {current} не разобран"
                )
            try:
                data_len = int(parts[0], 16)
                next_offset = int(parts[2], 16)
            except ValueError as error:
                raise V8ContainerError(
                    "invalid_block_header", f"заголовок блока по смещению {current} содержит не hex"
                ) from error
            if data_len > MAX_BLOCK_BYTES:
                raise V8LimitsExceeded("block_bytes", f"блок больше {MAX_BLOCK_BYTES} байт")
            if data_len > size - self.stream.tell():
                raise V8ContainerError(
                    "truncated_block", f"блок по смещению {current} обрезан: нет {data_len} байт данных"
                )
            data = self.stream.read(data_len)
            self.budget.spend_bytes(len(data))
            chain_bytes += len(data)
            if chain_bytes > MAX_CHAIN_BYTES:
                raise V8LimitsExceeded("chain_bytes", f"цепочка блоков больше {MAX_CHAIN_BYTES} байт")
            chunks.append(data)
            current = next_offset
        block = b"".join(chunks)
        self._cache[offset] = block
        return block


def unpack_v8_stream(data: bytes, max_bytes: int | None = None) -> bytes:
    """Распаковка потока Deflate без заголовка zlib с жёстким пределом выхода.

    Несжатые данные возвращаются как есть. Превышение предела — ошибка, а не
    безграничное выделение памяти; неполный поток тоже считается ошибкой.
    """
    if not data:
        return b""
    limit = MAX_STREAM_BYTES if max_bytes is None else max_bytes
    for wbits in (-15, 15):
        try:
            inflater = zlib.decompressobj(wbits)
            chunk = inflater.decompress(data, limit + 1)
        except zlib.error:
            continue
        if len(chunk) > limit:
            raise V8LimitsExceeded("stream_bytes", f"распакованный поток больше {limit} байт")
        rest = inflater.flush(max(1, limit + 1 - len(chunk)))
        if len(chunk) + len(rest) > limit:
            raise V8LimitsExceeded("stream_bytes", f"распакованный поток больше {limit} байт")
        if not inflater.eof:
            raise V8ContainerError("incomplete_stream", "поток Deflate обрезан или не распознан")
        return chunk + rest
    # Несжатый поток: размер проверяется до возврата, чтобы бюджет не обходился.
    if len(data) > limit:
        raise V8LimitsExceeded("stream_bytes", f"поток больше {limit} байт")
    return data


class V8Container:
    """Парсер контейнера 1С (.cf, .cfe, .epf, .erf).

    Поддерживаются оба варианта: служебная фикстура без файлового заголовка и
    настоящий файл платформы (16-байтовый заголовок 0xFFFFFF7F перед каталогом).
    """

    REAL_HEADER = b"\xff\xff\xff\x7f"
    MAX_DEPTH = 3

    def __init__(self, raw: bytes, depth: int = 0, budget: V8Budget | None = None):
        self.raw = raw
        self.stream = io.BytesIO(raw)
        self.budget = budget or V8Budget()
        self.reader = V8BlockReader(self.stream, self.budget)
        self.depth = depth

    @property
    def toc_offset(self) -> int:
        return 16 if self.raw.startswith(self.REAL_HEADER) else 0

    def is_valid(self) -> bool:
        if len(self.raw) < BLOCK_HEADER_SIZE:
            return False
        # Настоящий файл платформы: 16-байтовый заголовок, затем блок каталога.
        if self.raw.startswith(self.REAL_HEADER):
            return self.raw[16:18] == b"\r\n"
        return self.raw.startswith(b"\r\n") or self.raw.startswith(b"\n")

    def _block_header_parses(self, offset: int) -> bool:
        """Похоже ли содержимое на блок контейнера: нужно, чтобы не путать текст с вложенным файлом."""
        window = self.raw[offset:offset + BLOCK_HEADER_SIZE]
        if len(window) < BLOCK_HEADER_SIZE:
            return False
        parts = window.strip().split()
        if len(parts) < 3:
            return False
        try:
            data_len = int(parts[0], 16)
            int(parts[2], 16)
        except ValueError:
            return False
        return 0 <= data_len <= len(self.raw) - offset - BLOCK_HEADER_SIZE

    def _catalog_entries(self) -> list[tuple[int, int]]:
        catalog = self.reader.read_block(self.toc_offset)
        if not catalog:
            return []
        if len(catalog) % 12:
            raise V8ContainerError("invalid_catalog", "размер каталога не кратен 12 байтам")
        count = len(catalog) // 12
        if count > self.budget.max_entries:
            raise V8LimitsExceeded("entries", f"записей каталога больше {self.budget.max_entries}")
        return [
            (struct.unpack("<III", catalog[i * 12:(i + 1) * 12])[0],
             struct.unpack("<III", catalog[i * 12:(i + 1) * 12])[1])
            for i in range(count)
        ]

    @staticmethod
    def _entry_name(header: bytes) -> str:
        """Имя записи: после 20-байтового служебного префикса идёт UTF-16LE."""
        for start in (20, 0):
            chunk = header[start:start + 512]
            text = chunk.decode("utf-16le", errors="ignore")
            text = text.split("\x00", 1)[0].strip()
            if text and all(ord(c) >= 32 for c in text):
                return text[:200]
        return ""

    def _try_nested(self) -> dict[str, bytes] | None:
        """Разобрать поток как вложенный контейнер.

        None означает «это обычный поток»: структура не подтверждена. Если
        структура подтверждена (заголовок, каталог и заголовки всех записей
        разбираются), любая ошибка внутри — повреждение контейнера, а не повод
        вернуть сырые байты за успешный разбор.
        """
        if not (self.is_valid() and self._block_header_parses(self.toc_offset)):
            return None
        try:
            entries = self._catalog_entries()
        except V8LimitsExceeded:
            raise
        except V8ContainerError:
            return None
        if not entries or not all(self._block_header_parses(header) for header, _ in entries):
            return None
        return self.extract_files()

    def extract_files(self) -> dict[str, bytes]:
        """Извлечение всех файлов, включая вложенные контейнеры платформы."""
        if not self.is_valid():
            return {}
        files: dict[str, bytes] = {}
        for index, (header_offset, data_offset) in enumerate(self._catalog_entries()):
            self.budget.spend_entry()
            header = self.reader.read_block(header_offset)
            packed = self.reader.read_block(data_offset)
            data = unpack_v8_stream(packed, max_bytes=min(MAX_STREAM_BYTES, self.budget.remaining_bytes))
            # Байты блоков уже учтены при чтении; здесь — только рост при распаковке.
            self.budget.spend_bytes(max(0, len(data) - len(packed)))
            name = self._entry_name(header) or f"stream{index}"
            nested = V8Container(data, self.depth + 1, self.budget)
            if self.depth < self.MAX_DEPTH:
                # Подтверждённый вложенный контейнер: его повреждение — ошибка,
                # а не повод выдать сырые байты за успешный разбор.
                inner = nested._try_nested()
                if inner:
                    files.update({
                        f"{name}/{key}" if name != f"stream{index}" else key: value
                        for key, value in inner.items()
                    })
                    continue
            files[name] = data
        return files
