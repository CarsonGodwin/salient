---
id: large-uploads
anchors:
  - src/upload/**/*.ts
  - path: src/upload/chunker.ts
    hash: sha256:0000000000000000000000000000000000000000000000000000000000000000
context:
  - src/net/retry.ts
---
# Large field uploads

## Usage
- [verified: telemetry 2026-08] p50 upload size is 800MB, p95 is 6GB
- [observed] uploads happen from job sites over LTE with frequent dropouts

## Failure consequences
- [claimed] a failed upload costs the customer a return visit to the site

## Deliberately not supported
- [verified: product decision 2026-06] uploads from browsers, mobile app only
