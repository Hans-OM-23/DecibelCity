# DecibelCity · Backend

API con Python, FastAPI, Uvicorn y PostgreSQL. Las dependencias y el entorno virtual se gestionan con **uv**. Las instrucciones y skills compartidas del proyecto están en `../.agents/`.

## Ejecutar

Desde esta carpeta, con uv instalado y el PostgreSQL indicado en la configuración existente en ejecución:

```powershell
uv sync --locked
uv run --env-file .env uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

En la instalación local comprobada el 11 de noviembre de 2026 se usa PostgreSQL instalado en el puerto `5432`. Si se eligió la instancia portátil del puerto `55432`, iniciarla previamente con `uv run scripts/postgres_local.py start`. Ese asistente no inicia el servicio instalado; su operación `init` vuelve a escribir las conexiones del portátil. Conservar la configuración existente y consultar la [guía de PostgreSQL instalado](../Database/configurar-postgresql-instalado.md) o la [preparación del portátil](../Database/base-datos.md) según corresponda.

Documentación interactiva: [Swagger UI](http://127.0.0.1:8000/docs).

## Organización

- `schemas/noise.py`: contratos y validación de entradas y respuestas.
- `schemas/users.py`, `services/auth_service.py`, `apis/auth.py` y `repositories/user_repository.py`: catálogo territorial, registro e inicio de sesión con hashes Argon2id (`argon2-cffi`).
- `repositories/database.py`: conexión PostgreSQL compartida por los repositorios.
- `services/noise_service.py`: lógica del dominio, catálogo y datos ilustrativos iniciales.
- `services/osm_places.py`: búsqueda de lugares con Photon y verificación con Nominatim (OpenStreetMap).
- `apis/noise.py`: rutas HTTP.
- `repositories/noise_repository.py`: persistencia PostgreSQL mediante consultas parametrizadas.
- `repositories/migrations.py` y `repositories/schema.sql`: inicialización transaccional PostgreSQL, esquema versión 5, actualización automática desde v3 y v4 (`repositories/upgrade_v4.sql`, `repositories/upgrade_v5.sql`) y bloqueo de concurrencia.
- `repositories/data_import.py`: transferencia atómica desde una exportación normalizada v2.
- `scripts/postgres_local.py`: preparar, iniciar, detener y respaldar el servidor local sin instalar servicios.
- `repositories/catalogs.py`: carga inicial territorial y de catálogos sin sobrescribir personalizaciones ni estados de actividad.
- `config.py`: configuración del entorno.
- `main.py`: composición de la aplicación, ciclo de vida y CORS.
- `tests/`: pruebas de integración con un esquema temporal por prueba, en una base PostgreSQL exclusiva.

## API

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/api/health` | Estado del servicio |
| GET | `/api/hotspots` | Tres puntos ilustrativos de Huancayo |
| GET | `/api/maps/config` | URL de teselas, atribución de OpenStreetMap, centro y cobertura (sin claves) |
| GET | `/api/places/search?q=` | Hasta 8 sugerencias de Photon dentro de Huancayo: `{place_id, name, address, lat, lng}`. `q` de 3 a 120 caracteres |
| GET | `/api/noise-sources` | Catálogo `{value, label}` |
| POST | `/api/reports` | Requiere sesión activa; 201 con recibo, 401 sin sesión válida y 403 por cuenta/rol desactivados u origen ajeno |
| GET | `/api/territories` | `{departamentos, provincias, distritos}` disponibles (activos en toda la jerarquía) |
| POST | `/api/auth/register` | `201` con `UserProfile`. `409` nombre de usuario ocupado, `422` datos inválidos o distrito no disponible, `503` sin rol base activo |
| POST | `/api/auth/login` | `200` con `UserProfile` y cookie de sesión. `401` credenciales incorrectas, `403` cuenta desactivada |
| GET | `/api/auth/session` | `200` con el perfil confirmado; `401` sesión ausente, falsa, vencida o revocada; `403` cuenta/rol desactivados |
| POST | `/api/auth/logout` | `204`; revoca la sesión y elimina su cookie |

El registro recibe los nueve campos del formulario: `nombres`, `apellidos`, `telefono`, `direccion`, `id_departamento`, `id_provincia`, `id_distrito`, `nombre_usuario` y `contrasena`. Los tres IDs deben formar una jerarquía activa; solo `id_distrito` se persiste. Las respuestas de cuentas excluyen contraseñas y hashes, incluso en errores de validación. Consultar el [contrato y las restricciones de cuentas](../Database/base-datos.md#cuentas-de-usuario).

Registro y login correctos crean una sesión opaca de ocho horas en una cookie `HttpOnly`, `SameSite=Lax`, ruta `/api` y `Secure` bajo HTTPS. FastAPI valida la sesión y la actividad de la cuenta/rol antes de consultar Nominatim o guardar un reporte. Los POST rechazan un `Origin` de navegador ajeno a la aplicación; CORS usa credenciales y orígenes explícitos. El perfil en `sessionStorage` no acredita permisos y Angular ya no lo usa para iniciar una sesión.

Las sesiones se guardan únicamente en memoria del proceso, con identificadores resumidos mediante hash; un reinicio las invalida. Ejecutar con un solo worker. Esta implementación no es un almacén distribuido de sesiones y no cambia el esquema PostgreSQL v5. El reporte almacenado permanece sin relación con el usuario. Angular conserva el borrador durante login/registro y ante un 401/403, y lo elimina únicamente después de un envío confirmado o al iniciar otro reporte.

Ejemplo de cuerpo de reporte:

```json
{
  "location": "Calle Real y Av. Giráldez",
  "place_id": "W813046785",
  "source": "traffic",
  "description": "Bocinas frecuentes durante la tarde junto al paradero.",
  "decibels": 74
}
```

`location` admite de 5 a 1000 caracteres y `description` de 10 a 500, después de eliminar espacios al inicio y final. `source` admite `traffic`, `commerce`, `construction`, `entertainment` y `other`. `decibels` es opcional: `null` o un número entre 30 y 120. No se aceptan propiedades adicionales ni coordenadas enviadas por el cliente.

`place_id` es obligatorio: identificador de OpenStreetMap formado por `N`, `W` o `R` y hasta 19 dígitos, tal como lo devuelve `/api/places/search`. El backend lo consulta en [Nominatim `/lookup`](https://nominatim.org/release-docs/latest/api/Lookup/). El texto `location` enviado por el cliente se reemplaza por la dirección verificada. El recibo incluye `place_id`, `location`, `latitude` y `longitude` procedentes de Nominatim, además del resto de campos del reporte.

Se exige país `PE`, coordenadas dentro de la cobertura operativa de Huancayo (sur -12.16, oeste -75.30, norte -11.95, este -75.12) y un lugar específico (`place_rank` de Nominatim igual o mayor que 25): plaza, calle, edificio o establecimiento, entre otros. Una selección genérica de ciudad, barrio, distrito o límite administrativo no es válida. La cobertura es un rectángulo de servicio y no representa límites administrativos oficiales. Verificar un lugar no verifica que el ruido reportado haya ocurrido allí.

Un lugar inválido o fuera de cobertura produce `422`; la indisponibilidad temporal del servicio (límite de uso, error 5xx o tiempo de espera) produce `503`; un fallo de comunicación o respuesta no válida produce `502`. Los errores no devuelven detalles internos del proveedor. El backend espacia las consultas a Nominatim al menos 1 segundo, como pide su política de uso.

Los reportes se guardan en la base PostgreSQL configurada en `DECIBELCITY_DATABASE_URL` y sobreviven a los reinicios. Enviar requiere una cuenta activa; el reporte no guarda una relación con esa cuenta, ni IP o datos del navegador. El texto libre podría contener información personal si quien lo escribe la incluye; el formulario debe pedir que no se incluyan estos datos. No existe una ruta pública que permita consultar reportes individuales o listar su contenido. El catálogo ilustrativo no se modifica automáticamente a partir de reportes sin verificar.

Para reportes nuevos se persisten sólo el identificador del lugar (`id_lugar`, con `proveedor_lugar = 'osm'`) y los datos aportados sobre el ruido. La dirección y las coordenadas se devuelven en el recibo inmediato y no se guardan en PostgreSQL. Los reportes creados con la versión anterior conservan su identificador de Google con `proveedor_lugar = 'google'`. El modelo conserva las tablas `tipos_fuente_ruido`, `estados_reporte`, `reportes_ciudadanos` y `puntos_criticos`, y añade `departamentos`, `provincias`, `distritos`, `zonificaciones`, `categorias_fuente_ruido` y `roles_usuario`, con claves foráneas activas. Las ubicaciones antiguas de reportes se conservan en `ubicacion_heredada`, exclusivamente en filas marcadas como históricas.

Al iniciar, el backend crea el esquema PostgreSQL versión 5 si está vacío; si encuentra v3 o v4, ejecuta las migraciones pendientes en la misma transacción sin borrar reportes. La migración v5 añade `usuarios` y sus índices; después carga catálogos sin sobrescribirlos. La transferencia local conservó los reportes y puntos de la base anterior. No hay ejecución ni conversión automática de archivos del motor anterior. Consultar la [evaluación, transferencia, preparación local y restauración de respaldos](../Database/base-datos.md).

El catálogo territorial comprende las nueve provincias y los 124 distritos de Junín, según las fuentes INEI citadas en `Database/base-datos.md`. Los puntos referencian distritos del catálogo local; `district` mantiene su contrato y usa el nombre relacionado o `distrito_heredado` cuando la etiqueta es desconocida. La jerarquía local no certifica la ubicación de los puntos ilustrativos. Las zonificaciones se cargan sin asignarlas a puntos; el rol base activo `ciudadano` se asigna a las cuentas nuevas. Se incluyen registro, sesión y autorización del envío, sin pantallas administrativas, estaciones ni alertas.

Los puntos del catálogo incluyen `latitude` y `longitude` aproximadas, tomadas de OpenStreetMap (© colaboradores de OSM, ODbL). Sus valores de ruido siguen siendo ilustrativos. Las propiedades históricas `x`/`y` se mantienen por compatibilidad y no son coordenadas geográficas.

## Configuración

Si aún no existe, copiar `.env.example` a `.env`. Preparar PostgreSQL según la [guía](../Database/base-datos.md); `uv run scripts/postgres_local.py init` genera las conexiones de la instancia portátil. Luego utilizar:

```powershell
uv run uvicorn main:app --reload --env-file .env
```

También se pueden definir las variables directamente en el entorno:

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| `DECIBELCITY_DATABASE_URL` | `postgresql://decibelcity@127.0.0.1:5432/decibelcity` | Conexión; configurar contraseña en `.env` |
| `DECIBELCITY_DATABASE_SCHEMA` | `public` | Esquema existente dedicado a la aplicación |
| `DECIBELCITY_TEST_DATABASE_URL` | Sin valor | Base exclusiva de pruebas terminada en `_test` |
| `DECIBELCITY_CORS_ORIGINS` | `http://localhost:4200,http://127.0.0.1:4200` | Orígenes permitidos, separados por comas |
| `DECIBELCITY_NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | Verificación de lugares |
| `DECIBELCITY_PHOTON_URL` | `https://photon.komoot.io` | Búsqueda de lugares |
| `DECIBELCITY_GEOCODER_USER_AGENT` | `DecibelCity/0.1 (prototipo educativo…)` | Identificación exigida por Nominatim |
| `DECIBELCITY_MAP_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | Teselas del mapa |
| `DECIBELCITY_MAP_ATTRIBUTION` | © colaboradores de OpenStreetMap | Atribución visible |

`.env`, el entorno virtual y la carpeta de datos están ignorados en Git. Las pruebas requieren una base PostgreSQL diferente y crean esquemas temporales aislados. La configuración se lee al crear la aplicación y requiere reinicio al cambiarla.

Ninguna variable de mapas es obligatoria ni contiene claves. Consultar la [guía de mapas](../Documentacion/mapas-openstreetmap.md) para usar servidores propios.

## Verificar

```powershell
uv run pytest
uv pip check
```

Las pruebas verifican validación, persistencia entre reinicios, importación transaccional de una exportación v2, carga inicial idempotente, CORS, ausencia de rutas de lectura de reportes la migración v3 → v4 y el contrato con Nominatim y Photon mediante HTTP simulado. Cubren lugares inexistentes, fuera de cobertura, resultados genéricos, coordenadas manipuladas, tiempos de espera y errores del proveedor; no realizan llamadas reales. Los valores de los puntos son datos de diseño señalados con `illustrative: true`; no son mediciones ni una certificación de incumplimiento normativo.
