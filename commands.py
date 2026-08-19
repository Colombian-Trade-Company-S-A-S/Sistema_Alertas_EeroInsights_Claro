"""Comandos por WhatsApp: construyen respuestas de texto libre (ventana 24h).

Formato WhatsApp: *negrita*  _cursiva_ . Un solo mensaje consolidado por comando.
"""
import re
from datetime import datetime, timezone, timedelta

import config
import network_labels

COT = timezone(timedelta(hours=-5))

# WhatsApp corta el texto ~4096 caracteres; dejamos margen en /estado.
BUDGET_ESTADO = 3800


def _ids_incidencia(nid):
    """Linea de identificadores: Cuenta Claro (customer_account) + Identificador de
    la casa (Home Identifier) cuando existan, y siempre el ID de eero."""
    info = network_labels.get_full(nid)
    partes = []
    if info["customer_account"]:
        partes.append(f"Cuenta Claro: {info['customer_account']}")
    if info["label"]:
        partes.append(f"Casa: {info['label']}")
    partes.append(f"ID eero: {nid}")
    return " · ".join(partes)


def _bloque_incidencia(nid, name, criticidad):
    """Bloque de detalle de una incidencia para /estado: cliente, IDs (cuenta
    Claro + identificador de la casa + ID de eero), criticidad y enlace a Insight."""
    nid = str(nid)
    info = network_labels.get_full(nid)
    cliente = name or f"Red {nid}"
    if info["nickname"]:
        cliente = f"{cliente} ({info['nickname']})"
    url = config.INSIGHT_URL_TEMPLATE.format(network_id=nid)
    return (f"• *{cliente}*\n"
            f"  {_ids_incidencia(nid)}\n"
            f"  Criticidad: {criticidad}\n"
            f"  🔗 {url}")


def _solo_digitos(s):
    return re.sub(r"\D", "", s or "")


def help_text():
    return (
        "🤖 *Sistema de Alertas eero (WhatsApp)*\n"
        "_Las alertas se consultan por aqui (opciones 1 y 2)._\n\n"
        "*Responde con el numero de una opcion:*\n\n"
        "1️⃣  🚨 Alertas (incidencias activas ahora)\n"
        "2️⃣  🔁 Renotificaciones (pendientes: tiempo y avisos)\n"
        "3️⃣  ✅ Soluciones del dia\n"
        "4️⃣  ➕ Suscribir este numero\n"
        "5️⃣  ➖ Dar de baja este numero\n"
        "6️⃣  ❓ Ver este menu\n\n"
        "_Para otro numero: escribe 4 573001112233 (suscribir) "
        "o 5 573001112233 (baja)._"
    )


# Palabras que activan alta/baja (con o sin '/', en mayus o minus).
ALTA = {"suscribir", "suscribirme", "alta", "suscribete", "activar"}
BAJA = {"baja", "desuscribir", "desuscribirme", "cancelar", "salir"}


_NO_CONFIG = ("⚠️ La autogestion de suscripciones no esta configurada "
              "(falta la variable DATABASE_URL en el servidor).")
_DB_ERROR = ("⚠️ No pude conectar con la base de datos en este momento. "
             "Intenta de nuevo en un minuto.")


def _quien(numero, sender):
    """Texto para el numero afectado: 'este numero' si es el propio, o el numero."""
    return "este numero" if numero == sender else f"el numero {numero}"


def alta_text(subs, numero, sender):
    if subs is None:
        return _NO_CONFIG
    estado = subs.subscribe(numero)
    quien = _quien(numero, sender)
    if estado == "nuevo":
        return (f"✅ *Suscrito:* {quien} recibira las alertas de eero.\n"
                "Escribe *5* para darlo de baja.")
    if estado == "reactivado":
        return (f"✅ *Reactivado:* {quien} vuelve a recibir alertas.\n"
                "Escribe *5* para darlo de baja.")
    if estado == "ya_activo":
        return (f"ℹ️ {quien.capitalize()} *ya estaba suscrito*.\n"
                "Escribe *5* para dejar de recibir alertas.")
    return _DB_ERROR


def baja_text(subs, numero, sender):
    if subs is None:
        return _NO_CONFIG
    estado = subs.unsubscribe(numero)
    quien = _quien(numero, sender)
    if estado == "dado_de_baja":
        return (f"✅ *Baja realizada:* {quien} ya no recibira alertas.\n"
                "Escribe *4* para volver a activarlas.")
    if estado == "no_estaba":
        return (f"ℹ️ {quien.capitalize()} *no estaba suscrito*.\n"
                "Escribe *4* para recibir alertas.")
    return _DB_ERROR


def _fmt_local(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(COT).strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return iso or "N/D"


def _dur(a, b):
    try:
        m = int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)
        return f"{m} min" if m < 60 else f"{m // 60}h {m % 60}m"
    except (ValueError, TypeError):
        return "N/D"


def _start_of_today_utc():
    inicio = datetime.now(COT).replace(hour=0, minute=0, second=0, microsecond=0)
    return inicio.astimezone(timezone.utc).isoformat()


def estado_text(store):
    """Resumen de alertas: caidas (tiempo real) + no saludables (reporte del dia).
    Cada incidencia con cliente, cuenta Claro + ID de eero, criticidad y enlace."""
    caidas = store.all_active("outage")
    unhealthy = store.all_active("unhealthy")
    criticas = [r for r in unhealthy if r["ref"] == "CRITICAL"]
    no_criticas = [r for r in unhealthy if r["ref"] != "CRITICAL"]

    partes = [
        "🚨 *Alertas — incidencias activas*\n",
        f"🚨 *Caidas activas (tiempo real): {len(caidas)}*",
        f"🩺 *No saludables (reporte del dia): {len(unhealthy)}*",
    ]
    if not caidas and not unhealthy:
        partes.append("\n_Sin novedades activas._ 🎉")
        return "\n".join(partes)

    # Secciones en orden; cada bloque se agrega mientras quepa en el presupuesto.
    secciones = [
        ("\n🚨 *Caidas:*", caidas, "🚨 Caida"),
        (f"\n🔴 *No saludables criticas ({len(criticas)}):*", criticas, "🔴 Critica"),
        (f"\n🟠 *No saludables NO criticas ({len(no_criticas)}):*", no_criticas, "🟠 No critica"),
    ]
    omitidas = 0
    for titulo, rows, criticidad in secciones:
        if not rows:
            continue
        partes.append(titulo)
        for r in rows:
            bloque = _bloque_incidencia(r["item_id"], r["name"], criticidad)
            if sum(len(p) for p in partes) + len(bloque) + 1 > BUDGET_ESTADO:
                omitidas += 1
                continue
            partes.append(bloque)
    if omitidas:
        partes.append(f"\n_… y {omitidas} incidencia(s) mas. Usa la opcion 3 para verlas._")
    return "\n".join(partes)


def soluciones_text(store):
    rows = store.resolutions_since(_start_of_today_utc())
    if not rows:
        return "✅ *Soluciones del dia*\n\nHoy no se ha resuelto ninguna novedad."
    partes = [f"✅ *Soluciones del dia* ({len(rows)})\n"]
    for r in rows:
        name = r["name"] or f"Red {r['item_id']}"
        tipo = "Caida" if r["kind"] == "outage" else "No saludable"
        partes.append(
            f"• *{name}* ({r['item_id']}) · {tipo}\n"
            f"  Solucionada: {_fmt_local(r['resolved_at'])} · "
            f"Duro: {_dur(r['first_alert'], r['resolved_at'])} · Avisos: {r['alert_count']}\n"
            f"  {r['detalle'] or '-'}"
        )
    return "\n".join(partes)


def sin_solucionar_text(store):
    rows = store.all_active()
    if not rows:
        return "🔁 *Renotificaciones (pendientes)*\n\nNo hay incidencias pendientes. 🎉"
    now = datetime.now(timezone.utc).isoformat()
    partes = [f"🔁 *Renotificaciones — pendientes* ({len(rows)})\n"]
    for r in rows:
        name = r["name"] or f"Red {r['item_id']}"
        tipo = "Caida" if r["kind"] == "outage" else "No saludable"
        partes.append(
            f"• *{name}* · {tipo}\n"
            f"  {_ids_incidencia(r['item_id'])}\n"
            f"  Lleva: {_dur(r['first_alert'], now)} · Avisos: {r['alert_count']}\n"
            f"  {r['detalle'] or '-'}"
        )
    return "\n".join(partes)


def dispatch(text, store, subs=None, sender=None):
    """Devuelve el texto de respuesta para un mensaje entrante.

    Menu por NUMEROS (1..6). Se mantienen los /comandos y palabras como alias
    ocultos por compatibilidad. 'subs' es el SubscriberStore (o None) y 'sender'
    el numero que escribio, para las altas/bajas.
    """
    raw = (text or "").strip()
    partes = raw.split()
    if not partes:
        return help_text()

    # Primer token normalizado: sin '/', en minuscula. Si empieza por digitos se
    # toma la opcion numerica (tolera "1", "1.", "1)", "1️⃣").
    tok = partes[0].lstrip("/").lower()
    mdig = re.match(r"\d+", tok)
    opcion = mdig.group(0) if mdig else tok

    # Argumento opcional: numero de telefono destino (para suscribir/dar de baja
    # a OTRO numero, p. ej. "4 573001112233"). Si no se pasa, es quien escribe.
    arg = _solo_digitos(partes[1]) if len(partes) > 1 else ""
    objetivo = arg or sender

    if opcion in ("1", "alertas", "alerta", "estado"):
        return estado_text(store)          # Alertas = incidencias activas
    if opcion in ("2", "renotificaciones", "renotificacion", "renotif",
                  "sin_solucionar", "pendientes"):
        return sin_solucionar_text(store)  # Renotificaciones = pendientes (tiempo/avisos)
    if opcion in ("3", "soluciones", "solucionadas"):
        return soluciones_text(store)
    if opcion == "4" or opcion in ALTA:
        return alta_text(subs, objetivo, sender)
    if opcion == "5" or opcion in BAJA:
        return baja_text(subs, objetivo, sender)
    # 6, help, ayuda, menu o cualquier otra cosa -> muestra el menu.
    return help_text()
