"""Cuentas de usuario y catálogo territorial para el formulario de registro."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from repositories.user_repository import AccountInactiveError
from schemas.users import (
    AuthErrorResponse, AuthValidationResponse, LoginCredentials, TerritoryCatalog, UserProfile, UserRegistration,
)
from services.auth_service import AuthError, AuthService
from services.session_service import SESSION_COOKIE, SESSION_TTL_SECONDS, SessionStore

router = APIRouter(prefix="/api", tags=["Usuarios"])
AUTH_PREFIX = "/api/auth/"


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


SessionStoreDependency = Annotated[SessionStore, Depends(get_session_store)]


def validate_origin(request: Request) -> None:
    """Los clientes sin Origin son API; los navegadores deben usar un origen permitido."""
    origin = request.headers.get("origin")
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin not in {request_origin, *request.app.state.cors_origins}:
        raise HTTPException(status_code=403, detail="El origen de la solicitud no está permitido.")


def active_profile(service: AuthService, id_usuario: int) -> UserProfile:
    try:
        profile = service.repository.get_active_profile(id_usuario)
    except AccountInactiveError:
        raise HTTPException(status_code=403, detail="La cuenta o su rol están desactivados.") from None
    if profile is None:
        raise HTTPException(status_code=401, detail="La sesión no es válida. Inicia sesión de nuevo.")
    return profile


def require_active_user(
    request: Request, service: AuthServiceDependency, sessions: SessionStoreDependency,
) -> UserProfile:
    id_usuario = sessions.resolve(request.cookies.get(SESSION_COOKIE))
    if id_usuario is None:
        raise HTTPException(status_code=401, detail="Debes iniciar sesión para continuar.")
    return active_profile(service, id_usuario)


def issue_session(request: Request, response: Response, sessions: SessionStore, profile: UserProfile) -> None:
    token = sessions.create(profile.id_usuario, request.cookies.get(SESSION_COOKIE))
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True,
        secure=request.url.scheme == "https", samesite="lax", path="/api",
    )


@router.get("/territories", response_model=TerritoryCatalog)
def list_territories(service: AuthServiceDependency) -> TerritoryCatalog:
    return service.list_territories()


@router.post("/auth/register", response_model=UserProfile, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(validate_origin)],
             responses={403: {"model": AuthErrorResponse}, 409: {"model": AuthErrorResponse}, 422: {"model": AuthValidationResponse},
                        503: {"model": AuthErrorResponse}})
def register(payload: UserRegistration, request: Request, response: Response,
             service: AuthServiceDependency, sessions: SessionStoreDependency) -> UserProfile:
    try:
        profile = service.register(payload)
    except AuthError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None
    profile = active_profile(service, profile.id_usuario)
    issue_session(request, response, sessions, profile)
    return profile


@router.post("/auth/login", response_model=UserProfile,
             dependencies=[Depends(validate_origin)],
             responses={401: {"model": AuthErrorResponse}, 403: {"model": AuthErrorResponse},
                        422: {"model": AuthValidationResponse}})
def login(payload: LoginCredentials, request: Request, response: Response,
          service: AuthServiceDependency, sessions: SessionStoreDependency) -> UserProfile:
    try:
        profile = service.login(payload)
    except AuthError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None
    profile = active_profile(service, profile.id_usuario)
    issue_session(request, response, sessions, profile)
    return profile


@router.get("/auth/session", response_model=UserProfile,
            responses={401: {"model": AuthErrorResponse}, 403: {"model": AuthErrorResponse}})
def check_session(profile: Annotated[UserProfile, Depends(require_active_user)]) -> UserProfile:
    return profile


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(validate_origin)], responses={403: {"model": AuthErrorResponse}})
def logout(request: Request, sessions: SessionStoreDependency) -> Response:
    sessions.revoke(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE, path="/api", httponly=True, secure=request.url.scheme == "https", samesite="lax",
    )
    return response


async def validation_error_without_input(request: Request, error: RequestValidationError) -> Response:
    """En las rutas de cuentas, el 422 no repite los valores recibidos (podrían incluir la contraseña)."""
    if not request.url.path.startswith(AUTH_PREFIX):
        return await request_validation_exception_handler(request, error)
    # Las claves desconocidas de loc y los mensajes de validadores también pueden
    # contener datos recibidos. Solo se devuelven campos conocidos y mensajes propios.
    fields = UserRegistration.model_fields if request.url.path == AUTH_PREFIX + "register" else LoginCredentials.model_fields
    messages = {
        "missing": "Este campo es obligatorio.",
        "extra_forbidden": "El cuerpo contiene campos no permitidos.",
        "json_invalid": "El cuerpo debe contener JSON válido.",
    }
    details = []
    for item in error.errors():
        location = item.get("loc", ())
        field = location[1] if len(location) > 1 and location[1] in fields else None
        details.append({
            "type": item["type"],
            "loc": ["body", field] if field else ["body"],
            "msg": messages.get(item["type"], "El valor no cumple el formato o la longitud permitidos."),
        })
    return JSONResponse(status_code=422, content={"detail": details})
