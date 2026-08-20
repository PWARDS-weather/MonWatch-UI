import argparse
import ftplib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# JAXA P-Tree legacy account defaults (also stored in config/accounts.json
# under ftp_accounts with name "JAXA P-Tree").
JAXA_SERVER = "ftp.ptree.jaxa.jp"
JAXA_USERNAME = ""
JAXA_PASSWORD = ""
JAXA_PORT = 21
JAXA_PASSIVE = True

# Legacy FTP layout roots on the JAXA P-Tree server.
JAXA_HSD_ROOT = "/jma/hsd"      # hsd/{YYYYMM}/{DD}/{hh}/ -> HS_*.DAT.bz2
JAXA_NC_ROOT = "/jma/netcdf"    # netcdf/{YYYYMM}/{DD}/ -> NC_*.nc
JAXA_PUB_ROOT = "/pub"

_LIST_FLAGS = re.compile(r"^([d-])")
_LIST_SIZE_DATE = re.compile(
    r"^[d-][rwxsStT-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+([\d:]{4,5})"
)


def default_accounts_file():
    """Path to config/accounts.json relative to the repo root."""
    here = Path(__file__).resolve()
    return here.parent.parent.parent / "config" / "accounts.json"


def load_jaxa_account(accounts_path=None):
    """Resolve the JAXA P-Tree FTP account from accounts.json.

    Falls back to the well-known free P-Tree account when the config file is
    missing or does not contain a matching entry. Returns a dict with keys
    server/port/username/password/passive/name.
    """
    default = {
        "name": "JAXA P-Tree",
        "server": JAXA_SERVER,
        "port": JAXA_PORT,
        "username": JAXA_USERNAME,
        "password": JAXA_PASSWORD,
        "passive": JAXA_PASSIVE,
    }
    path = Path(accounts_path) if accounts_path else default_accounts_file()
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for acc in data.get("ftp_accounts", []) or []:
                if str(acc.get("name", "")).lower() == "jaxa p-tree":
                    return {
                        "name": acc.get("name", default["name"]),
                        "server": acc.get("server") or default["server"],
                        "port": int(acc.get("port") or default["port"]),
                        "username": acc.get("username") or default["username"],
                        "password": acc.get("password") or default["password"],
                        "passive": bool(acc.get("passive", default["passive"])),
                    }
    except Exception as e:
        log.warning(f"[JAXA] Could not read accounts file {path}: {e}")
    return dict(default)


def test_ftp_connection(host, port=21, user="", password="", passive=True, timeout=10):
    try:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout)
        ftp.login(user, password)
        ftp.set_pasv(passive)
        listing = []
        ftp.retrlines("LIST", listing.append)
        ftp.quit()
        return True, f"Connected. {len(listing)} items in root."
    except ftplib.all_errors as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


class JaxaFTP:
    """Small FTP client for browsing the JAXA P-Tree directory layout."""

    def __init__(self, account=None, timeout=30):
        account = account or load_jaxa_account()
        self.server = account["server"]
        self.port = int(account.get("port", JAXA_PORT))
        self.username = account["username"]
        self.password = account["password"]
        self.passive = bool(account.get("passive", JAXA_PASSIVE))
        self.timeout = timeout
        self._conn = None

    def connect(self):
        if self._conn is not None:
            return self._conn
        conn = ftplib.FTP()
        conn.connect(self.server, self.port, self.timeout)
        conn.login(self.username, self.password)
        if self.passive:
            conn.set_pasv(True)
        self._conn = conn
        return conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.quit()
            except Exception as e:
                try:
                    self._conn.close()
                except Exception as ce:
                    log.debug(f"[JAXA] FTP close error: {ce} ({e})")
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def list_dir(self, remote_path="/"):
        """Return (dirs, files) for an ABSOLUTE remote path.

        ``files`` items are dicts with 'name', 'size' and 'modify'. MLSD is
        preferred for machine-readable facts, with a LIST + NLST fallback.
        """
        conn = self.connect()
        remote_path = remote_path or "/"
        if not remote_path.startswith("/"):
            remote_path = "/" + remote_path
        path = remote_path.rstrip("/") or "/"

        try:
            conn.cwd(path)
        except ftplib.all_errors as e:
            raise OSError(f"cannot enter {path}: {e}") from e

        dirs, files = [], []
        mlsd = None
        try:
            if hasattr(conn, "mlsd"):
                mlsd = list(conn.mlsd())
        except ftplib.all_errors:
            mlsd = None

        if mlsd:
            for name, facts in mlsd:
                if name in (".", ".."):
                    continue
                if facts.get("type") == "dir":
                    dirs.append(name)
                else:
                    files.append(_entry(name, facts))
            dirs.sort()
            files.sort(key=lambda e: e["name"])
            return dirs, files

        lines = []
        try:
            conn.retrlines("LIST", lines.append)
        except ftplib.all_errors:
            pass
        names = []
        try:
            names = conn.nlst()
        except ftplib.all_errors:
            pass

        for line in lines:
            if not _LIST_FLAGS.match(line):
                continue
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            is_dir = line.startswith("d")
            name = parts[8]
            if not name:
                continue
            size, when = 0, ""
            dm = _LIST_SIZE_DATE.match(line)
            if dm:
                try:
                    size = int(dm.group(1))
                except ValueError:
                    size = 0
                when = dm.group(2)
            if is_dir:
                dirs.append(name)
            else:
                files.append({"name": name, "size": size, "modify": when})
        seen = set(dirs) | {f["name"] for f in files}
        for name in names:
            if name in (".", "..") or name in seen:
                continue
            files.append({"name": name, "size": 0, "modify": ""})
        dirs.sort()
        files.sort(key=lambda e: e["name"])
        return dirs, files


def _entry(name, facts):
    size = facts.get("size", "")
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0
    modify = str(facts.get("modify", ""))
    if len(modify) == 14:
        try:
            modify = (datetime.strptime(modify, "%Y%m%d%H%M%S")
                      .replace(tzinfo=timezone.utc)
                      .strftime("%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass
    return {"name": name, "size": size, "modify": modify}


def _node(kind="dir", size=0, modify="", children=None):
    node = {"type": kind}
    if size:
        node["size"] = size
    if modify:
        node["modify"] = modify
    if kind == "dir":
        node["children"] = children or {}
    return node


def discover_layout(account=None, max_depth=None, include_pub=True, progress=None):
    """Walk the JAXA P-Tree layout into a nested dict.

    Follows the real legacy tree: /jma/hsd/{YYYYMM}/{DD}/{hh}/*.bz2 and
    /jma/netcdf/{YYYYMM}/{DD}/*.nc, plus /pub. ``max_depth`` caps traversal
    (None = full walk). Returns {ok, tree, error}.
    """
    account = account or load_jaxa_account()
    tree = _node(kind="dir")
    try:
        with JaxaFTP(account) as ftp:
            if progress:
                progress("Listing server root...")
            dirs, files = ftp.list_dir("/")
            root_children = tree["children"]
            for f in files:
                root_children[f["name"]] = _node("file", f["size"], f["modify"])
            for d in sorted(set(dirs)):
                if d == "pub" and not include_pub:
                    continue
                root_children[d] = _walk_tree(
                    ftp, f"/{d}", max_depth, depth=1, progress=progress,
                )
        return {"ok": True, "tree": tree, "error": None}
    except ftplib.all_errors as e:
        return {"ok": False, "tree": None, "error": str(e)}
    except Exception as e:
        return {"ok": False, "tree": None, "error": str(e)}


def _walk_tree(ftp, base_path, max_depth, depth=0, progress=None):
    """Recursively list one absolute subtree, capping depth expansions."""
    node = _node("dir")
    if max_depth is not None and depth >= max_depth:
        if progress:
            progress(f"(depth cap {max_depth} reached at {base_path})")
        return node
    if progress:
        progress(f"Listing {base_path}...")
    dirs, files = ftp.list_dir(base_path)
    children = node["children"]
    for f in files:
        children[f["name"]] = _node("file", f["size"], f["modify"])
    for d in sorted(set(dirs)):
        children[d] = _walk_tree(ftp, f"{base_path}/{d}", max_depth,
                                 depth=depth + 1, progress=progress)
    return node


def layout_to_text(tree, indent="  "):
    """Render a layout tree dict to an indented, printable tree."""
    out = []

    def walk(node, prefix, label):
        if node.get("type") != "dir":
            out.append(f"{prefix}{label}")
            return
        if label:
            out.append(f"{prefix}{label}/")
        for name in sorted(node.get("children", {})):
            walk(node["children"][name], prefix + indent, name)

    walk(tree or {}, "", "")
    return "\n".join(out)


def read_layout_report(account=None, max_depth=None, include_pub=True, progress=None):
    """High-level helper: returns (ok, tree, text, error)."""
    result = discover_layout(account=account, max_depth=max_depth,
                             include_pub=include_pub, progress=progress)
    text = layout_to_text(result["tree"]) if result["ok"] and result["tree"] else ""
    return result["ok"], result["tree"], text, result["error"]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="src.clients.ftp_client",
        description="Read the JAXA P-Tree FTP layout (legacy).",
    )
    parser.add_argument("--jaxa-layout", action="store_true",
                        help="Print the live P-Tree directory layout.")
    parser.add_argument("--accounts", default=None,
                        help="Path to accounts.json (optional).")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="Cap directory walk depth (default: full).")
    parser.add_argument("--no-pub", action="store_true",
                        help="Skip /pub subtree.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.jaxa_layout:
        parser.print_help()
        return 0

    account = load_jaxa_account(args.accounts)
    print(f"Reading layout of {account['server']} as {account['username']} ...\n")
    ok, _, text, error = read_layout_report(
        account=account,
        max_depth=args.max_depth,
        include_pub=not args.no_pub,
        progress=lambda m: log.info(m),
    )
    if not ok:
        print(f"ERROR: {error}")
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())