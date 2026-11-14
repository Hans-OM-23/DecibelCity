# Database

Todo lo relativo a la base de datos PostgreSQL de DecibelCity.

| Ruta | Contenido |
| --- | --- |
| [base-datos.md](base-datos.md) | Evaluación del modelo, PostgreSQL portátil, importación, respaldo y restauración. |
| [configurar-postgresql-instalado.md](configurar-postgresql-instalado.md) | Traslado al PostgreSQL instalado (puerto 5432) y uso de pgAdmin. |
| [esquema-actual.dbml](esquema-actual.dbml) | Diagrama DBML del modelo vigente; las pruebas del backend lo contrastan con la base real. |
| [consultas-postgresql.sql](consultas-postgresql.sql) | Consultas de lectura de ejemplo. |
| [referencias/](referencias/) | Esquemas y DDL históricos, solo como documentación. |
| `local/` | Datos de esta computadora, **excluidos de Git**: binarios y clúster portátil, `postgresql-local.json` (credenciales), registro, respaldos `.dump`, exportación v2 y bases SQLite históricas (`qa/` contiene las de QA). |

El SQL que ejecuta la API (`schema.sql`) y su inicialización (`migrations.py`) permanecen en [backend/repositories/](../backend/repositories/schema.sql) porque se despliegan con el backend. El asistente `backend/scripts/postgres_local.py` usa `Database/local/` para el clúster portátil.

> No compartir ni publicar `local/`: contiene credenciales y datos de reportes.
