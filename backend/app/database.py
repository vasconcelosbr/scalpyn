from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from .config import settings


def _resolve_db_url(url: str) -> tuple[str, dict]:
    """
    asyncpg does not honour ?host=/path in the URL query string for Unix sockets.
    Extract it and return it as a connect_arg instead.
    """
    connect_args: dict = {}
    if "?" not in url:
        return url, connect_args

    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    if "host" in params and params["host"][0].startswith("/"):
        connect_args["host"] = params["host"][0]
        del params["host"]
        new_query = urlencode({k: v[0] for k, v in params.items()})
        url = urlunparse(parsed._replace(query=new_query))

    return url, connect_args


_db_url, _connect_args = _resolve_db_url(settings.DATABASE_URL)

engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args=_connect_args,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
