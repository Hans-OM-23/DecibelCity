import { inject, Injectable, InjectionToken } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, timeout } from 'rxjs';
import { Hotspot, NoiseReport, NoiseReportCreate, NoiseSource } from '../interfaces/noise.interface';

export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', { providedIn: 'root', factory: () => '/api' });

@Injectable({ providedIn: 'root' })
export class NoiseApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  getHotspots(): Observable<Hotspot[]> {
    return this.http.get<Hotspot[]>(`${this.baseUrl}/hotspots`).pipe(timeout(12000));
  }

  getNoiseSources(): Observable<NoiseSource[]> {
    return this.http.get<NoiseSource[]>(`${this.baseUrl}/noise-sources`).pipe(timeout(12000));
  }

  createReport(payload: NoiseReportCreate): Observable<NoiseReport> {
    return this.http.post<NoiseReport>(`${this.baseUrl}/reports`, payload).pipe(timeout(20000));
  }
}
