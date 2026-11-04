import { DecimalPipe } from '@angular/common';
import { Component, Input, OnChanges } from '@angular/core';

/** Gráfico simples (SVG) da evolução do melhor fitness por geração. */
@Component({
  selector: 'app-fitness-chart',
  standalone: true,
  template: `
    <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" class="chart" role="img" aria-label="Evolução do fitness">
      <line [attr.x1]="pad" [attr.y1]="height - pad" [attr.x2]="width - 10" [attr.y2]="height - pad" class="axis" />
      <line [attr.x1]="pad" y1="10" [attr.x2]="pad" [attr.y2]="height - pad" class="axis" />
      <polyline [attr.points]="points" class="line" />
      <text [attr.x]="pad - 6" y="16" text-anchor="end" class="label">{{ max | number: '1.0-0' }}</text>
      <text [attr.x]="pad - 6" [attr.y]="height - pad" text-anchor="end" class="label">{{ min | number: '1.0-0' }}</text>
      <text [attr.x]="width - 10" [attr.y]="height - 8" text-anchor="end" class="label">geração {{ data.length - 1 }}</text>
      <text [attr.x]="pad" [attr.y]="height - 8" class="label">0</text>
    </svg>
  `,
  styles: [`
    .chart { width: 100%; height: auto; }
    .axis { stroke: var(--muted); stroke-width: 1; }
    .line { fill: none; stroke: var(--primary); stroke-width: 2; }
    .label { font-size: 11px; fill: var(--muted); }
  `],
  imports: [DecimalPipe],
})
export class FitnessChartComponent implements OnChanges {
  @Input({ required: true }) data: number[] = [];
  width = 600;
  height = 220;
  pad = 50;
  points = '';
  min = 0;
  max = 0;

  ngOnChanges(): void {
    if (!this.data.length) return;
    this.min = Math.min(...this.data);
    this.max = Math.max(...this.data);
    const span = this.max - this.min || 1;
    const n = Math.max(this.data.length - 1, 1);
    const w = this.width - this.pad - 10;
    const h = this.height - this.pad - 10;
    this.points = this.data
      .map((v, i) => `${(this.pad + (i / n) * w).toFixed(1)},${(10 + (1 - (v - this.min) / span) * h).toFixed(1)}`)
      .join(' ');
  }
}

