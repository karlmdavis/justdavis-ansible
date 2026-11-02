#!/usr/bin/python3
"""Write a consistent copy of Jellyfin's SQLite database while the server is running.

Jellyfin keeps its database in WAL mode and its documentation says a plain file copy is only safe with the
server stopped. SQLite's online backup API produces a consistent snapshot without stopping anything, so
this is what the offsite backup picks up instead of the live file. Managed by Ansible (roles/jellyfin).
"""

import os
import sqlite3
import sys


def main(source: str, destination: str) -> int:
    temporary = destination + ".tmp"
    try:
        live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        copy = sqlite3.connect(temporary)
        try:
            live.backup(copy)
            (result,) = copy.execute("PRAGMA integrity_check").fetchone()
        finally:
            live.close()
            copy.close()
        if result != "ok":
            print(f"integrity_check on the copy returned: {result}", file=sys.stderr)
            return 1
        os.replace(temporary, destination)
    finally:
        # A failed attempt never leaves a half-written copy behind; the previous good copy stays in place.
        if os.path.exists(temporary):
            os.remove(temporary)
    print(f"wrote {destination} ({os.path.getsize(destination)} bytes)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} SOURCE_DB DESTINATION_DB", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
