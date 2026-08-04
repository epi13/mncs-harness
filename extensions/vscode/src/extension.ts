import * as vscode from 'vscode';
import { Epi13Backend } from './backend';
import { Epi13ModelProvider } from './provider';
import { Epi13Participant } from './participant';

let backend: Epi13Backend | undefined;
let provider: Epi13ModelProvider | undefined;
let participant: Epi13Participant | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel('Epi13 Local Harness');
  context.subscriptions.push(output);

  const config = vscode.workspace.getConfiguration('epi13LocalHarness');
  const repositoryPath = config.get<string>('repositoryPath', '');
  const pythonPath = config.get<string>('pythonPath', '');
  const configPath = config.get<string>('configPath', '');

  backend = new Epi13Backend({
    context,
    output,
    repositoryPath,
    pythonPath,
    configPath,
  });
  context.subscriptions.push(backend);

  provider = new Epi13ModelProvider(backend);
  participant = new Epi13Participant(backend);

  context.subscriptions.push(
    vscode.lm.registerLanguageModelChatProvider('epi13-local', provider),
    vscode.chat.createChatParticipant('epi13.localHarness', participant.handleRequest),
    vscode.commands.registerCommand('epi13-local-harness.startBackend', () => backend?.start()),
    vscode.commands.registerCommand('epi13-local-harness.stopBackend', () => backend?.stop()),
    vscode.commands.registerCommand('epi13-local-harness.restartBackend', () => backend?.restart()),
    vscode.commands.registerCommand('epi13-local-harness.runDoctor', () => backend?.doctor()),
    vscode.commands.registerCommand('epi13-local-harness.previewRoute', () => backend?.previewRoute()),
    vscode.commands.registerCommand('epi13-local-harness.showModels', () => backend?.showModels()),
    vscode.commands.registerCommand('epi13-local-harness.showLanes', () => backend?.showLanes()),
    vscode.commands.registerCommand('epi13-local-harness.showMetrics', () => backend?.showMetrics()),
    vscode.commands.registerCommand('epi13-local-harness.openConfig', () => backend?.openConfig()),
    vscode.commands.registerCommand('epi13-local-harness.openSettings', () => backend?.openSettings()),
    vscode.commands.registerCommand('epi13-local-harness.showLogs', () => output.show(true)),
    vscode.commands.registerCommand('epi13-local-harness.selectPythonInterpreter', () => backend?.selectPythonInterpreter()),
    vscode.commands.registerCommand('epi13-local-harness.selectRepository', () => backend?.selectRepository()),
  );

  await backend.start({ lazy: true });
}

export async function deactivate(): Promise<void> {
  await backend?.stop();
}
