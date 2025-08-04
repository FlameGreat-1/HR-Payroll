from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()


@register.filter
def mul(value, arg):
    """
    Multiply two values
    Usage: {{ value|mul:5 }}
    """
    try:
        if value is None or arg is None:
            return 0
        return float(value) * float(arg)
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def div(value, arg):
    """
    Divide two values
    Usage: {{ value|div:5 }}
    """
    try:
        if value is None or arg is None:
            return 0
        arg_float = float(arg)
        if arg_float == 0:
            return 0
        return float(value) / arg_float
    except (ValueError, TypeError, InvalidOperation, ZeroDivisionError):
        return 0


@register.filter
def sub(value, arg):
    """
    Subtract two values
    Usage: {{ value|sub:5 }}
    """
    try:
        if value is None or arg is None:
            return 0
        return float(value) - float(arg)
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def add_custom(value, arg):
    """
    Add two values (custom addition filter)
    Usage: {{ value|add_custom:5 }}
    """
    try:
        if value is None or arg is None:
            return 0
        return float(value) + float(arg)
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def percentage(value, total):
    """
    Calculate percentage
    Usage: {{ value|percentage:total }}
    """
    try:
        if value is None or total is None:
            return 0
        total_float = float(total)
        if total_float == 0:
            return 0
        return (float(value) / total_float) * 100
    except (ValueError, TypeError, InvalidOperation, ZeroDivisionError):
        return 0


@register.filter
def currency(value):
    """
    Format number as currency
    Usage: {{ value|currency }}
    """
    try:
        if value is None:
            return "$0"
        return f"${float(value):,.2f}"
    except (ValueError, TypeError, InvalidOperation):
        return "$0"


@register.filter
def abs_value(value):
    """
    Get absolute value
    Usage: {{ value|abs_value }}
    """
    try:
        if value is None:
            return 0
        return abs(float(value))
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def round_to(value, decimals):
    """
    Round to specified decimal places
    Usage: {{ value|round_to:2 }}
    """
    try:
        if value is None:
            return 0
        return round(float(value), int(decimals))
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def max_value(value, max_val):
    """
    Return maximum of two values
    Usage: {{ value|max_value:100 }}
    """
    try:
        if value is None:
            return float(max_val) if max_val is not None else 0
        if max_val is None:
            return float(value)
        return max(float(value), float(max_val))
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def min_value(value, min_val):
    """
    Return minimum of two values
    Usage: {{ value|min_value:0 }}
    """
    try:
        if value is None:
            return float(min_val) if min_val is not None else 0
        if min_val is None:
            return float(value)
        return min(float(value), float(min_val))
    except (ValueError, TypeError, InvalidOperation):
        return 0


@register.filter
def default_if_zero(value, default):
    """
    Return default value if the value is zero
    Usage: {{ value|default_if_zero:"N/A" }}
    """
    try:
        if value is None or float(value) == 0:
            return default
        return value
    except (ValueError, TypeError, InvalidOperation):
        return default


@register.filter
def format_number(value):
    """
    Format number with commas
    Usage: {{ value|format_number }}
    """
    try:
        if value is None:
            return "0"
        return f"{float(value):,.0f}"
    except (ValueError, TypeError, InvalidOperation):
        return "0"
