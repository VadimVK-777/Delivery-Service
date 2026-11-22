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
        "--cov=delivery_service",
        "--cov-report=term-missing",
    ],
    check=True,
)
