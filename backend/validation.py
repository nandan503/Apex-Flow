from backend.errors import ValidationError

MAX_TEXT = 200
MAX_NOTES = 500
MAX_WEIGHT_KG = 100_000.0
MAX_QUANTITY = 100_000


def require_nonempty(value, field):
    text = (value or '').strip() if isinstance(value, str) else value
    if not text:
        raise ValidationError(f'{field} is required')
    return text


def bound_text(value, field, max_len=MAX_TEXT, required=False, default=''):
    if value is None:
        if required:
            raise ValidationError(f'{field} is required')
        return default
    text = str(value).strip()
    if required and not text:
        raise ValidationError(f'{field} is required')
    if len(text) > max_len:
        raise ValidationError(f'{field} is too long')
    return text


def parse_weight(value, default=1000.0):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = float(value)
        except (TypeError, ValueError):
            raise ValidationError('weight must be a number')
    if raw <= 0 or raw > MAX_WEIGHT_KG:
        raise ValidationError(f'weight must be between 0 and {int(MAX_WEIGHT_KG)} kg')
    return raw


def parse_quantity(value, default=1):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = int(value)
        except (TypeError, ValueError):
            raise ValidationError('quantity must be an integer')
    if raw < 1 or raw > MAX_QUANTITY:
        raise ValidationError(f'quantity must be between 1 and {MAX_QUANTITY}')
    return raw


def parse_year(value, default=2023):
    if value is None or value == '':
        return default
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValidationError('year must be an integer')
    if year < 1980 or year > 2100:
        raise ValidationError('year is out of range')
    return year


def parse_capacity(value, default=15.0):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = float(value)
        except (TypeError, ValueError):
            raise ValidationError('capacity must be a number')
    if raw <= 0 or raw > 200:
        raise ValidationError('capacity must be between 0 and 200 MT')
    return raw


def parse_experience(value, default=5):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = int(value)
        except (TypeError, ValueError):
            raise ValidationError('experience_years must be an integer')
    if raw < 0 or raw > 60:
        raise ValidationError('experience_years is out of range')
    return raw
