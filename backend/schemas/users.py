"""Contratos de cuentas de usuario y del catálogo territorial para el registro.

Las restricciones replican los CHECK de la tabla `usuarios` para responder 422 antes de
llegar a PostgreSQL. La contraseña se recibe como `SecretStr`: no aparece en `repr`, en
volcados del modelo ni en registros; nunca se devuelve `password_hash`.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, StringConstraints, field_validator

USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,40}$"
PHONE_PATTERN = r"^[0-9+() -]{6,20}$"
PASSWORD_MIN_LENGTH = 8
# Límite superior para acotar el coste de Argon2 ante entradas desmesuradas.
PASSWORD_MAX_LENGTH = 128

PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, pattern=USERNAME_PATTERN)]


class Departamento(BaseModel):
    id_departamento: int
    ubigeo: str
    nombre_departamento: str


class Provincia(BaseModel):
    id_provincia: int
    id_departamento: int
    ubigeo: str
    nombre_provincia: str


class Distrito(BaseModel):
    id_distrito: int
    id_provincia: int
    ubigeo: str
    nombre_distrito: str


class TerritoryCatalog(BaseModel):
    """Solo elementos disponibles: activos ellos y toda su jerarquía superior."""

    departamentos: list[Departamento]
    provincias: list[Provincia]
    distritos: list[Distrito]


class UserRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombres: PersonName
    apellidos: PersonName
    telefono: Annotated[str, StringConstraints(strip_whitespace=True, pattern=PHONE_PATTERN)]
    direccion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=200)]
    # Se validan los tres selectores, pero solo se persiste el distrito.
    id_departamento: StrictInt = Field(gt=0)
    id_provincia: StrictInt = Field(gt=0)
    id_distrito: StrictInt = Field(gt=0)
    nombre_usuario: Username
    # Sin recorte de espacios: la contraseña se conserva exactamente como se escribió.
    contrasena: SecretStr = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("contrasena")
    @classmethod
    def password_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("La contraseña no puede estar formada solo por espacios.")
        return value


class LoginCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre_usuario: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    contrasena: SecretStr = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserProfile(BaseModel):
    """Datos públicos de la cuenta. No incluye `password_hash` ni la contraseña."""

    id_usuario: int
    nombre_usuario: str
    nombres: str
    apellidos: str
    telefono: str
    direccion: str
    id_distrito: int
    nombre_distrito: str
    id_provincia: int
    nombre_provincia: str
    id_departamento: int
    nombre_departamento: str
    rol: str
    fecha_registro: datetime


class AuthValidationDetail(BaseModel):
    type: str
    loc: list[str | int]
    msg: str


class AuthErrorResponse(BaseModel):
    detail: str


class AuthValidationResponse(BaseModel):
    detail: str | list[AuthValidationDetail]
