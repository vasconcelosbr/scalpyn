import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function proxy(request: NextRequest) {
  // Token lives in localStorage (client-side only).
  // Server-side route protection is handled by AuthGuard component.
  // Cookie-based fallback can be added when server-side auth is needed.
  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
