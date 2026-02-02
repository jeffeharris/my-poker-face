import { Page } from '@playwright/test';

/**
 * Mock the Engine.IO / Socket.IO polling transport.
 *
 * Protocol overview (Engine.IO packet types used):
 *   0  OPEN      — server handshake with sid, pingInterval, etc.
 *   2  PING      — keep-alive (not used in mock)
 *   6  NOOP      — empty long-poll response
 *
 * Socket.IO layer (prefixed to Engine.IO message packets):
 *   40 CONNECT   — namespace connect acknowledgement
 *   42 EVENT     — JSON-encoded [eventName, data] payload
 */
export async function mockSocketIO(
  page: Page,
  opts: {
    connected?: boolean;
    events?: Array<[string, unknown]>;
  } = {}
) {
  const connected = opts.connected !== false;
  const events = opts.events || [];
  let pollCount = 0;

  await page.route('**/socket.io/**', route => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.includes('transport=polling') && method === 'GET') {
      if (!url.includes('sid=')) {
        // Engine.IO OPEN handshake
        route.fulfill({
          contentType: 'text/plain',
          body: '0{"sid":"fake-sid","upgrades":[],"pingInterval":25000,"pingTimeout":20000}',
        });
      } else {
        pollCount++;
        if (pollCount === 1 && connected) {
          // Socket.IO CONNECT ack
          route.fulfill({
            contentType: 'text/plain',
            body: '40{"sid":"fake-socket-sid"}',
          });
        } else if (pollCount > 1 && connected && events.length > 0) {
          const eventIdx = pollCount - 2;
          if (eventIdx < events.length) {
            // Socket.IO EVENT
            const [eventName, eventData] = events[eventIdx];
            const payload = JSON.stringify([eventName, eventData]);
            route.fulfill({
              contentType: 'text/plain',
              body: `42${payload}`,
            });
          } else {
            route.fulfill({ contentType: 'text/plain', body: '6' });
          }
        } else {
          // NOOP — either disconnected or no more events
          route.fulfill({ contentType: 'text/plain', body: '6' });
        }
      }
    } else if (method === 'POST') {
      route.fulfill({ contentType: 'text/plain', body: 'ok' });
    } else {
      route.fulfill({ body: '' });
    }
  });
}
