import http from 'node:http';
import { PDF_SOCKET_PATH } from './contracts';

export type PdfOperation = 'validate' | 'unlock' | 'profile';
export interface PdfResponse { statusCode: number; headers: http.IncomingHttpHeaders; body: Buffer; }

export async function callPdfUtility(operation: PdfOperation, pdf: Buffer, password?: string): Promise<PdfResponse> {
  if (!Buffer.isBuffer(pdf) || pdf.length < 8 || pdf.length > 25 * 1024 * 1024) throw new Error('PDF input must contain 8 bytes..25 MiB');
  if (password !== undefined && (typeof password !== 'string' || Buffer.byteLength(password, 'utf8') > 1024)) throw new Error('statement password is invalid');
  const path = `/v1/${operation}`;
  return new Promise<PdfResponse>((resolve, reject) => {
    const request = http.request({
      socketPath: PDF_SOCKET_PATH,
      path,
      method: 'POST',
      headers: {
        'content-type': 'application/pdf',
        'content-length': pdf.length,
        ...(password === undefined ? {} : { 'x-statement-password': Buffer.from(password, 'utf8').toString('base64') }),
        ...(operation === 'profile' ? { 'x-pdf-profile': 'statement-v1' } : {}),
      },
      timeout: 35_000,
    }, response => {
      const chunks: Buffer[] = [];
      let size = 0;
      response.on('error', reject);
      response.on('aborted', () => reject(new Error('PDF utility response was interrupted')));
      response.on('data', chunk => {
        size += chunk.length;
        if (size > 26 * 1024 * 1024) response.destroy(new Error('PDF utility response exceeded limit'));
        else chunks.push(Buffer.from(chunk));
      });
      response.on('end', () => {
        const body = Buffer.concat(chunks);
        if ((response.statusCode ?? 500) >= 400) {
          let message = `PDF utility rejected ${operation}`;
          try { message = String(JSON.parse(body.toString('utf8')).error ?? message); } catch { /* redacted generic failure */ }
          reject(new Error(message));
          return;
        }
        resolve({ statusCode: response.statusCode ?? 200, headers: response.headers, body });
      });
    });
    request.on('timeout', () => request.destroy(new Error('PDF utility timed out')));
    request.on('error', reject);
    request.end(pdf);
  });
}
