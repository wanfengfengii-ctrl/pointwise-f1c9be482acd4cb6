from dataclasses import dataclass


@dataclass(frozen=True)
class SourceReading:
    reading_id: str
    raw_um: int | None
    origin: str


@dataclass(frozen=True)
class SourcePage:
    page_id: str
    readings: tuple[SourceReading, ...]


@dataclass(frozen=True)
class SourceVolume:
    source_id: str
    pages: tuple[SourcePage, ...]


def _fields(material, *names):
    if not isinstance(material, dict) or material.keys() != set(names):
        raise ValueError(f'expected fields {names!r}')
    return tuple(material[name] for name in names)


def _sequence(material):
    if not isinstance(material, (tuple, list)):
        raise ValueError('pages and readings must be ordered lists or tuples')
    return material


def _normalize_reading(reading):
    if isinstance(reading, SourceReading):
        return reading
    return SourceReading(*_fields(reading, 'reading_id', 'raw_um', 'origin'))


def _normalize_page(page):
    if isinstance(page, SourcePage):
        page_id, readings = page.page_id, page.readings
    else:
        page_id, readings = _fields(page, 'page_id', 'readings')
    return SourcePage(page_id, tuple(_normalize_reading(item) for item in _sequence(readings)))


def normalize_source(material) -> SourceVolume:
    if isinstance(material, SourceVolume):
        source_id, pages = material.source_id, material.pages
    else:
        source_id, pages = _fields(material, 'source_id', 'pages')
    return SourceVolume(source_id, tuple(_normalize_page(page) for page in _sequence(pages)))
