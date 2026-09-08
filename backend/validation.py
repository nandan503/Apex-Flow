import math
from decimal import Decimal, ROUND_HALF_UP

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
    if isinstance(value, (list, dict, bool)):
        raise ValidationError(f'{field} is invalid')
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
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('weight must be a number')
    if not math.isfinite(raw) or isinstance(value, bool) or raw <= 0 or raw > MAX_WEIGHT_KG:
        raise ValidationError(f'weight must be between 0 and {int(MAX_WEIGHT_KG)} kg')
    normalized = Decimal(str(raw)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if normalized <= 0:
        raise ValidationError('weight must be at least 0.01 kg')
    return normalized


def parse_quantity(value, default=1):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('quantity must be an integer')
    if isinstance(value, bool) or (isinstance(value, float) and value != raw) or raw < 1 or raw > MAX_QUANTITY:
        raise ValidationError(f'quantity must be between 1 and {MAX_QUANTITY}')
    return raw


def parse_year(value, default=2023):
    if value is None or value == '':
        return default
    try:
        year = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError('year must be an integer')
    if isinstance(value, bool) or (isinstance(value, float) and value != year) or year < 1980 or year > 2100:
        raise ValidationError('year is out of range')
    return year


def parse_capacity(value, default=15.0):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('capacity must be a number')
    if not math.isfinite(raw) or isinstance(value, bool) or raw <= 0 or raw > 200:
        raise ValidationError('capacity must be between 0 and 200 MT')
    return raw


def parse_experience(value, default=5):
    if value is None or value == '':
        raw = default
    else:
        try:
            raw = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('experience_years must be an integer')
    if isinstance(value, bool) or (isinstance(value, float) and value != raw) or raw < 0 or raw > 60:
        raise ValidationError('experience_years is out of range')
    return raw
