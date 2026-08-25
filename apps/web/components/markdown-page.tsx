// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Pablo Cano

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { rewriteMarkdownHref } from "@/lib/docs";

function headingId(children: React.ReactNode) {
  const text = Array.isArray(children) ? children.join(" ") : String(children ?? "");
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

export function MarkdownPage({ content }: { content: string }) {
  return (
    <div className="prose-dynosai max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a href={rewriteMarkdownHref(href)} {...props}>{children}</a>
          ),
          h1: ({ children }) => <h1 id={headingId(children)}>{children}</h1>,
          h2: ({ children }) => <h2 id={headingId(children)}>{children}</h2>,
          h3: ({ children }) => <h3 id={headingId(children)}>{children}</h3>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
