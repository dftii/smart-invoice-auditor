import { describe, expect, it } from 'vitest'
import {
  isImageFile,
  isOcrCandidate,
  isPdfFile,
  MIN_PDF_TEXT_CHARS,
  pageProgress,
  stageLabel,
  visibleCharCount,
} from './ocr'

function file(name: string, type: string): File {
  return new File(['x'], name, { type })
}

describe('file kinds', () => {
  it('recognises images by type or, without a type, by extension', () => {
    expect(isImageFile(file('foto.jpg', 'image/jpeg'))).toBe(true)
    expect(isImageFile(file('captura', 'image/png'))).toBe(true)
    expect(isImageFile(file('scan.WEBP', ''))).toBe(true)
    expect(isImageFile(file('factura.pdf', 'application/pdf'))).toBe(false)
    expect(isImageFile(file('icono.gif', 'image/gif'))).toBe(false)
  })

  it('recognises PDFs by type or extension', () => {
    expect(isPdfFile(file('factura.pdf', 'application/pdf'))).toBe(true)
    expect(isPdfFile(file('FACTURA.PDF', ''))).toBe(true)
    expect(isPdfFile(file('foto.png', 'image/png'))).toBe(false)
  })

  it('only images and PDFs are OCR candidates', () => {
    expect(isOcrCandidate(file('foto.png', 'image/png'))).toBe(true)
    expect(isOcrCandidate(file('scan.pdf', 'application/pdf'))).toBe(true)
    expect(isOcrCandidate(file('notas.txt', 'text/plain'))).toBe(false)
  })
})

describe('progress', () => {
  it('reserves the first 15 % for loading the engine and splits the rest between pages', () => {
    expect(pageProgress(0, 1, 0)).toBeCloseTo(0.15)
    expect(pageProgress(0, 1, 0.5)).toBeCloseTo(0.575)
    expect(pageProgress(0, 1, 1)).toBeCloseTo(1)
    expect(pageProgress(1, 2, 0)).toBeCloseTo(0.575)
    expect(pageProgress(1, 2, 1)).toBeCloseTo(1)
  })

  it('clamps out-of-range page progress', () => {
    expect(pageProgress(0, 2, -1)).toBeCloseTo(0.15)
    expect(pageProgress(1, 2, 7)).toBeCloseTo(1)
    expect(pageProgress(0, 0, 0.5)).toBeCloseTo(0.575)
  })

  it('translates the Tesseract statuses', () => {
    expect(stageLabel('loading language traineddata')).toBe('Descargando el modelo de español')
    expect(stageLabel('loading tesseract core')).toBe('Cargando el motor OCR')
    expect(stageLabel('something new')).toBe('Preparando el motor OCR')
  })
})

describe('scanned PDF detection', () => {
  it('ignores whitespace when counting the text of a PDF', () => {
    expect(visibleCharCount('  F A C\n\tT  ')).toBe(4)
    expect(visibleCharCount(' '.repeat(500))).toBe(0)
  })

  it('treats a page with a few stray characters as a scan', () => {
    expect(visibleCharCount('1 / 1') < MIN_PDF_TEXT_CHARS).toBe(true)
    expect(visibleCharCount('FACTURA Nº 2024-0001 Fecha 15/03/2024 Emisor B46271896') >= MIN_PDF_TEXT_CHARS).toBe(true)
  })
})
