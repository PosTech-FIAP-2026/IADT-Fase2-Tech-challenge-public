import { Component, Input, OnChanges, SimpleChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { marked } from 'marked';
import { Audience, LLMOutput } from '../models';
import { ApiService } from '../services/api.service';

interface QA {
  question: string;
  answerHtml: string;
}

/** Relatório da LLM para um público (técnico ou motoristas) e o chat de perguntas desse público. */
@Component({
  selector: 'app-llm-panel',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatIconModule, MatInputModule, MatProgressSpinnerModule],
  template: `
    <p class="hint">{{ description }}</p>
    @if (previousRuns > 0) {
      <p class="history-note"><mat-icon>history</mat-icon>
        Comparando com {{ previousRuns === 1 ? 'a execução anterior' : 'as ' + previousRuns + ' execuções anteriores' }} com estas cidades.
        Ao mudar as cidades, origem ou destino, o histórico recomeça.</p>
    }

    @if (report) {
      <p class="hint">
        @if (report.generated) { Gerado via OpenRouter · {{ report.model }} }
        @else { LLM indisponível — texto gerado por regras. {{ report.error }} }
      </p>
      <div class="markdown" [innerHTML]="reportHtml"></div>
    } @else {
      <p class="hint">Relatório não gerado: a opção "Gerar relatórios com LLM" estava desmarcada. As perguntas abaixo continuam disponíveis.</p>
    }

    <div class="chat">
      <h3>{{ chatTitle }}</h3>
      @for (qa of history; track $index) {
        <div class="qa"><p class="q">🙋 {{ qa.question }}</p><div class="markdown" [innerHTML]="qa.answerHtml"></div></div>
      }
      @if (asking) {
        <div class="qa pending" role="status" aria-live="polite">
          <p class="q">🙋 {{ pendingQuestion }}</p>
          <p class="hint"><mat-spinner class="btn-spinner" diameter="16" /> A LLM está analisando...</p>
        </div>
      }
      <form class="ask" (ngSubmit)="ask()">
        <mat-form-field>
          <mat-label>Sua pergunta</mat-label>
          <input matInput [name]="'question-' + audience" [(ngModel)]="question" [placeholder]="placeholder" [disabled]="asking" />
        </mat-form-field>
        <button mat-flat-button type="submit" [disabled]="asking || !question.trim()">
          @if (asking) { <mat-spinner class="btn-spinner" diameter="18" /> } @else { <mat-icon>send</mat-icon> }
          {{ asking ? 'Consultando...' : 'Perguntar' }}
        </button>
      </form>
    </div>
  `,
})
export class LlmPanelComponent implements OnChanges {
  @Input({ required: true }) audience!: Audience;
  @Input({ required: true }) resultId!: string;
  @Input() report: LLMOutput | null | undefined = null;
  @Input() description = '';
  @Input() chatTitle = 'Perguntas';
  @Input() placeholder = '';
  @Input() previousRuns = 0;

  private api = inject(ApiService);

  reportHtml = '';
  question = '';
  asking = false;
  pendingQuestion = '';
  history: QA[] = [];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['report']) {
      this.reportHtml = this.report ? (marked.parse(this.report.content, { async: false }) as string) : '';
    }
    if (changes['resultId']) {
      this.history = [];  // nova otimização: conversa nova
    }
  }

  ask(): void {
    const question = this.question.trim();
    if (!question) return;
    this.asking = true;
    this.pendingQuestion = question;
    this.api.ask(this.resultId, question, this.audience).subscribe({
      next: ({ answer }) => {
        this.history.push({ question, answerHtml: marked.parse(answer.content, { async: false }) as string });
        this.question = '';
        this.asking = false;
      },
      error: (err) => {
        this.history.push({ question, answerHtml: `<p class="bad">Erro: ${err?.error?.detail ?? err.message}</p>` });
        this.asking = false;
      },
    });
  }
}
