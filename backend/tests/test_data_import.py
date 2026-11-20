"""Transferencia v2 → PostgreSQL, comparación del contenido y reversión atómica."""

from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from conftest import db_connection
from main import create_app
from repositories.catalogs import insert_hotspot
from repositories.data_import import import_snapshot
from repositories.migrations import ACCOUNT_TABLES, TABLE_ORDER, DatabaseMigrationError
from repositories.noise_repository import NoiseRepository
from services.noise_service import DEMO_HOTSPOTS, NOISE_SOURCES


def repository(settings):
    return NoiseRepository(settings.database_url, settings.database_schema)


@pytest.fixture
def snapshot(settings):
    repo = repository(settings)
    repo.initialize(DEMO_HOTSPOTS, NOISE_SOURCES)
    with repo._connection() as connection:
        connection.execute("UPDATE tipos_fuente_ruido SET id_tipo_fuente=100,nombre_fuente='Tráfico personalizado' WHERE codigo='traffic'")
        connection.execute("UPDATE departamentos SET activo=FALSE")
        connection.execute("UPDATE puntos_criticos SET descripcion='Descripción personalizada conservada'")
        point = DEMO_HOTSPOTS[0].model_copy(update={'id':'custom','district':'Distrito histórico desconocido','source_label':'Etiqueta particular'})
        insert_hotspot(connection, point)
        connection.execute("""INSERT INTO reportes_ciudadanos
            (id_reporte,id_tipo_fuente,id_estado,descripcion,nivel_estimado_db,fecha_hora,id_lugar,proveedor_lugar,es_historico,ubicacion_heredada) VALUES
            ('historical',100,1,'Descripción histórica conservada',74.123456789,'2026-10-27T07:00:00-05:00',NULL,NULL,TRUE,'Referencia antigua'),
            ('verified',100,1,'Descripción verificada conservada',NULL,'2026-11-07T12:00:00Z','ChIJ_Verified','google',FALSE,NULL)""")
        tables = {}
        for table in TABLE_ORDER:
            rows = connection.execute(sql.SQL('SELECT * FROM {} ORDER BY 1').format(sql.Identifier(table))).fetchall()
            tables[table] = [{key: value.isoformat() if isinstance(value,datetime) else int(value) if isinstance(value,bool) else value
                             for key,value in row.items()} for row in rows]
        connection.execute(sql.SQL('DROP TABLE {} CASCADE').format(sql.SQL(',').join(map(sql.Identifier, (*TABLE_ORDER,*ACCOUNT_TABLES,'decibelcity_schema')))))
    return {'format':'decibelcity-v2','tables':tables}


def test_import_preserves_all_fields_ids_and_http_contract(settings, snapshot):
    counts = import_snapshot(repository(settings), snapshot)
    assert counts['reportes_ciudadanos'] == 2
    assert counts['puntos_criticos'] == 4
    for _ in range(3):
        with TestClient(create_app(settings)) as client:
            points = client.get('/api/hotspots').json()
            assert len(points) == 4
            custom = next(p for p in points if p['id']=='custom')
            assert custom['district'] == 'Distrito histórico desconocido'
            assert custom['source_label'] == 'Etiqueta particular'
            assert client.get('/api/noise-sources').json()[-1] == {'value':'traffic','label':'Tráfico personalizado'}
    with db_connection(settings) as connection:
        assert connection.execute('SELECT COUNT(*) FROM reportes_ciudadanos').fetchone() == (2,)
        historical = connection.execute("SELECT id_tipo_fuente,fecha_hora,nivel_estimado_db,es_historico,ubicacion_heredada FROM reportes_ciudadanos WHERE id_reporte='historical'").fetchone()
        assert historical == (100,datetime.fromisoformat('2026-10-27T12:00:00+00:00'),74.123456789,True,'Referencia antigua')
        assert connection.execute("SELECT id_lugar,proveedor_lugar,nivel_estimado_db,es_historico FROM reportes_ciudadanos WHERE id_reporte='verified'").fetchone() == ('ChIJ_Verified','google',None,False)
        assert connection.execute('SELECT activo FROM departamentos').fetchone() == (False,)
        # Las identidades continúan después del mayor ID importado.
        identity = connection.execute("INSERT INTO roles_usuario(id_rol,nombre_rol,descripcion) VALUES(400,'Rol conservado','Descripción conservada') RETURNING id_rol").fetchone()
        assert identity == (400,)
        assert connection.execute("INSERT INTO departamentos(ubigeo,nombre_departamento) VALUES('99','Otro') RETURNING id_departamento").fetchone()[0] > snapshot['tables']['departamentos'][0]['id_departamento']


@pytest.mark.parametrize('damage', ['fk','level','boolean','date','extra-column','missing-table','nan','unverified'])
def test_invalid_import_rolls_back_schema_and_all_rows(settings, snapshot, damage):
    tables = snapshot['tables']
    if damage == 'fk':
        tables['puntos_criticos'][0]['id_distrito'] = 999
    elif damage == 'level':
        tables['reportes_ciudadanos'][0]['nivel_estimado_db'] = 999
    elif damage == 'boolean':
        tables['departamentos'][0]['activo'] = 2
    elif damage == 'date':
        tables['reportes_ciudadanos'][0]['fecha_hora'] = '2026-10-27T12:00:00'
    elif damage == 'extra-column':
        tables['puntos_criticos'][0]['contenido_extra'] = 'No descartar silenciosamente'
    elif damage == 'missing-table':
        del tables['roles_usuario']
    elif damage == 'nan':
        tables['puntos_criticos'][0]['nivel_db'] = float('nan')
    else:
        tables['reportes_ciudadanos'][0]['es_historico'] = 0
        tables['reportes_ciudadanos'][0]['id_lugar'] = None
        tables['reportes_ciudadanos'][0]['proveedor_lugar'] = None
    with pytest.raises((ValueError,psycopg.IntegrityError)):
        import_snapshot(repository(settings), snapshot)
    with db_connection(settings) as connection:
        assert connection.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()').fetchall() == []


def test_import_rejects_account_tables_in_snapshot(settings, snapshot):
    """La exportación v2 no transporta cuentas ni hashes de contraseña."""
    snapshot['tables']['usuarios'] = []
    with pytest.raises(ValueError, match='diez tablas'):
        import_snapshot(repository(settings), snapshot)
    with db_connection(settings) as connection:
        assert connection.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()').fetchall() == []


def test_import_refuses_nonempty_database(settings, snapshot):
    repo = repository(settings)
    import_snapshot(repo,snapshot)
    before = repo.list_hotspots()
    with pytest.raises(DatabaseMigrationError,match='vacías'):
        import_snapshot(repo,snapshot)
    assert repo.list_hotspots() == before
    with db_connection(settings) as connection:
        assert connection.execute('SELECT COUNT(*) FROM reportes_ciudadanos').fetchone() == (2,)


def test_import_restarts_all_identity_sequences(settings, snapshot):
    snapshot['tables']['roles_usuario'] = [{'id_rol':450,'nombre_rol':'Conservado','descripcion':'Sin cuentas','activo':0}]
    import_snapshot(repository(settings), snapshot)
    with db_connection(settings) as connection:
        assert connection.execute("INSERT INTO roles_usuario(nombre_rol,descripcion) VALUES('Nuevo','Sin cuentas') RETURNING id_rol").fetchone() == (451,)


def test_configuration_rejects_other_engines_and_schema_injection(settings):
    with pytest.raises(ValueError,match='PostgreSQL'):
        NoiseRepository('file:database')
    with pytest.raises(ValueError,match='esquema'):
        NoiseRepository(settings.database_url,'public; DROP TABLE puntos_criticos')
