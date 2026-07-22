from src.api.profile import get_profile


def test_profile_with_valid_token():
    body, status = get_profile({"sub": "demo", "expired": False})
    assert status == 200
    assert body["user_id"] == "demo"
