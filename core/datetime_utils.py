"""
Утилиты для работы с datetime в проекте.
Нормализация различных типов datetime из разных источников.
"""

from datetime import datetime, timezone
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

def normalize_dt(value: Any) -> Optional[datetime]:
    """
    Нормализация различных типов datetime в timezone-aware UTC datetime.

    Поддерживает:
    - Python datetime (naive/aware)
    - Neo4j temporal types (DateTime, etc.)
    - ISO strings
    - None

    Возвращает: timezone-aware UTC datetime или None
    """
    if value is None:
        return None

    # Уже правильный datetime
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Naive datetime - предполагаем UTC
            return value.replace(tzinfo=timezone.utc)
        else:
            # Уже timezone-aware - конвертируем в UTC
            return value.astimezone(timezone.utc)

    # Neo4j temporal types
    if hasattr(value, 'to_native'):
        try:
            # Neo4j DateTime.to_native() возвращает Python datetime
            native_dt = value.to_native()
            if isinstance(native_dt, datetime):
                return normalize_dt(native_dt)
        except Exception as e:
            logger.warning(f"Failed to convert Neo4j temporal {type(value)} to native: {e}")

    # ISO string
    if isinstance(value, str):
        try:
            # Пробуем разные форматы
            for fmt in [
                "%Y-%m-%dT%H:%M:%S.%f%z",  # 2023-12-01T12:00:00.123456+00:00
                "%Y-%m-%dT%H:%M:%S%z",     # 2023-12-01T12:00:00+00:00
                "%Y-%m-%dT%H:%M:%S.%f",    # 2023-12-01T12:00:00.123456 (naive)
                "%Y-%m-%dT%H:%M:%S",       # 2023-12-01T12:00:00 (naive)
                "%Y-%m-%d %H:%M:%S.%f%z",  # 2023-12-01 12:00:00.123456+00:00
                "%Y-%m-%d %H:%M:%S%z",     # 2023-12-01 12:00:00+00:00
                "%Y-%m-%d %H:%M:%S.%f",    # 2023-12-01 12:00:00.123456 (naive)
                "%Y-%m-%d %H:%M:%S",       # 2023-12-01 12:00:00 (naive)
            ]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return normalize_dt(dt)
                except ValueError:
                    continue

            # Если ничего не подошло, пробуем fromisoformat
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return normalize_dt(dt)
            except ValueError:
                pass

        except Exception as e:
            logger.warning(f"Failed to parse datetime string '{value}': {e}")

    # Timestamp (число секунд)
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
            return dt
        except (ValueError, OSError) as e:
            logger.warning(f"Failed to convert timestamp {value}: {e}")

    # Неизвестный тип
    logger.warning(f"Unknown datetime type: {type(value)} = {value}")
    return None

def dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    """
    Конвертация datetime в ISO строку для JSON API.

    Возвращает: ISO 8601 строку или None
    """
    if dt is None:
        return None

    if not isinstance(dt, datetime):
        logger.warning(f"dt_to_iso received non-datetime: {type(dt)} = {dt}")
        return None

    # Убеждаемся что timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.isoformat()

def calculate_recency_days(created_at: Any, now: Optional[datetime] = None) -> float:
    """
    Вычисление количества дней с момента создания.

    Args:
        created_at: Время создания (любой поддерживаемый тип)
        now: Текущее время (по умолчанию datetime.now(timezone.utc))

    Returns:
        Количество дней (float), всегда >= 0
    """
    if now is None:
        now = datetime.now(timezone.utc)

    normalized_created = normalize_dt(created_at)
    if normalized_created is None:
        return 0.0

    try:
        delta = now - normalized_created
        days = delta.total_seconds() / (24 * 60 * 60)
        return max(0.0, days)  # Не отрицательные значения
    except (ValueError, OverflowError) as e:
        logger.warning(f"Failed to calculate recency for {created_at}: {e}")
        return 0.0