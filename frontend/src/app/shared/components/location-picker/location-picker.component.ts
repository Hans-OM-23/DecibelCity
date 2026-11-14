import { ChangeDetectionStrategy, Component, computed, DestroyRef, ElementRef, inject, input, output, signal, viewChild, ViewEncapsulation } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, debounceTime, distinctUntilChanged, map, of, Subject, switchMap } from 'rxjs';
import { MapPoint, PlaceSuggestion, SelectedPlace } from '../../../core/interfaces/maps.interface';
import { MapsService } from '../../../core/services/maps.service';
import { IconComponent } from '../icon/icon.component';
import { MapComponent } from '../map/map.component';

type SearchState = 'idle' | 'searching' | 'results' | 'empty' | 'error';
const MIN_QUERY_LENGTH = 3;

/**
 * Accessible combobox (WAI-ARIA 1.2 pattern) that searches OpenStreetMap places
 * through the backend. Free text is never accepted as a location: only a chosen
 * suggestion, which the server verifies again with Nominatim when saving.
 */
@Component({
  selector: 'app-location-picker',
  imports: [MapComponent, IconComponent],
  templateUrl: './location-picker.component.html',
  styleUrl: './location-picker.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  // Styles are explicitly scoped to app-location-picker in the stylesheet.
  encapsulation: ViewEncapsulation.None,
})
export class LocationPickerComponent {
  readonly disabled = input(false);
  readonly locationSelected = output<SelectedPlace | null>();
  readonly query = signal('');
  readonly state = signal<SearchState>('idle');
  readonly error = signal('');
  readonly suggestions = signal<PlaceSuggestion[]>([]);
  readonly activeIndex = signal(-1);
  readonly open = signal(false);
  readonly selected = signal<SelectedPlace | null>(null);
  readonly expanded = computed(() => this.open() && this.suggestions().length > 0);
  readonly activeId = computed(() => this.expanded() && this.activeIndex() >= 0 ? `location-option-${this.activeIndex()}` : null);
  readonly mapPoints = computed<readonly MapPoint[]>(() => {
    const place = this.selected();
    return place ? [{ id: place.placeId, title: place.address, label: 'Ubicación elegida', position: place.position }] : [];
  });
  readonly statusMessage = computed(() => {
    switch (this.state()) {
      case 'searching': return 'Buscando lugares…';
      case 'results': return `${this.suggestions().length} ${this.suggestions().length === 1 ? 'sugerencia disponible' : 'sugerencias disponibles'}. Usa las flechas para recorrerlas.`;
      case 'empty': return 'No encontramos lugares con ese nombre en Huancayo. Prueba con el nombre de la calle o un lugar cercano.';
      default: return '';
    }
  });
  private readonly service = inject(MapsService);
  private readonly searchInput = viewChild<ElementRef<HTMLInputElement>>('searchInput');
  private readonly queries = new Subject<string>();

  constructor() {
    this.queries.pipe(
      map(value => value.trim().replace(/\s+/g, ' ')),
      debounceTime(400),
      distinctUntilChanged(),
      switchMap(value => {
        if (value.length < MIN_QUERY_LENGTH) return of({ value, results: [] as PlaceSuggestion[], error: null });
        this.state.set('searching');
        return this.service.searchPlaces(value).pipe(
          map(results => ({ value, results, error: null as unknown })),
          catchError((error: unknown) => of({ value, results: [] as PlaceSuggestion[], error })),
        );
      }),
      takeUntilDestroyed(inject(DestroyRef)),
    ).subscribe(({ value, results, error }) => {
      if (this.selected()) return;
      this.suggestions.set(results);
      this.activeIndex.set(-1);
      this.open.set(results.length > 0);
      if (error) {
        this.error.set(this.service.messageFor(error));
        this.state.set('error');
      } else {
        this.error.set('');
        this.state.set(value.length < MIN_QUERY_LENGTH ? 'idle' : results.length ? 'results' : 'empty');
      }
    });
  }

  onInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.query.set(value);
    if (value.trim().length < MIN_QUERY_LENGTH) {
      this.suggestions.set([]);
      this.open.set(false);
      this.state.set('idle');
      this.error.set('');
    }
    this.queries.next(value);
  }

  onKeydown(event: KeyboardEvent): void {
    const count = this.suggestions().length;
    switch (event.key) {
      case 'ArrowDown':
        if (!count) return;
        event.preventDefault();
        this.open.set(true);
        this.activeIndex.update(index => (index + 1) % count);
        break;
      case 'ArrowUp':
        if (!count) return;
        event.preventDefault();
        this.open.set(true);
        this.activeIndex.update(index => (index <= 0 ? count - 1 : index - 1));
        break;
      case 'Enter':
        // Never submit the form with unconfirmed text.
        event.preventDefault();
        if (this.expanded() && this.activeIndex() >= 0) this.choose(this.suggestions()[this.activeIndex()]);
        break;
      case 'Escape':
        if (this.open()) {
          event.preventDefault();
          this.open.set(false);
          this.activeIndex.set(-1);
        }
        break;
    }
  }

  reopen(): void {
    if (this.suggestions().length) this.open.set(true);
  }

  close(): void {
    this.open.set(false);
    this.activeIndex.set(-1);
  }

  choose(suggestion: PlaceSuggestion): void {
    if (this.disabled()) return;
    const position = { lat: suggestion.lat, lng: suggestion.lng };
    if (!this.service.isValidPosition(position)) return;
    const address = suggestion.address ? `${suggestion.name}, ${suggestion.address}` : suggestion.name;
    const place: SelectedPlace = { placeId: suggestion.place_id, address, position };
    this.selected.set(place);
    this.close();
    this.locationSelected.emit(place);
  }

  changeLocation(): void {
    if (this.disabled()) return;
    this.selected.set(null);
    this.locationSelected.emit(null);
    this.state.set(this.suggestions().length ? 'results' : 'idle');
    // The input is rendered again on the next change detection pass.
    setTimeout(() => {
      this.searchInput()?.nativeElement.focus();
      this.reopen();
    });
  }
}
