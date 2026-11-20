import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { ActivatedRoute, provideRouter, Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LoginComponent } from './login.component';
import { AuthService } from '../../../core/services/auth.service';
import { ReportDraftService } from '../../../core/services/report-draft.service';
import { UserProfile } from '../../../core/interfaces/auth.interface';

const mockUser: UserProfile = {
  id_usuario: 1,
  nombre_usuario: 'jperez',
  nombres: 'Juan',
  apellidos: 'Perez',
  telefono: '964123456',
  direccion: 'Calle Real 123',
  id_distrito: 120101,
  nombre_distrito: 'Huancayo',
  id_provincia: 1201,
  nombre_provincia: 'Huancayo',
  id_departamento: 12,
  nombre_departamento: 'Junín',
  rol: 'ciudadano',
  fecha_registro: '2026-11-19T00:00:00Z',
};

describe('LoginComponent', () => {
  let component: LoginComponent;
  let fixture: ComponentFixture<LoginComponent>;
  let authServiceMock: { login: ReturnType<typeof vi.fn> };
  let reportDraftService: ReportDraftService;
  let router: Router;

  beforeEach(async () => {
    sessionStorage.clear();
    authServiceMock = {
      login: vi.fn(),
    };

    await TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        { provide: AuthService, useValue: authServiceMock },
        ReportDraftService,
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              queryParamMap: {
                get: (key: string) => (key === 'returnUrl' ? '/reportar' : null),
              },
            },
          },
        },
      ],
    }).compileComponents();

    router = TestBed.inject(Router);
    vi.spyOn(router, 'navigateByUrl');
    reportDraftService = TestBed.inject(ReportDraftService);

    fixture = TestBed.createComponent(LoginComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('inicia con el formulario vacío e inválido', () => {
    expect(component.loginForm.valid).toBe(false);
    expect(component.nombreUsuarioControl.value).toBe('');
    expect(component.contrasenaControl.value).toBe('');
  });

  it('muestra aviso accesible de reporte pendiente cuando returnUrl es /reportar', () => {
    expect(component.hasPendingDraft()).toBe(false);
    reportDraftService.saveDraft({ location: '', selectedPlace: null, source: '', includeDecibels: false,
      decibels: 70, description: 'Borrador pendiente' });
    fixture.detectChanges();
    expect(component.hasPendingDraft()).toBe(true);
    const alertInfo = fixture.nativeElement.querySelector('.alert-info');
    expect(alertInfo).not.toBeNull();
    expect(alertInfo.textContent).toContain('reporte guardado');
  });

  it('marca controles y muestra error si se intenta enviar vacío', () => {
    component.onSubmit();
    fixture.detectChanges();

    expect(authServiceMock.login).not.toHaveBeenCalled();
    expect(component.loginForm.touched).toBe(true);
    expect(component.errorMessage()).toContain('completa los campos obligatorios');
  });

  it('respeta las longitudes del contrato de login', () => {
    component.loginForm.setValue({
      nombre_usuario: 'a'.repeat(41),
      contrasena: 'a'.repeat(129),
    });

    expect(component.nombreUsuarioControl.invalid).toBe(true);
    expect(component.contrasenaControl.invalid).toBe(true);
    expect(component.loginForm.valid).toBe(false);
    component.loginForm.setValue({ nombre_usuario: 'a', contrasena: '1' });
    expect(component.loginForm.valid).toBe(true);
  });

  it('llama a authService.login y redirige a returnUrl tras éxito', async () => {
    vi.useFakeTimers();
    authServiceMock.login.mockReturnValue(of(mockUser));

    component.loginForm.setValue({
      nombre_usuario: 'jperez',
      contrasena: 'claveSegura123',
    });

    component.onSubmit();
    fixture.detectChanges();

    expect(authServiceMock.login).toHaveBeenCalledWith({
      nombre_usuario: 'jperez',
      contrasena: 'claveSegura123',
    });
    expect(component.successMessage()).toContain('Juan');

    await vi.advanceTimersByTimeAsync(700);
    expect(router.navigateByUrl).toHaveBeenCalledWith('/reportar');
    vi.useRealTimers();
  });

  it('muestra el mensaje de error si el servidor devuelve HTTP 401', () => {
    const errorResponse = new HttpErrorResponse({
      status: 401,
      statusText: 'Unauthorized',
      error: { detail: 'Credenciales inválidas proporcionadas.' },
    });
    authServiceMock.login.mockReturnValue(throwError(() => errorResponse));

    component.loginForm.setValue({
      nombre_usuario: 'desconocido',
      contrasena: 'erronea123',
    });

    component.onSubmit();
    fixture.detectChanges();

    expect(component.isSubmitting()).toBe(false);
    expect(component.errorMessage()).toBe('Credenciales inválidas proporcionadas.');
  });

  it('presenta el detail de una cuenta desactivada (403)', () => {
    authServiceMock.login.mockReturnValue(throwError(() => new HttpErrorResponse({
      status: 403, error: { detail: 'La cuenta está desactivada.' },
    })));
    component.loginForm.setValue({ nombre_usuario: 'jperez', contrasena: 'claveSegura123' });
    component.onSubmit();
    expect(component.errorMessage()).toBe('La cuenta está desactivada.');
  });

  it('convierte errores estructurados de validación en un mensaje legible', () => {
    authServiceMock.login.mockReturnValue(throwError(() => new HttpErrorResponse({
      status: 422, error: { detail: [{ loc: ['body'], msg: 'Datos inválidos' }] },
    })));
    component.loginForm.setValue({ nombre_usuario: 'jperez', contrasena: 'claveSegura123' });
    component.onSubmit();
    fixture.detectChanges();
    expect(component.errorMessage()).toContain('Los datos de acceso no son válidos');
    expect(fixture.nativeElement.textContent).not.toContain('[object Object]');
  });
});
