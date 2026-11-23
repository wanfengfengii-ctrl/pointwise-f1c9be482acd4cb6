import json
from dataclasses import dataclass
from hashlib import sha256

from intake.record import SourcePage


@dataclass(frozen=True)
class ArchivedReading:
    reading_id: str
    origin: str
    raw_um: int | None
    value_um: int | None


@dataclass(frozen=True)
class ArchivedPage:
    page_index: int
    source_page: str
    readings: tuple[ArchivedReading, ...]


def make_initial_page(page_index: int, source_page: SourcePage) -> ArchivedPage:
    return ArchivedPage(
        page_index,
        source_page.page_id,
        tuple(
            ArchivedReading(item.reading_id, item.origin, item.raw_um, item.raw_um)
            for item in source_page.readings
        ),
    )


def _encode_value(value):
    return None if value is None else hex(value)


def _decode_value(value):
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError('expected hexadecimal integer or null')
    number = int(value, 16)
    if hex(number) != value:
        raise ValueError('noncanonical hexadecimal integer')
    return number


def encode_page(page: ArchivedPage) -> bytes:
    document = {
        'page_index': page.page_index,
        'source_page': page.source_page,
        'readings': [
            [item.reading_id, item.origin, _encode_value(item.raw_um), _encode_value(item.value_um)]
            for item in page.readings
        ],
    }
    return json.dumps(document, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')


def decode_page(payload: bytes) -> ArchivedPage:
    document = json.loads(payload)
    if not isinstance(document, dict) or document.keys() != {'page_index', 'source_page', 'readings'}:
        raise ValueError('invalid payload page fields')
    index, source_page, rows = document['page_index'], document['source_page'], document['readings']
    if type(index) is not int or index < 0 or type(source_page) is not str or not source_page:
        raise ValueError('invalid payload page identity')
    if not isinstance(rows, list) or not rows:
        raise ValueError('invalid payload readings')
    readings = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError('invalid payload reading fields')
        reading_id, origin, raw, value = row
        if type(reading_id) is not str or not reading_id or type(origin) is not str or not origin:
            raise ValueError('invalid payload reading identity')
        raw_um, value_um = _decode_value(raw), _decode_value(value)
        if (raw_um is None) != (value_um is None):
            raise ValueError('payload missingness is inconsistent')
        readings.append(ArchivedReading(reading_id, origin, raw_um, value_um))
    return ArchivedPage(index, source_page, tuple(readings))


def payload_fingerprint(page: ArchivedPage) -> str:
    return sha256(encode_page(page)).hexdigest()
