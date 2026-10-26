#!/usr/bin/env node
// Copies the in-browser OCR assets from node_modules into public/ocr/ (gitignored), so they are served from the
// app's own origin (no CDN: works offline, behind the nginx CSP and on GitHub Pages):
//
//   ocr/tesseract/worker.min.js            Tesseract.js Web Worker
//   ocr/tesseract/core/*-lstm.wasm.js      Tesseract core, LSTM-only builds (the worker picks one by CPU features)
//   ocr/tesseract/lang/spa.traineddata.gz  Spanish model, LSTM "best_int" (~2 MB)
//   ocr/pdfjs/wasm/*                       pdf.js decoders for JPEG 2000 / JBIG2 images found in scanned PDFs
//
// Runs before `vite` (dev) and `vite build`; the folder is rebuilt from scratch so stale files never ship.

import { copyFileSync, existsSync, mkdirSync, rmSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = join(dirname(fileURLToPath(import.meta.url)), '..')
const modules = join(frontend, 'node_modules')
const outDir = join(frontend, 'public', 'ocr')

const files = [
  ['tesseract.js/dist/worker.min.js', 'tesseract/worker.min.js'],
  ['@tesseract.js-data/spa/4.0.0_best_int/spa.traineddata.gz', 'tesseract/lang/spa.traineddata.gz'],
]
for (const variant of ['', '-simd', '-relaxedsimd']) {
  const name = `tesseract-core${variant}-lstm.wasm.js`
  files.push([`tesseract.js-core/${name}`, `tesseract/core/${name}`])
}
for (const name of ['jbig2.wasm', 'jbig2_nowasm_fallback.js', 'openjpeg.wasm', 'openjpeg_nowasm_fallback.js', 'qcms_bg.wasm']) {
  files.push([`pdfjs-dist/wasm/${name}`, `pdfjs/wasm/${name}`])
}

rmSync(outDir, { recursive: true, force: true })
let bytes = 0
for (const [from, to] of files) {
  const source = join(modules, from)
  if (!existsSync(source)) throw new Error(`[ocr] missing ${from}: run npm install`)
  const target = join(outDir, to)
  mkdirSync(dirname(target), { recursive: true })
  copyFileSync(source, target)
  bytes += statSync(source).size
}
console.log(`[ocr] ${files.length} assets copied to public/ocr (${Math.round(bytes / 1024)} KB)`)
