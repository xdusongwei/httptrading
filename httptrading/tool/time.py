import re
import time
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, UTC, timezone
import humanize


class TimeTools:
    @classmethod
    def _get_utc(cls):
        return datetime.now(timezone.utc)

    @classmethod
    def utc_now(cls):
        return cls._get_utc()

    @classmethod
    def date_to_ymd(cls, date: datetime, join=True) -> str:
        if join:
            return date.strftime('%Y-%m-%d')
        else:
            return date.strftime('%Y%m%d')

    @classmethod
    def format_ymd(cls, s: str | int) -> str:
        if s is None:
            return '--'
        elif isinstance(s, int):
            s = str(s)
        if m := re.match(r'^(\d{4})(\d{2})(\d{2})$', s):
            yyyy, mm, dd = m.groups()
            return f'{yyyy}-{mm}-{dd}'
        elif re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s):
            return s
        else:
            return s

    @classmethod
    def timedelta(cls, date: datetime, days=0, minutes=0, seconds=0):
        return date + timedelta(days=days, minutes=minutes, seconds=seconds)

    @classmethod
    def from_timestamp(cls, timestamp, tz: str = None) -> datetime:
        """
        秒单位时间戳
        :param timestamp:
        :param tz:
        :return:
        """
        tz = tz if tz else cls.current_tz()
        date = datetime.fromtimestamp(timestamp, UTC)
        return date.astimezone(ZoneInfo(tz))

    @classmethod
    def from_params(cls, year: int, month: int, day: int, hour: int, minute: int, second: int, tz: str):
        return datetime(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            tzinfo=ZoneInfo(tz),
        )

    @classmethod
    def sleep(cls, secs: float):
        if secs <= 0.0:
            return
        time.sleep(secs)

    @classmethod
    def precisedelta(cls, value, minimum_unit='seconds', suppress=(), format='%0.2f'):
        return humanize.precisedelta(value=value, minimum_unit=minimum_unit, suppress=suppress, format=format)


__all__ = ['TimeTools', ]
