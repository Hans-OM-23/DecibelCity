import { HttpErrorResponse } from '@angular/common/http';
import { Component, input, output } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SelectedPlace } from '../../core/interfaces/maps.interface';
import { NoiseReport, NoiseReportCreate, NoiseSource } from '../../core/interfaces/noise.interface';
import { NoiseApiService } from '../../core/services/noise-api.service';
import { LocationPickerComponent } from '../../shared/components/location-picker/location-picker.component';
import { ReportComponent } from './report.component';

@Component({
  selector: 'app-location-picker',
  standalone: true,
  template: '<input aria-label="Buscar ubicación" [disabled]="disabled()" (input)="locationSelected.emit(null)">',
})
class LocationPickerStubComponent {
  readonly disabled = input(false);
  readonly locationSelected = output<SelectedPlace | null>();
}

const place: SelectedPlace = {
  placeId: 'W813046785',
  address: 'Calle Real 123, Huancayo, Perú',
  position: { lat: -12.067, lng: -75.210 },
};
const description = 'Bocinas constantes junto al paradero durante la tarde.';
const sources: NoiseSource[] = [{ value: 'traffic', label: 'Tráfico vehicular y transporte público' }];

function createApiMock(response: Subject<NoiseReport>) {
  return {
    getNoiseSources: vi.fn(() => of(sources)),
    createReport: vi.fn((_payload: NoiseReportCreate) => response.asObservable()),
  };
}

describe('ReportComponent: reportes con ubicación seleccionada', () => {
  let fixture: ComponentFixture<ReportComponent>;
  let component: ReportComponent;
  let response: Subject<NoiseReport>;
  let api: ReturnType<typeof createApiMock>;

  beforeEach(async () => {
    response = new Subject<NoiseReport>();
    api = createApiMock(response);
    await TestBed.configureTestingModule({
      imports: [ReportComponent],
      providers: [provideRouter([]), { provide: NoiseApiService, useValue: api }],
    })
      .overrideComponent(ReportComponent, {
        remove: { imports: [LocationPickerComponent] },
        add: { imports: [LocationPickerStubComponent] },
      })
      .compileComponents();
    fixture = TestBed.createComponent(ReportComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  function element<T extends HTMLElement>(selector: string): T {
    const found = (fixture.nativeElement as HTMLElement).querySelector<T>(selector);
    if (!found) throw new Error(`No se encontró el elemento: ${selector}`);
    return found;
  }

  function fillDetails(): void {
    const source = element<HTMLSelectElement>('#report-source');
    source.value = 'traffic';
    source.dispatchEvent(new Event('change', { bubbles: true }));
    const textarea = element<HTMLTextAreaElement>('#report-description');
    textarea.value = description;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  function selectPlace(): void {
    const picker = fixture.debugElement.query(By.directive(LocationPickerStubComponent))
      .componentInstance as LocationPickerStubComponent;
    picker.locationSelected.emit(place);
    fixture.detectChanges();
  }

  function submitForm(): void {
    element<HTMLFormElement>('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    fixture.detectChanges();
  }

  it('no envía texto de búsqueda sin seleccionar un lugar, incluso si se dispara submit', () => {
    fillDetails();
    const search = element<HTMLInputElement>('app-location-picker input');
    search.value = 'Una dirección escrita sin verificar';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();

    expect(element<HTMLButtonElement>('.submit-button').disabled).toBe(true);
    submitForm();

    expect(api.createReport).not.toHaveBeenCalled();
    expect(component.selectedPlace()).toBeNull();
    expect(element<HTMLElement>('#location-error').textContent).toContain('Selecciona una ubicación');
    expect(fixture.nativeElement.querySelector('.receipt-card')).toBeNull();
  });

  it('envía el place_id seleccionado y omite el nivel cuando no se activa la medición', () => {
    fillDetails();
    selectPlace();
    expect(element<HTMLButtonElement>('.submit-button').disabled).toBe(false);

    submitForm();

    expect(api.createReport).toHaveBeenCalledExactlyOnceWith({
      location: place.address,
      place_id: place.placeId,
      source: 'traffic',
      description,
      decibels: null,
    });
    expect(component.submitting()).toBe(true);
    expect(fixture.nativeElement.querySelector('.receipt-card')).toBeNull();
  });

  it('invalida la selección anterior cuando se vuelve a editar la búsqueda', () => {
    fillDetails();
    selectPlace();
    const search = element<HTMLInputElement>('app-location-picker input');
    search.value = 'Otra calle sin seleccionar';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();

    expect(component.selectedPlace()).toBeNull();
    expect(component.location()).toBe('');
    expect(element<HTMLButtonElement>('.submit-button').disabled).toBe(true);
    expect(element<HTMLElement>('#submit-location-hint').textContent).toContain('Selecciona una ubicación');
    submitForm();
    expect(api.createReport).not.toHaveBeenCalled();
  });

  it('bloquea envíos duplicados y la edición del selector mientras espera la respuesta', () => {
    fillDetails();
    selectPlace();
    submitForm();
    submitForm();

    expect(api.createReport).toHaveBeenCalledTimes(1);
    expect(element<HTMLButtonElement>('.submit-button').disabled).toBe(true);
    expect(element<HTMLInputElement>('app-location-picker input').disabled).toBe(true);
    expect(element<HTMLTextAreaElement>('#report-description').closest('fieldset')?.disabled).toBe(true);
    expect(fixture.nativeElement.querySelector('.receipt-card')).toBeNull();
  });

  it('conserva el lugar y la descripción y muestra el motivo seguro de un rechazo del backend', () => {
    fillDetails();
    selectPlace();
    submitForm();
    const detail = 'El lugar debe estar dentro del área de cobertura de Huancayo, Perú.';
    response.error(new HttpErrorResponse({ status: 422, error: { detail } }));
    fixture.detectChanges();

    expect(component.selectedPlace()).toEqual(place);
    expect(component.location()).toBe(place.address);
    expect(element<HTMLTextAreaElement>('#report-description').value).toBe(description);
    expect(element<HTMLElement>('.submit-error').textContent).toContain(detail);
    expect(element<HTMLButtonElement>('.submit-button').disabled).toBe(false);
    expect(element<HTMLButtonElement>('.submit-button').textContent).toContain('Reintentar envío');
    expect(component.receipt()).toBeNull();
    expect(fixture.nativeElement.querySelector('.receipt-card')).toBeNull();
  });

  it('muestra el recibo solo tras la confirmación del servidor y exige otra selección para un reporte nuevo', () => {
    fillDetails();
    selectPlace();
    submitForm();
    expect(fixture.nativeElement.querySelector('.receipt-card')).toBeNull();
    const receipt: NoiseReport = {
      location: 'Calle Real 123, Huancayo 12001, Perú',
      place_id: place.placeId,
      source: 'traffic',
      description,
      decibels: null,
      id: 'report-verified-001',
      created_at: '2026-10-28T15:30:00Z',
      status: 'received',
      latitude: place.position.lat,
      longitude: place.position.lng,
    };
    response.next(receipt);
    response.complete();
    fixture.detectChanges();

    const confirmation = element<HTMLElement>('.receipt-card');
    expect(confirmation.textContent).toContain(receipt.id);
    expect(confirmation.textContent).toContain(receipt.location);
    expect(fixture.nativeElement.querySelector('form')).toBeNull();
    element<HTMLButtonElement>('.receipt-actions button').click();
    fixture.detectChanges();

    expect(component.selectedPlace()).toBeNull();
    expect(element<HTMLTextAreaElement>('#report-description').value).toBe('');
    expect(element<HTMLButtonElement>('.submit-button').disabled).toBe(true);
    expect(fixture.nativeElement.querySelector('.receipt-card')).toBeNull();
    expect(api.createReport).toHaveBeenCalledTimes(1);
  });
});
