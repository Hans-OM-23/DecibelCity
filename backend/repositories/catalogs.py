"""Carga inicial idempotente de catálogos; no sustituye validaciones geográficas."""

from psycopg import Connection
from collections.abc import Sequence

from schemas.noise import Hotspot, NoiseSourceOption

# INEI, página 74: https://www.inei.gob.pe/media/MenuRecursivo/publicaciones_digitales/Est/Lib1887/libro.pdf#page=74
# Solo se cargan nombres y UBIGEO; no población, coordenadas ni datos personales.
DISTRICT_CODES = {"Huancayo": "120101", "Chilca": "120107"}
SOURCE_CATEGORIES = {
    "traffic": "TRANSPORTE", "commerce": "COMERCIO", "construction": "CONSTRUCCION",
    "entertainment": "ACTIVIDAD_RECREATIVA", "other": "OTRAS",
}
CATEGORIES = (
    ("TRANSPORTE", "Transporte", "Tránsito vehicular particular y transporte público"),
    ("COMERCIO", "Comercio", "Actividad comercial, comercio ambulante y perifoneo"),
    ("CONSTRUCCION", "Construcción", "Obras de construcción y obras viales"),
    ("ACTIVIDAD_RECREATIVA", "Actividad recreativa", "Música, eventos y actividades de entretenimiento"),
    ("OTRAS", "Otras", "Fuentes sin clasificación específica en el formulario"),
)
ZONIFICATIONS = (
    ("ZONA_PROTECCION_ESPECIAL", "Zona de protección especial", "Clasificación pendiente de asignación territorial validada"),
    ("RESIDENCIAL", "Residencial", "Clasificación residencial; sin límites acústicos normativos cargados"),
    ("COMERCIAL", "Comercial", "Clasificación comercial; sin límites acústicos normativos cargados"),
    ("INDUSTRIAL", "Industrial", "Clasificación industrial; sin límites acústicos normativos cargados"),
)


def seed_references(connection: Connection) -> None:
    connection.execute("""INSERT INTO departamentos(ubigeo,nombre_departamento) VALUES ('12','Junín')
        ON CONFLICT(ubigeo) DO NOTHING""")
    connection.execute("""INSERT INTO provincias(id_departamento,ubigeo,nombre_provincia)
        VALUES ((SELECT id_departamento FROM departamentos WHERE ubigeo='12'),'1201','Huancayo')
        ON CONFLICT(ubigeo) DO NOTHING""")
    connection.cursor().executemany("""INSERT INTO distritos(id_provincia,ubigeo,nombre_distrito)
        VALUES ((SELECT id_provincia FROM provincias WHERE ubigeo='1201'),%s,%s)
        ON CONFLICT(ubigeo) DO NOTHING""", [(code, name) for name, code in DISTRICT_CODES.items()])
    connection.cursor().executemany("""INSERT INTO categorias_fuente_ruido(codigo,nombre,descripcion)
        VALUES (%s,%s,%s) ON CONFLICT(codigo) DO NOTHING""", CATEGORIES)
    connection.cursor().executemany("""INSERT INTO zonificaciones(codigo,nombre,descripcion)
        VALUES (%s,%s,%s) ON CONFLICT(codigo) DO NOTHING""", ZONIFICATIONS)
    # roles_usuario se prepara sin inventar roles ni crear cuentas.


def seed_sources(connection: Connection, sources: Sequence[NoiseSourceOption]) -> None:
    connection.cursor().executemany("""INSERT INTO tipos_fuente_ruido(codigo,nombre_fuente,id_categoria)
        VALUES (%s, %s, (SELECT id_categoria FROM categorias_fuente_ruido WHERE codigo=%s))
        ON CONFLICT(codigo) DO NOTHING""", [
            (source.value.value, source.label, SOURCE_CATEGORIES[source.value.value]) for source in sources
        ])
    connection.execute("""INSERT INTO estados_reporte(codigo_estado,descripcion)
        VALUES ('received','Reporte recibido; no constituye una denuncia formal')
        ON CONFLICT(codigo_estado) DO NOTHING""")


def insert_hotspot(connection: Connection, point: Hotspot, *, seed: bool = False) -> None:
    district_code = DISTRICT_CODES.get(point.district)
    district = connection.execute("SELECT id_distrito FROM distritos WHERE ubigeo=%s", (district_code,)).fetchone()
    district_id = district["id_distrito"] if district else None
    conflict = " ON CONFLICT(id_punto) DO NOTHING" if seed else ""
    connection.execute("""INSERT INTO puntos_criticos
        (id_punto,id_tipo_fuente,nombre,area,id_distrito,distrito_heredado,sector,nivel_db,
         etiqueta_fuente,descripcion,franja_horaria,x,y,limite_referencia,ilustrativo,latitud,longitud)
        VALUES (%s,(SELECT id_tipo_fuente FROM tipos_fuente_ruido WHERE codigo=%s),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """ + conflict, (
        point.id, point.source.value, point.name, point.area, district_id,
        None if district_id is not None else point.district, point.sector, point.decibels,
        point.source_label, point.description, point.time_label, point.x, point.y,
        point.reference_limit, point.illustrative, point.latitude, point.longitude,
    ))
