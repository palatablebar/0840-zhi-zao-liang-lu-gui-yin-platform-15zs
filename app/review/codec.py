"""JSON codec for stored run payloads.

补报会跨请求保存最新行数据、已签发结果与状态，滚动良率是十进制数，
落库时不能用二进制浮点漂移，因此这里显式保留 ``Decimal`` 与日期。
"""

import json
from datetime import date, datetime
from decimal import Decimal


DECIMAL = "$decimal"
DATE = "$date"
DATETIME = "$datetime"


def encode(value):
    if isinstance(value, Decimal):
        return {DECIMAL: str(value)}
    if isinstance(value, datetime):
        return {DATETIME: value.isoformat()}
    if isinstance(value, date):
        return {DATE: value.isoformat()}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if len(value) == 1:
            (key, item), = value.items()
            if key == DECIMAL:
                return Decimal(item)
            if key == DATE:
                return date.fromisoformat(item)
            if key == DATETIME:
                return datetime.fromisoformat(item)
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def dumps(value):
    return json.dumps(encode(value), ensure_ascii=False)


def loads(text):
    return decode(json.loads(text))
