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
