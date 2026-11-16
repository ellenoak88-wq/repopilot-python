"""Make a consistent, independent SQLite snapshot; never overwrite a target."""
import argparse
import sqlite3
from pathlib import Path


def snapshot(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file():
        raise ValueError("源数据库不存在。")
    if destination.exists():
        raise ValueError("目标已存在，请使用新的目标文件名。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents replacing an existing database.
    with destination.open("xb"):
        pass
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("数据库完整性检查失败。")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path("data/repopilot.db"))
    args = parser.parse_args()
    snapshot(args.source, args.destination)
    print("数据库已复制并通过完整性检查。首次启动会自动迁移旧版会话。")
