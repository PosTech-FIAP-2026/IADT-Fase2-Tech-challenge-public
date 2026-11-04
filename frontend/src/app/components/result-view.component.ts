import { DecimalPipe } from '@angular/common';
import { Component, Input, OnChanges } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Catalog, OptimizeResponse, VEHICLE_ICONS, VehicleRoute } from '../models';
import { FitnessChartComponent } from './fitness-chart.component';
import { LlmPanelComponent } from './llm-panel.component';
import { RouteMapComponent, VEHICLE_COLORS } from './route-map.component';
import { TransportRulesComponent } from './transport-rules.component';

@Component({
  selector: 'app-result-view',
  standalone: true,
  imports: [
    DecimalPipe, RouteMapComponent, FitnessChartComponent, TransportRulesComponent, LlmPanelComponent,
    MatIconModule, MatTabsModule, MatTooltipModule,
  ],
  template: `
    <section class="card">
      <h2>Resultado</h2>
      <div class="kpis">
        <div><span>Distância total</span><strong>{{ result.total_distance_km | number: '1.0-1' }} km</strong></div>
        <div><span>Tempo estimado</span><strong>{{ hours(result.total_duration_min) }}</strong></div>
        <div><span>Veículos usados</span><strong>{{ usedVehicles }}/{{ result.vehicles.length }}</strong></div>
        <div><span>Restrições rígidas</span><strong [class.bad]="!result.feasible">{{ result.feasible ? 'Atendidas' : 'Violadas' }}</strong></div>
        <div><span>Contaminação</span><strong [class.bad]="!contaminationOk">{{ contaminationOk ? '✅ Sem conflitos' : '❌ Conflito' }}</strong></div>
        <div><span>Saída dos veículos</span><strong>{{ result.schedule.departure_time }}</strong></div>
        <div><span>Abastecimentos</span><strong>{{ result.schedule.refuel_enabled ? '⛽ ' + totalRefuels : 'Desativado' }}</strong></div>
        <div><span>Tempo do AG</span><strong>{{ result.execution_time_ms | number: '1.0-0' }} ms</strong></div>
        <div><span>Fonte das distâncias</span><strong>{{ result.distance_source === 'google_maps' ? 'Google Maps' : 'Haversine (estimada)' }}</strong></div>
      </div>
    </section>

    @if (result.soft_violations.length) {
      <section class="card warn-box" role="status">
        <h2>⚠️ Restrições flexíveis excedidas</h2>
        <p class="hint">Estas violações foram aceitas porque a restrição está marcada como flexível (soft).</p>
        <ul>
          @for (v of result.soft_violations; track $index) { <li>{{ v }}</li> }
        </ul>
      </section>
    }

    <app-transport-rules [rules]="result.transport_rules" [groups]="catalog?.product_groups ?? []" />

    <section class="card">
      <h2>Mapa das rotas</h2>
      <app-route-map [result]="result" />
    </section>

    <section class="card">
      <h2>Rotas por veículo</h2>
      <div class="table-scroll">
        <table class="data stack stack-wide t-routes">
          <thead><tr><th>Veículo</th><th>Carga transportada</th><th>Sequência e horários</th><th>Distância</th><th>Carga</th><th>Status</th></tr></thead>
          <tbody>
            @for (v of result.vehicles; track v.vehicle_id; let i = $index) {
              <tr>
                <td>
                  <span class="dot" [style.background]="color(i)"></span> {{ v.vehicle_id }}.
                  {{ icon(v.vehicle_type) }} {{ v.vehicle_name }}
                  @if (v.strict_autonomy) { <small class="hint" matTooltip="Autonomia nunca pode ser excedida">🔒</small> }
                </td>
                <td>
                  @for (g of v.groups; track g) { <span [attr.class]="'grp grp-' + g" [matTooltip]="groupName(g)">{{ g }}</span> }
                  @if (!v.groups.length) { <span class="hint">—</span> }
                </td>
                <td>
                  @if (v.stops.length) {
                    <div class="stop-line"><span class="hint">Saída {{ result.schedule.departure_time }}</span> {{ short(result.locations[v.stops[0]].name) }}</div>
                    @for (f of enRoute(v, v.stops[0]); track $index) {
                      <div class="stop-line refuel-line">
                        ⛽ No caminho: abastecer em {{ f.place }}
                        <span class="hint">· ~{{ f.time }} · km {{ f.km_into_leg | number: '1.0-0' }} do trecho · {{ result.schedule.refuel_minutes }} min · reserva de {{ f.range_left_pct | number: '1.0-0' }}%</span>
                      </div>
                    }
                    @for (st of v.schedule; track $index) {
                      <div class="stop-line">
                        <span class="arrow">→</span>
                        <span [class.critical]="result.locations[st.location].priority === 'critical'">{{ short(result.locations[st.location].name) }}</span>
                        <span [attr.class]="'grp grp-' + result.locations[st.location].product_group">{{ result.locations[st.location].product_group }}</span>
                        <span class="hint">chega {{ st.arrival }} · janela {{ result.locations[st.location].window_start }}–{{ result.locations[st.location].window_end }}</span>
                        @if (st.wait_min) { <span class="hint">· espera {{ st.wait_min | number: '1.0-0' }} min</span> }
                        @if (st.late_min) { <span class="bad">· atraso {{ st.late_min | number: '1.0-0' }} min</span> }
                      </div>
                      @if (st.refuel) {
                        <div class="stop-line refuel-line">
                          ⛽ Abastece em {{ short(result.locations[st.location].name) }} antes de seguir
                          <span class="hint">· {{ result.schedule.refuel_minutes }} min · chegou com {{ refuelAt(v, st.location)?.range_left_pct | number: '1.0-0' }}% da autonomia</span>
                        </div>
                      }
                      @for (f of enRoute(v, st.location); track $index) {
                        <div class="stop-line refuel-line">
                          ⛽ No caminho: abastecer em {{ f.place }}
                          <span class="hint">· ~{{ f.time }} · km {{ f.km_into_leg | number: '1.0-0' }} do trecho · {{ result.schedule.refuel_minutes }} min · reserva de {{ f.range_left_pct | number: '1.0-0' }}%</span>
                        </div>
                      }
                    }
                    <div class="stop-line"><span class="arrow">→</span> {{ short(result.locations[v.stops[v.stops.length - 1]].name) }} <span class="hint">chega {{ v.return_time }}</span></div>
                  } @else { <em>não utilizado</em> }
                </td>
                <td>
                  {{ v.distance_km | number: '1.0-1' }} km
                  <span class="hint">
                    · autonomia {{ v.autonomy_km }} km
                    @if (v.refuels.length) { · ⛽ {{ v.refuels.length }} abastecimento{{ v.refuels.length > 1 ? 's' : '' }} }
                  </span>
                </td>
                <td>{{ v.load }} / {{ v.capacity }} kg</td>
                <td>
                  @if (!v.stops.length) { — } @else if (allOk(v)) { ✅ } @else {
                    <span class="bad">{{ problems(v) }}</span>
                  }
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <p class="hint"><span class="critical">Sublinhado</span> = entrega crítica (prioridade alta). Letras coloridas = grupo de medicamentos/insumos.</p>
    </section>

    <div class="grid2">
      <section class="card">
        <h2>Comparativo de abordagens</h2>
        <table class="data stack t-compare">
          <thead><tr><th>Abordagem</th><th>Distância</th><th>Fitness</th><th>Factível</th></tr></thead>
          <tbody>
            <tr><td><b>Algoritmo genético</b></td><td>{{ result.comparison.genetic_algorithm.distance_km | number: '1.0-1' }} km</td><td>{{ result.comparison.genetic_algorithm.fitness | number: '1.0-0' }}</td><td>{{ result.comparison.genetic_algorithm.feasible ? 'Sim' : 'Não' }}</td></tr>
            <tr><td>Vizinho mais próximo</td><td>{{ result.comparison.nearest_neighbor.distance_km | number: '1.0-1' }} km</td><td>{{ result.comparison.nearest_neighbor.fitness | number: '1.0-0' }}</td><td>{{ result.comparison.nearest_neighbor.feasible ? 'Sim' : 'Não' }}</td></tr>
            <tr><td>Aleatória (média)</td><td>{{ result.comparison.random_average.distance_km | number: '1.0-1' }} km</td><td>{{ result.comparison.random_average.fitness | number: '1.0-0' }}</td><td>{{ result.comparison.random_average.feasible ? 'Sim' : 'Não' }}</td></tr>
          </tbody>
        </table>
        <p class="hint">
          Melhoria do fitness: {{ result.comparison.improvement_vs_nearest_neighbor_pct | number: '1.0-1' }}% vs. vizinho mais próximo ·
          {{ result.comparison.improvement_vs_random_pct | number: '1.0-1' }}% vs. aleatória
        </p>
      </section>
      <section class="card">
        <h2>Evolução do fitness ({{ result.generations }} gerações)</h2>
        <app-fitness-chart [data]="result.fitness_history" />
        <p class="hint">
          @if (result.stopped_by_stagnation) {
            ⏹️ Parou na geração {{ result.generations }} de {{ result.ga.generations }}: {{ result.ga.stagnation_generations }} gerações sem melhora (critério de estagnação).
          } @else {
            Executou todas as {{ result.ga.generations }} gerações.
          }
          Seed: {{ result.ga.seed ?? 'aleatória' }}.
        </p>
      </section>
    </div>

    <section class="card">
      <h2>Relatórios e perguntas (LLM)</h2>
      <!-- preserveContent: os dois painéis ficam montados, preservando o histórico de cada chat ao trocar de aba. -->
      <mat-tab-group [(selectedIndex)]="llmTabIndex" preserveContent mat-stretch-tabs="false" mat-align-tabs="start">
        <mat-tab>
          <ng-template mat-tab-label><mat-icon>science</mat-icon>&nbsp;Análise técnica</ng-template>
          <div class="llm-tab">
          <app-llm-panel
          [audience]="'technical'"
          [resultId]="result.id"
          [report]="result.llm_technical"
          [previousRuns]="result.previous_result_ids.length"
          description="Para a equipe que testa o sistema: diagnóstico da execução, convergência do algoritmo genético e sugestões de ajuste de parâmetros com justificativa."
          chatTitle="Perguntas técnicas (execução e parâmetros)"
          placeholder="Ex.: Vale aumentar a mutação ou as gerações neste cenário?" />
          </div>
        </mat-tab>
        <mat-tab>
          <ng-template mat-tab-label><mat-icon>local_shipping</mat-icon>&nbsp;Instruções para motoristas</ng-template>
          <div class="llm-tab">
          <app-llm-panel
          [audience]="'driver'"
          [resultId]="result.id"
          [report]="result.llm_driver"
          description="Para quem faz o trajeto: o que carregar, cuidados com os medicamentos, horários e ordem das paradas."
          chatTitle="Perguntas sobre o trajeto"
          placeholder="Ex.: Que horas devo chegar em Campinas e o que entrego lá?" />
          </div>
        </mat-tab>
      </mat-tab-group>
    </section>
  `,
})
export class ResultViewComponent implements OnChanges {
  @Input({ required: true }) result!: OptimizeResponse;
  @Input() catalog: Catalog | null = null;

  llmTabIndex = 0;

  get usedVehicles(): number {
    return this.result.vehicles.filter((v) => v.stops.length).length;
  }

  ngOnChanges(): void {
    this.llmTabIndex = 0;
  }

  get totalRefuels(): number {
    return this.result.vehicles.reduce((n, v) => n + v.refuels.length, 0);
  }

  refuelAt(v: VehicleRoute, location: number) {
    return v.refuels.find((f) => !f.en_route && f.location === location);
  }

  /** Paradas para abastecer no caminho do trecho que sai da cidade `from`. */
  enRoute(v: VehicleRoute, from: number) {
    return v.refuels.filter((f) => f.en_route && f.leg_from === from);
  }

  get contaminationOk(): boolean {
    return this.result.vehicles.every((v) => v.contamination_ok);
  }

  icon(type: string): string {
    return VEHICLE_ICONS[type] ?? '';
  }

  groupName(code: string): string {
    return this.catalog?.product_groups.find((g) => g.code === code)?.name ?? `Medicamentos/Insumos ${code}`;
  }

  allOk(v: VehicleRoute): boolean {
    return v.capacity_ok && v.autonomy_ok && v.priority_ok && v.contamination_ok && v.time_ok;
  }

  problems(v: VehicleRoute): string {
    const out: string[] = [];
    if (!v.capacity_ok) out.push('Capacidade excedida.');
    if (!v.autonomy_ok) out.push('Autonomia excedida.');
    if (!v.priority_ok) out.push('Crítica após regular.');
    if (!v.time_ok) out.push('Fora da janela de horário.');
    if (!v.contamination_ok) out.push('Risco de contaminação!');
    return out.join(' ');
  }

  color(i: number): string {
    return VEHICLE_COLORS[i % VEHICLE_COLORS.length];
  }

  short(name: string): string {
    return name.split(',')[0];
  }

  hours(min: number): string {
    const h = Math.floor(min / 60);
    const m = Math.round(min % 60);
    return h ? `${h}h ${m}min` : `${m} min`;
  }

}
