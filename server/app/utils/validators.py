import re
from decimal import Decimal, InvalidOperation

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_PASSWORD_BYTES = 72

def validate_required_string(value, field_name, minimum_length=1):
    if not isinstance(value, str):
        return f"{field_name} must be a string"
    if len(value.strip()) < minimum_length:
        return f"{field_name} must not be empty"
    return None

def validate_email(value):
    if not isinstance(value, str) or not EMAIL_PATTERN.match(value.strip()):
        return "Invalid email format"
    return None


def validate_password(value, minimum_length=6):
    error = validate_required_string(value, "Password", minimum_length)

    if error:
        return error

    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be {MAX_PASSWORD_BYTES} bytes or fewer"

    return None

def validate_positive_integer(value, field_name, allow_zero=False):
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{field_name} must be an integer"
    if allow_zero and value < 0:
        return f"{field_name} must be zero or greater"
    if not allow_zero and value <= 0:
        return f"{field_name} must be greater than zero"
    return None

def validate_non_negative_number(value, field_name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return f"{field_name} must be a valid number"
    if not number.is_finite():
        return f"{field_name} must be a finite number"
    if number < 0:
        return f"{field_name} must be zero or greater"
    return None
