import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Observable } from 'rxjs';
import { ManualCitiesComponent, ManualSelection } from './components/manual-cities.component';
import { ResultViewComponent } from './components/result-view.component';
import { TransportRulesComponent } from './components/transport-rules.component';
import {
  Catalog,
  ConstraintsConfig,
  GAConfig,
  Location,
  OptimizeResponse,
  ScheduleConfig,
  VEHICLE_ICONS,
  VehicleIn,
  VehicleTypeInfo,
} from './models';
import { ApiService } from './services/api.service';

type Mode = 'default' | 'random' | 'manual';
type LoadingKind = 'cities' | 'optimize';

interface LoadingStep {
  label: string;
  /** Segundos decorridos a partir dos quais a etapa aparece como "em andamento" (estimativa). */
  after: number;
}

interface ParamField<K> {
  key: K;
  label: string;
  help: string;
  min: number;
  max?: number;
  step?: number;
}

type GANumericKey = Exclude<keyof GAConfig, 'seed'>;
type ConstraintKey = Exclude<keyof ConstraintsConfig, 'soft_penalty'>;

interface ConstraintField {
  key: ConstraintKey;
  label: string;
  help: string;
}

const MAX_VEHICLES = 10;

@Component({
  selector: 'app-planner',
  standalone: true,
  imports: [
    FormsModule, ResultViewComponent, TransportRulesComponent, ManualCitiesComponent,
    MatButtonModule, MatButtonToggleModule, MatCheckboxModule, MatFormFieldModule, MatIconModule, MatInputModule,
    MatProgressSpinnerModule, MatSelectModule, MatTooltipModule,
  ],
  template: `
    <main>
      <section class="card">
        <mat-button-toggle-group class="mode-toggle" [value]="mode" (change)="setMode($event.value)"
                                 aria-label="Tipo de cenário" hideSingleSelectionIndicator>
          <mat-button-toggle value="default"><mat-icon>star</mat-icon> Cenário padrão (5 cidades)</mat-button-toggle>
          <mat-button-toggle value="random"><mat-icon>shuffle</mat-icon> Cidades aleatórias</mat-button-toggle>
          <mat-button-toggle value="manual"><mat-icon>edit_location_alt</mat-icon> Cadastro manual</mat-button-toggle>
        </mat-button-toggle-group>

        @if (catalog) {
          <app-transport-rules [rules]="catalog.transport_rules" [groups]="catalog.product_groups" />
        }

        @if (mode === 'random') {
          <div class="row">
            <mat-form-field>
              <mat-label>Nº de cidades</mat-label>
              <input matInput type="number" min="3" max="50" [(ngModel)]="cityCount" name="cityCount" />
            </mat-form-field>
            <button mat-stroked-button type="button" (click)="generateCities()" [disabled]="loading">
              @if (loadingKind === 'cities') { <mat-spinner class="btn-spinner" diameter="18" /> } @else { <mat-icon>shuffle</mat-icon> }
              {{ loadingKind === 'cities' ? 'Gerando...' : 'Gerar cidades' }}
            </button>
          </div>
        }

        @if (mode === 'manual') {
          <app-manual-cities [catalog]="catalog" (selectionChange)="onManualChange($event)" />
        }

        @if (cities.length && mode !== 'manual') {
          <div class="table-scroll">
            <table class="data stack stack-wide t-cities">
              <thead>
                <tr><th>#</th><th>Cidade</th><th>Grupo</th><th>Prioridade</th><th>Demanda (kg)</th><th>Janela de recebimento</th><th>Papel</th></tr>
              </thead>
              <tbody>
                @for (c of cities; track c.name; let i = $index) {
                  <tr>
                    <td>{{ i + 1 }}</td>
                    <td>{{ c.name }} @if (c.address) { <small class="hint">· {{ c.address }}</small> }</td>
                    <td>
                      @if (isHub(i)) { <span class="hint">—</span> }
                      @else if (mode === 'random' && catalog) {
                        <mat-form-field class="compact w-wide">
                          <mat-select [(ngModel)]="c.product_group" [name]="'grp' + i" [aria-label]="'Grupo de ' + c.name" [panelWidth]="null">
                            <mat-select-trigger><span [attr.class]="'grp grp-' + c.product_group">{{ c.product_group }}</span> Medicamentos/Insumos {{ c.product_group }}</mat-select-trigger>
                            @for (g of catalog.product_groups; track g.code) {
                              <mat-option [value]="g.code"><span [attr.class]="'grp grp-' + g.code">{{ g.code }}</span> {{ g.name }}</mat-option>
                            }
                          </mat-select>
                        </mat-form-field>
                      } @else {
                        <span class="nowrap"><span [attr.class]="'grp grp-' + c.product_group">{{ c.product_group }}</span> {{ groupName(c.product_group) }}</span>
                      }
                    </td>
                    <td>
                      @if (isHub(i)) { <span class="hint">—</span> }
                      @else if (mode === 'random') {
                        <mat-form-field class="compact">
                          <mat-select [(ngModel)]="c.priority" [name]="'prio' + i" [aria-label]="'Prioridade de ' + c.name">
                            <mat-option value="critical">Crítica</mat-option>
                            <mat-option value="regular">Regular</mat-option>
                          </mat-select>
                        </mat-form-field>
                      } @else {
                        <span [class.critical]="c.priority === 'critical'">{{ c.priority === 'critical' ? 'Crítica' : 'Regular' }}</span>
                      }
                    </td>
                    <td>
                      @if (isHub(i)) { <span class="hint">—</span> }
                      @else if (mode === 'random') {
                        <mat-form-field class="compact w-num">
                          <input matInput type="number" min="0" [(ngModel)]="c.demand" [name]="'dem' + i" [attr.aria-label]="'Demanda de ' + c.name" />
                        </mat-form-field>
                      } @else { {{ c.demand }} }
                    </td>
                    <td>
                      @if (isHub(i)) { <span class="hint">—</span> }
                      @else if (mode === 'random') {
                        <span class="window-inputs">
                          <mat-form-field class="compact w-time">
                            <input matInput type="time" [(ngModel)]="c.window_start" [name]="'ws' + i" [attr.aria-label]="'Início da janela de ' + c.name" />
                          </mat-form-field>
                          <span>às</span>
                          <mat-form-field class="compact w-time">
                            <input matInput type="time" [(ngModel)]="c.window_end" [name]="'we' + i" [attr.aria-label]="'Fim da janela de ' + c.name" />
                          </mat-form-field>
                        </span>
                      } @else { {{ c.window_start }} às {{ c.window_end }} }
                    </td>
                    <td>{{ role(i) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        @if (mode === 'random' && cities.length) {
          <div class="row">
            <mat-form-field>
              <mat-label>Origem</mat-label>
              <mat-select [(ngModel)]="originIndex" name="origin">
                @for (c of cities; track c.name; let i = $index) { <mat-option [value]="i">{{ c.name }}</mat-option> }
              </mat-select>
              <mat-icon matSuffix>local_hospital</mat-icon>
            </mat-form-field>
            <mat-form-field>
              <mat-label>Destino</mat-label>
              <mat-select [(ngModel)]="destinationIndex" name="destination">
                @for (c of cities; track c.name; let i = $index) { <mat-option [value]="i">{{ c.name }}</mat-option> }
              </mat-select>
              <mat-icon matSuffix>flag</mat-icon>
            </mat-form-field>
          </div>
        }

        <div class="param-section">
          <fieldset class="param-group">
            <legend>🚚 Frota e operação</legend>
            <div class="params">
              <mat-form-field>
                <mat-label>Quantidade de veículos</mat-label>
                <input matInput type="number" min="1" [max]="maxVehicles" [(ngModel)]="vehicleCount" name="vehicleCount"
                       (change)="generateFleet()" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="fleetHelp" tabindex="0" aria-label="Ajuda: quantidade de veículos">help_outline</mat-icon>
              </mat-form-field>
              <mat-form-field>
                <mat-label>Horário de saída</mat-label>
                <input matInput type="time" [(ngModel)]="schedule.departure_time" name="departure" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="departureHelp" tabindex="0" aria-label="Ajuda: horário de saída">help_outline</mat-icon>
              </mat-form-field>
              <mat-form-field>
                <mat-label>Tempo de descarga (min)</mat-label>
                <input matInput type="number" min="0" max="240" [(ngModel)]="schedule.service_minutes" name="service" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="serviceHelp" tabindex="0" aria-label="Ajuda: tempo de descarga">help_outline</mat-icon>
              </mat-form-field>
              <mat-form-field>
                <mat-label>Reserva para abastecer (%)</mat-label>
                <input matInput type="number" min="0" max="50" [(ngModel)]="schedule.refuel_reserve_pct" name="refuelReserve"
                       [disabled]="!schedule.refuel_enabled" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="reserveHelp" tabindex="0" aria-label="Ajuda: reserva para abastecer">help_outline</mat-icon>
              </mat-form-field>
              <mat-form-field>
                <mat-label>Tempo de abastecimento (min)</mat-label>
                <input matInput type="number" min="0" max="120" [(ngModel)]="schedule.refuel_minutes" name="refuelMinutes"
                       [disabled]="!schedule.refuel_enabled" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="refuelTimeHelp" tabindex="0" aria-label="Ajuda: tempo de abastecimento">help_outline</mat-icon>
              </mat-form-field>
            </div>
            <div class="check-row">
              <mat-checkbox [(ngModel)]="schedule.refuel_enabled" name="refuelEnabled">⛽ Abastecer em rota (todos saem de tanque cheio)</mat-checkbox>
              <mat-icon class="help-icon" [matTooltip]="refuelHelp" tabindex="0" aria-label="Ajuda: abastecimento em rota">help_outline</mat-icon>
            </div>

            @if (catalog && fleet.length) {
              <div class="table-scroll">
                <table class="data stack t-fleet">
                  <thead><tr><th>#</th><th>Veículo</th><th>Autonomia</th><th>Peso máximo</th><th>Refrigeração</th><th>Observação</th><th><span class="sr-only">Ações</span></th></tr></thead>
                  <tbody>
                    @for (v of fleet; track v; let i = $index) {
                      <tr>
                        <td>{{ i + 1 }}</td>
                        <td>
                          <mat-form-field class="compact w-wide">
                            <mat-select [(ngModel)]="v.type" [name]="'veh' + i" [aria-label]="'Tipo do veículo ' + (i + 1)" [panelWidth]="null">
                              @for (t of catalog.vehicle_types; track t.type) { <mat-option [value]="t.type">{{ icon(t.type) }} {{ t.name }}</mat-option> }
                            </mat-select>
                          </mat-form-field>
                        </td>
                        <td>{{ typeInfo(v.type)?.autonomy_km }} km</td>
                        <td>{{ typeInfo(v.type)?.capacity }} kg</td>
                        <td>{{ typeInfo(v.type)?.refrigerated ? '❄️ Sim' : 'Não' }}</td>
                        <td class="hint">
                          @if (typeInfo(v.type)?.strict_autonomy) { 🔒 Autonomia nunca pode ser excedida }
                          @if (typeInfo(v.type)?.refrigerated) { Único apto a levar o grupo B }
                        </td>
                        <td>
                          <button mat-icon-button type="button" (click)="removeVehicle(i)" [disabled]="fleet.length <= 1"
                                  [attr.aria-label]="'Excluir veículo ' + (i + 1) + ' (' + (typeInfo(v.type)?.name ?? v.type) + ')'"
                                  [matTooltip]="fleet.length <= 1 ? 'A frota precisa de ao menos um veículo' : 'Excluir veículo'">
                            <mat-icon>delete</mat-icon>
                          </button>
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
              <p class="hint">A frota é sorteada ao alterar a quantidade; o tipo de cada veículo pode ser trocado e veículos podem ser excluídos na lista, sem novo sorteio. Autonomia e peso seguem o cadastro de cada tipo.</p>
            }
          </fieldset>

          <fieldset class="param-group">
            <legend>🧬 Parâmetros do algoritmo genético</legend>
            <div class="params">
              @for (f of gaFields; track f.key) {
                <mat-form-field>
                  <mat-label>{{ f.label }}</mat-label>
                  <input matInput type="number" [min]="f.min" [max]="f.max ?? null" [attr.step]="f.step ?? 1" [(ngModel)]="ga[f.key]" [name]="'ga_' + f.key" />
                  <mat-icon matSuffix class="help-icon" [matTooltip]="f.help" tabindex="0" [attr.aria-label]="'Ajuda: ' + f.label">help_outline</mat-icon>
                </mat-form-field>
              }
              <mat-form-field>
                <mat-label>Seed (semente)</mat-label>
                <input matInput type="number" min="0" step="1" [(ngModel)]="ga.seed" name="ga_seed" placeholder="vazio = aleatória" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="seedHelp" tabindex="0" aria-label="Ajuda: seed">help_outline</mat-icon>
              </mat-form-field>
            </div>
          </fieldset>

          <fieldset class="param-group">
            <legend>⚖️ Tipo das restrições</legend>
            <div class="params">
              @for (f of constraintFields; track f.key) {
                <mat-form-field>
                  <mat-label>{{ f.label }}</mat-label>
                  <mat-select [(ngModel)]="constraints[f.key]" [name]="'c_' + f.key">
                    <mat-option value="hard">Rígida (hard)</mat-option>
                    <mat-option value="soft">Flexível (soft)</mat-option>
                  </mat-select>
                  <mat-icon matSuffix class="help-icon" [matTooltip]="f.help" tabindex="0" [attr.aria-label]="'Ajuda: ' + f.label">help_outline</mat-icon>
                </mat-form-field>
              }
              <mat-form-field>
                <mat-label>Contaminação</mat-label>
                <mat-select value="hard" disabled name="c_contamination">
                  <mat-option value="hard">🔒 Sempre rígida</mat-option>
                </mat-select>
                <mat-icon matSuffix class="help-icon" [matTooltip]="contaminationHelp" tabindex="0" aria-label="Ajuda: contaminação e refrigeração">help_outline</mat-icon>
              </mat-form-field>
              <mat-form-field>
                <mat-label>Penalidade soft</mat-label>
                <input matInput type="number" min="0" step="5" [(ngModel)]="constraints.soft_penalty" name="c_soft_penalty"
                       [disabled]="!hasSoftLimit" />
                <mat-icon matSuffix class="help-icon" [matTooltip]="softPenaltyHelp" tabindex="0" aria-label="Ajuda: penalidade soft">help_outline</mat-icon>
              </mat-form-field>
            </div>
          </fieldset>

          <div class="check-row">
            <mat-checkbox [(ngModel)]="useLlm" name="llm">Gerar relatórios com LLM</mat-checkbox>
            <mat-icon class="help-icon" [matTooltip]="llmHelp" tabindex="0" aria-label="Ajuda: relatórios com LLM">help_outline</mat-icon>
          </div>
        </div>

        <div class="actions">
          <button mat-flat-button type="button" (click)="optimize()" [disabled]="loading || !canOptimize">
            @if (loadingKind === 'optimize') { <mat-spinner class="btn-spinner" diameter="18" /> } @else { <mat-icon>route</mat-icon> }
            {{ loadingKind === 'optimize' ? 'Otimizando...' : 'Otimizar rotas' }}
          </button>
          @if (!canOptimize && blockReason) { <span class="hint">{{ blockReason }}</span> }
        </div>

        @if (loading) {
          <div class="loading-panel" role="status" aria-live="polite">
            <mat-spinner diameter="32" />
            <div class="loading-body">
              <div class="loading-title">
                <strong>{{ loadingSteps[currentStep].label }}...</strong>
                <span>{{ elapsedSeconds }}s</span>
              </div>
              @if (loadingSteps.length > 1) {
                <ol class="loading-steps">
                  @for (step of loadingSteps; track step.label; let i = $index) {
                    <li [class.done]="i < currentStep" [class.active]="i === currentStep">
                      <span class="step-icon">{{ i < currentStep ? '✓' : i === currentStep ? '●' : '○' }}</span>
                      {{ step.label }}
                    </li>
                  }
                </ol>
                @if (useLlm && loadingKind === 'optimize') {
                  <p class="hint">A geração dos relatórios pela LLM pode levar de 10 a 40 segundos.</p>
                }
              }
            </div>
          </div>
        }
        @if (error) {
          <div class="error-box" role="alert">
            <p>{{ error }}</p>
            @if (errorReasons.length) {
              <ul>
                @for (r of errorReasons; track $index) { <li>{{ r }}</li> }
              </ul>
            }
          </div>
        }
      </section>

      @if (result) {
        <app-result-view [result]="result" [catalog]="catalog" />
      }
    </main>
  `,
})
export class PlannerComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  mode: Mode = 'default';
  cityCount = 8;
  cities: Location[] = [];
  originIndex = 0;
  destinationIndex = 0;

  catalog: Catalog | null = null;
  readonly maxVehicles = MAX_VEHICLES;
  vehicleCount = 5;
  fleet: VehicleIn[] = [];
  schedule: ScheduleConfig = {
    departure_time: '07:00',
    service_minutes: 15,
    refuel_enabled: true,
    refuel_reserve_pct: 15,
    refuel_minutes: 15,
  };

  ga: GAConfig = {
    population_size: 100,
    generations: 300,
    mutation_probability: 0.3,
    tournament_size: 3,
    elite_size: 2,
    priority_weight: 0.5,
    seed: 42,
    stagnation_generations: 100,
  };
  useLlm = true;

  readonly fleetHelp =
    'Ao sair do campo, é sorteada uma frota com essa quantidade de veículos (moto, carro, carro elétrico, caminhão com ou sem refrigeração). Cada tipo tem autonomia e peso máximo do cadastro. O algoritmo decide quais veículos usar.';
  readonly departureHelp = 'Horário em que todos os veículos saem da origem. É a base para calcular a chegada em cada cidade.';
  readonly serviceHelp = 'Tempo parado em cada entrega para descarregar e conferir a mercadoria.';
  readonly refuelHelp =
    'Todos os veículos saem de tanque cheio. Antes de um trecho que deixaria a autonomia abaixo da reserva, o veículo abastece na cidade em que está, o que adiciona o tempo de abastecimento à rota. Assim a autonomia limita cada trecho, não o percurso total. O carro elétrico nunca abastece.';
  readonly reserveHelp = 'Percentual da autonomia que dispara o abastecimento: com 15%, o veículo abastece antes de ficar com menos de 15% do tanque.';
  readonly refuelTimeHelp = 'Tempo parado em cada abastecimento, somado aos horários de chegada das paradas seguintes.';

  readonly seedHelp =
    'Semente do gerador aleatório do algoritmo genético. Com a mesma semente e os mesmos parâmetros, o resultado é sempre o mesmo, o que permite comparar experimentos. Deixe vazio para uma semente aleatória a cada execução.';

  readonly gaFields: ParamField<GANumericKey>[] = [
    {
      key: 'population_size',
      label: 'População',
      help: 'Quantas soluções (rotas candidatas) existem em cada geração. Mais indivíduos exploram mais possibilidades, mas cada geração fica mais lenta.',
      min: 10, max: 1000,
    },
    {
      key: 'generations',
      label: 'Gerações',
      help: 'Quantas vezes a população é recombinada e evolui. Mais gerações dão mais tempo para melhorar a rota, com custo de tempo proporcional.',
      min: 1, max: 5000,
    },
    {
      key: 'stagnation_generations',
      label: 'Parada por estagnação',
      help: 'Critério de parada: encerra antes do limite de gerações se o melhor fitness ficar esse número de gerações sem melhorar. Economiza tempo quando o algoritmo já convergiu. 0 = desativado (roda todas as gerações).',
      min: 0, max: 5000,
    },
    {
      key: 'mutation_probability',
      label: 'Probabilidade de mutação',
      help: 'Chance (0 a 1) de cada nova solução sofrer uma mutação: troca de duas entregas ou inversão de um trecho da rota. Valores altos aumentam a diversidade e ajudam a escapar de ótimos locais.',
      min: 0, max: 1, step: 0.05,
    },
    {
      key: 'tournament_size',
      label: 'Tamanho do torneio (k)',
      help: 'Para escolher cada pai, k soluções são sorteadas e a melhor vence. k maior favorece mais as melhores soluções (convergência rápida); k menor mantém mais diversidade.',
      min: 2, max: 20,
    },
    {
      key: 'elite_size',
      label: 'Elitismo',
      help: 'Quantas das melhores soluções passam intactas para a próxima geração. Garante que a melhor rota encontrada nunca se perca.',
      min: 1, max: 50,
    },
    {
      key: 'priority_weight',
      label: 'Peso da prioridade',
      help: 'Quanto o fitness penaliza entregas críticas feitas tarde na rota. 0 = só minimiza distância; valores maiores antecipam as entregas críticas, mesmo que a rota fique um pouco mais longa.',
      min: 0, step: 0.1,
    },
  ];

  constraints: ConstraintsConfig = {
    capacity: 'hard',
    autonomy: 'hard',
    priority: 'soft',
    time_window: 'soft',
    soft_penalty: 50,
  };

  readonly constraintFields: ConstraintField[] = [
    {
      key: 'capacity',
      label: 'Capacidade',
      help: 'Rígida: nenhum veículo pode levar mais que seu peso máximo; se for impossível, a otimização é recusada com o motivo. Flexível: o excesso é permitido, pagando a penalidade soft por kg excedido.',
    },
    {
      key: 'autonomy',
      label: 'Autonomia',
      help: 'Rígida: nenhum veículo pode rodar mais que sua autonomia (ida e volta). Flexível: o excesso é permitido, pagando a penalidade soft por km excedido. O carro elétrico nunca pode exceder a autonomia.',
    },
    {
      key: 'priority',
      label: 'Prioridade',
      help: 'Flexível: entregas críticas tendem a vir antes, conforme o peso da prioridade. Rígida: em cada veículo, todas as entregas críticas são feitas antes de qualquer regular.',
    },
    {
      key: 'time_window',
      label: 'Janela de horário',
      help: 'Rígida: o veículo nunca chega depois do fim da janela de recebimento. Flexível: o atraso é permitido, pagando a penalidade soft por minuto. Chegar antes do início gera espera, sem penalidade.',
    },
  ];

  readonly contaminationHelp =
    'Regras sanitárias, sempre rígidas: Medicamentos/Insumos A e C não viajam juntos; B viaja sozinho e somente em caminhão com refrigeração. Se a frota não permitir, a otimização é recusada com o motivo.';
  readonly softPenaltyHelp =
    'Custo, em km equivalentes, de cada kg, km ou minuto excedido nas restrições flexíveis. Valores baixos aceitam excessos para encurtar a rota; valores altos fazem a restrição se comportar quase como rígida.';

  get hasSoftLimit(): boolean {
    const c = this.constraints;
    return c.capacity === 'soft' || c.autonomy === 'soft' || c.time_window === 'soft';
  }

  readonly llmHelp =
    'Envia o resultado, os parâmetros e a frota para uma LLM (via OpenRouter), que gera dois textos: uma análise técnica com sugestões de ajuste de parâmetros e as instruções para os motoristas. Desmarque para otimizar mais rápido e sem consumir a API.';

  loading = false;
  loadingKind: LoadingKind | null = null;
  loadingSteps: LoadingStep[] = [];
  currentStep = 0;
  elapsedSeconds = 0;
  private loadingTimer?: ReturnType<typeof setInterval>;
  error = '';
  errorReasons: string[] = [];
  manualError = '';
  result: OptimizeResponse | null = null;
  /** Execuções com as mesmas cidades, origem e destino: contexto da LLM técnica (zera ao mudar as cidades). */
  private runChain: { key: string; ids: string[] } = { key: '', ids: [] };

  ngOnInit(): void {
    this.api.catalog().subscribe({
      next: (c) => {
        this.catalog = c;
        this.fleet = c.default_fleet.map((type) => ({ type }));
        this.vehicleCount = this.fleet.length;
      },
      error: () => (this.error = 'Não foi possível carregar o cadastro de medicamentos/insumos e veículos.'),
    });
    this.loadDefault();
  }

  ngOnDestroy(): void {
    this.stopLoading();
  }

  /** Motivo pelo qual o botão "Otimizar" está desabilitado (ou vazio). */
  get blockReason(): string {
    if (this.mode === 'manual' && this.manualError) return this.manualError;
    if (!this.cities.length) return '';
    const hubs = this.originIndex === this.destinationIndex ? 1 : 2;
    if (this.cities.length - hubs < 1) return 'Selecione origem/destino de forma que reste ao menos uma cidade para entrega.';
    if (!this.fleet.length) return 'Informe a quantidade de veículos.';
    const bad = this.cities.find((c, i) => !this.isHub(i) && !(c.window_start < c.window_end));
    if (bad) return `${bad.name}: o início da janela de recebimento deve ser antes do fim.`;
    return '';
  }

  get canOptimize(): boolean {
    return this.cities.length > 0 && !this.blockReason;
  }

  isHub(i: number): boolean {
    return i === this.originIndex || i === this.destinationIndex;
  }

  typeInfo(type: string): VehicleTypeInfo | undefined {
    return this.catalog?.vehicle_types.find((t) => t.type === type);
  }

  groupName(code: string): string {
    return this.catalog?.product_groups.find((g) => g.code === code)?.name ?? '';
  }

  icon(type: string): string {
    return VEHICLE_ICONS[type] ?? '';
  }

  /** Sorteia a frota ao sair do campo de quantidade. */
  generateFleet(): void {
    if (!this.catalog) return;
    const n = Math.min(MAX_VEHICLES, Math.max(1, Math.floor(Number(this.vehicleCount) || 1)));
    this.vehicleCount = n;
    const types = this.catalog.vehicle_types.map((t) => t.type);
    this.fleet = Array.from({ length: n }, () => ({ type: types[Math.floor(Math.random() * types.length)] }));
  }

  /** Remove um veículo da grade sem sortear de novo; o contador acompanha a frota. */
  removeVehicle(index: number): void {
    if (this.fleet.length <= 1) return;
    this.fleet = this.fleet.filter((_, i) => i !== index);
    this.vehicleCount = this.fleet.length;
  }

  /** Cidades cadastradas no modo manual (origem/destino já resolvidos pelo componente). */
  onManualChange(sel: ManualSelection): void {
    this.cities = sel.locations;
    this.originIndex = sel.originIndex;
    this.destinationIndex = sel.destinationIndex;
    this.manualError = sel.error;
  }

  setMode(mode: Mode): void {
    if (this.mode === mode) return;
    this.mode = mode;
    this.result = null;
    this.error = '';
    this.errorReasons = [];
    this.cities = [];
    this.originIndex = 0;
    this.destinationIndex = 0;
    if (mode === 'default') this.loadDefault();
  }

  role(i: number): string {
    if (i === this.originIndex && i === this.destinationIndex) return '🏥 Origem e destino';
    if (i === this.originIndex) return '🏥 Origem';
    if (i === this.destinationIndex) return '🏁 Destino';
    return 'Entrega';
  }

  loadDefault(): void {
    this.run(this.api.defaultCities(), (r) => (this.cities = r.cities), 'cities', [
      { label: 'Carregando as cidades do cenário padrão', after: 0 },
    ]);
  }

  generateCities(): void {
    this.result = null;
    this.originIndex = 0;
    this.destinationIndex = 0;
    this.run(this.api.randomCities(this.cityCount), (r) => (this.cities = r.cities), 'cities', [
      { label: `Sorteando ${this.cityCount} cidades e buscando as coordenadas`, after: 0 },
    ]);
  }

  optimize(): void {
    this.result = null;
    const ga = { ...this.ga };
    const vehicles = this.fleet.map((v) => ({ type: v.type }));
    const request$ =
      this.mode === 'default'
        ? this.api.optimizeDefault(vehicles, this.schedule, ga, this.constraints, this.useLlm, this.runChain.ids)
        : this.api.optimize({
            locations: this.cities,
            origin_index: this.originIndex,
            destination_index: this.destinationIndex,
            vehicles,
            schedule: this.schedule,
            ga,
            constraints: this.constraints,
            use_llm: this.useLlm,
            previous_result_ids: this.runChain.ids,
          });
    // O backend só responde no final; as etapas avançam por tempo estimado para dar feedback ao usuário.
    const steps: LoadingStep[] = [
      { label: 'Calculando as distâncias entre as cidades', after: 0 },
      { label: 'Verificando regras de transporte e janelas de horário', after: 1 },
      { label: 'Executando o algoritmo genético', after: 2 },
      { label: 'Comparando com outras abordagens', after: 4 },
      { label: 'Buscando o traçado das rotas no mapa', after: 5.5 },
    ];
    if (this.useLlm) steps.push({ label: 'Gerando a análise técnica e as instruções para motoristas com a LLM', after: 7 });
    this.run(request$, (r) => this.onResult(r), 'optimize', steps);
  }

  private onResult(r: OptimizeResponse): void {
    this.result = r;
    const key = scenarioKey(r);
    const ids = key === this.runChain.key ? this.runChain.ids : [];
    this.runChain = { key, ids: [...ids, r.id].slice(-5) };
  }

  private run<T>(obs: Observable<T>, onNext: (value: T) => void, kind: LoadingKind, steps: LoadingStep[]): void {
    this.startLoading(kind, steps);
    this.error = '';
    this.errorReasons = [];
    obs.subscribe({
      next: (value) => {
        onNext(value);
        this.stopLoading();
      },
      error: (err) => {
        const detail = err?.error?.detail;
        if (detail && typeof detail === 'object' && 'message' in detail) {
          // 422 de restrição rígida impossível: mensagem + motivos.
          this.error = detail.message;
          this.errorReasons = Array.isArray(detail.reasons) ? detail.reasons : [];
        } else {
          this.error = `Erro ao chamar a API: ${typeof detail === 'string' ? detail : JSON.stringify(detail ?? err.message)}`;
        }
        this.stopLoading();
      },
    });
  }

  private startLoading(kind: LoadingKind, steps: LoadingStep[]): void {
    this.stopLoading();
    this.loading = true;
    this.loadingKind = kind;
    this.loadingSteps = steps;
    this.currentStep = 0;
    this.elapsedSeconds = 0;
    const startedAt = Date.now();
    this.loadingTimer = setInterval(() => {
      const elapsed = (Date.now() - startedAt) / 1000;
      this.elapsedSeconds = Math.floor(elapsed);
      // Permanece na última etapa até a resposta chegar.
      this.currentStep = Math.max(0, steps.filter((s) => elapsed >= s.after).length - 1);
    }, 250);
  }

  private stopLoading(): void {
    if (this.loadingTimer) clearInterval(this.loadingTimer);
    this.loadingTimer = undefined;
    this.loading = false;
    this.loadingKind = null;
  }
}

/** Cidades + origem/destino: se mudar, a LLM técnica começa um histórico novo (mesma regra do backend). */
function scenarioKey(r: OptimizeResponse): string {
  const names = r.locations.map((l) => l.name).sort();
  return JSON.stringify([names, r.locations[r.origin_index].name, r.locations[r.destination_index].name]);
}
