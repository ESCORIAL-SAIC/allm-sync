"""Punto de entrada: loop de polling con parada limpia ante SIGTERM/SIGINT."""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

from allm_client import AllmClient, AllmError
from config import load_config
from reconciler import Reconciler
from state import State

APP_VERSION = os.getenv("APP_VERSION", "dev")
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/state/heartbeat")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    logging.getLogger("allm").info("Señal %s recibida, cerrando tras el ciclo actual...", signum)
    _stop = True


def _beat() -> None:
    """Refresca el heartbeat de liveness (lo lee healthcheck.py)."""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        logging.getLogger("allm").warning("No se pudo escribir heartbeat %s: %s", HEARTBEAT_FILE, exc)


def main() -> int:
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("allm")
    log.info("allm-sync v%s iniciando | root=%s | allm=%s | intervalo=%ss | dry_run=%s",
             APP_VERSION, cfg.root_path, cfg.base_url, cfg.poll_interval_seconds, cfg.dry_run)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = AllmClient(cfg.base_url, cfg.api_key, timeout=cfg.http_timeout)

    # Verificación de conectividad/credenciales antes de empezar el loop.
    try:
        if not client.verify_auth():
            log.error("La API key no es válida (auth=false). Abortando.")
            return 1
        log.info("Autenticación con AnythingLLM OK.")
    except AllmError as exc:
        log.error("No se pudo contactar AnythingLLM en %s: %s", cfg.base_url, exc)
        return 1

    state = State(cfg.state_db_path)
    reconciler = Reconciler(cfg, client, state, heartbeat=_beat)

    try:
        _beat()  # heartbeat inicial: el proceso está vivo aunque el 1er ciclo tarde
        while not _stop:
            start = time.monotonic()
            try:
                stats = reconciler.run_cycle()
                log.info("Ciclo completo | %s", stats.summary())
            except Exception as exc:  # noqa: BLE001 - un ciclo no debe tumbar el proceso
                log.exception("Fallo inesperado en el ciclo: %s", exc)
            # El heartbeat marca "el loop sigue vivo", aunque el ciclo haya tenido errores.
            _beat()

            # Espera interrumpible: chequea _stop cada segundo.
            elapsed = time.monotonic() - start
            remaining = max(0.0, cfg.poll_interval_seconds - elapsed)
            while remaining > 0 and not _stop:
                nap = min(1.0, remaining)
                time.sleep(nap)
                remaining -= nap
    finally:
        state.close()
        log.info("allm-sync detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
