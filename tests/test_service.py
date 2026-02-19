from venom_module_brand_studio.services.service import (
    _canonical_url,
    get_brand_studio_service,
)


def test_canonical_url_removes_tracking_params() -> None:
    url = _canonical_url(
        "https://example.org/post?utm_source=a&utm_medium=b&ref=r&id=1&gclid=foo"
    )
    assert url == "https://example.org/post?id=1"


def test_candidates_are_scored_and_deduplicated() -> None:
    service = get_brand_studio_service()
    items, _ = service.list_candidates(channel=None, lang=None, limit=50, min_score=0.0)

    assert len(items) >= 4
    assert all(item.score_breakdown.final_score == item.score for item in items)
    assert all(item.score_breakdown.reasons for item in items)
    assert sorted([it.score for it in items], reverse=True) == [it.score for it in items]

    urls = [it.url for it in items]
    assert len(urls) == len(set(urls))


def test_candidates_filters_work_for_lang_and_channel() -> None:
    service = get_brand_studio_service()

    pl_items, _ = service.list_candidates(channel=None, lang="pl", limit=50, min_score=0.0)
    assert pl_items
    assert all(item.language == "pl" for item in pl_items)

    github_items, _ = service.list_candidates(
        channel="github", lang=None, limit=50, min_score=0.0
    )
    assert github_items
    assert all(item.source in {"github", "arxiv"} for item in github_items)
