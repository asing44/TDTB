"""The cockpit HTML revalidates; hashed assets are left cacheable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


def test_cockpit_index_revalidates():
    client = TestClient(main.app)
    response = client.get("/static/cockpit/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_keep_default_caching():
    client = TestClient(main.app)
    asset = next(
        (Path(main._STATIC_DIR) / "cockpit" / "assets").glob("index-*.js")
    ).name
    response = client.get(f"/static/cockpit/assets/{asset}")
    assert response.status_code == 200
    assert "cache-control" not in response.headers
