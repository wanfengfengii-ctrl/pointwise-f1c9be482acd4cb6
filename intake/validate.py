from intake.record import SourceVolume, normalize_source


class SourceValidationError(ValueError):
    pass


def _check_text(value, field):
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise SourceValidationError(f'{field} must be nonempty text without NUL')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise SourceValidationError(f'{field} must be valid Unicode text') from exc


def validate_source(material) -> SourceVolume:
    try:
        source = normalize_source(material)
    except ValueError as exc:
        raise SourceValidationError(str(exc)) from exc
    _check_text(source.source_id, 'source_id')
    if not source.pages:
        raise SourceValidationError('source must contain a page')
    page_ids = set()
    reading_ids = set()
    for page in source.pages:
        _check_text(page.page_id, 'page_id')
        if page.page_id in page_ids or not page.readings:
            raise SourceValidationError('source page must be unique and nonempty')
        page_ids.add(page.page_id)
        for reading in page.readings:
            _check_text(reading.reading_id, 'reading_id')
            _check_text(reading.origin, 'origin')
            if reading.reading_id in reading_ids:
                raise SourceValidationError(f'duplicate reading {reading.reading_id}')
            if reading.raw_um is not None and type(reading.raw_um) is not int:
                raise SourceValidationError('raw_um must be an integer or null')
            reading_ids.add(reading.reading_id)
    return source
