/// <reference lib="webworker" />
import { precacheAndRoute, createHandlerBoundToURL } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { clientsClaim } from "workbox-core";

declare const self: ServiceWorkerGlobalScope;

precacheAndRoute(self.__WB_MANIFEST);
// App shell for every in-app route; API calls always go to the network.
registerRoute(new NavigationRoute(createHandlerBoundToURL("/index.html"), { denylist: [/^\/api\//] }));
self.skipWaiting();
clientsClaim();

self.addEventListener("push", (event) => {
  const data = (() => {
    try {
      return event.data?.json() ?? {};
    } catch {
      return { title: "Octagon Edge", body: event.data?.text() ?? "" };
    }
  })();
  event.waitUntil(
    self.registration.showNotification(data.title ?? "Octagon Edge", {
      body: data.body ?? "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.url ?? "octagon-edge",
      requireInteraction: Boolean(data.urgent),
      data: { url: data.url ?? "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data?.url as string) ?? "/";
  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of wins) {
        if ("focus" in w) {
          await (w as WindowClient).navigate(url);
          return (w as WindowClient).focus();
        }
      }
      return self.clients.openWindow(url);
    })(),
  );
});
