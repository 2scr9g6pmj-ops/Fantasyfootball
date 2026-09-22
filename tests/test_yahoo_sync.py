from app.services.yahoo_sync_service import extract_resources, merge_fragments


def test_merge_fragments_flattens_yahoo_resource():
    assert merge_fragments([{"league_key": "461.l.1"}, {"name": "Test League"}]) == {
        "league_key": "461.l.1",
        "name": "Test League",
    }


def test_extract_resources_finds_nested_yahoo_collections():
    payload = {
        "fantasy_content": {
            "users": {
                "0": {
                    "user": [
                        {"guid": "abc"},
                        {"games": {"0": {"game": [{"game_key": "461"}, {"leagues": {
                            "0": {"league": [{"league_key": "461.l.1"}, {"name": "Test League"}]},
                            "count": 1,
                        }}]}, "count": 1}},
                    ]
                },
                "count": 1,
            }
        }
    }
    assert extract_resources(payload, "leagues", "league") == [
        {"league_key": "461.l.1", "name": "Test League"}
    ]
