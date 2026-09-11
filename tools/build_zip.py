# -*- coding: utf-8 -*-
"""
Package the repository into a ZIP that QGIS can install.

The repository root *is* the plugin package, but QGIS requires the archive
to contain a single top level folder named after the plugin. This script
therefore writes every packaged file under a `dstretch/` prefix.

    python tools/build_zip.py            # -> build/dstretch-<version>.zip

The version is read from metadata.txt.
"""

import io
import os
import sys
import zipfile

PACKAGE = "dstretch"

# Repository scaffolding that should not end up in the installed plugin
SKIP_DIRS = {".git", ".github", "tools", "build", "__pycache__",
             ".vscode", ".idea"}
SKIP_FILES = {".gitignore"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".zip")


def read_version(root):
    path = os.path.join(root, "metadata.txt")
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    return "0.0.0"


def collect(root):
    """Yield (absolute path, path inside the archive) for every packaged file."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES):
                continue
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            yield path, "%s/%s" % (PACKAGE, relative)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version = read_version(root)

    build_dir = os.path.join(root, "build")
    os.makedirs(build_dir, exist_ok=True)
    target = os.path.join(build_dir, "%s-%s.zip" % (PACKAGE, version))

    translations = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in collect(root):
            archive.write(path, arcname)
            translations += arcname.endswith(".qm")
            print("  " + arcname)

    if not translations:
        print("warning: no compiled .qm found - run tools/build_qm.py first",
              file=sys.stderr)

    print("%s (%d bytes)" % (os.path.relpath(target, root),
                             os.path.getsize(target)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
