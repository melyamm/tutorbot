import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
FMT = "%Y-%m-%d %H:%M:%S"

WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WD_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WD_PLURAL = ["по понедельникам", "по вторникам", "по средам", "по четвергам", "по пятницам", "по субботам", "по воскресеньям"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
TZ_CHOICES = [
    ("Калининград", "Europe/Kaliningrad"),
    ("Москва", "Europe/Moscow"),
    ("Самара", "Europe/Samara"),
    ("Екатеринбург", "Asia/Yekaterinburg"),
    ("Омск", "Asia/Omsk"),
    ("Новосибирск", "Asia/Novosibirsk"),
    ("Красноярск", "Asia/Krasnoyarsk"),
    ("Иркутск", "Asia/Irkutsk"),
    ("Якутск", "Asia/Yakutsk"),
    ("Владивосток", "Asia/Vladivostok"),
]


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(FMT)


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=UTC)


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def tz_label(name: str) -> str:
    city = next((c for c, z in TZ_CHOICES if z == name), name)
    ref = now_utc()
    diff = (ref.astimezone(zone(name)).utcoffset() - ref.astimezone(zone("Europe/Moscow")).utcoffset()).total_seconds() / 3600
    if diff == 0:
        return f"{city} (МСК)"
    sign = "+" if diff > 0 else "-"
    return f"{city} (МСК{sign}{abs(diff):g})"


def fmt_dt(dt: datetime, tz) -> str:
    l = dt.astimezone(tz)
    return f"{WD[l.weekday()]} {l.day} {MONTHS[l.month - 1]}, {l:%H:%M}"


def fmt_day(dt: datetime, tz) -> str:
    l = dt.astimezone(tz)
    return f"{WD[l.weekday()]} {l.day} {MONTHS[l.month - 1]}"


def fmt_short(dt: datetime, tz) -> str:
    l = dt.astimezone(tz)
    return f"{WD[l.weekday()]} {l:%d.%m %H:%M}"


def fmt_time(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def rel_day(dt: datetime, tz) -> str:
    d = dt.astimezone(tz).date()
    today = now_utc().astimezone(tz).date()
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return f"{WD_FULL[d.weekday()]}, {d.day} {MONTHS[d.month - 1]},"


TIME_RE = re.compile(r"^(\d{1,2})[:.\s-]?(\d{2})$")
DT_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?:\s+(.+))?$")


def parse_hhmm(s: str) -> str | None:
    m = TIME_RE.match(s.strip())
    if not m:
        return None
    hh, mm = int(m[1]), int(m[2])
    if hh > 23 or mm > 59:
        return None
    return f"{hh:02d}:{mm:02d}"


def parse_dt(s: str, tz, need_time: bool = True) -> datetime | None:
    s = s.strip().lower()
    now_l = now_utc().astimezone(tz)
    d: date | None = None
    rest: str | None = None
    for word, shift in (("сегодня", 0), ("послезавтра", 2), ("завтра", 1)):
        if s.startswith(word):
            d = now_l.date() + timedelta(days=shift)
            rest = s[len(word):].strip() or None
            break
    if d is None:
        m = DT_RE.match(s)
        if not m:
            return None
        day, mon, yr, rest = int(m[1]), int(m[2]), m[3], m[4]
        year = (int(yr) + (2000 if len(yr) == 2 else 0)) if yr else now_l.year
        try:
            d = date(year, mon, day)
            if not yr and d < now_l.date() - timedelta(days=60):
                d = d.replace(year=year + 1)
        except ValueError:
            return None
    if rest:
        hm = parse_hhmm(re.sub(r"^в\s+", "", rest))
        if not hm:
            return None
        hh, mm = map(int, hm.split(":"))
    elif need_time:
        return None
    else:
        hh, mm = 23, 59
    return datetime.combine(d, time(hh, mm), tzinfo=tz).astimezone(UTC)
