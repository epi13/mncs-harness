import * as vscode from 'vscode';
import { Epi13Backend } from './backend';
import { Epi13ModelProvider } from './provider';
import { Epi13Participant } from './participant';

let backend: Epi13Backend | undefined;
let provider: Epi13ModelProvider | undefined;
let participant: Epi13Participant | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel('MNCS Harness');
  context.subscriptions.push(output);

  const config = vscode.workspace.getConfiguration('mncsHarness');
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
    vscode.lm.registerLanguageModelChatProvider('mncs', provider),
    vscode.chat.createChatParticipant('mncs.harness', participant.handleRequest),
    vscode.commands.registerCommand('mncs-harness.startBackend', () => backend?.start()),
    vscode.commands.registerCommand('mncs-harness.stopBackend', () => backend?.stop()),
    vscode.commands.registerCommand('mncs-harness.restartBackend', () => backend?.restart()),
    vscode.commands.registerCommand('mncs-harness.runDoctor', () => backend?.doctor()),
    vscode.commands.registerCommand('mncs-harness.previewRoute', () => backend?.previewRoute()),
    vscode.commands.registerCommand('mncs-harness.showModels', () => backend?.showModels()),
    vscode.commands.registerCommand('mncs-harness.showLanes', () => backend?.showLanes()),
    vscode.commands.registerCommand('mncs-harness.showMetrics', () => backend?.showMetrics()),
    vscode.commands.registerCommand('mncs-harness.openConfig', () => backend?.openConfig()),
    vscode.commands.registerCommand('mncs-harness.openSettings', () => backend?.openSettings()),
    vscode.commands.registerCommand('mncs-harness.showLogs', () => output.show(true)),
    vscode.commands.registerCommand('mncs-harness.selectPythonInterpreter', () => backend?.selectPythonInterpreter()),
    vscode.commands.registerCommand('mncs-harness.selectRepository', () => backend?.selectRepository()),
  );

  await backend.start({ lazy: true });
}

export async function deactivate(): Promise<void> {
  await backend?.stop();
}
