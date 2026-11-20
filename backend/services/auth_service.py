"""Registro, inicio de sesión y catálogo territorial con contraseñas Argon2id (argon2-cffi).

La contraseña en texto plano solo existe durante el cálculo o la verificación del hash:
no se guarda, no se registra en logs y no se incluye en mensajes de error.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from repositories.user_repository import (
    DefaultRoleUnavailableError,
    DistrictUnavailableError,
    UsernameTakenError,
    UserRepository,
)
from schemas.users import LoginCredentials, TerritoryCatalog, UserProfile, UserRegistration

INVALID_CREDENTIALS = "Nombre de usuario o contraseña incorrectos."


class AuthError(Exception):
    """Error de dominio con el código HTTP que le corresponde y un mensaje en español."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthService:
    def __init__(self, repository: UserRepository, password_hasher: PasswordHasher | None = None) -> None:
        self.repository = repository
        # Valores por defecto de argon2-cffi: Argon2id con los parámetros recomendados (RFC 9106).
        self.password_hasher = password_hasher or PasswordHasher()
        # Hash de referencia para igualar el tiempo de respuesta cuando el usuario no existe.
        self._dummy_hash = self.password_hasher.hash("decibelcity-usuario-inexistente")

    def list_territories(self) -> TerritoryCatalog:
        return self.repository.list_territories()

    def register(self, registration: UserRegistration) -> UserProfile:
        password_hash = self.password_hasher.hash(registration.contrasena.get_secret_value())
        try:
            return self.repository.create_user(registration, password_hash)
        except DistrictUnavailableError:
            raise AuthError("El distrito seleccionado no existe o no está disponible para el registro.", 422) from None
        except UsernameTakenError:
            raise AuthError("El nombre de usuario ya está registrado.", 409) from None
        except DefaultRoleUnavailableError:
            raise AuthError("El registro no está disponible en este momento.", 503) from None

    def login(self, credentials: LoginCredentials) -> UserProfile:
        password = credentials.contrasena.get_secret_value()
        stored = self.repository.find_credentials(credentials.nombre_usuario)
        if stored is None:
            self._verify(self._dummy_hash, password)
            raise AuthError(INVALID_CREDENTIALS, 401)
        if not self._verify(stored.password_hash, password):
            raise AuthError(INVALID_CREDENTIALS, 401)
        # El estado se revela solo a quien demostró conocer la contraseña.
        if not stored.activo:
            raise AuthError("La cuenta está desactivada. Comunícate con el equipo de DecibelCity.", 403)
        if self.password_hasher.check_needs_rehash(stored.password_hash):
            self.repository.update_password_hash(stored.id_usuario, self.password_hasher.hash(password))
        profile = self.repository.get_profile(stored.id_usuario)
        if profile is None:  # Borrada entre la verificación y la lectura del perfil.
            raise AuthError(INVALID_CREDENTIALS, 401)
        return profile

    def _verify(self, password_hash: str, password: str) -> bool:
        try:
            return self.password_hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
