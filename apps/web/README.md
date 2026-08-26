# DynosAI website

This directory contains the public website for **https://www.dynosai.com/**.

## Architecture

- Next.js 16 App Router
- React 19
- Tailwind CSS 4
- shadcn/ui-style source components
- Static export (`next build` with `output: "export"`)
- Static documentation rendered from repository Markdown

The website intentionally has no application database or server-side business logic. All product and documentation pages are statically generated. The Local Studio is a separate loopback-only product surface shipped by the Python package, not by this Vercel site.

## Local development

From `apps/web`:

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

## Quality checks

```bash
npm run typecheck
npm run lint
npm run build
```

The production output is written to `apps/web/out/`.

## Content

Product documentation routes are registered in `lib/docs.ts` and rendered during the static build. The 0.14 site also includes dedicated `/studio/` and `/roadmap/` product pages.

## Deployment

Recommended setup:

- Repository: https://github.com/pablo-cano/dynosai
- Deployment root: `apps/web`.
- Domain: `www.dynosai.com` (with `dynosai.com` redirected to the canonical `www` domain if desired).
- Build command: `npm run build`.
- Output directory: `out`.

Because the site is static, it can be hosted by Vercel, Cloudflare Pages, Netlify, GitHub Pages, S3/CloudFront, or any static web server.
