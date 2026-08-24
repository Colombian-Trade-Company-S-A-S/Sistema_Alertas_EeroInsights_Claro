"""Webhook de WhatsApp Cloud API (Flask).

GET  /webhook  -> verificacion con Meta (hub.challenge).
POST /webhook  -> recibe mensajes entrantes, ejecuta el comando y responde.
GET  /         -> healthcheck para Render.
"""
import logging
import os
import re

from flask import Flask, request

import config
import commands

log = logging.getLogger("webhook")


def _digitos(numero):
    """Deja solo los digitos (Meta envia el 'from' sin '+')."""
    return re.sub(r"\D", "", numero or "")


def _autorizado(numero, subs):
    """True si el numero puede usar el bot (esta de ALTA). Con BOT_SOLO_SUSCRITOS
    apagado, todos autorizados. Si la DB no responde, se autoriza (no bloquear)."""
    if not config.BOT_SOLO_SUSCRITOS:
        return True
    if subs is not None:
        activo = subs.is_active(numero)
        if activo is None:  # DB no responde -> no bloquear a los reales
            log.warning("No pude verificar suscripcion de %s (DB); se responde igual.", numero)
            return True
        return activo
    # Sin DB: autorizados = WA_RECIPIENTS (normalizados a solo digitos).
    return numero in {_digitos(n) for n in config.WA_RECIPIENTS}


def create_app(store, wa, subs=None):
    app = Flask(__name__)

    # Consulta abierta: cualquiera que escriba puede usar el menu (opciones 1-6),
    # este suscrito o no. El PUSH automatico (modelo hibrido) va SOLO a los numeros
    # de ALERTAS_PUSH_NUMBERS; esto de aqui es la via de CONSULTA para todos.

    @app.get("/")
    def health():
        return "ok", 200

    @app.get("/diag")
    def diag():
        """Diagnostico visible en el navegador (sin exponer secretos).

        Abrir: https://<tu-servicio>.onrender.com/diag
        """
        import sys
        url = os.getenv("DATABASE_URL")
        try:
            import psycopg2
            pg = psycopg2.__version__
        except Exception as e:  # noqa: BLE001
            pg = f"ERROR: {e}"
        info = {
            "python": sys.version.split()[0],
            "psycopg2": pg,
            "database_url_presente": bool(url),
            "database_url_longitud": len(url) if url else 0,
            "autogestion_activa": subs is not None,
        }
        if subs is not None:
            nums = subs.active_numbers()
            info["db_responde"] = nums is not None
            info["receptores_activos"] = len(nums) if nums is not None else 0
        return info, 200

    @app.get("/webhook")
    def verify():
        args = request.args
        if (args.get("hub.mode") == "subscribe"
                and args.get("hub.verify_token") == config.WA_VERIFY_TOKEN):
            log.info("Webhook verificado por Meta.")
            return args.get("hub.challenge", ""), 200
        log.warning("Verificacion de webhook fallida.")
        return "forbidden", 403

    @app.post("/webhook")
    def receive():
        data = request.get_json(silent=True) or {}
        try:
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    for msg in value.get("messages", []):
                        if msg.get("type") != "text":
                            continue
                        frm = msg.get("from")
                        texto = (msg.get("text") or {}).get("body", "")
                        sender = _digitos(frm)
                        # Bot exclusivo: si el numero no esta de alta, no se responde.
                        if not _autorizado(sender, subs):
                            log.info("Mensaje de %s IGNORADO (no esta de alta).", frm)
                            continue
                        log.info("Comando de %s: %r", frm, texto)
                        respuesta = commands.dispatch(texto, store, subs=subs, sender=sender)
                        wa.send_text(frm, respuesta)
        except Exception:  # noqa: BLE001
            log.exception("Error procesando webhook.")
        # Siempre 200 para que Meta no reintente en bucle.
        return "ok", 200

    return app
