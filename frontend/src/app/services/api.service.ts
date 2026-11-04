import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  Audience,
  Catalog,
  CitiesResponse,
  ConstraintsConfig,
  GAConfig,
  GeocodeResponse,
  LLMOutput,
  OptimizeRequest,
  OptimizeResponse,
  ScheduleConfig,
  StateInfo,
  VehicleIn,
} from '../models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  catalog(): Observable<Catalog> {
    return this.http.get<Catalog>(`${this.base}/catalog`);
  }

  states(): Observable<StateInfo[]> {
    return this.http.get<StateInfo[]>(`${this.base}/geo/states`);
  }

  stateCities(uf: string): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/geo/states/${uf}/cities`);
  }

  geocode(city: string, state: string): Observable<GeocodeResponse> {
    return this.http.post<GeocodeResponse>(`${this.base}/cities/geocode`, { city, state });
  }

  defaultCities(): Observable<CitiesResponse> {
    return this.http.get<CitiesResponse>(`${this.base}/cities/default`);
  }

  randomCities(count: number): Observable<CitiesResponse> {
    return this.http.get<CitiesResponse>(`${this.base}/cities/random`, { params: { count } });
  }

  optimizeDefault(
    vehicles: VehicleIn[],
    schedule: ScheduleConfig,
    ga: GAConfig,
    constraints: ConstraintsConfig,
    useLlm: boolean,
    previousResultIds: string[] = [],
  ): Observable<OptimizeResponse> {
    return this.http.post<OptimizeResponse>(`${this.base}/routes/optimize/default`, {
      vehicles,
      schedule,
      ga,
      constraints,
      use_llm: useLlm,
      previous_result_ids: previousResultIds,
    });
  }

  optimize(req: OptimizeRequest): Observable<OptimizeResponse> {
    return this.http.post<OptimizeResponse>(`${this.base}/routes/optimize`, req);
  }

  ask(resultId: string, question: string, audience: Audience): Observable<{ answer: LLMOutput }> {
    return this.http.post<{ answer: LLMOutput }>(`${this.base}/routes/ask`, { result_id: resultId, question, audience });
  }
}
