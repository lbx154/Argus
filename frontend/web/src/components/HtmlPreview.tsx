export function HtmlPreview({
  html,
  title,
  className = '',
}: {
  html: string;
  title: string;
  className?: string;
}) {
  return (
    <iframe
      title={title}
      srcDoc={html}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className={`min-h-0 w-full flex-1 border-0 bg-white ${className}`}
    />
  );
}
