import * as vscode from 'vscode';

export interface LaneInfo {
  name: string;
  description: string;
  worker_role: string;
  enabled: boolean;
  requires_image: boolean;
  model: string;
  backend: string;
  escalation: string[];
}

export interface BackendModelInfo {
  id: string;
  name: string;
  family: string;
  version: string;
  workerRole: string;
  lane?: string;
  maxInputTokens: number;
  maxOutputTokens: number;
  supportsImage: boolean;
  supportsTools: boolean;
  detail: string;
}

export async function detectModelsFromConfig(): Promise<BackendModelInfo[]> {
  const models: BackendModelInfo[] = [
    {
      id: 'auto',
      name: 'MNCS Harness — Auto',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'auto',
      maxInputTokens: 8192,
      maxOutputTokens: 4096,
      supportsImage: true,
      supportsTools: true,
      detail: 'Hybrid deterministic preflight + semantic route + lane worker + verification',
    },
    {
      id: 'lane:chat',
      name: 'MNCS Harness — Chat',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'e2b',
      lane: 'chat',
      maxInputTokens: 8192,
      maxOutputTokens: 4096,
      supportsImage: false,
      supportsTools: true,
      detail: 'Chat lane using the configured chat worker',
    },
    {
      id: 'lane:ocr',
      name: 'MNCS Harness — OCR',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'reviewer',
      lane: 'ocr',
      maxInputTokens: 8192,
      maxOutputTokens: 4096,
      supportsImage: true,
      supportsTools: true,
      detail: 'OCR and vision extraction lane',
    },
    {
      id: 'lane:tool_worker',
      name: 'MNCS Harness — Tool Worker',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'e4b',
      lane: 'tool_worker',
      maxInputTokens: 8192,
      maxOutputTokens: 4096,
      supportsImage: false,
      supportsTools: true,
      detail: 'Tool-worker lane preserving approvals and verification',
    },
    {
      id: 'lane:coding',
      name: 'MNCS Harness — Coding',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'coder',
      lane: 'coding',
      maxInputTokens: 12288,
      maxOutputTokens: 4096,
      supportsImage: false,
      supportsTools: true,
      detail: 'Coding lane using the configured coding specialist worker',
    },
    {
      id: 'lane:vision',
      name: 'MNCS Harness — Vision',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'reviewer',
      lane: 'vision',
      maxInputTokens: 8192,
      maxOutputTokens: 4096,
      supportsImage: true,
      supportsTools: true,
      detail: 'Vision lane for diagram or screenshot interpretation',
    },
    {
      id: 'lane:review',
      name: 'MNCS Harness — Review',
      family: 'epi13-local',
      version: '0.1.0',
      workerRole: 'reviewer',
      lane: 'review',
      maxInputTokens: 16384,
      maxOutputTokens: 4096,
      supportsImage: false,
      supportsTools: true,
      detail: 'Review lane for high-risk or high-complexity tasks',
    },
  ];

  return models;
}

export function toLanguageModelChatInformation(model: BackendModelInfo): vscode.LanguageModelChatInformation {
  return {
    id: model.id,
    name: model.name,
    family: model.family,
    version: model.version,
    maxInputTokens: model.maxInputTokens,
    maxOutputTokens: model.maxOutputTokens,
    capabilities: {
      imageInput: model.supportsImage,
      toolCalling: model.supportsTools,
    },
    detail: model.detail,
  };
}
