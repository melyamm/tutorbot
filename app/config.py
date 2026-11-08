import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def ids(raw: str | None) -> set[int]:
    out = set()
    for part in (raw or "").replace(" ", "").split(","):
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def _token() -> str:
    for name in ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TOKEN", "TG_TOKEN", "TELEGRAM_TOKEN", "API_TOKEN"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    for value in os.environ.values():
        if re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", value.strip()):
            return value.strip()
    return ""


def _db_path() -> str:
    if os.getenv("DB_PATH"):
        return os.environ["DB_PATH"]
    if os.path.isdir("/app"):
        return "/app/data/bot.db"
    return "data/bot.db"


@dataclass(frozen=True)
class Config:
    token: str
    admin_ids: set[int]
    tz: ZoneInfo
    db_path: str
    digest_hour: int
    bill_delay_min: int
    pay_info: str


cfg = Config(
    token=_token(),
    admin_ids=ids(os.getenv("ADMIN_IDS")),
    tz=ZoneInfo(os.getenv("TUTOR_TZ", "Europe/Moscow")),
    db_path=_db_path(),
    digest_hour=int(os.getenv("DIGEST_HOUR", "9")),
    bill_delay_min=int(os.getenv("BILL_DELAY_MIN", "30")),
    pay_info=os.getenv("PAY_INFO", "").strip(),
)
