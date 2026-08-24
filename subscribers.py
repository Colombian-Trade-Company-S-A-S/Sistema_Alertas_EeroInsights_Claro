"""Receptores de alertas en Postgres (autogestion por WhatsApp).

Reemplaza la lista fija WA_RECIPIENTS: cada persona se da de ALTA o BAJA
escribiendo al bot. Todo vive en un esquema propio para no mezclarse con otras
tablas de la misma base de datos.

    esquema:  eero_insight_whatsapp
    tabla:    receptores_de_alertas (numero UNIQUE, activo, fecha_alta, fecha_baja)

Se abre una conexion por operacion: las operaciones son poco frecuentes y asi
es robusto frente a cortes de conexion del plan free de Render.
"""
import logging
import re

import psycopg2

log = logging.getLogger("subs")

_NOMBRE_VALIDO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalizar(numero):
    """Deja solo digitos (formato E.164 sin '+'), como los envia Meta."""
    return re.sub(r"\D", "", numero or "")


class SubscriberStore:
    def __init__(self, dsn, schema="eero_insight_whatsapp", sslmode="require"):
        if not _NOMBRE_VALIDO.match(schema):
            raise ValueError(f"Nombre de esquema invalido: {schema!r}")
        # Render Postgres soporta SSL en la URL interna y externa. Si el dsn ya
        # trae sslmode, se respeta; si no, se agrega el indicado.
        if "sslmode=" in dsn:
            self.dsn = dsn
        else:
            sep = "&" if "?" in dsn else "?"
            self.dsn = f"{dsn}{sep}sslmode={sslmode}"
        self.schema = schema
        self.tabla = f"{schema}.receptores_de_alertas"
        # No dejamos que un fallo transitorio al arrancar desactive todo: si el
        # _init falla, se reintenta en la primera operacion (las conexiones son
        # por-llamada). 'ready' solo es informativo para el log.
        try:
            self._init()
            self.ready = True
        except psycopg2.Error as e:
            self.ready = False
            log.error("No se pudo preparar la tabla al arrancar (se reintenta al usar): %s", e)

    def _connect(self):
        return psycopg2.connect(self.dsn, connect_timeout=15)

    def _init(self):
        """Crea el esquema y la tabla si no existen (idempotente)."""
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.tabla} (
                        id          SERIAL PRIMARY KEY,
                        numero      VARCHAR(20) UNIQUE NOT NULL,
                        activo      BOOLEAN NOT NULL DEFAULT true,
                        fecha_alta  TIMESTAMPTZ NOT NULL DEFAULT now(),
                        fecha_baja  TIMESTAMPTZ
                    )
                    """
                )
            log.info("Esquema/tabla de receptores listos (%s).", self.tabla)
        finally:
            conn.close()

    # ---------- alta / baja ----------

    def subscribe(self, numero):
        """Da de alta (o reactiva) un numero.

        Devuelve: 'nuevo' | 'reactivado' | 'ya_activo' | None (error).
        """
        num = normalizar(numero)
        if not num:
            return None
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT activo FROM {self.tabla} WHERE numero=%s", (num,)
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        f"INSERT INTO {self.tabla} (numero, activo) VALUES (%s, true)",
                        (num,),
                    )
                    return "nuevo"
                if not row[0]:
                    cur.execute(
                        f"UPDATE {self.tabla} SET activo=true, fecha_baja=NULL "
                        f"WHERE numero=%s",
                        (num,),
                    )
                    return "reactivado"
                return "ya_activo"
        except psycopg2.Error as e:
            log.error("Error al suscribir %s: %s", num, e)
            return None
        finally:
            conn.close()

    def unsubscribe(self, numero):
        """Da de baja un numero.

        Devuelve: 'dado_de_baja' | 'no_estaba' | None (error).
        """
        num = normalizar(numero)
        if not num:
            return None
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT activo FROM {self.tabla} WHERE numero=%s", (num,)
                )
                row = cur.fetchone()
                if row is None or not row[0]:
                    return "no_estaba"
                cur.execute(
                    f"UPDATE {self.tabla} SET activo=false, fecha_baja=now() "
                    f"WHERE numero=%s",
                    (num,),
                )
                return "dado_de_baja"
        except psycopg2.Error as e:
            log.error("Error al dar de baja %s: %s", num, e)
            return None
        finally:
            conn.close()

    # ---------- consulta ----------

    def active_numbers(self):
        """Numeros activos. Devuelve None si la DB falla (para usar fallback)."""
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT numero FROM {self.tabla} WHERE activo=true ORDER BY numero"
                )
                return [r[0] for r in cur.fetchall()]
        except psycopg2.Error as e:
            log.error("Error al leer receptores activos: %s", e)
            return None
        finally:
            conn.close()

    def count_active(self):
        nums = self.active_numbers()
        return len(nums) if nums is not None else 0

    def is_active(self, numero):
        """¿El numero esta de alta y activo? True / False, o None si la DB falla."""
        num = normalizar(numero)
        if not num:
            return False
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(f"SELECT activo FROM {self.tabla} WHERE numero=%s", (num,))
                row = cur.fetchone()
                return bool(row and row[0])
        except psycopg2.Error as e:
            log.error("Error al verificar suscripcion de %s: %s", num, e)
            return None
        finally:
            conn.close()
