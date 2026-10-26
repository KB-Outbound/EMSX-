"""공유 상태의 복합 연산을 재진입 가능한 잠금으로 보호한다."""

from functools import wraps
from typing import Any, Callable, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


def synchronized(method: F) -> F:
    """중첩된 메서드 호출에서도 동일한 인스턴스 잠금을 사용한다."""

    @wraps(method)
    def locked(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return cast(F, locked)
