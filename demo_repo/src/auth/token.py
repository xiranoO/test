class TokenExpiredError(Exception):
    pass


def validate_token(token: dict) -> dict:
    if token.get("expired"):
        raise TokenExpiredError("JWT token has expired")
    return token
