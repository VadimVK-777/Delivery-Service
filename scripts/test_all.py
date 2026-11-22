"""Run migrations and tests in the disposable integration container."""

import subprocess
import sys

subprocess.run(["alembic", "upgrade", "head"], check=True)
subprocess.run(["alembic", "check"], check=True)
subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit",
        "tests/integration",
        "tests/e2e",
        "--cov=delivery_service",
        "--cov-report=term-missing",
        "--cov-fail-under=85",
    ],
    check=True,
)
