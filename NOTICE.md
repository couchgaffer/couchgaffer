# Third-party software

This project uses the open-source software below, under its own licence. Add every new dependency, font, tool or asset here when it is added (rule 4.3, third-party integrations).

## Shipped in the app image

| Package | Licence |
|---|---|
| FastAPI | MIT |
| Starlette | BSD-3-Clause |
| Uvicorn (with uvloop, httptools, watchfiles, websockets, python-dotenv, PyYAML) | BSD-3-Clause; uvloop, httptools, watchfiles, PyYAML: MIT; websockets, python-dotenv: BSD-3-Clause |
| Pydantic, pydantic-core | MIT |
| AnyIO, h11, annotated-types, annotated-doc, typing-inspection | MIT |
| click, idna | BSD-3-Clause |
| typing-extensions | PSF-2.0 |
| opentelemetry-api | Apache-2.0 |
| OpenAI Python library (openai), jiter | Apache-2.0; jiter: MIT |
| HTTPX 2 (httpx2, httpcore2), sniffio | BSD-3-Clause; sniffio: MIT or Apache-2.0 |
| Azure Identity, Azure Core, MSAL, msal-extensions | MIT |
| PyJWT, cffi, charset-normalizer, truststore, urllib3 | MIT; cffi: MIT-0 |
| cryptography | Apache-2.0 or BSD-3-Clause |
| Requests | Apache-2.0 |
| pycparser | BSD-3-Clause |
| certifi | MPL-2.0, used unmodified |
| Microsoft Agent Framework (agent-framework-core, agent-framework-openai) | MIT |
| MCP Python SDK (mcp), httpx-sse, pydantic-settings | MIT |
| HTTPX, httpcore | BSD-3-Clause |
| sse-starlette | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| jsonschema, jsonschema-specifications, referencing, rpds-py, attrs | MIT |
| msgspec | BSD-3-Clause |
| regex | Apache-2.0 and CNRI-Python |
| React, React DOM | MIT |
| uqr (QR codes, drawn in the browser) | MIT |
| Big Shoulders Display and Atkinson Hyperlegible Next (fonts, self-hosted in `frontend/src/fonts/`) | OFL-1.1; licence text and copyright lines in `frontend/src/fonts/OFL.txt` |
| Azure Storage Blob and Queue client libraries (azure-storage-blob, azure-storage-queue) | MIT |
| isodate | BSD-3-Clause |
| Python (base image `python:3.12-slim`) | PSF-2.0, on Debian |

## Shipped in the recap job image only (#15)

| Package | Licence |
|---|---|
| Pillow | MIT-CMU (HPND) |
| FFmpeg, from Debian, run as a separate program | GPL-2.0-or-later as built by Debian (includes libx264, GPL-2.0-or-later) |
| DejaVu fonts (`fonts-dejavu-core`) | Bitstream Vera Fonts licence, with DejaVu changes in the public domain |

The recap videos contain no music or sound, and no third-party logos or marks (rule 4.4).

## Development and build only

| Package | Licence |
|---|---|
| Vite, @vitejs/plugin-react, Vitest | MIT |
| jsQR (decodes the QR code in a test) | Apache-2.0 |
| TypeScript | Apache-2.0 |
| @types/react, @types/react-dom | MIT |
| json-schema-to-typescript | MIT |
| datamodel-code-generator | MIT |
| pytest, Ruff, mypy | MIT |
| uv | MIT or Apache-2.0 |
| Hatchling (Python build backend) | MIT |
| Node.js (build image `node:24-slim`) | MIT |
| Gitleaks (CI) | MIT |
