from src.auth.token import validate_token


def get_profile(token: dict) -> tuple[dict, int]:
    user = validate_token(token)
    return {"user_id": user["sub"]}, 200
