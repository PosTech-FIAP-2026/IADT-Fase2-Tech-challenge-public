export type Priority = 'critical' | 'regular';
export type ProductGroup = 'A' | 'B' | 'C' | 'D' | 'E';
export type VehicleType = 'moto' | 'carro' | 'carro_eletrico' | 'caminhao_refrigerado' | 'caminhao';

export interface Location {
  name: string;
  lat: number;
  lng: number;
  priority: Priority;
  demand: number;
  product_group: ProductGroup;
  window_start: string; // HH:MM
  window_end: string; // HH:MM
  address?: string | null;
}

export interface VehicleIn {
  type: VehicleType;
}

export interface ScheduleConfig {
  departure_time: string; // HH:MM
  service_minutes: number;
  refuel_enabled: boolean; // abastecer em rota (todos saem de tanque cheio; carro elétrico não abastece)
  refuel_reserve_pct: number; // reserva (% da autonomia) que dispara o abastecimento
  refuel_minutes: number; // duração de cada abastecimento
}

export interface GAConfig {
  population_size: number;
  generations: number;
  mutation_probability: number;
  tournament_size: number;
  elite_size: number;
  priority_weight: number;
  seed: number | null; // null = semente aleatória
  stagnation_generations: number; // 0 = sem parada por estagnação
}

export type ConstraintMode = 'soft' | 'hard';

export interface ConstraintsConfig {
  capacity: ConstraintMode;
  autonomy: ConstraintMode;
  priority: ConstraintMode;
  time_window: ConstraintMode;
  soft_penalty: number;
}

export interface OptimizeRequest {
  locations: Location[];
  origin_index: number;
  destination_index: number;
  vehicles: VehicleIn[];
  schedule: ScheduleConfig;
  ga: GAConfig;
  constraints: ConstraintsConfig;
  use_llm: boolean;
  previous_result_ids?: string[]; // execuções anteriores (contexto da LLM técnica se as cidades forem as mesmas)
}

export interface StopSchedule {
  location: number;
  arrival: string;
  departure: string;
  wait_min: number;
  late_min: number;
  refuel: boolean; // abastece nesta cidade antes de seguir
}

export interface RefuelInfo {
  location: number | null; // cidade da rota onde abastece (null = no caminho)
  en_route: boolean; // parada no caminho de um trecho longo
  place: string; // cidade da rota ou município estimado no caminho
  leg_from: number | null;
  leg_to: number | null;
  km_into_leg: number;
  lat: number | null;
  lng: number | null;
  time: string;
  km_since_refuel: number;
  range_left_pct: number;
}

export interface VehicleRoute {
  vehicle_id: number;
  vehicle_type: VehicleType | 'generico';
  vehicle_name: string;
  refrigerated: boolean;
  strict_autonomy: boolean;
  stops: number[];
  distance_km: number;
  duration_min: number;
  load: number;
  capacity: number;
  autonomy_km: number;
  groups: ProductGroup[];
  schedule: StopSchedule[];
  refuels: RefuelInfo[];
  return_time: string | null;
  capacity_ok: boolean;
  autonomy_ok: boolean;
  priority_ok: boolean;
  contamination_ok: boolean;
  time_ok: boolean;
  polyline: [number, number][];
}

export interface StrategyResult {
  distance_km: number;
  fitness: number;
  feasible: boolean;
}

export interface Comparison {
  genetic_algorithm: StrategyResult;
  nearest_neighbor: StrategyResult;
  random_average: StrategyResult;
  improvement_vs_nearest_neighbor_pct: number;
  improvement_vs_random_pct: number;
}

export type Audience = 'technical' | 'driver';

export interface LLMOutput {
  generated: boolean;
  provider: string;
  model?: string | null;
  content: string;
  error?: string | null;
}

export interface OptimizeResponse {
  id: string;
  distance_source: string;
  locations: Location[];
  origin_index: number;
  destination_index: number;
  vehicles: VehicleRoute[];
  total_distance_km: number;
  total_duration_min: number;
  fitness: number;
  feasible: boolean;
  ga: GAConfig;
  constraints: ConstraintsConfig;
  schedule: ScheduleConfig;
  transport_rules: string[];
  soft_violations: string[];
  fitness_history: number[];
  generations: number; // gerações efetivamente executadas
  stopped_by_stagnation: boolean;
  execution_time_ms: number;
  comparison: Comparison;
  llm_technical?: LLMOutput | null;
  llm_driver?: LLMOutput | null;
  previous_result_ids: string[]; // execuções anteriores com as mesmas cidades usadas como contexto
}

export interface CitiesResponse {
  distance_source: string;
  cities: Location[];
}

export interface ProductGroupInfo {
  code: ProductGroup;
  name: string;
  refrigerated: boolean;
  incompatible_with: ProductGroup[];
  rule: string;
}

export interface VehicleTypeInfo {
  type: VehicleType;
  name: string;
  autonomy_km: number;
  capacity: number;
  refrigerated: boolean;
  strict_autonomy: boolean;
}

export interface Catalog {
  product_groups: ProductGroupInfo[];
  vehicle_types: VehicleTypeInfo[];
  transport_rules: string[];
  default_fleet: VehicleType[];
}

export interface StateInfo {
  uf: string;
  name: string;
}

export interface GeocodeResponse {
  name: string; // "Cidade, UF"
  lat: number;
  lng: number;
  address?: string | null;
  source: 'google_maps' | 'catalogo_local';
}

export const VEHICLE_ICONS: Record<string, string> = {
  moto: '🏍️',
  carro: '🚗',
  carro_eletrico: '⚡🚗',
  caminhao_refrigerado: '❄️🚚',
  caminhao: '🚚',
  generico: '🚐',
};
