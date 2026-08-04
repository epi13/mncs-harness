export interface ProtocolMessage {
  protocolVersion: number;
  requestId?: string;
  timestamp: string;
  method: string;
  params?: Record<string, unknown>;
}

export function encodeMessage(message: ProtocolMessage): string {
  return `${JSON.stringify(message)}\n`;
}

export function decodeMessage(raw: string): ProtocolMessage {
  const parsed = JSON.parse(raw) as ProtocolMessage;
  if (parsed.protocolVersion !== 1) {
    throw new Error(`Unsupported protocolVersion: ${parsed.protocolVersion}`);
  }
  return parsed;
}
