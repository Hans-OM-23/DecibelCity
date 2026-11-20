"""Carga inicial idempotente de catálogos; no sustituye validaciones geográficas."""

from psycopg import Connection
from collections.abc import Sequence

from schemas.noise import Hotspot, NoiseSourceOption

# Catálogo territorial de Junín del INEI para 2017: 9 provincias y 124 distritos.
# Fecundidad y sus Diferenciales (2022), pp. 74–77; cotejados UBIGEO, nombre y provincia:
# https://www.inei.gob.pe/media/MenuRecursivo/publicaciones_digitales/Est/Lib1887/libro.pdf#page=74
# Se conservan las tildes de Huancán, Pucará, Huamalí, Pancán, Ricrán y Chunán y la forma
# completa Santa Bárbara de Carhuacayán: Compendio Junín 2010, cuadro 1.5, pp. 56–58.
# https://www1.inei.gob.pe/media/MenuRecursivo/publicaciones_digitales/Est/Lib0968/libro.pdf#page=53
# Solo se cargan nombres y UBIGEO; no población, coordenadas ni datos personales.
# La provincia de cada distrito son los cuatro primeros dígitos de su UBIGEO.
DEFAULT_ROLE = "ciudadano"
JUNIN_UBIGEO = "12"
JUNIN_PROVINCES = (
    ("1201", "Huancayo"), ("1202", "Concepción"), ("1203", "Chanchamayo"), ("1204", "Jauja"),
    ("1205", "Junín"), ("1206", "Satipo"), ("1207", "Tarma"), ("1208", "Yauli"), ("1209", "Chupaca"),
)
JUNIN_DISTRICTS = (
    # 1201 Huancayo (28)
    ("120101", "Huancayo"), ("120104", "Carhuacallanga"), ("120105", "Chacapampa"), ("120106", "Chicche"),
    ("120107", "Chilca"), ("120108", "Chongos Alto"), ("120111", "Chupuro"), ("120112", "Colca"),
    ("120113", "Cullhuas"), ("120114", "El Tambo"), ("120116", "Huacrapuquio"), ("120117", "Hualhuas"),
    ("120119", "Huancán"), ("120120", "Huasicancha"), ("120121", "Huayucachi"), ("120122", "Ingenio"),
    ("120124", "Pariahuanca"), ("120125", "Pilcomayo"), ("120126", "Pucará"), ("120127", "Quichuay"),
    ("120128", "Quilcas"), ("120129", "San Agustín"), ("120130", "San Jerónimo de Tunán"), ("120132", "Saño"),
    ("120133", "Sapallanga"), ("120134", "Sicaya"), ("120135", "Santo Domingo de Acobamba"), ("120136", "Viques"),
    # 1202 Concepción (15)
    ("120201", "Concepción"), ("120202", "Aco"), ("120203", "Andamarca"), ("120204", "Chambara"),
    ("120205", "Cochas"), ("120206", "Comas"), ("120207", "Heroínas Toledo"), ("120208", "Manzanares"),
    ("120209", "Mariscal Castilla"), ("120210", "Matahuasi"), ("120211", "Mito"), ("120212", "Nueve de Julio"),
    ("120213", "Orcotuna"), ("120214", "San José de Quero"), ("120215", "Santa Rosa de Ocopa"),
    # 1203 Chanchamayo (6)
    ("120301", "Chanchamayo"), ("120302", "Perené"), ("120303", "Pichanaqui"), ("120304", "San Luis de Shuaro"),
    ("120305", "San Ramón"), ("120306", "Vitoc"),
    # 1204 Jauja (34)
    ("120401", "Jauja"), ("120402", "Acolla"), ("120403", "Apata"), ("120404", "Ataura"),
    ("120405", "Canchayllo"), ("120406", "Curicaca"), ("120407", "El Mantaro"), ("120408", "Huamalí"),
    ("120409", "Huaripampa"), ("120410", "Huertas"), ("120411", "Janjaillo"), ("120412", "Julcán"),
    ("120413", "Leonor Ordóñez"), ("120414", "Llocllapampa"), ("120415", "Marco"), ("120416", "Masma"),
    ("120417", "Masma Chicche"), ("120418", "Molinos"), ("120419", "Monobamba"), ("120420", "Muqui"),
    ("120421", "Muquiyauyo"), ("120422", "Paca"), ("120423", "Paccha"), ("120424", "Pancán"),
    ("120425", "Parco"), ("120426", "Pomacancha"), ("120427", "Ricrán"), ("120428", "San Lorenzo"),
    ("120429", "San Pedro de Chunán"), ("120430", "Sausa"), ("120431", "Sincos"), ("120432", "Tunán Marca"),
    ("120433", "Yauli"), ("120434", "Yauyos"),
    # 1205 Junín (4)
    ("120501", "Junín"), ("120502", "Carhuamayo"), ("120503", "Ondores"), ("120504", "Ulcumayo"),
    # 1206 Satipo (9)
    ("120601", "Satipo"), ("120602", "Coviriali"), ("120603", "Llaylla"), ("120604", "Mazamari"),
    ("120605", "Pampa Hermosa"), ("120606", "Pangoa"), ("120607", "Río Negro"), ("120608", "Río Tambo"),
    ("120609", "Vizcatán del Ene"),
    # 1207 Tarma (9)
    ("120701", "Tarma"), ("120702", "Acobamba"), ("120703", "Huaricolca"), ("120704", "Huasahuasi"),
    ("120705", "La Unión"), ("120706", "Palca"), ("120707", "Palcamayo"), ("120708", "San Pedro de Cajas"),
    ("120709", "Tapo"),
    # 1208 Yauli (10)
    ("120801", "La Oroya"), ("120802", "Chacapalpa"), ("120803", "Huay-Huay"), ("120804", "Marcapomacocha"),
    ("120805", "Morococha"), ("120806", "Paccha"), ("120807", "Santa Bárbara de Carhuacayán"),
    ("120808", "Santa Rosa de Sacco"), ("120809", "Suitucancha"), ("120810", "Yauli"),
    # 1209 Chupaca (9)
    ("120901", "Chupaca"), ("120902", "Ahuac"), ("120903", "Chongos Bajo"), ("120904", "Huachac"),
    ("120905", "Huamancaca Chico"), ("120906", "San Juan de Iscos"), ("120907", "San Juan de Jarpa"),
    ("120908", "Tres de Diciembre"), ("120909", "Yanacancha"),
)
# Vinculación de los puntos críticos ilustrativos: nombre de distrito del punto -> UBIGEO.
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
    # Los IDs dependen de la base existente; todas las relaciones se resuelven por UBIGEO.
    # DO NOTHING conserva IDs, nombres, activo y vínculos; no corrige filas preexistentes.
    connection.execute("""INSERT INTO departamentos(ubigeo,nombre_departamento) VALUES (%s,'Junín')
        ON CONFLICT(ubigeo) DO NOTHING""", (JUNIN_UBIGEO,))
    connection.cursor().executemany("""INSERT INTO provincias(id_departamento,ubigeo,nombre_provincia)
        VALUES ((SELECT id_departamento FROM departamentos WHERE ubigeo=%s),%s,%s)
        ON CONFLICT(ubigeo) DO NOTHING""", [(JUNIN_UBIGEO, code, name) for code, name in JUNIN_PROVINCES])
    connection.cursor().executemany("""INSERT INTO distritos(id_provincia,ubigeo,nombre_distrito)
        VALUES ((SELECT id_provincia FROM provincias WHERE ubigeo=%s),%s,%s)
        ON CONFLICT(ubigeo) DO NOTHING""", [(code[:4], code, name) for code, name in JUNIN_DISTRICTS])
    connection.cursor().executemany("""INSERT INTO categorias_fuente_ruido(codigo,nombre,descripcion)
        VALUES (%s,%s,%s) ON CONFLICT(codigo) DO NOTHING""", CATEGORIES)
    connection.cursor().executemany("""INSERT INTO zonificaciones(codigo,nombre,descripcion)
        VALUES (%s,%s,%s) ON CONFLICT(codigo) DO NOTHING""", ZONIFICATIONS)
    # Rol asignado a las cuentas registradas. Si se desactiva o se cambia su descripción,
    # el reinicio lo respeta: el registro se rechaza mientras no exista un rol base activo.
    connection.execute("""INSERT INTO roles_usuario(nombre_rol,descripcion,activo)
        VALUES (%s,'Persona registrada que puede enviar reportes ciudadanos',TRUE)
        ON CONFLICT(nombre_rol) DO NOTHING""", (DEFAULT_ROLE,))


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
