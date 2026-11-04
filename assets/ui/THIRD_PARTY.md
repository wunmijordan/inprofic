# Owned interaction components

`components/*.tsx` are adapted from the shadcn/ui `new-york` registry, retrieved
2026-10-28: https://ui.shadcn.com/r/styles/new-york/{component}.json.
They use Radix primitives and are built as React islands on Django pages.

shadcn/ui: MIT License, Copyright (c) 2023 shadcn.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Local dashboard runtime

`apps/core/static/core/js/alpine.min.js` vendors Alpine.js 3.17.1 from the
official npm distribution (`alpinejs/dist/cdn.min.js`) so dashboard behavior
does not depend on a third-party CDN at runtime.

Alpine.js: MIT License, Copyright © 2019–2025 Caleb Porzio and contributors.
The MIT permission and warranty terms above also apply to this runtime.
Upstream license: https://github.com/alpinejs/alpine/blob/main/LICENSE.md.
Bundled Vue copyright and license notices are retained in the runtime file.
