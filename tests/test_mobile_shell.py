"""Guard the HTML contract used by the shared web/Android application script."""
from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


class PageIds(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = set()
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.ids.update(value for key, value in attrs if key == "id")


def test_mobile_has_shared_script_controls():
    """A missing player control must never prevent auth handlers from loading."""
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    desktop = PageIds((ROOT / "app/static/index.html").read_text(encoding="utf-8"))
    mobile = PageIds((ROOT / "ydkmobile/www/index.html").read_text(encoding="utf-8"))
    referenced = set(re.findall(r'byId\("([^"]+)"\)', script))
    missing = (desktop.ids & referenced) - mobile.ids
    assert not missing, f"Android shell is missing shared controls: {sorted(missing)}"
