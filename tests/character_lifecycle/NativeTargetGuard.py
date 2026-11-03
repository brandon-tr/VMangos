"""Pure target-guard helpers for the opt-in native MySQL runner.

These functions perform no Docker or database operation themselves except in
`docker_target`, which is the runner's explicit target query.  Tests mock that
query and never connect.
"""

import re
import subprocess

CONTAINER = "vmangos-mysql"
IMAGE = "mysql:5.6"
HOST = "127.0.0.1"
PORT = "3306"
SCHEMA_RE = re.compile(r"fn05h_v20_test_[0-9a-f]{12}\Z")


def parse_published_endpoint(output):
    """Return (host, port) from `docker port vmangos-mysql 3306` output.

    Only the exact loopback mapping is accepted; an empty, wildcard or
    multi-line result is rejected so a substituted endpoint cannot be used.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("expected exactly one published endpoint")
    match = re.fullmatch(r"([^:]+):(\d+)", lines[0])
    if not match:
        raise ValueError("malformed published endpoint")
    host, port = match.group(1), match.group(2)
    if host != HOST or port != PORT:
        raise ValueError(f"published endpoint must be {HOST}:{PORT}")
    return host, port


def validate_target(image, published_host, published_port, schema,
                    user, password):
    if image != IMAGE:
        return f"container image must be {IMAGE}"
    if published_host != HOST or published_port != PORT:
        return f"published endpoint must be {HOST}:{PORT}"
    if not SCHEMA_RE.fullmatch(schema or ""):
        return "schema must match fn05h_v20_test_[0-9a-f]{12}"
    if not user:
        return "user is required"
    if not password:
        return "password is required"
    return None


def docker_target(container=CONTAINER):
    """Query and validate the one permitted container target.

    Returns (host, port) on success and raises RuntimeError with a non-secret
    diagnostic otherwise.  No database connection is made.
    """
    if container != CONTAINER:
        raise RuntimeError(f"container must be {CONTAINER}")
    image = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", container],
        text=True, capture_output=True, check=True).stdout.strip()
    if image != IMAGE:
        raise RuntimeError(f"container image must be {IMAGE}")
    port_output = subprocess.run(
        ["docker", "port", container, "3306"],
        text=True, capture_output=True, check=True).stdout
    host, port = parse_published_endpoint(port_output)
    error = validate_target(image, host, port,
                            "fn05h_v20_test_0123456789ab", "root", "secret")
    if error:
        raise RuntimeError(error)
    return host, port
