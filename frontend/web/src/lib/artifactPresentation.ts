type ArtifactPresentation = {
  kind: string;
  mime?: string;
  name?: string;
  path?: string;
};

export function isMarkdownArtifact(artifact: ArtifactPresentation): boolean {
  if (artifact.kind === 'markdown') return true;
  if (artifact.mime?.split(';', 1)[0].trim().toLowerCase() === 'text/markdown') return true;
  return /\.(?:md|markdown)$/i.test(artifact.name || artifact.path || '');
}

export function canEditArtifact(artifact: { kind: string; source?: string; size: number }): boolean {
  return ['markdown', 'text', 'json', 'table', 'html'].includes(artifact.kind) && artifact.size <= 512 * 1024
    && artifact.source !== 'attachment' && artifact.source !== 'reader_foundation' && artifact.source !== 'progress_snapshot';
}

export function readerFoundationTitle(foundation: { question: string; title?: string }): string {
  return foundation.title?.trim() || foundation.question;
}
