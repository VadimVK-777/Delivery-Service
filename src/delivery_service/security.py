"""Anonymous signed sessions and CSRF protection."""

import hashlib
import hmac
from uuid import UUID, uuid4

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from delivery_service.domain import Problem
from delivery_service.settings import Settings

COOKIE = "delivery_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Sessions:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.signer = URLSafeTimedSerializer(
            settings.session_secret.get_secret_value(), salt="delivery-session-v1"
        )

    def read(self, request: Request) -> UUID | None:
        try:
            value = self.signer.loads(
                request.cookies.get(COOKIE, ""), max_age=self.settings.session_ttl
            )
            return UUID(str(value))
        except BadSignature, SignatureExpired, ValueError:
            return None

    def initialize(self, request: Request, response: Response) -> UUID:
        owner = self.read(request) or uuid4()
        response.set_cookie(
            COOKIE,
            self.signer.dumps(str(owner)),
            max_age=self.settings.session_ttl,
            httponly=True,
            secure=self.settings.cookie_secure,
            samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"
        return owner

    def csrf(self, owner: UUID) -> str:
        return hmac.new(
            self.settings.session_secret.get_secret_value().encode(),
            f"csrf:{owner}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def require(self, request: Request, *, mutate: bool = False) -> UUID:
        owner = self.read(request)
        if owner is None:
            raise Problem(401, "session_required", "Сначала откройте /api/v1/session")
        if mutate and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", ""), self.csrf(owner)
        ):
            raise Problem(403, "csrf_invalid", "Неверный CSRF-токен")
        return owner
