import Markdown from "react-markdown";

/**
 * The agent's closing answer, rendered as the markdown it actually is.
 *
 * It arrived as `**bold**` and `1.` sitting in a `whitespace-pre-wrap`
 * paragraph, which is the one place in the app where the model's formatting
 * was being shown as source rather than read.
 *
 * Every element is mapped explicitly. The defaults are a browser's, not this
 * app's — `prose` classes and unstyled `<ul>` would both drag in the rounded,
 * generous look the design direction rules out. Posting ids and other
 * `code` spans go to mono because that is what mono is for here: data, not
 * decoration.
 */
export function Answer({ text }: { text: string }) {
  return (
    <div className="mt-4 space-y-3 text-sm leading-relaxed">
      <Markdown
        components={{
          p: ({ children }) => <p>{children}</p>,
          strong: ({ children }) => <strong className="font-medium text-text">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          ul: ({ children }) => <ul className="space-y-1 pl-4">{children}</ul>,
          ol: ({ children }) => (
            <ol className="list-decimal space-y-1 pl-5 marker:font-mono marker:text-2xs marker:text-text-faint">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="pl-0.5">{children}</li>,
          code: ({ children }) => (
            <code className="rounded-xs bg-surface-sunken px-1 py-0.5 font-mono text-2xs">
              {children}
            </code>
          ),
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-xs border border-hairline p-3 font-mono text-2xs">
              {children}
            </pre>
          ),
          // The agent writes headings occasionally. Weight, not size — the
          // type scale is doing the hierarchy everywhere else in the app.
          h1: ({ children }) => <p className="font-medium">{children}</p>,
          h2: ({ children }) => <p className="font-medium">{children}</p>,
          h3: ({ children }) => <p className="font-medium">{children}</p>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-signal underline underline-offset-2"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="border-hairline" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-hairline pl-3 text-text-muted">
              {children}
            </blockquote>
          ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
