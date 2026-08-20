import sys, json, os
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("ERROR: --settings-file required", file=sys.stderr)
        sys.exit(1)

    settings_file = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--settings-file' and i + 1 < len(sys.argv[1:]):
            settings_file = Path(sys.argv[i + 2])
            break

    if settings_file is None:
        print("ERROR: --settings-file <path> required", file=sys.stderr)
        sys.exit(1)

    raw = sys.stdin.read()
    if not raw:
        print("ERROR: no JSON on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(settings_file)
        print("OK", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
