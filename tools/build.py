"""Package the junction-compatible root layout without build dependencies."""
from pathlib import Path
import html
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendorize import check as check_vendor_tree

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_TIME = (1980, 1, 1, 0, 0, 0)


def _included(path):
    relative = path.relative_to(ROOT)
    return not ("__pycache__" in relative.parts or path.suffix in (".pyc", ".pyo"))


def _write_file(archive, path):
    info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), ARCHIVE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    data = path.read_bytes()
    try:
        data = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError:
        pass
    archive.writestr(info, data)


def _verify_archive(output):
    forbidden_roots = (
        "globalPlugins/sonosController/lib/",      # the old in-tree staging area
        "globalPlugins/sonosController/_vendor/lxml/",
        "globalPlugins/sonosController/bin/",
        "thirdParty/ffmpeg/",
    )
    forbidden_files = {
        "globalPlugins/sonosController/relay.py",
        "globalPlugins/sonosController/relay_urls.py",
    }
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("The add-on contains duplicate archive entries")
        if "globalPlugins/sonosController/_vendor/vendor-manifest.json" not in names:
            raise RuntimeError("The private vendor manifest is missing from the add-on")
        forbidden = [
            name for name in names
            if name.startswith(forbidden_roots)
            or name in forbidden_files
            or Path(name).suffix.lower() in {".pyd", ".dll", ".exe"}
        ]
        if forbidden:
            raise RuntimeError("Forbidden public-build files: " + ", ".join(forbidden))
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt add-on entry: {bad}")


def _output_path():
    """Name the package from manifest.ini so the version lives in one place."""
    values = {}
    for line in (ROOT / "manifest.ini").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"')
    folder = ROOT / "build"
    folder.mkdir(exist_ok=True)
    return folder / f"{values['name']}-{values['version']}.nvda-addon"


def build():
    check_vendor_tree()
    for language in ("en", "bg"):
        directory = ROOT / "doc" / language
        source = (directory / "readme.md").read_text(encoding="utf-8")
        body = []
        for line in source.splitlines():
            if line.startswith("# "):
                body.append("<h1>" + html.escape(line[2:]) + "</h1>")
            elif line.startswith("## "):
                body.append("<h2>" + html.escape(line[3:]) + "</h2>")
            elif line:
                body.append("<p>" + html.escape(line) + "</p>")
        (directory / "readme.html").write_text(
            f'<!doctype html><html lang="{language}"><meta charset="utf-8">'
            '<title>Sonos Controller</title><body>' + "\n".join(body) + '</body></html>',
            encoding="utf-8",
            newline="\n",
        )
    output = _output_path()
    files = [ROOT / "manifest.ini", ROOT / "COPYING.txt"]
    for folder in ("globalPlugins", "doc"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file()
                     and _included(p))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            _write_file(archive, path)
    _verify_archive(output)
    print(output)


if __name__ == "__main__":
    build()
