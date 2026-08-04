import * as vscode from 'vscode';
import { detectModelsFromConfig, toLanguageModelChatInformation } from './modelRegistry';
import { Epi13Backend } from './backend';

export class Epi13ModelProvider implements vscode.LanguageModelChatProvider {
  constructor(private readonly backend: Epi13Backend) {}

  async provideLanguageModelChatInformation(
    options: vscode.PrepareLanguageModelChatModelOptions,
    token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelChatInformation[]> {
    const models = await detectModelsFromConfig();
    return models.map((model) => toLanguageModelChatInformation(model));
  }

  async provideLanguageModelChatResponse(
    model: vscode.LanguageModelChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    token: vscode.CancellationToken,
  ): Promise<void> {
    const content = messages
      .flatMap((message) => message.content)
      .filter((part): part is vscode.LanguageModelTextPart => part instanceof vscode.LanguageModelTextPart)
      .map((part) => part.value)
      .join('\n')
      .trim();

    const lane = model.id === 'auto' ? 'auto' : model.id.replace(/^lane:/, '');
    const result = await this.backend.sendChat([
      { role: 'user', content },
    ], { lane });

    const output = this.extractOutput(result);
    progress.report(new vscode.LanguageModelTextPart(output));
  }

  async provideTokenCount(
    model: vscode.LanguageModelChatInformation,
    text: string | vscode.LanguageModelChatRequestMessage,
    token: vscode.CancellationToken,
  ): Promise<number> {
    const source = typeof text === 'string' ? text : text.content
      .filter((part): part is vscode.LanguageModelTextPart => part instanceof vscode.LanguageModelTextPart)
      .map((part) => part.value)
      .join(' ');
    const words = source.trim().split(/\s+/).length;
    return Math.max(1, words * 2);
  }

  private extractOutput(result: unknown): string {
    if (typeof result === 'string') {
      return result;
    }

    if (result && typeof result === 'object') {
      const record = result as Record<string, unknown>;
      const nested = record.params as Record<string, unknown> | undefined;
      const payload = nested && typeof nested === 'object' ? nested.result : undefined;
      if (typeof payload === 'string') {
        return payload;
      }
      if (payload && typeof payload === 'object') {
        const payloadRecord = payload as Record<string, unknown>;
        if (typeof payloadRecord.final_content === 'string') {
          return payloadRecord.final_content;
        }
        if (typeof payloadRecord.result === 'string') {
          return payloadRecord.result;
        }
        if (typeof payloadRecord.content === 'string') {
          return payloadRecord.content;
        }
        return JSON.stringify(payloadRecord, null, 2);
      }
      if (typeof record.result === 'string') {
        return record.result;
      }
      if (typeof record.final_content === 'string') {
        return record.final_content;
      }
      return JSON.stringify(record, null, 2);
    }

    return String(result ?? '');
  }
}
