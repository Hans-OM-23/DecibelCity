"""Integridad y comportamiento del esquema nativo de PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from conftest import db_connection
from main import create_app
from repositories import catalogs, migrations
from repositories.catalogs import JUNIN_PROVINCES, SOURCE_CATEGORIES
from repositories.noise_repository import NoiseRepository
from services.noise_service import DEMO_HOTSPOTS, NOISE_SOURCES


def repository(settings):
    return NoiseRepository(settings.database_url, settings.database_schema)


def initialize(settings):
    repository(settings).initialize(DEMO_HOTSPOTS, NOISE_SOURCES)


def test_native_schema_types_and_identity(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
        assert tables == migrations.TABLES | {"decibelcity_schema"}
        assert connection.execute("SELECT version FROM decibelcity_schema").fetchone() == (5,)
        types = dict(connection.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='reportes_ciudadanos'"))
        assert types['fecha_hora'] == 'timestamp with time zone'
        assert types['es_historico'] == 'boolean'
        assert types['nivel_estimado_db'] == 'double precision'
        assert connection.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=current_schema() AND is_identity='YES'").fetchone() == (9,)
        # Solo el rol base para cuentas registradas; no se crean cuentas iniciales.
        assert connection.execute("SELECT nombre_rol,activo FROM roles_usuario").fetchall() == [('ciudadano', True)]
        assert connection.execute("SELECT COUNT(*) FROM usuarios").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM puntos_criticos WHERE id_zonificacion IS NOT NULL").fetchone() == (0,)


def test_documented_model_matches_postgresql_columns(settings):
    initialize(settings)
    model = Path(__file__).resolve().parents[2] / 'Database/esquema-actual.dbml'
    documented = {}
    table = None
    for line in model.read_text(encoding='utf-8').splitlines():
        if match := re.match(r'Table (\w+) \{', line):
            table = match[1]
        elif match := re.match(r'  (\w+) (integer|text|boolean|timestamptz|"double precision")(?:\s|$)', line):
            data_type = {'timestamptz':'timestamp with time zone', '"double precision"':'double precision'}.get(match[2],match[2])
            documented[table,match[1]] = data_type
    with db_connection(settings) as connection:
        actual = {(table,column):data_type for table,column,data_type in connection.execute(
            'SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema()')}
    assert actual == documented


def test_public_catalog_is_identical(client):
    assert client.get('/api/hotspots').json() == [point.model_dump(mode='json') for point in DEMO_HOTSPOTS]
    assert client.get('/api/noise-sources').json() == [source.model_dump(mode='json') for source in NOISE_SOURCES]


def test_hierarchy_and_source_categories(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        rows = connection.execute("""SELECT d.ubigeo,d.nombre_distrito,p.ubigeo,p.nombre_provincia,a.ubigeo,a.nombre_departamento
            FROM distritos d JOIN provincias p USING(id_provincia) JOIN departamentos a USING(id_departamento) ORDER BY d.ubigeo""").fetchall()
        assert len(rows) == 124
        assert ('120101','Huancayo','1201','Huancayo','12','Junín') in rows
        assert ('120107','Chilca','1201','Huancayo','12','Junín') in rows
        # Cada distrito pertenece a la provincia indicada por los cuatro primeros dígitos de su UBIGEO.
        assert all(district[:4] == province and province[:2] == department == '12' for district,_,province,_,department,_ in rows)
        assert connection.execute("SELECT ubigeo,nombre_provincia FROM provincias ORDER BY id_provincia").fetchall() == list(JUNIN_PROVINCES)
        # Los puntos ilustrativos siguen vinculados a Huancayo y Chilca.
        assert connection.execute("""SELECT p.id_punto,d.ubigeo FROM puntos_criticos p JOIN distritos d USING(id_distrito)
            ORDER BY p.id_punto""").fetchall() == [('sector-01','120101'), ('sector-02','120101'), ('sector-03','120107')]
        assert dict(connection.execute("SELECT f.codigo,c.codigo FROM tipos_fuente_ruido f JOIN categorias_fuente_ruido c USING(id_categoria)")) == SOURCE_CATEGORIES
        assert connection.execute("SELECT COUNT(*) FROM zonificaciones").fetchone() == (4,)


@pytest.mark.parametrize('first_id', [1, 41])
def test_territorial_seed_is_idempotent_and_completes_older_databases(settings, monkeypatch, first_id):
    """Amplía el catálogo anterior real, incluidos IDs distintos del orden nuevo y vínculos existentes."""
    with db_connection(settings) as connection:
        connection.execute(migrations.SCHEMA_PATH.read_text(encoding='utf-8'), prepare=False)
        if first_id == 41:
            connection.execute("ALTER TABLE departamentos ALTER COLUMN id_departamento RESTART WITH 41")
            connection.execute("ALTER TABLE provincias ALTER COLUMN id_provincia RESTART WITH 41")
            connection.execute("ALTER TABLE distritos ALTER COLUMN id_distrito RESTART WITH 41")
    # Ejecutar la carga de solo Huancayo y Chilca reproduce sus identidades originales:
    # con secuencias nuevas son 1 y 2; Chilca ocuparía el ID 5 en el catálogo ampliado.
    with monkeypatch.context() as legacy:
        legacy.setattr(catalogs, 'JUNIN_PROVINCES', (('1201', 'Huancayo'),))
        legacy.setattr(catalogs, 'JUNIN_DISTRICTS', (('120101', 'Huancayo'), ('120107', 'Chilca')))
        initialize(settings)
    with db_connection(settings) as connection:
        assert connection.execute("SELECT id_distrito,ubigeo FROM distritos ORDER BY ubigeo").fetchall() == [
            (first_id, '120101'), (first_id + 1, '120107')]
        connection.execute("UPDATE departamentos SET activo=FALSE, nombre_departamento='Junín (personalizado)'")
        connection.execute("UPDATE provincias SET activo=FALSE, nombre_provincia='Huancayo (personalizado)'")
        connection.execute("UPDATE distritos SET activo=FALSE, nombre_distrito='Chilca (personalizado)' WHERE ubigeo='120107'")
        connection.execute("""INSERT INTO usuarios(id_rol,id_distrito,nombres,apellidos,telefono,direccion,
                nombre_usuario,password_hash)
            SELECT r.id_rol,d.id_distrito,'Ana','Quispe','964123456','Jr. Ica 123','cuenta.previa',
                'Valor ficticio para integridad referencial; no se usa para autenticar'
            FROM roles_usuario r CROSS JOIN distritos d
            WHERE r.nombre_rol='ciudadano' AND d.ubigeo='120107'""")
        before = {
            'departamentos': connection.execute("SELECT * FROM departamentos").fetchall(),
            'provincias': connection.execute("SELECT * FROM provincias").fetchall(),
            'distritos': connection.execute("SELECT * FROM distritos ORDER BY ubigeo").fetchall(),
            'usuarios': connection.execute("SELECT * FROM usuarios").fetchall(),
            'puntos_criticos': connection.execute("SELECT * FROM puntos_criticos ORDER BY id_punto").fetchall(),
        }
    expanded = None
    for _ in range(2):
        initialize(settings)
        with db_connection(settings) as connection:
            assert connection.execute("SELECT COUNT(*) FROM provincias").fetchone() == (9,)
            assert connection.execute("SELECT COUNT(*) FROM distritos").fetchone() == (124,)
            assert connection.execute("SELECT COUNT(DISTINCT ubigeo) FROM distritos").fetchone() == (124,)
            assert connection.execute("SELECT * FROM departamentos").fetchall() == before['departamentos']
            assert connection.execute("SELECT * FROM provincias WHERE ubigeo='1201'").fetchall() == before['provincias']
            assert connection.execute("SELECT * FROM distritos WHERE ubigeo IN ('120101','120107') ORDER BY ubigeo").fetchall() == before['distritos']
            assert connection.execute("SELECT * FROM usuarios").fetchall() == before['usuarios']
            assert connection.execute("SELECT * FROM puntos_criticos ORDER BY id_punto").fetchall() == before['puntos_criticos']
            assert connection.execute("""SELECT p.id_punto,d.ubigeo FROM puntos_criticos p
                JOIN distritos d USING(id_distrito) ORDER BY p.id_punto""").fetchall() == [
                    ('sector-01', '120101'), ('sector-02', '120101'), ('sector-03', '120107')]
            assert connection.execute("""SELECT d.ubigeo,p.ubigeo,a.ubigeo FROM distritos d
                JOIN provincias p USING(id_provincia) JOIN departamentos a USING(id_departamento)
                WHERE left(d.ubigeo,4) <> p.ubigeo OR left(p.ubigeo,2) <> a.ubigeo""").fetchall() == []
            # Se comparan todas las filas entre reinicios, no solo los dos IDs originales.
            current = (
                connection.execute("SELECT * FROM provincias ORDER BY ubigeo").fetchall(),
                connection.execute("SELECT * FROM distritos ORDER BY ubigeo").fetchall(),
            )
            if expanded is not None:
                assert current == expanded
            expanded = current


@pytest.mark.parametrize('statement', [
    "INSERT INTO provincias(id_departamento,ubigeo,nombre_provincia) VALUES(999,'9999','Inválida')",
    "INSERT INTO distritos(id_provincia,ubigeo,nombre_distrito) VALUES(999,'999999','Inválido')",
    "INSERT INTO departamentos(ubigeo,nombre_departamento) VALUES('12','Duplicado')",
    "INSERT INTO departamentos(ubigeo,nombre_departamento) VALUES('a1','Inválido')",
    "UPDATE tipos_fuente_ruido SET id_categoria=NULL WHERE codigo='traffic'",
    "UPDATE tipos_fuente_ruido SET id_categoria=999 WHERE codigo='traffic'",
    "UPDATE puntos_criticos SET id_zonificacion=999",
    "UPDATE puntos_criticos SET distrito_heredado='Ambos'",
    "UPDATE puntos_criticos SET id_distrito=NULL",
    "DELETE FROM distritos",
    "UPDATE departamentos SET activo=NULL",
])
def test_relational_constraints(settings, statement):
    initialize(settings)
    with db_connection(settings) as connection:
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(statement)
        assert connection.execute('SELECT COUNT(*) FROM puntos_criticos').fetchone() == (3,)


@pytest.mark.parametrize('source,state,place,provider,legacy', [
    (999,1,'W1','osm',None), (1,999,'W1','osm',None),
    (1,1,None,None,None), (1,1,'','osm',None), (1,1,'../invalid','osm',None), (1,1,'W1','osm','Dirección'),
    (1,1,'W1',None,None), (1,1,None,'osm',None), (1,1,'ChIJ_Test','osm',None), (1,1,'W1','otro',None),
])
def test_new_report_requires_valid_foreign_keys_and_place(settings, source, state, place, provider, legacy):
    initialize(settings)
    with db_connection(settings) as connection:
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("""INSERT INTO reportes_ciudadanos(id_reporte,id_tipo_fuente,id_estado,descripcion,fecha_hora,id_lugar,proveedor_lugar,ubicacion_heredada)
                VALUES ('new',%s,%s,'Descripción del reporte',CURRENT_TIMESTAMP,%s,%s,%s)""", (source,state,place,provider,legacy))


def test_hotspot_coordinates_must_be_complete(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("UPDATE puntos_criticos SET longitud=NULL WHERE id_punto='sector-01'")


def test_version_3_database_is_upgraded_in_place_without_losing_reports(settings):
    """Base creada con el esquema v3 (Google): se migra hasta v5 al iniciar y conserva los reportes."""
    v3_schema = (Path(__file__).parent / 'fixtures' / 'schema_v3.sql').read_text(encoding='utf-8')
    with db_connection(settings) as connection:
        connection.execute(v3_schema, prepare=False)
        connection.execute("INSERT INTO categorias_fuente_ruido(codigo,nombre,descripcion) VALUES('TRANSPORTE','Transporte','Tránsito')")
        connection.execute("INSERT INTO tipos_fuente_ruido(codigo,nombre_fuente,id_categoria) VALUES('traffic','Tráfico',1)")
        connection.execute("INSERT INTO estados_reporte(codigo_estado,descripcion) VALUES('received','Recibido')")
        connection.execute("""INSERT INTO puntos_criticos(id_punto,id_tipo_fuente,nombre,area,distrito_heredado,sector,nivel_db,
            etiqueta_fuente,descripcion,franja_horaria,x,y,limite_referencia,consulta_google)
            VALUES('sector-01',1,'Punto anterior','Centro','Huancayo','S1',70,'Tráfico','Descripción conservada','Día',10,10,60,'Consulta antigua')""")
        connection.execute("""INSERT INTO reportes_ciudadanos(id_reporte,id_tipo_fuente,id_estado,descripcion,fecha_hora,google_place_id)
            VALUES('previo',1,1,'Reporte guardado con Google',CURRENT_TIMESTAMP,'ChIJ_Anterior')""")
    for _ in range(2):  # La segunda vez ya es v5 y no vuelve a migrar.
        initialize(settings)
    with db_connection(settings) as connection:
        assert connection.execute('SELECT version FROM decibelcity_schema').fetchone() == (5,)
        assert connection.execute('SELECT COUNT(*) FROM usuarios').fetchone() == (0,)
        assert connection.execute("SELECT id_lugar,proveedor_lugar,descripcion FROM reportes_ciudadanos WHERE id_reporte='previo'").fetchone() == (
            'ChIJ_Anterior', 'google', 'Reporte guardado con Google')
        assert connection.execute("SELECT descripcion,latitud,longitud FROM puntos_criticos WHERE id_punto='sector-01'").fetchone() == (
            'Descripción conservada', -12.06865, -75.21025)
        columns = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name IN ('reportes_ciudadanos','puntos_criticos')")}
        assert 'google_place_id' not in columns and 'consulta_google' not in columns
        assert connection.execute('SELECT COUNT(*) FROM puntos_criticos').fetchone() == (3,)


def test_deactivation_and_custom_fields_survive_restarts(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        for table in ('departamentos','provincias','distritos'):
            connection.execute(f'UPDATE {table} SET activo=FALSE')
        connection.execute("UPDATE roles_usuario SET activo=FALSE")  # Incluye el rol base: no se reactiva.
        connection.execute("INSERT INTO roles_usuario(nombre_rol,descripcion,activo) VALUES('Personalizado','Rol preparado sin cuentas',FALSE)")
        connection.execute("UPDATE tipos_fuente_ruido SET nombre_fuente='Etiqueta personalizada' WHERE codigo='traffic'")
        connection.execute("UPDATE estados_reporte SET descripcion='Estado personalizado'")
        connection.execute("UPDATE puntos_criticos SET descripcion='Descripción personalizada', etiqueta_fuente='Bocinas locales'")
    for _ in range(3):
        with TestClient(create_app(settings)) as client:
            assert client.get('/api/noise-sources').json()[0]['label'] == 'Etiqueta personalizada'
            points = client.get('/api/hotspots').json()
            assert len(points) == 3
            assert all(p['description'] == 'Descripción personalizada' for p in points)
            assert points[0]['source_label'] == 'Bocinas locales'
    with db_connection(settings) as connection:
        for table in ('departamentos','provincias','distritos','roles_usuario'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table} WHERE activo').fetchone() == (0,)
        assert connection.execute('SELECT descripcion FROM estados_reporte').fetchone() == ('Estado personalizado',)


def test_future_version_is_rejected_before_seeding(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        connection.execute('UPDATE decibelcity_schema SET version=99')
        connection.execute("UPDATE tipos_fuente_ruido SET nombre_fuente='No cambiar' WHERE codigo='traffic'")
    with pytest.raises(migrations.DatabaseMigrationError, match='versión'):
        initialize(settings)
    with db_connection(settings) as connection:
        assert connection.execute('SELECT version FROM decibelcity_schema').fetchone() == (99,)
        assert connection.execute("SELECT nombre_fuente FROM tipos_fuente_ruido WHERE codigo='traffic'").fetchone() == ('No cambiar',)


def test_unknown_tables_are_rejected_without_changes(settings):
    with db_connection(settings) as connection:
        connection.execute('CREATE TABLE ajena (id INTEGER)')
    with pytest.raises(migrations.DatabaseMigrationError, match='no reconocidas'):
        initialize(settings)
    with db_connection(settings) as connection:
        assert connection.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()').fetchall() == [('ajena',)]


def test_ddl_and_seed_failure_roll_back_together(settings, monkeypatch):
    def fail(*args):
        raise RuntimeError('Fallo simulado después del DDL')
    monkeypatch.setattr(migrations, 'seed_sources', fail)
    with pytest.raises(RuntimeError, match='simulado'):
        initialize(settings)
    with db_connection(settings) as connection:
        assert connection.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()').fetchall() == []


def test_concurrent_initialization_is_serialized(settings):
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(initialize, [settings]*3))
    with db_connection(settings) as connection:
        for table, expected in [('puntos_criticos',3), ('tipos_fuente_ruido',5), ('decibelcity_schema',1)]:
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone() == (expected,)


def test_foreign_key_graph_has_no_cycles_or_duplicate_territorial_routes(settings):
    initialize(settings)
    with db_connection(settings) as connection:
        edges = connection.execute("""SELECT c.relname,p.relname FROM pg_constraint f
            JOIN pg_class c ON c.oid=f.conrelid JOIN pg_class p ON p.oid=f.confrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE f.contype='f' AND n.nspname=current_schema()""").fetchall()
    assert len(edges) == 10
    assert set(parent for child,parent in edges if child=='usuarios') == {'roles_usuario','distritos'}
    assert set(parent for child,parent in edges if child=='puntos_criticos') == {'distritos','zonificaciones','tipos_fuente_ruido'}
    def visit(node, path):
        assert node not in path
        for child,parent in edges:
            if child == node:
                visit(parent, path | {node})
    for table in migrations.TABLES:
        visit(table, set())
