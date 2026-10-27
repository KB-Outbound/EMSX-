"""BLPAPI 스칼라 값의 엄격한 변환과 누락·파싱 실패를 구분한다."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Callable

from diagnostics import LOGGER

DATE_FIELDS = {"EMSX_DATE", "EMSX_ROUTE_CREATE_DATE"}
TIME_FIELDS = {"EMSX_TIME", "EMSX_ROUTE_CREATE_TIME"}


def integer(value: Any) -> int:
    """불리언·소수·비유한 수를 정수로 절삭하지 않고 거부한다."""
    if isinstance(value, bool) or value is None:
        raise ValueError("정수가 아닌 값")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("유한 정수가 아닌 값")
        if not -(2**63) <= number < 2**63:
            raise ValueError("64비트 정수 범위 초과")
        return int(number)
    except InvalidOperation as exc:
        raise ValueError("정수 형식 오류") from exc


def numeric_field(field: str, value: Any) -> int:
    """날짜·시각 객체와 숫자 문자열을 의미를 잃지 않고 변환한다."""
    if field in DATE_FIELDS:
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return int(value.strftime("%Y%m%d"))
        raw = str(value)
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return int(date.fromisoformat(raw).strftime("%Y%m%d"))
    if field in TIME_FIELDS:
        if isinstance(value, time):
            return value.hour * 3600 + value.minute * 60 + value.second
        if isinstance(value, str) and ":" in value:
            parsed = time.fromisoformat(value)
            return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
    return integer(value)


def read_scalar(
    msg: Any,
    field: str,
    int_fields: set[str],
    float_fields: set[str],
    on_warning: Callable[[str], None] | None = None,
) -> Any:
    """선택 필드 오류는 호출자에게 경고하고 값만 제외한다. 메시지 원문은 기록하지 않는다."""
    try:
        if not msg.hasElement(field):
            return None
        element = msg.getElement(field) if hasattr(msg, "getElement") else None
        if element is not None and element.isNull():
            return None
        if field in int_fields or field in float_fields:
            # 원래 실수인 값을 정수 getter가 절삭하는 일을 방지한다.
            if element is not None:
                value = element.getValue()
            else:
                try:
                    value = msg.getElementAsString(field)
                except Exception:
                    getter = (
                        msg.getElementAsInteger
                        if field in int_fields
                        else msg.getElementAsFloat
                    )
                    value = getter(field)
            if value is None:
                return None
            if field in int_fields:
                return numeric_field(field, value)
            result = float(value)
            if not math.isfinite(result):
                raise ValueError("비유한 실수")
            return result
        value = msg.getElementAsString(field)
        return None if value is None else str(value)
    except Exception:
        LOGGER.warning("필드 추출 실패: 함수=read_scalar 필드=%s", field, exc_info=True)
        if on_warning is not None:
            on_warning(field)
        return None
