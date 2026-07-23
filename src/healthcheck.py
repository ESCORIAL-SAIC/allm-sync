"""Healthcheck de liveness para el container (worker sin API HTTP).

Sale 0 si el loop refrescó el heartbeat hace menos de HEARTBEAT_MAX_AGE segundos,
1 en caso contrario. Lo usa el HEALTHCHECK del Dockerfile.
"""
import os
import sys
import time

hb = os.getenv("HEARTBEAT_FILE", "/state/heartbeat")
max_age = int(os.getenv("HEARTBEAT_MAX_AGE", "300"))

try:
    age = time.time() - os.path.getmtime(hb)
except OSError:
    print("sin heartbeat todavía", file=sys.stderr)
    sys.exit(1)

if age > max_age:
    print(f"heartbeat viejo: {age:.0f}s > {max_age}s", file=sys.stderr)
    sys.exit(1)

print(f"ok: heartbeat de hace {age:.0f}s")
sys.exit(0)
