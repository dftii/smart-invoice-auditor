#!/usr/bin/env node
// Builds the assets of the in-browser demo (Pyodide) into frontend/public/demo/ (gitignored):
//
//   backend.zip       app/ (backend/app without __pycache__) + samples/ + runtime.py (ASGI bridge)
//   wheels/*.whl      pure-Python wheels (fastapi, starlette, ...) pinned from backend/requirements.txt
//   manifest.json     versions + sha256 of everything above (the worker reads it to know what to fetch)
//
// Needs Node >= 22.2 and Python with pip (only to download wheels; the zip is written by this script).
// Pyodide itself (wasm + its own packages: sqlalchemy, pydantic, lxml, ...) is loaded from jsDelivr at runtime.

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { crc32, deflateRawSync } from 'node:zlib'

const PYODIDE_VERSION = '0.29.5'
const PYODIDE_PYTHON = '3.13' // the CPython version Pyodide 0.29 ships
// Pure-Python distributions (fastapi 0.162 imports opentelemetry-api unconditionally) that Pyodide does not provide (or provides too old: its fastapi 0.116 cannot bind a
// Query() model next to other query params). Everything else the backend needs is a Pyodide built-in package.
const WHEELS = ['fastapi', 'starlette', 'pydantic-settings', 'python-dotenv', 'python-multipart', 'pypdf', 'annotated-doc', 'opentelemetry-api']

const here = dirname(fileURLToPath(import.meta.url))
const frontend = resolve(here, '..')
const backend = resolve(frontend, '..', 'backend')
const outDir = join(frontend, 'public', 'demo')
const wheelsDir = join(outDir, 'wheels')

const sha256 = (data) => createHash('sha256').update(data).digest('hex')
const normalize = (name) => name.toLowerCase().replace(/[-_.]+/g, '-')

function readPins() {
  const pins = new Map()
  for (const line of readFileSync(join(backend, 'requirements.txt'), 'utf8').split(/\r?\n/)) {
    const match = /^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)/.exec(line)
    if (match) pins.set(normalize(match[1]), match[2])
  }
  return pins
}

// ---------------------------------------------------------------------------------------------- backend.zip

function listFiles(dir) {
  const files = []
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== '__pycache__') files.push(...listFiles(full))
    } else if (!/\.py[co]$/.test(entry.name)) {
      files.push(full)
    }
  }
  return files
}

/** Minimal deterministic ZIP writer (deflate, fixed timestamp) so no dependency is needed. */
function buildZip(entries) {
  const DOS_TIME = 0
  const DOS_DATE = (2024 - 1980) << 9 | (1 << 5) | 1
  const locals = []
  const centrals = []
  let offset = 0
  for (const { name, data } of entries) {
    const nameBuf = Buffer.from(name, 'utf8')
    const packed = deflateRawSync(data, { level: 9 })
    const crc = crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt16LE(0x0800, 6) // UTF-8 names
    local.writeUInt16LE(8, 8)
    local.writeUInt16LE(DOS_TIME, 10)
    local.writeUInt16LE(DOS_DATE, 12)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(packed.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(nameBuf.length, 26)
    locals.push(local, nameBuf, packed)

    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt16LE(0x0800, 8)
    central.writeUInt16LE(8, 10)
    central.writeUInt16LE(DOS_TIME, 12)
    central.writeUInt16LE(DOS_DATE, 14)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(packed.length, 20)
    central.writeUInt32LE(data.length, 24)
    central.writeUInt16LE(nameBuf.length, 28)
    central.writeUInt32LE(offset, 42)
    centrals.push(central, nameBuf)
    offset += local.length + nameBuf.length + packed.length
  }
  const centralBuf = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(centralBuf.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, centralBuf, end])
}

function buildBackendZip() {
  const entries = []
  const add = (file, name) => entries.push({ name, data: readFileSync(file) })
  const appDir = join(backend, 'app')
  if (!existsSync(appDir)) throw new Error(`backend/app not found at ${appDir}`)
  for (const file of listFiles(appDir)) add(file, relative(backend, file).split(sep).join('/'))
  // Sample invoices for "Probar con un ejemplo" (GET /api/v1/samples; runtime.py points SAMPLES_DIR here).
  const samplesDir = join(backend, 'samples')
  for (const file of listFiles(samplesDir)) add(file, `samples/${relative(samplesDir, file).split(sep).join('/')}`)
  add(join(frontend, 'src', 'demo', 'runtime.py'), 'runtime.py')
  const zip = buildZip(entries)
  writeFileSync(join(outDir, 'backend.zip'), zip)
  return { file: 'backend.zip', size: zip.length, sha256: sha256(zip), files: entries.length }
}

// ---------------------------------------------------------------------------------------------- wheels

function findPython() {
  const candidates = process.env.PYTHON ? [[process.env.PYTHON]] : [['python3'], ['python'], ['py', '-3']]
  for (const [cmd, ...pre] of candidates) {
    const probe = spawnSync(cmd, [...pre, '-m', 'pip', '--version'], { encoding: 'utf8' })
    if (probe.status === 0) return [cmd, ...pre]
  }
  throw new Error('Python with pip not found (set PYTHON=/path/to/python)')
}

function downloadWheels(pins) {
  const requirements = WHEELS.map((name) => {
    const version = pins.get(normalize(name))
    if (!version) throw new Error(`${name} is not pinned in backend/requirements.txt`)
    return `${name}==${version}`
  })
  rmSync(wheelsDir, { recursive: true, force: true })
  mkdirSync(wheelsDir, { recursive: true })
  const [cmd, ...pre] = findPython()
  const args = [
    ...pre, '-m', 'pip', 'download',
    '--only-binary=:all:', '--no-deps', '--python-version', PYODIDE_PYTHON, '--platform', 'any',
    '--disable-pip-version-check', '--quiet', '-d', wheelsDir, ...requirements,
  ]
  console.log(`[demo] pip download ${requirements.join(' ')}`)
  const result = spawnSync(cmd, args, { stdio: 'inherit' })
  if (result.status !== 0) throw new Error(`pip download failed (exit ${result.status})`)
  return readdirSync(wheelsDir)
    .filter((file) => file.endsWith('.whl'))
    .sort()
    .map((file) => {
      const [name, version] = file.split('-')
      const data = readFileSync(join(wheelsDir, file))
      return { name: normalize(name), version, file: `wheels/${file}`, size: data.length, sha256: sha256(data) }
    })
}

// ---------------------------------------------------------------------------------------------- main

mkdirSync(outDir, { recursive: true })
const pins = readPins()
const backendZip = buildBackendZip()
const wheels = downloadWheels(pins)
const missing = WHEELS.filter((name) => !wheels.some((wheel) => wheel.name === normalize(name)))
if (missing.length > 0) throw new Error(`wheels missing after download: ${missing.join(', ')}`)

const manifest = {
  pyodide: PYODIDE_VERSION,
  python: PYODIDE_PYTHON,
  backend: backendZip,
  wheels,
}
writeFileSync(join(outDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`)
const totalKb = Math.round(
  (backendZip.size + wheels.reduce((sum, wheel) => sum + wheel.size, 0) + statSync(join(outDir, 'manifest.json')).size) / 1024,
)
console.log(`[demo] backend.zip ${backendZip.files} files, ${wheels.length} wheels, ${totalKb} KB total -> ${relative(process.cwd(), outDir) || '.'}`)
