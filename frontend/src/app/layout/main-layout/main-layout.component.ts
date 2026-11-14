import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon.component';

@Component({
  selector: 'app-main-layout',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, IconComponent],
  templateUrl: './main-layout.component.html',
  styleUrl: './main-layout.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:keydown.escape)': 'profileOpen.set(false)' },
})
export class MainLayoutComponent {
  readonly profileOpen = signal(false);
  toggleProfile(): void { this.profileOpen.update((value) => !value); }
  skipToContent(event: Event): void {
    event.preventDefault();
    const content = document.getElementById('main-content');
    content?.focus();
    content?.scrollIntoView({ block: 'start' });
  }
}
