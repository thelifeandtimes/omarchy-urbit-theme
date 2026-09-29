#!/usr/bin/env python3
"""Bundle only the reviewed source/install inventory, never state or credentials."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

from install import FILES, SOURCE


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 -B scripts/package.py /existing/directory/bundle.tar.gz")
    output = Path(sys.argv[1]).resolve()
    if not output.parent.is_dir():
        raise SystemExit("The output directory must already exist.")
    version = json.loads((SOURCE / "manifest.json").read_text())["version"]
    prefix = "omarchy-urbit-theme-" + version
    files = FILES + ("hooks/omarchy-urbit-theme", "hooks/omarchy-urbit-font", "scripts/install.py", "scripts/check-desktop.py", "scripts/package.py")
    with tarfile.open(output, "w:gz") as bundle:
        for name in files:
            path = SOURCE / name
            if path.is_symlink() or not path.is_file():
                raise SystemExit("Expected a regular source file: " + name)
            bundle.add(path, arcname=prefix + "/" + name, recursive=False)
    print(output)
    print("SHA256 " + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
