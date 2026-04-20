import { ToolName } from './toolStats';

export type ToolAvailability = Record<ToolName, boolean>;

const TOOL_ORDER: ToolName[] = ['copilot', 'claude', 'codex'];

export function emptyToolAvailability(): ToolAvailability {
  return {
    copilot: false,
    claude: false,
    codex: false,
  };
}

export function listAvailableTools(availability: ToolAvailability): ToolName[] {
  return TOOL_ORDER.filter((tool) => availability[tool]);
}

export function firstAvailableTool(availability: ToolAvailability): ToolName {
  return listAvailableTools(availability)[0] ?? 'copilot';
}

export function isToolAvailable(availability: ToolAvailability, tool: ToolName): boolean {
  return availability[tool] === true;
}
