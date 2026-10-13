"""Check local HTML links and decode every referenced or embedded image."""

import argparse
import base64
from html.parser import HTMLParser
import io
import json
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes, urlsplit

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.anchors = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.anchors.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.anchors.add(attrs["name"])
        for key in ("src", "href"):
            if key in attrs:
                self.references.append((tag, key, attrs[key]))


def check_page(path, root=ROOT):
    page = Page(path.read_text(encoding="utf-8"))
    failures, external = [], []
    images = 0
    for tag, attribute, reference in page.references:
        parts = urlsplit(reference)
        try:
            if not reference:
                raise ValueError("empty reference")
            if parts.scheme == "data":
                if tag != "img":
                    continue
                header, payload = reference.split(",", 1)
                data = (base64.b64decode(payload, validate=True)
                        if ";base64" in header else unquote_to_bytes(payload))
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                images += 1
                continue
            if parts.scheme or parts.netloc:
                external.append(reference)
                continue
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if not target.is_relative_to(root.resolve()):
                raise ValueError("reference leaves project")
            if not target.is_file():
                raise ValueError("target file is missing")
            if tag == "img" and attribute == "src":
                with Image.open(target) as image:
                    image.verify()
                images += 1
            if parts.fragment and target.suffix.lower() == ".html":
                anchors = (page.anchors if target == path else
                           Page(target.read_text(encoding="utf-8")).anchors)
                if unquote(parts.fragment) not in anchors:
                    raise ValueError("anchor is missing")
        except (ValueError, OSError) as error:
            failures.append({"tag": tag, "attribute": attribute,
                             "reference": reference[:160], "error": str(error)})
    return {"page": path.relative_to(root).as_posix(), "images": images,
            "references": len(page.references), "external": external, "failures": failures}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = [check_page(path) for path in sorted((ROOT / "design").rglob("*.html"))]
    failures = sum(len(row["failures"]) + len(row["external"]) for row in rows)
    for row in rows:
        print("%s images=%s failures=%s external=%s" %
              (row["page"], row["images"], len(row["failures"]), len(row["external"])))
        for failure in row["failures"]:
            print("FAIL", failure)
    report = {"pages": rows, "failures": failures}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("HTML_DONE pages=%s images=%s failures=%s" %
          (len(rows), sum(row["images"] for row in rows), failures))
    return 0 if rows and failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
