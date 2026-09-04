'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const moduleRoot = process.argv[2];
if (!moduleRoot) {
  throw new Error('usage: node nodemailer-smoke.cjs <nodemailer-module-root>');
}

const nodemailer = require(moduleRoot);
const packageJson = JSON.parse(fs.readFileSync(path.join(moduleRoot, 'package.json'), 'utf8'));
assert.equal(packageJson.name, 'nodemailer');
assert.equal(packageJson.version, '9.0.1');
assert.equal(typeof nodemailer.createTransport, 'function');

function smtpServer({ rejectRecipient = false, advertiseStartTls = false } = {}) {
  return new Promise((resolve, reject) => {
    const server = net.createServer(socket => {
      socket.setEncoding('utf8');
      socket.write('220 localhost ESMTP\r\n');
      let buffer = '';
      let inData = false;
      socket.on('data', chunk => {
        buffer += chunk;
        while (buffer.includes('\r\n')) {
          const separator = buffer.indexOf('\r\n');
          const line = buffer.slice(0, separator);
          buffer = buffer.slice(separator + 2);
          if (inData) {
            if (line === '.') {
              inData = false;
              socket.write('250 queued\r\n');
            }
            continue;
          }
          if (/^(EHLO|HELO)\b/.test(line)) {
            socket.write(advertiseStartTls ? '250-localhost\r\n250-STARTTLS\r\n250 8BITMIME\r\n' : '250-localhost\r\n250 8BITMIME\r\n');
          } else if (/^MAIL FROM:/i.test(line)) {
            socket.write('250 OK\r\n');
          } else if (/^RCPT TO:/i.test(line)) {
            socket.write(rejectRecipient ? '550 rejected\r\n' : '250 OK\r\n');
          } else if (/^DATA\b/.test(line)) {
            inData = true;
            socket.write('354 end data\r\n');
          } else if (/^QUIT\b/.test(line)) {
            socket.write('221 bye\r\n');
            socket.end();
          }
        }
      });
    });
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

async function smtpSend({ rejectRecipient = false, secure = false, requireTLS = false, advertiseStartTls = false } = {}) {
  const server = await smtpServer({ rejectRecipient, advertiseStartTls });
  try {
    const transport = nodemailer.createTransport({
      host: '127.0.0.1',
      port: server.address().port,
      secure,
      requireTLS,
      tls: { rejectUnauthorized: false },
      connectionTimeout: 3_000,
      greetingTimeout: 3_000,
      socketTimeout: 3_000,
    });
    return await transport.sendMail({
      from: 'platform@example.test',
      to: 'recipient@example.test',
      subject: 'platform smoke',
      html: '<img src="cid:platform-logo@example.test">',
      attachments: [{
        filename: 'logo.png',
        content: Buffer.from('platform-logo'),
        cid: 'platform-logo@example.test',
      }],
    });
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
}

async function expectRawDenied(raw, code) {
  const transport = nodemailer.createTransport({
    streamTransport: true,
    buffer: true,
    disableFileAccess: true,
    disableUrlAccess: true,
  });
  await assert.rejects(
    transport.sendMail({
      from: 'platform@example.test',
      to: 'recipient@example.test',
      raw,
    }),
    error => error && error.code === code,
  );
}

async function main() {
  const accepted = await smtpSend();
  assert.deepEqual(accepted.accepted, ['recipient@example.test']);
  assert.match(accepted.response, /^250 /);

  await assert.rejects(smtpSend({ rejectRecipient: true }), error => error && error.code === 'EENVELOPE');
  await assert.rejects(smtpSend({ requireTLS: true, advertiseStartTls: true }));
  await assert.rejects(smtpSend({ secure: true }));

  const rawFile = path.join(os.tmpdir(), `n8n-platform-raw-${process.pid}.eml`);
  fs.writeFileSync(rawFile, 'From: should-not-be-read@example.test\r\n\r\nbody\r\n');
  try {
    await expectRawDenied({ path: rawFile }, 'EFILEACCESS');
    await expectRawDenied({ href: 'http://127.0.0.1:1/should-not-be-fetched' }, 'EURLACCESS');
  } finally {
    fs.rmSync(rawFile, { force: true });
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
