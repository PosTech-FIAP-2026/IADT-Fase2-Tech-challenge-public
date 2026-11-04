import { Component, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Catalog, Location, Priority, ProductGroup, StateInfo } from '../models';
import { ApiService } from '../services/api.service';

export type CityRole = 'both' | 'origin' | 'destination' | 'delivery';

interface ManualEntry {
  location: Location;
  role: CityRole;
}

/** Cidades cadastradas manualmente, já com origem/destino resolvidos para a otimização. */
export interface ManualSelection {
  locations: Location[];
  originIndex: number;
  destinationIndex: number;
  /** Motivo pelo qual ainda não dá para otimizar (vazio = pronto). */
  error: string;
}

const STORAGE_KEY = 'rotas-medicas.manual-cities';
const MAX_SUGGESTIONS = 50;

/** Minúsculas e sem acentos, para filtrar municípios enquanto o usuário digita. */
function fold(text: string): string {
  return text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

const ROLE_LABELS: Record<CityRole, string> = {
  both: '🏥 Origem e destino',
  origin: '🏥 Origem',
  destination: '🏁 Destino',
  delivery: '📦 Entrega',
};

@Component({
  selector: 'app-manual-cities',
  standalone: true,
  imports: [
    FormsModule, MatAutocompleteModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule,
    MatProgressSpinnerModule, MatSelectModule, MatTooltipModule,
  ],
  template: `
    <div class="manual-form">
      <h3>Adicionar cidade</h3>
      <div class="params">
        <mat-form-field>
          <mat-label>Estado</mat-label>
          <mat-select [(ngModel)]="form.state" (ngModelChange)="loadCities($event)" name="m_state">
            @for (s of states; track s.uf) { <mat-option [value]="s.uf">{{ s.uf }} — {{ s.name }}</mat-option> }
          </mat-select>
        </mat-form-field>
        <mat-form-field>
          <mat-label>Cidade</mat-label>
          <input matInput [(ngModel)]="form.city" name="m_city" [matAutocomplete]="cityAuto" autocomplete="off"
                 placeholder="Digite o nome do município" />
          @if (loadingCities) { <mat-spinner matSuffix diameter="18" /> }
          <mat-autocomplete #cityAuto="matAutocomplete">
            @for (n of filteredCities; track n) { <mat-option [value]="n">{{ n }}</mat-option> }
          </mat-autocomplete>
          @if (cityOptions.length) { <mat-hint>{{ cityOptions.length }} municípios (IBGE)</mat-hint> }
        </mat-form-field>
        <mat-form-field>
          <mat-label>Papel</mat-label>
          <mat-select [(ngModel)]="form.role" name="m_role">
            @for (r of roles; track r) { <mat-option [value]="r">{{ roleLabel(r) }}</mat-option> }
          </mat-select>
        </mat-form-field>
        @if (form.role === 'delivery') {
          <mat-form-field>
            <mat-label>Grupo</mat-label>
            <mat-select [(ngModel)]="form.product_group" name="m_group" [panelWidth]="null">
              <mat-select-trigger><span [attr.class]="'grp grp-' + form.product_group">{{ form.product_group }}</span> Medicamentos/Insumos {{ form.product_group }}</mat-select-trigger>
              @for (g of catalog?.product_groups ?? []; track g.code) {
                <mat-option [value]="g.code"><span [attr.class]="'grp grp-' + g.code">{{ g.code }}</span> {{ g.name }}</mat-option>
              }
            </mat-select>
          </mat-form-field>
          <mat-form-field>
            <mat-label>Prioridade</mat-label>
            <mat-select [(ngModel)]="form.priority" name="m_prio">
              <mat-option value="critical">Crítica</mat-option>
              <mat-option value="regular">Regular</mat-option>
            </mat-select>
          </mat-form-field>
          <mat-form-field>
            <mat-label>Demanda (kg)</mat-label>
            <input matInput type="number" min="0" [(ngModel)]="form.demand" name="m_demand" />
          </mat-form-field>
          <mat-form-field>
            <mat-label>Janela — início</mat-label>
            <input matInput type="time" [(ngModel)]="form.window_start" name="m_ws" />
          </mat-form-field>
          <mat-form-field>
            <mat-label>Janela — fim</mat-label>
            <input matInput type="time" [(ngModel)]="form.window_end" name="m_we" />
          </mat-form-field>
        }
      </div>
      <div class="row">
        <button mat-flat-button type="button" (click)="add()" [disabled]="adding || !form.city.trim()">
          @if (adding) { <mat-spinner class="btn-spinner" diameter="18" /> } @else { <mat-icon>add_location_alt</mat-icon> }
          {{ adding ? 'Buscando coordenadas...' : 'Adicionar cidade' }}
        </button>
        @if (entries.length) {
          <button mat-stroked-button type="button" (click)="clear()"><mat-icon>delete_sweep</mat-icon> Limpar lista</button>
        }
      </div>
      @if (formError) { <p class="bad" role="alert">{{ formError }}</p> }
      <p class="hint">As coordenadas são buscadas no Google Maps e o nome é conferido na lista oficial de municípios do IBGE.</p>
    </div>

    @if (entries.length) {
      <div class="table-scroll">
        <table class="data stack stack-wide t-manual">
          <thead>
            <tr><th>#</th><th>Cidade</th><th>Papel</th><th>Grupo</th><th>Prioridade</th><th>Demanda (kg)</th><th>Janela de recebimento</th><th><span class="sr-only">Ações</span></th></tr>
          </thead>
          <tbody>
            @for (e of entries; track e; let i = $index) {
              <tr>
                <td>{{ i + 1 }}</td>
                <td>{{ e.location.name }} @if (e.location.address) { <small class="hint">· {{ e.location.address }}</small> }</td>
                <td>
                  <mat-form-field class="compact w-wide">
                    <mat-select [ngModel]="e.role" (ngModelChange)="setRole(i, $event)" [name]="'m_role' + i" [aria-label]="'Papel de ' + e.location.name">
                      @for (r of roles; track r) { <mat-option [value]="r">{{ roleLabel(r) }}</mat-option> }
                    </mat-select>
                  </mat-form-field>
                </td>
                @if (e.role === 'delivery') {
                  <td>
                    <mat-form-field class="compact w-wide">
                      <mat-select [(ngModel)]="e.location.product_group" (ngModelChange)="emit()" [name]="'m_grp' + i" [aria-label]="'Grupo de ' + e.location.name" [panelWidth]="null">
                        <mat-select-trigger><span [attr.class]="'grp grp-' + e.location.product_group">{{ e.location.product_group }}</span> Medicamentos/Insumos {{ e.location.product_group }}</mat-select-trigger>
                        @for (g of catalog?.product_groups ?? []; track g.code) {
                          <mat-option [value]="g.code"><span [attr.class]="'grp grp-' + g.code">{{ g.code }}</span> {{ g.name }}</mat-option>
                        }
                      </mat-select>
                    </mat-form-field>
                  </td>
                  <td>
                    <mat-form-field class="compact">
                      <mat-select [(ngModel)]="e.location.priority" (ngModelChange)="emit()" [name]="'m_prio' + i" [aria-label]="'Prioridade de ' + e.location.name">
                        <mat-option value="critical">Crítica</mat-option>
                        <mat-option value="regular">Regular</mat-option>
                      </mat-select>
                    </mat-form-field>
                  </td>
                  <td>
                    <mat-form-field class="compact w-num">
                      <input matInput type="number" min="0" [(ngModel)]="e.location.demand" (ngModelChange)="emit()" [name]="'m_dem' + i" [attr.aria-label]="'Demanda de ' + e.location.name" />
                    </mat-form-field>
                  </td>
                  <td>
                    <span class="window-inputs">
                      <mat-form-field class="compact w-time">
                        <input matInput type="time" [(ngModel)]="e.location.window_start" (ngModelChange)="emit()" [name]="'m_ws' + i" [attr.aria-label]="'Início da janela de ' + e.location.name" />
                      </mat-form-field>
                      <span>às</span>
                      <mat-form-field class="compact w-time">
                        <input matInput type="time" [(ngModel)]="e.location.window_end" (ngModelChange)="emit()" [name]="'m_we' + i" [attr.aria-label]="'Fim da janela de ' + e.location.name" />
                      </mat-form-field>
                    </span>
                  </td>
                } @else {
                  <td class="hint">—</td><td class="hint">—</td><td class="hint">—</td><td class="hint">—</td>
                }
                <td>
                  <button mat-icon-button type="button" (click)="remove(i)" matTooltip="Excluir cidade" [attr.aria-label]="'Excluir ' + e.location.name">
                    <mat-icon>delete</mat-icon>
                  </button>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
})
export class ManualCitiesComponent implements OnInit {
  @Input() catalog: Catalog | null = null;
  @Output() selectionChange = new EventEmitter<ManualSelection>();

  private api = inject(ApiService);

  readonly roles: CityRole[] = ['both', 'origin', 'destination', 'delivery'];
  states: StateInfo[] = [];
  cityOptions: string[] = [];
  loadingCities = false;
  adding = false;
  formError = '';
  entries: ManualEntry[] = [];

  form = {
    state: 'SP',
    city: '',
    role: 'both' as CityRole,
    product_group: 'D' as ProductGroup,
    priority: 'regular' as Priority,
    demand: 20,
    window_start: '08:00',
    window_end: '18:00',
  };

  ngOnInit(): void {
    this.api.states().subscribe({ next: (s) => (this.states = s) });
    this.loadCities(this.form.state);
    this.entries = this.restore();
    this.form.role = this.entries.length ? 'delivery' : 'both';
    // Emite depois do ciclo atual de detecção de mudanças do pai.
    Promise.resolve().then(() => this.emit());
  }

  /** Sugestões do autocomplete: municípios que começam com o texto digitado, depois os que o contêm. */
  get filteredCities(): string[] {
    const q = fold(this.form.city);
    if (!q) return this.cityOptions.slice(0, MAX_SUGGESTIONS);
    const starts: string[] = [];
    const contains: string[] = [];
    for (const name of this.cityOptions) {
      const f = fold(name);
      if (f.startsWith(q)) starts.push(name);
      else if (f.includes(q)) contains.push(name);
      if (starts.length >= MAX_SUGGESTIONS) break;
    }
    return [...starts, ...contains].slice(0, MAX_SUGGESTIONS);
  }

  roleLabel(role: CityRole): string {
    return ROLE_LABELS[role];
  }

  loadCities(uf: string): void {
    this.cityOptions = [];
    this.loadingCities = true;
    this.api.stateCities(uf).subscribe({
      next: (names) => {
        this.cityOptions = names;
        this.loadingCities = false;
      },
      // Sem IBGE: o campo continua aceitando texto livre.
      error: () => (this.loadingCities = false),
    });
  }

  add(): void {
    const city = this.form.city.trim();
    if (!city || this.adding) return;
    this.formError = '';
    this.adding = true;
    this.api.geocode(city, this.form.state).subscribe({
      next: (geo) => {
        this.adding = false;
        if (this.entries.some((e) => e.location.name === geo.name)) {
          this.formError = `${geo.name} já está na lista.`;
          return;
        }
        if (this.form.role === 'delivery' && !(this.form.window_start < this.form.window_end)) {
          this.formError = 'O início da janela de recebimento deve ser antes do fim.';
          return;
        }
        const isHub = this.form.role !== 'delivery';
        this.entries = [
          ...this.entries,
          {
            role: 'delivery',
            location: {
              name: geo.name,
              lat: geo.lat,
              lng: geo.lng,
              address: geo.address,
              product_group: this.form.product_group,
              priority: isHub ? 'regular' : this.form.priority,
              demand: isHub ? 0 : Number(this.form.demand) || 0,
              window_start: isHub ? '00:00' : this.form.window_start,
              window_end: isHub ? '23:59' : this.form.window_end,
            },
          },
        ];
        this.setRole(this.entries.length - 1, this.form.role);
        this.form.city = '';
        this.form.role = 'delivery';
      },
      error: (err) => {
        this.adding = false;
        this.formError = err?.error?.detail ?? `Não foi possível localizar ${city}.`;
      },
    });
  }

  /** Mantém uma única origem e um único destino: o novo papel tira o mesmo papel das outras cidades. */
  setRole(index: number, role: CityRole): void {
    const takesOrigin = role === 'origin' || role === 'both';
    const takesDestination = role === 'destination' || role === 'both';
    this.entries.forEach((e, j) => {
      if (j === index) return;
      let origin = e.role === 'origin' || e.role === 'both';
      let destination = e.role === 'destination' || e.role === 'both';
      if (takesOrigin) origin = false;
      if (takesDestination) destination = false;
      e.role = origin && destination ? 'both' : origin ? 'origin' : destination ? 'destination' : 'delivery';
    });
    this.entries[index].role = role;
    this.emit();
  }

  remove(index: number): void {
    this.entries = this.entries.filter((_, i) => i !== index);
    this.emit();
  }

  clear(): void {
    this.entries = [];
    this.form.role = 'both';
    this.emit();
  }

  emit(): void {
    this.persist();
    const originIndex = this.entries.findIndex((e) => e.role === 'origin' || e.role === 'both');
    const destinationIndex = this.entries.findIndex((e) => e.role === 'destination' || e.role === 'both');
    const deliveries = this.entries.filter((e) => e.role === 'delivery');
    const badWindow = deliveries.find((e) => !(e.location.window_start < e.location.window_end));
    let error = '';
    if (!this.entries.length) error = 'Cadastre as cidades: defina a origem, o destino e ao menos uma entrega.';
    else if (originIndex < 0) error = 'Defina a cidade de origem.';
    else if (destinationIndex < 0) error = 'Defina a cidade de destino (ou marque a origem como "Origem e destino").';
    else if (!deliveries.length) error = 'Cadastre ao menos uma cidade de entrega.';
    else if (badWindow) error = `${badWindow.location.name}: o início da janela de recebimento deve ser antes do fim.`;
    this.selectionChange.emit({
      locations: this.entries.map((e) => e.location),
      originIndex: Math.max(0, originIndex),
      destinationIndex: Math.max(0, destinationIndex),
      error,
    });
  }

  private persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.entries));
    } catch {
      /* armazenamento indisponível: a lista fica só em memória */
    }
  }

  private restore(): ManualEntry[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.filter((e) => e?.location?.name && e?.role) : [];
    } catch {
      return [];
    }
  }
}
