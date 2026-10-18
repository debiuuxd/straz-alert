self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SHOW_ALARM_NOTIFICATION') {
    const item = event.data.incident;
    self.registration.showNotification(`🚨 ${item.title}`, {
      body: `📍 ${item.location} • SiŚ: ${item.units}`,
      icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='24' fill='%23dc2626'/><text x='50%' y='56%' dominant-baseline='central' text-anchor='middle' font-size='62'>🚒</text></svg>",
      badge: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚨</text></svg>",
      vibrate: [400, 150, 400, 150, 600],
      tag: 'straz-alert-' + item.id,
      renotify: true,
      requireInteraction: true,
      data: { id: item.id }
    });
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow('./');
    })
  );
});
