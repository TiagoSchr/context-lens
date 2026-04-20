export interface CodexSessionCandidate {
  sessionId: string;
  fileId: string;
}

export function matchesCodexSessionId(
  requestedSessionId: string,
  candidate: CodexSessionCandidate,
): boolean {
  if (!requestedSessionId) { return false; }
  return requestedSessionId === candidate.sessionId || requestedSessionId === candidate.fileId;
}
