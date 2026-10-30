import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from app.models import SearchRequest
from app.pipeline import Counts
from app.youtube import YouTubeProvider


def test_youtube_search_keeps_hidden_likes_unknown(monkeypatch, tmp_path):
    published = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat().replace("+00:00", "Z")

    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "abc"}}, {"id": {"videoId": "def"}}]})
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [
                {"id": "abc", "snippet": {"publishedAt": published, "channelId": "ch", "title": "Open"},
                 "statistics": {"viewCount": "50000", "likeCount": "2500", "commentCount": "100"},
                 "contentDetails": {"duration": "PT45S"}},
                {"id": "def", "snippet": {"publishedAt": published, "channelId": "ch", "title": "Hidden"},
                 "statistics": {"viewCount": "40000"},
                 "contentDetails": {"duration": "PT30S"}},
            ]})
        return httpx.Response(200, json={"items": [{"id": "ch", "statistics": {"subscriberCount": "10000"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr("app.youtube.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    found, videos = asyncio.run(YouTubeProvider(api_key="key").search(SearchRequest(query="cats", min_views=1000)))

    assert found == 2
    by_id = {video.video_id: video for video in videos}
    assert by_id["abc"].like_rate == 0.05 and by_id["abc"].breakout_ratio == 5
    hidden = by_id["def"]
    # Hidden likes and disabled comments are unknown, not zero.
    assert hidden.likes is None and hidden.comments is None and hidden.like_rate is None
    assert hidden.popularity_score is not None
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as conn:
        assert conn.execute("SELECT video_id FROM video_snapshots").fetchall() == [("abc",)]
        assert conn.execute("SELECT video_id FROM view_snapshots").fetchall() == [("def",)]


def test_youtube_counts_use_one_request_per_50_ids(monkeypatch):
    batches = []

    def handler(request):
        assert request.url.path.endswith("/videos")
        assert request.url.params["part"] == "statistics"
        ids = request.url.params["id"].split(",")
        batches.append(len(ids))
        # One video disappeared: the API omits it from items.
        return httpx.Response(200, json={"items": [
            {"id": video_id, "statistics": {"viewCount": "10", "likeCount": "2"}}
            for video_id in ids if video_id != "v7"
        ]})

    original = httpx.AsyncClient
    monkeypatch.setattr("app.youtube.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    counts = asyncio.run(YouTubeProvider(api_key="key").fetch_counts([f"v{i}" for i in range(120)]))

    assert batches == [50, 50, 20]
    assert counts["v0"] == Counts(10, 2, None)
    assert "v7" not in counts and len(counts) == 119
