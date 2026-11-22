"""Create a local .env without overwriting existing secrets."""

import secrets
from pathlib import Path

target = Path(".env")
with target.open("x", encoding="utf-8") as output:
    output.write(
        Path(".env.example")
        .read_text(encoding="utf-8")
        .replace(
            "replace-with-a-random-secret-of-at-least-32-characters", secrets.token_urlsafe(48)
        )
    )
print("Created .env. Set GROQ_API_KEY to enable support answers.")
