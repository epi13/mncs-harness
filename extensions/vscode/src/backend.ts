import * as vscode from 'vscode';
import { spawn, ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { homedir } from 'node:os';
import * as path from 'node:path';
import { encodeMessage, decodeMessage } from './protocol';

export interface Epi13BackendOptions {
  context: vscode.ExtensionContext;
  output: vscode.OutputChannel;
  repositoryPath: string;
  pythonPath: string;
  configPath: string;
}

export class Epi13Backend implements vscode.Disposable {
  private proc?: ChildProcessWithoutNullStreams;
  private rl?: ReturnType<typeof createInterface>;
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason: unknown) => void }>();
  private active = false;
  private readonly output: vscode.OutputChannel;
  private repositoryPath: string;
  private pythonPath: string;
  private readonly configPath: string;

  constructor(private readonly options: Epi13BackendOptions) {
    this.output = options.output;
    this.repositoryPath = options.repositoryPath || path.join(homedir(), 'Documents', 'Projects', 'mncs-harness');
    this.pythonPath = options.pythonPath || path.join(this.repositoryPath, '.venv', 'bin', 'python');
    this.configPath = options.configPath || path.join(homedir(), '.config', 'mncs-harness', 'config.toml');
  }

  private getResolvedPythonPath(): string {
    return this.pythonPath;
  }

  private getResolvedRepositoryPath(): string {
    return this.repositoryPath;
  }

  async start(options?: { lazy?: boolean }): Promise<void> {
    if (this.active && this.proc && !this.proc.killed) {
      return;
    }
    const pythonPath = this.getResolvedPythonPath();
    const repositoryPath = this.getResolvedRepositoryPath();
    this.output.appendLine(`Starting backend using ${pythonPath}`);
    this.proc = spawn(pythonPath, ['-m', 'epi13_local_harness.bridge', '--stdio'], {
      cwd: repositoryPath,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
      shell: false,
    });
    this.rl = createInterface({ input: this.proc.stdout, crlfDelay: Infinity });
    this.proc.stderr.on('data', (chunk) => {
      this.output.appendLine(`backend stderr: ${String(chunk).trim()}`);
    });
    this.proc.on('error', (error) => {
      this.output.appendLine(`backend error: ${String(error)}`);
    });

    this.rl.on('line', (line) => {
      try {
        const message = decodeMessage(line);
        const requestId = message.requestId;
        if (requestId && this.pending.has(requestId)) {
          const pending = this.pending.get(requestId);
          this.pending.delete(requestId);
          pending?.resolve(message);
        }
      } catch (error) {
        this.output.appendLine(`Protocol parse error: ${String(error)}`);
      }
    });

    await this.sendRequest('initialize', { repositoryPath, configPath: this.configPath, protocolVersion: 1 });
    this.active = true;
    if (options?.lazy) {
      this.output.appendLine('Backend started lazily');
    }
  }

  async stop(): Promise<void> {
    if (!this.proc) {
      return;
    }
    await this.sendRequest('shutdown', {});
    this.proc.kill('SIGTERM');
    this.active = false;
    this.pending.clear();
  }

  async restart(): Promise<void> {
    await this.stop();
    await this.start();
  }

  async doctor(): Promise<void> {
    const result = await this.sendRequest('health/check', {});
    await vscode.window.showInformationMessage(JSON.stringify(result, null, 2));
  }

  async previewRoute(): Promise<void> {
    const task = await vscode.window.showInputBox({ prompt: 'Task to preview', placeHolder: 'Explain this repository.' });
    if (!task) {
      return;
    }
    const result = await this.sendRequest('route/preview', { task });
    await vscode.window.showInformationMessage(JSON.stringify(result, null, 2));
  }

  async showModels(): Promise<void> {
    const result = await this.sendRequest('models/list', {});
    await vscode.window.showInformationMessage(JSON.stringify(result, null, 2));
  }

  async showLanes(): Promise<void> {
    const result = await this.sendRequest('lanes/list', {});
    await vscode.window.showInformationMessage(JSON.stringify(result, null, 2));
  }

  async showMetrics(): Promise<void> {
    const result = await this.sendRequest('metrics/recent', {});
    await vscode.window.showInformationMessage(JSON.stringify(result, null, 2));
  }

  async openConfig(): Promise<void> {
    const configUri = vscode.Uri.file(this.configPath);
    await vscode.commands.executeCommand('vscode.open', configUri);
  }

  async openSettings(): Promise<void> {
    await vscode.commands.executeCommand('workbench.action.openSettings', 'epi13LocalHarness');
  }

  async selectPythonInterpreter(): Promise<void> {
    const pathResult = await vscode.window.showInputBox({ prompt: 'Absolute Python path', placeHolder: this.pythonPath });
    if (!pathResult) {
      return;
    }
    await vscode.workspace.getConfiguration('epi13LocalHarness').update('pythonPath', pathResult, true);
    this.pythonPath = pathResult;
  }

  async selectRepository(): Promise<void> {
    const repo = await vscode.window.showInputBox({ prompt: 'Repository path', placeHolder: this.repositoryPath });
    if (!repo) {
      return;
    }
    await vscode.workspace.getConfiguration('epi13LocalHarness').update('repositoryPath', repo, true);
    this.repositoryPath = repo;
  }

  async sendRequest(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.proc || !this.rl || this.proc.exitCode !== null) {
      await this.start({ lazy: false });
    }
    const requestId = randomUUID();
    const envelope = encodeMessage({
      protocolVersion: 1,
      requestId,
      timestamp: new Date().toISOString(),
      method,
      params,
    });
    const response = new Promise<unknown>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    this.proc!.stdin.write(envelope);
    return response;
  }

  async sendChat(messages: Array<{ role: string; content: string }>, options: { lane?: string }): Promise<unknown> {
    return this.sendRequest('chat/start', { messages, lane: options.lane ?? 'auto' });
  }

  dispose(): void {
    void this.stop();
  }
}
