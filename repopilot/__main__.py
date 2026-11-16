import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    from .server import create_app
    # One worker preserves the local MVP's global import/chat operation lock.
    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "3211")),
                workers=1, proxy_headers=False, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
