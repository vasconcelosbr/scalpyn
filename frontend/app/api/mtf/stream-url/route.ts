import { NextResponse } from 'next/server';

export function GET() {
  const url = new URL(process.env.BACKEND_URL ?? 'http://localhost:8000');
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = '/api/mtf/stream';
  url.search = ''; url.hash = ''; url.username = ''; url.password = '';
  return NextResponse.json({url: url.toString()}, {headers: {'Cache-Control': 'no-store'}});
}
