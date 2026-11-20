import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AuthService } from '../../../core/services/auth.service';
import { ReportDraftService } from '../../../core/services/report-draft.service';
import { IconComponent } from '../../../shared/components/icon/icon.component';
import { HttpErrorResponse } from '@angular/common/http';
import { textLength } from '../auth.validators';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink, IconComponent],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoginComponent {
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly reportDraftService = inject(ReportDraftService);

  readonly returnUrl = this.route.snapshot.queryParamMap.get('returnUrl') === '/reportar' || this.reportDraftService.hasDraft()
    ? '/reportar' : null;
  readonly hasPendingDraft = this.reportDraftService.hasDraftSignal.asReadonly();

  readonly isSubmitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly successMessage = signal<string | null>(null);

  readonly loginForm = new FormGroup({
    nombre_usuario: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, textLength(1, 40, true)],
    }),
    contrasena: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, textLength(1, 128)],
    }),
  });

  get nombreUsuarioControl() {
    return this.loginForm.controls.nombre_usuario;
  }

  get contrasenaControl() {
    return this.loginForm.controls.contrasena;
  }

  onSubmit(): void {
    if (this.isSubmitting() || this.successMessage()) return;
    if (this.loginForm.invalid) {
      this.loginForm.markAllAsTouched();
      this.errorMessage.set('Por favor, completa los campos obligatorios correctamente.');
      return;
    }

    this.isSubmitting.set(true);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    const { nombre_usuario, contrasena } = this.loginForm.getRawValue();

    this.authService.login({ nombre_usuario: nombre_usuario.trim(), contrasena }).subscribe({
      next: (user) => {
        this.isSubmitting.set(false);
        this.successMessage.set(`¡Bienvenido/a, ${user.nombres || user.nombre_usuario}! Redirigiendo...`);
        const target = this.returnUrl || '/';
        setTimeout(() => {
          this.router.navigateByUrl(target);
        }, 600);
      },
      error: (err: HttpErrorResponse | Error) => {
        this.isSubmitting.set(false);
        if (err instanceof HttpErrorResponse) {
          const detail = typeof err.error?.detail === 'string' ? err.error.detail : null;
          if (err.status === 401 || err.status === 403) {
            this.errorMessage.set(detail ?? 'Credenciales incorrectas. Verifica tu usuario y contraseña.');
          } else if (err.status === 0) {
            this.errorMessage.set('No se pudo conectar con el servidor. Verifica tu conexión.');
          } else {
            this.errorMessage.set(detail ?? 'Los datos de acceso no son válidos. Revisa el usuario y la contraseña.');
          }
        } else {
          this.errorMessage.set('Error en la comunicación. Inténtalo de nuevo.');
        }
      },
    });
  }
}
