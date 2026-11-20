"""Persistencia de cuentas de usuario y lectura del catálogo territorial disponible.

Recibe siempre el hash ya calculado: este módulo nunca ve ni guarda la contraseña.
"""

from dataclasses import dataclass, field

import psycopg

from repositories.catalogs import DEFAULT_ROLE
from repositories.database import PostgresDatabase
from schemas.users import Departamento, Distrito, Provincia, TerritoryCatalog, UserProfile, UserRegistration

# Un distrito está disponible solo si él, su provincia y su departamento están activos.
_AVAILABLE_DISTRICT = """
    SELECT d.id_distrito FROM distritos d
    JOIN provincias p USING (id_provincia) JOIN departamentos a USING (id_departamento)
    WHERE d.id_distrito = %s AND p.id_provincia = %s AND a.id_departamento = %s
      AND d.activo AND p.activo AND a.activo
"""
_PROFILE_SELECT = """
    SELECT u.id_usuario, u.nombre_usuario, u.nombres, u.apellidos, u.telefono, u.direccion,
           d.id_distrito, d.nombre_distrito, p.id_provincia, p.nombre_provincia,
           a.id_departamento, a.nombre_departamento, r.nombre_rol AS rol, u.fecha_registro,
           u.activo AS usuario_activo, r.activo AS rol_activo
    FROM usuarios u
    JOIN roles_usuario r USING (id_rol)
    JOIN distritos d USING (id_distrito)
    JOIN provincias p ON p.id_provincia = d.id_provincia
    JOIN departamentos a ON a.id_departamento = p.id_departamento
    WHERE u.id_usuario = %s
"""


class DistrictUnavailableError(Exception):
    """El distrito no existe o él o alguno de sus niveles superiores está inactivo."""


class UsernameTakenError(Exception):
    """El nombre de usuario ya está registrado (sin distinguir mayúsculas)."""


class DefaultRoleUnavailableError(Exception):
    """No existe un rol base activo para asignar a la cuenta nueva."""


class AccountInactiveError(Exception):
    """La cuenta o su rol dejaron de estar activos."""


@dataclass(frozen=True)
class StoredCredentials:
    id_usuario: int
    # repr=False evita volcar el hash en trazas o registros.
    password_hash: str = field(repr=False)
    activo: bool


class UserRepository(PostgresDatabase):
    def list_territories(self) -> TerritoryCatalog:
        with self._connection() as connection:
            departamentos = connection.execute("""
                SELECT id_departamento, ubigeo, nombre_departamento FROM departamentos
                WHERE activo ORDER BY nombre_departamento, id_departamento""").fetchall()
            provincias = connection.execute("""
                SELECT p.id_provincia, p.id_departamento, p.ubigeo, p.nombre_provincia
                FROM provincias p JOIN departamentos a USING (id_departamento)
                WHERE p.activo AND a.activo ORDER BY p.nombre_provincia, p.id_provincia""").fetchall()
            distritos = connection.execute("""
                SELECT d.id_distrito, d.id_provincia, d.ubigeo, d.nombre_distrito
                FROM distritos d JOIN provincias p USING (id_provincia) JOIN departamentos a USING (id_departamento)
                WHERE d.activo AND p.activo AND a.activo ORDER BY d.nombre_distrito, d.id_distrito""").fetchall()
        return TerritoryCatalog(
            departamentos=[Departamento.model_validate(row) for row in departamentos],
            provincias=[Provincia.model_validate(row) for row in provincias],
            distritos=[Distrito.model_validate(row) for row in distritos],
        )

    def create_user(self, registration: UserRegistration, password_hash: str) -> UserProfile:
        """Inserta la cuenta con el rol base en una sola transacción y devuelve su perfil."""
        with self._connection() as connection:
            # FOR SHARE impide que otra transacción desactive o borre el distrito mientras se registra.
            if connection.execute(_AVAILABLE_DISTRICT + " FOR SHARE OF d, p, a",
                                  (registration.id_distrito, registration.id_provincia,
                                   registration.id_departamento)).fetchone() is None:
                raise DistrictUnavailableError
            role = connection.execute("SELECT id_rol FROM roles_usuario WHERE nombre_rol = %s AND activo FOR SHARE",
                                      (DEFAULT_ROLE,)).fetchone()
            if role is None:
                raise DefaultRoleUnavailableError
            try:
                with connection.transaction():
                    row = connection.execute("""
                        INSERT INTO usuarios (id_rol, id_distrito, nombres, apellidos, telefono, direccion,
                                              nombre_usuario, password_hash)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id_usuario""", (
                        role["id_rol"], registration.id_distrito, registration.nombres, registration.apellidos,
                        registration.telefono, registration.direccion, registration.nombre_usuario, password_hash,
                    )).fetchone()
            except psycopg.errors.UniqueViolation:
                raise UsernameTakenError from None
            return UserProfile.model_validate(connection.execute(_PROFILE_SELECT, (row["id_usuario"],)).fetchone())

    def find_credentials(self, nombre_usuario: str) -> StoredCredentials | None:
        with self._connection() as connection:
            row = connection.execute("""
                SELECT id_usuario, password_hash, activo FROM usuarios
                WHERE lower(nombre_usuario) = lower(%s)""", (nombre_usuario,)).fetchone()
        return StoredCredentials(**row) if row else None

    def update_password_hash(self, id_usuario: int, password_hash: str) -> None:
        with self._connection() as connection:
            connection.execute("UPDATE usuarios SET password_hash = %s WHERE id_usuario = %s",
                               (password_hash, id_usuario))

    def get_profile(self, id_usuario: int) -> UserProfile | None:
        with self._connection() as connection:
            row = connection.execute(_PROFILE_SELECT, (id_usuario,)).fetchone()
        return UserProfile.model_validate(row) if row else None

    def get_active_profile(self, id_usuario: int) -> UserProfile | None:
        """Consulta de nuevo el estado de cuenta y rol en cada operación protegida."""
        with self._connection() as connection:
            row = connection.execute(_PROFILE_SELECT, (id_usuario,)).fetchone()
        if row is None:
            return None
        if not row["usuario_activo"] or not row["rol_activo"]:
            raise AccountInactiveError
        return UserProfile.model_validate(row)
