"""Use an isolated Compose project; always remove only this run's test volumes."""

import subprocess
import uuid

project = f"delivery-test-{uuid.uuid4().hex[:8]}"
compose = ["docker", "compose", "-p", project, "-f", "compose.test.yaml"]
try:
    subprocess.run([*compose, "build", "tests"], check=True)
    subprocess.run(
        [*compose, "up", "-d", "--wait", "postgres", "redis", "rabbitmq", "mongo", "qdrant"],
        check=True,
    )
    subprocess.run([*compose, "run", "--rm", "--no-deps", "mongo-init"], check=True)
    subprocess.run([*compose, "run", "--rm", "--no-deps", "tests"], check=True)
finally:
    subprocess.run([*compose, "down", "--volumes", "--remove-orphans"], check=False)
