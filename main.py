"""Servicio WhatsApp: polling de eero (alertas consolidadas) + webhook de comandos."""
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import logging
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from apscheduler.schedulers.background import BackgroundScheduler

import config
from eero_client import EeroClient
from state import StateStore
from whatsapp_cloud import WhatsAppCloud
from batch import Collector
from alert_engine import AlertEngine
from unhealthy_engine import UnhealthyEngine
from webhook import create_app

try:
    from subscribers import SubscriberStore
except ImportError:  # psycopg2 no instalado
    SubscriberStore = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")


def _host_seguro(url):
    """Extrae el host de la URL de la DB sin exponer la contrasena (para el log)."""
    m = re.search(r"@([^/:?]+)", url or "")
    return m.group(1) if m else "?"


def _build_subscribers():
    """Crea el store de receptores en Postgres (o None si no hay DB configurada).

    Ojo: si DATABASE_URL existe, se devuelve el store SIEMPRE (aunque la DB este
    momentaneamente caida). Las operaciones se reintentan por-llamada, asi que la
    autogestion no queda desactivada por un fallo transitorio del arranque.
    """
    # --- Diagnostico: que ve el proceso realmente (sin exponer valores) ---
    val = os.getenv("DATABASE_URL")
    log.info("DIAG DATABASE_URL: presente=%s longitud=%s",
             val is not None, len(val) if val else 0)
    relacionadas = sorted(
        k for k in os.environ
        if any(t in k.upper() for t in ("DATA", "POSTGRES", "_DB", "DB_", "URL"))
    )
    log.info("DIAG variables de entorno relacionadas visibles: %s", relacionadas)

    if SubscriberStore is None:
        log.warning("psycopg2 no instalado: autogestion DESACTIVADA (fallback WA_RECIPIENTS).")
        return None
    if not config.DATABASE_URL:
        log.warning("Sin DATABASE_URL: autogestion DESACTIVADA (fallback WA_RECIPIENTS).")
        return None

    log.info("DATABASE_URL detectada (host=%s, sslmode=%s). Preparando receptores...",
             _host_seguro(config.DATABASE_URL), config.DB_SSLMODE)
    subs = SubscriberStore(
        config.DATABASE_URL, config.SUBSCRIBERS_SCHEMA, sslmode=config.DB_SSLMODE
    )
    # Migracion: mete los numeros de WA_RECIPIENTS que aun no esten en la tabla.
    for numero in config.WA_RECIPIENTS:
        if subs.subscribe(numero) == "nuevo":
            log.info("Migrado a receptores: %s", numero)

    activos = subs.active_numbers()
    if activos is None:
        log.warning("Autogestion ACTIVA pero la DB no respondio al arrancar "
                    "(se reintenta al usar). Revisa host/region/credenciales.")
    else:
        log.info("Autogestion ACTIVA. Receptores activos: %d.", len(activos))
    return subs


def build():
    eero = EeroClient(config.EERO_ADMIN_TOKEN, config.EERO_ORG_ID)
    store = StateStore(config.DB_PATH)
    subs = _build_subscribers()
    wa = WhatsAppCloud(
        config.WA_TOKEN, config.WA_PHONE_NUMBER_ID,
        api_version=config.WA_API_VERSION, dry_run=config.DRY_RUN,
    )

    def _mk_collector():
        # HIBRIDO: el push automatico va SOLO a ALERTAS_PUSH_NUMBERS (max 2).
        # La consulta por menu la maneja el webhook y funciona para todos.
        return Collector(
            wa, config.ALERTAS_PUSH_NUMBERS,
            config.WA_TEMPLATE_INDIVIDUAL, config.WA_TEMPLATE_INDIVIDUAL_LANG,
            config.WA_TEMPLATE_CONSOL, config.WA_TEMPLATE_CONSOL_LANG,
            budget=config.WA_BODY_BUDGET, max_count=config.WA_BATCH_MAX,
            dry_run=config.DRY_RUN, push_enabled=config.ALERTAS_PUSH_ENABLED,
        )

    # Collectors separados: las caidas (interval) y el reporte diario (cron) corren
    # en hilos distintos del scheduler; no deben compartir estado mutable.
    collector = _mk_collector()          # caidas (tiempo real)
    collector_diario = _mk_collector()   # reporte diario de no saludables
    engine = AlertEngine(
        eero, collector, store,
        insight_template=config.INSIGHT_URL_TEMPLATE,
        renotify_minutes=config.RENOTIFY_MINUTES,
        excluded=config.EXCLUDED_NETWORK_IDS,
    )
    unhealthy = UnhealthyEngine(
        eero, collector_diario, store,
        insight_template=config.INSIGHT_URL_TEMPLATE,
        excluded=config.EXCLUDED_NETWORK_IDS,
        critical_only=config.UNHEALTHY_REPORT_CRITICAL_ONLY,
    )
    return store, wa, collector, collector_diario, engine, unhealthy, subs


def poll_outages(collector, engine):
    """Ciclo de CAIDAS (tiempo real): notifica nuevas y re-notifica activas."""
    collector.reset()
    try:
        engine.poll_once()
    except Exception:  # noqa: BLE001
        log.exception("Error en el ciclo de caidas (se continua).")
    collector.flush()


def reporte_diario(collector_diario, unhealthy):
    """Reporte DIARIO de redes no saludables en la manana.

    Envia hasta dos consolidados: (1) las redes con problema y (2) un mensaje de
    CIERRE aparte con las que se recuperaron desde el ultimo reporte.
    """
    if not config.UNHEALTHY_ENABLED:
        return
    collector_diario.reset()
    recuperadas = []
    try:
        recuperadas = unhealthy.daily_report(send=True)
    except Exception:  # noqa: BLE001
        log.exception("Error en el reporte diario de no saludables (se continua).")
    collector_diario.flush()  # mensaje 1: problemas actuales
    # Mensaje 2 (aparte): cierre de las recuperadas.
    if recuperadas:
        log.info("Cierre no-saludables: %d red(es) recuperada(s).", len(recuperadas))
        collector_diario.reset()
        for linea in recuperadas:
            collector_diario.add(linea)
        collector_diario.flush()


def main():
    store, wa, collector, collector_diario, engine, unhealthy, subs = build()

    if len(sys.argv) > 1:
        modo = sys.argv[1]
        if modo == "once":                          # solo caidas (prueba)
            poll_outages(collector, engine)
            return
        if modo in ("reporte", "diario", "unhealthy"):  # solo reporte diario (prueba)
            reporte_diario(collector_diario, unhealthy)
            return

    activos = subs.count_active() if subs is not None else len(config.WA_RECIPIENTS)
    log.info(
        "Iniciando WhatsApp. Caidas cada %d min | re-notif %d min | reporte no-saludables %02d:00 COT | DRY_RUN=%s | receptores=%d | excluidas=%d",
        config.POLL_MINUTES, config.RENOTIFY_MINUTES, config.UNHEALTHY_REPORT_HOUR,
        config.DRY_RUN, activos, len(config.EXCLUDED_NETWORK_IDS),
    )
    if config.ALERTAS_PUSH_TRUNCADO:
        log.warning("ALERTAS_PUSH_NUMBERS tiene mas de 2 numeros; se usan solo los 2 primeros.")
    if config.ALERTAS_PUSH_ENABLED and config.ALERTAS_PUSH_NUMBERS:
        log.info("MODO HIBRIDO: push automatico a %d numero(s): %s. Los demas solo consultan por el menu.",
                 len(config.ALERTAS_PUSH_NUMBERS), ", ".join(config.ALERTAS_PUSH_NUMBERS))
    elif config.ALERTAS_PUSH_ENABLED:
        log.warning("ALERTAS_PUSH_ENABLED=true pero ALERTAS_PUSH_NUMBERS esta VACIO: nadie recibe push. "
                    "Configura hasta 2 numeros para reactivarlo.")
    else:
        log.warning("MODO CONSULTA (ALERTAS_PUSH_ENABLED=false): nadie recibe push; todos consultan por el menu.")
    if config.EXCLUDED_NETWORK_IDS:
        log.info("Redes de prueba excluidas: %s", ", ".join(sorted(config.EXCLUDED_NETWORK_IDS)))

    # Al arrancar, si el snapshot de no saludables esta vacio (primer despliegue),
    # se refresca en silencio para que /estado no salga vacio antes del reporte.
    if config.UNHEALTHY_ENABLED and not store.all_ids("unhealthy"):
        log.info("Snapshot de no saludables vacio: refrescando en silencio para /estado.")
        try:
            unhealthy.daily_report(send=False)
        except Exception:  # noqa: BLE001
            log.exception("Error refrescando snapshot inicial de no saludables.")

    sched = BackgroundScheduler(timezone="America/Bogota")
    sched.add_job(
        poll_outages, "interval", minutes=config.POLL_MINUTES,
        args=[collector, engine],
        misfire_grace_time=300, coalesce=True,
    )
    if config.UNHEALTHY_ENABLED:
        sched.add_job(
            reporte_diario, "cron", hour=config.UNHEALTHY_REPORT_HOUR, minute=0,
            args=[collector_diario, unhealthy],
            misfire_grace_time=3600, coalesce=True,
        )
    sched.start()
    poll_outages(collector, engine)  # corrida inmediata de caidas

    # Servidor webhook (bloquea). Render enruta el trafico al PORT.
    app = create_app(store, wa, subs)
    from waitress import serve
    log.info("Webhook escuchando en 0.0.0.0:%d", config.PORT)
    serve(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
