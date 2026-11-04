import { AfterViewInit, Component, ElementRef, Input, OnChanges, OnDestroy, ViewChild } from '@angular/core';
import * as L from 'leaflet';
import { OptimizeResponse } from '../models';

export const VEHICLE_COLORS = ['#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c', '#0891b2', '#db2777', '#65a30d', '#4f46e5', '#ca8a04'];

@Component({
  selector: 'app-route-map',
  standalone: true,
  template: `<div #map class="map"></div>`,
  styles: [`.map { height: 460px; width: 100%; border-radius: 8px; border: 1px solid var(--border); }`],
})
export class RouteMapComponent implements AfterViewInit, OnChanges, OnDestroy {
  @Input({ required: true }) result!: OptimizeResponse;
  @ViewChild('map', { static: true }) mapEl!: ElementRef<HTMLDivElement>;

  private map?: L.Map;
  private layer = L.layerGroup();

  ngAfterViewInit(): void {
    this.map = L.map(this.mapEl.nativeElement);
    // Tiles do OpenStreetMap; o tema escuro é aplicado por filtro CSS só na camada de tiles (styles.css).
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 18,
    }).addTo(this.map);
    this.layer.addTo(this.map);
    this.render();
  }

  ngOnChanges(): void {
    this.render();
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }

  private render(): void {
    if (!this.map || !this.result) return;
    this.layer.clearLayers();
    const r = this.result;
    const bounds: L.LatLngExpression[] = [];

    r.vehicles.forEach((v, idx) => {
      if (v.stops.length < 2) return;
      const color = VEHICLE_COLORS[idx % VEHICLE_COLORS.length];
      const path = v.polyline.length ? v.polyline : v.stops.map((i) => [r.locations[i].lat, r.locations[i].lng] as [number, number]);
      L.polyline(path, { color, weight: 4, opacity: 0.8 }).bindTooltip(`Veículo ${v.vehicle_id}`).addTo(this.layer);

      v.stops.slice(1, -1).forEach((locIdx, order) => {
        const loc = r.locations[locIdx];
        const critical = loc.priority === 'critical';
        L.marker([loc.lat, loc.lng], {
          icon: L.divIcon({
            className: '',
            html: `<div class="stop-pin" style="background:${color};${critical ? 'box-shadow:0 0 0 3px #fbbf24;' : ''}">${order + 1}</div>`,
            iconSize: [26, 26],
            iconAnchor: [13, 13],
          }),
        })
          .bindPopup(
            `<b>${loc.name}</b><br>Veículo ${v.vehicle_id} (${v.vehicle_name}) · parada ${order + 1}` +
              `<br>Medicamentos/Insumos ${loc.product_group} · ${loc.demand} kg · ${critical ? '⚠️ crítico' : 'regular'}` +
              (v.schedule[order]
                ? `<br>Chegada ${v.schedule[order].arrival} (janela ${loc.window_start}–${loc.window_end})` +
                  (v.schedule[order].late_min ? ` · <b style="color:#dc2626">atraso ${Math.round(v.schedule[order].late_min)} min</b>` : '') +
                  (v.schedule[order].refuel ? `<br>⛽ Abastece aqui antes de seguir (${r.schedule.refuel_minutes} min)` : '')
                : ''),
          )
          .addTo(this.layer);
      });
    });

    // Paradas para abastecer no caminho de trechos longos (local estimado pela rota).
    r.vehicles.forEach((v, idx) => {
      v.refuels.filter((f) => f.en_route && f.lat !== null && f.lng !== null).forEach((f) => {
        L.marker([f.lat!, f.lng!], {
          icon: L.divIcon({
            className: '',
            html: `<div class="fuel-pin" style="border-color:${VEHICLE_COLORS[idx % VEHICLE_COLORS.length]}">⛽</div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          }),
        })
          .bindPopup(`<b>⛽ ${f.place}</b><br>Veículo ${v.vehicle_id} (${v.vehicle_name}) · ~${f.time}<br>Abastecer no caminho (reserva de ${Math.round(f.range_left_pct)}%)`)
          .addTo(this.layer);
      });
    });

    const hubs = new Set([r.origin_index, r.destination_index]);
    hubs.forEach((i) => {
      const loc = r.locations[i];
      const label = i === r.origin_index && i === r.destination_index ? 'Origem/Destino' : i === r.origin_index ? 'Origem' : 'Destino';
      L.marker([loc.lat, loc.lng], {
        icon: L.divIcon({ className: '', html: `<div class="hub-pin">🏥</div>`, iconSize: [30, 30], iconAnchor: [15, 15] }),
      })
        .bindPopup(`<b>${loc.name}</b><br>${label}`)
        .addTo(this.layer);
    });

    r.locations.forEach((l) => bounds.push([l.lat, l.lng]));
    if (bounds.length) this.map.fitBounds(L.latLngBounds(bounds), { padding: [30, 30] });
  }
}
