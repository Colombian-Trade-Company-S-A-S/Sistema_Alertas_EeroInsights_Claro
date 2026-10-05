"""Motor de redes no saludables para WhatsApp. Dos modos (config.UNHEALTHY_TIEMPO_REAL):

TIEMPO REAL — `poll_once()` corre en el MISMO ciclo de las caidas y usa el MISMO
collector, asi que sus avisos llegan junto a los de interrupciones de red:
- Solo las CRITICAS notifican: alerta individual (plantilla de 8 variables) la
  primera vez que la red es critica; re-notificacion cada `renotify_minutes` como
  linea del consolidado; "Estado saludable" cuando sale de la lista.
- Las NO criticas solo se rastrean (cuentan en el menu, no envian WhatsApp).

DIARIO — `daily_report()` corre una vez al dia (job programado):
- Agrega al collector las redes con problema (todas, o solo criticas segun config)
  para enviar UN mensaje consolidado.
- Refresca el snapshot que consulta /estado (agrega/actualiza las actuales y marca
  como resueltas las que ya no aparecen).
"""
import logging
from datetime import datetime, timezone, timedelta

from eero_client import EeroAuthError
import network_labels

log = logging.getLogger("unhealthy")

SEVERITY = {"CRITICAL": ("🔴", "CRITICA"), "NON_CRITICAL": ("🟠", "NO CRITICA")}

ALERTS_ES = {
    "Wifi network conflict": "Conflicto WiFi",
    "WAN limited by ethernet speed": "WAN limitada por ethernet",
    "High channel utilization": "Alta utilizacion de canal",
    "Leaf eero outage over 5 min": "eero secundario caido +5min",
    "Gateway eero outage over 5 min": "eero principal caido +5min",
    "5 or more Network outages": "5+ caidas de red",
}


def _fmt_dt(iso_str):
    if not iso_str:
        return "N/D"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M:%S (COT)")
    except (ValueError, AttributeError):
        return iso_str


def _con_etiqueta(nid, name):
    label, nick = network_labels.get(nid)
    val = nick or label
    return f"{name} [{val}]" if val else name


class UnhealthyEngine:
    KIND = "unhealthy"

    def __init__(self, eero, collector, store, insight_template,
                 excluded=None, critical_only=False, renotify_minutes=10):
        self.eero = eero
        self.collector = collector
        self.store = store
        self.insight_template = insight_template
        # IDs de red (texto) a ignorar por completo: redes de prueba.
        self.excluded = set(excluded or ())
        # Si True, el reporte solo incluye criticas (las no criticas solo en /estado).
        self.critical_only = critical_only
        # Tiempo real: cada cuanto se re-notifica una critica que sigue activa.
        self.renotify_minutes = renotify_minutes

    def _net_name(self, network_id):
        return self.eero.network_info(network_id).get("name") or f"Red {network_id}"

    def _alerts_text(self, alerts):
        if not alerts:
            return "N/D"
        return ", ".join(ALERTS_ES.get(a, a) for a in alerts)

    def _conciso(self, net):
        nid = str(net["network_id"])
        _, label = SEVERITY.get(net.get("highest_severity", ""), ("⚪", "?"))
        name = _con_etiqueta(nid, self._net_name(nid))
        return f"{name} ({nid}): Estado {label}"

    def _should_renotify(self, row):
        last = datetime.fromisoformat(row["last_alert"])
        return (datetime.now(timezone.utc) - last).total_seconds() / 60 >= self.renotify_minutes

    def _params_individual(self, net):
        """Lista de 8 variables para la plantilla individual (misma de las caidas)."""
        nid = str(net["network_id"])
        _, label = SEVERITY.get(net.get("highest_severity", ""), ("⚪", "?"))
        return [
            f"Red NO SALUDABLE ({label})",                       # {{1}}
            _con_etiqueta(nid, self._net_name(nid)),             # {{2}}
            nid,                                                 # {{3}}
            net.get("network_type") or "N/D",                    # {{4}}
            self._alerts_text(net.get("alerts")),                # {{5}}
            str(net.get("count", "N/D")),                        # {{6}}
            _fmt_dt(net.get("last_occurrence")),                 # {{7}}
            self.insight_template.format(network_id=nid),        # {{8}}
        ]

    def poll_once(self):
        """Ciclo en TIEMPO REAL (junto a las caidas): notifica solo las CRITICAS.

        'notificada' = ya se envio al menos un aviso (alert_count > 0). Asi una red
        que escala de NO CRITICA a CRITICA recibe su alerta individual la primera
        vez que es critica, y las que venian del reporte diario (rastreadas con
        bump=False) tambien.
        """
        log.info("Consultando redes no saludables (tiempo real)...")
        dry = getattr(self.collector, "dry_run", False)
        try:
            nets = self.eero.unhealthy_networks()
        except EeroAuthError:
            # El aviso de token lo da el motor de caidas. No se toca el store para
            # no disparar falsos "saludable".
            log.warning("Token fallo al consultar unhealthy (lo notifica el motor de caidas).")
            return

        activos = {str(n["network_id"]): n for n in nets if not n.get("is_deleted")}
        if self.excluded:
            antes = len(activos)
            activos = {nid: n for nid, n in activos.items() if nid not in self.excluded}
            if antes != len(activos):
                log.info("No saludables: %d red(es) de prueba excluidas.", antes - len(activos))
        criticas = sum(1 for n in activos.values() if n.get("highest_severity") == "CRITICAL")
        log.info("Redes no saludables: %d (criticas: %d)", len(activos), criticas)

        for nid, net in activos.items():
            row = self.store.get(nid, kind=self.KIND)
            notificada = row is not None and row["alert_count"] > 0
            bump = False
            if net.get("highest_severity") == "CRITICAL":
                if not notificada:
                    self.collector.send_individual(self._params_individual(net))
                    bump = True
                elif self._should_renotify(row):
                    self.collector.add(self._conciso(net))
                    bump = True
            # NO criticas (y criticas entre re-notificaciones): solo rastreo.
            if not dry:
                self.store.upsert_alert(
                    nid, net.get("highest_severity"), kind=self.KIND,
                    detalle=self._alerts_text(net.get("alerts")),
                    name=self._net_name(nid), bump=bump,
                )

        for nid in self.store.all_ids(kind=self.KIND) - set(activos.keys()):
            if nid in self.excluded:
                # Red de prueba: se limpia en silencio, sin anunciar ni registrar.
                if not dry:
                    self.store.remove(nid, kind=self.KIND)
                continue
            row = self.store.get(nid, kind=self.KIND)
            name = (row["name"] if row and row["name"] else self._net_name(nid))
            # Solo se anuncia el cierre si la red llego a notificarse (fue critica).
            if row and row["alert_count"] > 0:
                self.collector.add(f"{_con_etiqueta(nid, name)} ({nid}): Estado saludable")
            if not dry:
                self.store.record_resolution(
                    self.KIND, nid, name,
                    row["detalle"] if row else None,
                    row["first_alert"] if row else None,
                    row["alert_count"] if row else 0,
                )
                self.store.remove(nid, kind=self.KIND)

    def daily_report(self, send=True):
        """Genera el reporte diario y refresca el snapshot del store.

        send=True: agrega las redes con problema al collector (consolidado de
        problemas) y DEVUELVE la lista de lineas de las redes RECUPERADAS desde el
        ultimo reporte (para que el caller las envie como un mensaje de cierre
        aparte). send=False: solo refresca el snapshot (para /estado), sin enviar
        ni anunciar cierres (se usa al arrancar si el snapshot esta vacio).

        Retorna: lista de lineas de recuperadas (vacia si no hay o si send=False).
        """
        log.info("Reporte diario de redes no saludables (send=%s)...", send)
        dry = getattr(self.collector, "dry_run", False)
        try:
            nets = self.eero.unhealthy_networks()
        except EeroAuthError:
            log.warning("Token fallo al consultar unhealthy (reporte diario).")
            return []

        activos = {str(n["network_id"]): n for n in nets if not n.get("is_deleted")}
        if self.excluded:
            antes = len(activos)
            activos = {nid: n for nid, n in activos.items() if nid not in self.excluded}
            if antes != len(activos):
                log.info("Reporte diario: %d red(es) de prueba excluidas.", antes - len(activos))
        log.info("Redes no saludables (reporte diario): %d", len(activos))

        # Criticas primero en el consolidado (van arriba del mensaje).
        def _orden(item):
            return 0 if item[1].get("highest_severity") == "CRITICAL" else 1

        for nid, net in sorted(activos.items(), key=_orden):
            critica = net.get("highest_severity") == "CRITICAL"
            if send and (critica or not self.critical_only):
                self.collector.add(self._conciso(net))
            if not dry:
                # Solo rastreo (bump=False): no infla el contador de avisos.
                self.store.upsert_alert(
                    nid, net.get("highest_severity"), kind=self.KIND,
                    detalle=self._alerts_text(net.get("alerts")),
                    name=self._net_name(nid), bump=False,
                )

        # Redes que ya no estan = RECUPERADAS: se anuncian (mensaje de cierre
        # aparte), se marcan resueltas y se quitan del snapshot.
        recuperadas = []
        for nid in self.store.all_ids(kind=self.KIND) - set(activos.keys()):
            if nid in self.excluded:
                # Red de prueba: se limpia en silencio, sin anunciar ni registrar.
                if not dry:
                    self.store.remove(nid, kind=self.KIND)
                continue
            row = self.store.get(nid, kind=self.KIND)
            name = (row["name"] if row and row["name"] else self._net_name(nid))
            if send:
                recuperadas.append(f"{_con_etiqueta(nid, name)} ({nid}): Estado recuperada")
            if not dry:
                self.store.record_resolution(
                    self.KIND, nid, name,
                    row["detalle"] if row else None,
                    row["first_alert"] if row else None,
                    row["alert_count"] if row else 0,
                )
                self.store.remove(nid, kind=self.KIND)
        return recuperadas
