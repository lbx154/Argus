type Translate = (key: string) => string;

export interface RequestFailure {
  /** The sentence for the page. */
  text: string;
  /** The request line and status, kept for a fold or a tooltip. */
  technical: string;
}

export function requestFailureText(error: unknown, t: Translate): RequestFailure {
  return {
    text: t('operations.requestFailed'),
    technical: error instanceof Error ? error.message : String(error || ''),
  };
}
