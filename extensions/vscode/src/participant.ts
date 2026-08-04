import * as vscode from 'vscode';
import { Epi13Backend } from './backend';

export class Epi13Participant {
  constructor(private readonly backend: Epi13Backend) {}

  async handleRequest(request: vscode.ChatRequest, context: vscode.ChatContext, stream: vscode.ChatResponseStream): Promise<void> {
    const prompt = request.prompt.trim();
    const command = prompt.startsWith('/') ? prompt.split(/\s+/)[0] : null;

    if (command === '/doctor') {
      await this.backend.doctor();
      return;
    }
    if (command === '/models') {
      await this.backend.showModels();
      return;
    }
    if (command === '/lanes') {
      await this.backend.showLanes();
      return;
    }
    if (command === '/metrics') {
      await this.backend.showMetrics();
      return;
    }
    if (command === '/route') {
      const routeTask = prompt.replace(/^\/route\s*/, '').trim();
      if (!routeTask) {
        stream.markdown('Usage: @epi13 /route <request>');
        return;
      }
      const result = await this.backend.sendRequest('route/preview', { task: routeTask });
      stream.markdown(`\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``);
      return;
    }

    if (prompt.startsWith('@epi13')) {
      const payload = prompt.replace(/^@epi13\s*/, '').trim();
      const result = await this.backend.sendChat([{ role: 'user', content: payload }], { lane: 'auto' });
      stream.markdown(String(result));
      return;
    }

    const result = await this.backend.sendChat([{ role: 'user', content: prompt }], { lane: 'auto' });
    stream.markdown(String(result));
  }
}
