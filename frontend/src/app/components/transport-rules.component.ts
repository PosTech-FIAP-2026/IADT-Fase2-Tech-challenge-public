import { Component, Input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { ProductGroupInfo } from '../models';

/** Destaque das regras de transporte (contaminação e refrigeração) e legenda dos grupos. */
@Component({
  selector: 'app-transport-rules',
  standalone: true,
  imports: [MatIconModule],
  template: `
    <section class="rules-box" role="note" aria-label="Restrições de transporte dos medicamentos e insumos">
      <h3><mat-icon>warning</mat-icon> Restrições de transporte — risco de contaminação</h3>
      <ul class="rules-list">
        @for (rule of rules; track $index) {
          <li>{{ rule.includes('refriger') ? '❄️' : '🚫' }} {{ rule }}</li>
        }
      </ul>
      @if (groups.length) {
        <div class="group-legend">
          @for (g of groups; track g.code) {
            <span class="grp-chip">
              <span [attr.class]="'grp grp-' + g.code">{{ g.code }}</span>
              {{ g.name }}
              @if (g.refrigerated) { <small>❄️ refrigerado</small> }
              @if (g.incompatible_with.length && !g.refrigerated) { <small>· não viaja com {{ g.incompatible_with.join(', ') }}</small> }
            </span>
          }
        </div>
      }
    </section>
  `,
})
export class TransportRulesComponent {
  @Input({ required: true }) rules: string[] = [];
  @Input() groups: ProductGroupInfo[] = [];
}
